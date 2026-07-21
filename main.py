import os
import requests
from flask import Flask, request, jsonify, send_file
from datetime import datetime, timedelta
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
import random
import io
import base64
import sqlite3
import difflib

app = Flask(__name__)

# --- Variáveis de ambiente ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OWNER_ID = os.getenv("OWNER_ID")
MEMORY_CHANNEL_ID = os.getenv("MEMORY_CHANNEL_ID") # Ex: -1003791940625
WEBHOOK_URL = os.getenv("WEBHOOK_URL") # Ex: https://seu-app.onrender.com

# Suporte a múltiplas chaves Groq
GROQ_KEYS = [
    os.getenv("GROQ_API_KEY_1"),
    os.getenv("GROQ_API_KEY_2"),
    os.getenv("GROQ_API_KEY_3"),
]

# Configurações fixas
BOT_NAME = "Hansel"
CREATOR_NAME = "Kleber"
BOT_USERNAME = "@Group_klbBot"

# Limites e histórico
HISTORY_LIMIT = 30
DEFAULT_TIMEZONE = "UTC"
conversations = {}
user_timezones = {}
group_ids = set()
group_languages = {}

# --- BANCO LOCAL PRA BUSCA RÁPIDA - RENDER FREE USA /tmp ---
os.makedirs("/tmp", exist_ok=True)
DB_PATH = "/tmp/memoria_bot.db"

conn = sqlite3.connect(DB_PATH, check_same_thread=False)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS memoria
             (id INTEGER PRIMARY KEY, chat_id INTEGER, user_id INTEGER, pergunta TEXT, resposta TEXT, timestamp DATETIME)''')
conn.commit()

# --- Funções de suporte ---
def detect_timezone(ip):
    try:
        response = requests.get(f"https://ipapi.co/{ip}/timezone/", timeout=5)
        if response.status_code == 200:
            tz = response.text.strip()
            if tz: return tz
    except: pass
    return DEFAULT_TIMEZONE

def get_user_time(user_id):
    tz_name = user_timezones.get(user_id, DEFAULT_TIMEZONE)
    tz = pytz.timezone(tz_name)
    now = datetime.now(tz)
    return now.strftime("%Y-%m-%d %H:%M:%S %Z")

def should_add_time_info(user_msg):
    keywords = ["hora", "horário", "data", "dia", "que horas", "que dia"]
    return any(keyword in user_msg.lower() for keyword in keywords)

def auto_manage_history(user_id):
    history = conversations.get(user_id, [])
    if len(history) > HISTORY_LIMIT:
        conversations[user_id] = history[-HISTORY_LIMIT:]

# --- Funções de Memória no Canal ---
def salvar_no_canal(chat_origem, user_id, pergunta, resposta):
    if not MEMORY_CHANNEL_ID or not TELEGRAM_TOKEN:
        print("ERRO: MEMORY_CHANNEL_ID ou TOKEN vazio")
        return

    # Não salvar comandos pra não poluir o canal
    if pergunta.startswith("/"):
        return

    texto = f"""🧠 NOVA MEMÓRIA

**De:** `{chat_origem}`
**User:** `{user_id}`
**Pergunta:** {pergunta}
**Resposta:** {resposta}
**Data:** {datetime.now().strftime('%d/%m %H:%M')}"""

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": MEMORY_CHANNEL_ID, "text": texto, "parse_mode": "Markdown"}

    try:
        r = requests.post(url, json=payload, timeout=5)
        print("Resposta do Telegram ao salvar:", r.json())
        if not r.json().get("ok"):
            print("ERRO TELEGRAM:", r.json().get("description"))
    except Exception as e:
        print("Erro ao salvar no canal:", e)

def buscar_memoria_local(pergunta, limite=0.8):
    c.execute("SELECT pergunta, resposta FROM memoria ORDER BY timestamp DESC LIMIT 500")
    dados = c.fetchall()
    for p_salva, r_salva in dados:
        similaridade = difflib.SequenceMatcher(None, pergunta.lower(), p_salva).ratio()
        if similaridade > limite:
            return r_salva
    return None

def salvar_memoria_local(chat_id, user_id, pergunta, resposta):
    c.execute("INSERT INTO memoria (chat_id, user_id, pergunta, resposta, timestamp) VALUES (?,?,?,?,?)",
              (chat_id, user_id, pergunta.lower(), resposta, datetime.now()))
    conn.commit()

# --- Função Groq (IA) ---
def call_groq_api(payload, model="llama-3.3-70b-versatile"):
    for key in GROQ_KEYS:
        if not key: continue
        try:
            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={**payload, "model": model}, timeout=10
            )
            if response.status_code == 200:
                return response.json()["choices"][0]["message"]["content"]
        except: pass
    if model == "llama-3.3-70b-versatile":
        return call_groq_api(payload, model="llama-3.1-8b-instant")
    return None

# --- Função de chat com economia de IA ---
def groq_chat(user_id, user_msg, chat_id):
    # 1. BUSCA PRIMEIRO NA MEMÓRIA LOCAL
    resposta_memoria = buscar_memoria_local(user_msg)
    if resposta_memoria:
        print(f"[MEMÓRIA] Resposta sem IA para: {user_msg[:30]}")
        return resposta_memoria

    # 2. SE NÃO ACHOU: USA IA
    print(f"[IA] Gastando token para: {user_msg[:30]}")
    history = conversations.get(user_id, [])
    history.append({"role": "user", "content": user_msg})
    auto_manage_history(user_id)

    time_info = ""
    if should_add_time_info(user_msg):
        current_time = get_user_time(user_id)
        time_info = f"A data e hora atual do usuário é {current_time}."

    creator_info = f"O criador do bot é {CREATOR_NAME}. ID do criador: {OWNER_ID}."
    if str(user_id) == str(OWNER_ID):
        creator_info += " Conversando diretamente com o criador."

    payload = {
        "messages": [
            {"role": "system", "content": f"Você é {BOT_NAME}, bot criado por {CREATOR_NAME}. {creator_info} Responda sempre no idioma enviado pelo usuário, seja curto, natural, direto, com emojis quando fizer sentido. {time_info}"}
        ] + history
    }

    reply = call_groq_api(payload)
    if not reply: reply = "Ops, buguei 🤯 tenta de novo aí!"

    # 3. SALVA EM TUDO: CANAL + LOCAL + HISTORICO
    salvar_no_canal(chat_id, user_id, user_msg, reply)
    salvar_memoria_local(chat_id, user_id, user_msg, reply)
    history.append({"role": "assistant", "content": reply})
    conversations[user_id] = history[-HISTORY_LIMIT:]
    return reply

# --- Funções API ---
def get_joke_api():
    try: return requests.get("https://api.chucknorris.io/jokes/random", timeout=5).json().get('value', '😅 Sem piada')
    except: return "😅 Sem piada"

def get_fact_api():
    try: return requests.get("https://uselessfacts.jsph.pl/random.json?language=en", timeout=5).json().get('text', '🤔 Sem fato')
    except: return "🤔 Sem fato"

def get_quiz_api():
    try:
        r = requests.get("https://opentdb.com/api.php?amount=1&type=multiple", timeout=5)
        data = r.json()
        if data.get("results"):
            q = data["results"][0]
            question = q["question"]
            correct = q["correct_answer"]
            options = q["incorrect_answers"] + [correct]
            random.shuffle(options)
            return f"❓ {question}\nOpções: {', '.join(options)}\nResposta: {correct}"
    except: return "🤔 Sem quiz"
    return "🤔 Sem quiz"

# --- Telegram ---
def send_telegram_message(chat_id, text, reply_to_message_id=None):
    if not TELEGRAM_TOKEN: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    if reply_to_message_id: payload["reply_to_message_id"] = reply_to_message_id
    try: requests.post(url, json=payload, timeout=5)
    except Exception as e: print("Erro Telegram:", e)

# --- Quiz com enquetes ---
def send_translated_quiz(chat_id, user_id, lang="pt"):
    quiz_raw = get_quiz_api()
    if not quiz_raw or "❓" not in quiz_raw: return
    try:
        question_part, options_part = quiz_raw.split("\nOpções: ")
        question = question_part.replace("❓", "").strip()
        options = [opt.strip() for opt in options_part.split(",")]
    except: return

    translated_question = groq_chat(user_id, f"Traduza para {lang} apenas esta pergunta: {question}", chat_id)
    translated_options = [groq_chat(user_id, f"Traduza para {lang} apenas esta opção: {opt}", chat_id) for opt in options]

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPoll"
    payload = {"chat_id": chat_id, "question": translated_question, "options": translated_options, "is_anonymous": False}
    try:
        resp = requests.post(url, json=payload, timeout=5).json()
        if resp.get("ok"):
            poll_message_id = resp["result"]["message_id"]
            scheduler.add_job(lambda: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage",
                json={"chat_id": chat_id, "message_id": poll_message_id}, timeout=5), "date",
                run_date=datetime.now(pytz.UTC) + timedelta(minutes=2))
    except Exception as e: print("Erro ao enviar quiz:", e)

# --- Postagens automáticas ---
def auto_post():
    if not group_ids: return
    post_type = random.choice(["piada", "fato", "quiz"])
    for gid in group_ids:
        user_lang = group_languages.get(gid, "pt")
        if post_type == "piada":
            post = get_joke_api()
            post = groq_chat(OWNER_ID, f"Traduza e adapte para {user_lang}: {post}", gid)
            post = f"PIADA\n🤣 {post}"
        elif post_type == "fato":
            post = get_fact_api()
            post = groq_chat(OWNER_ID, f"Traduza e adapte para {user_lang}: {post}", gid)
            post = f"FATO CURIOSO\n📚 {post}"
        else:
            send_translated_quiz(gid, OWNER_ID, user_lang)
            continue
        send_telegram_message(gid, post)

scheduler = BackgroundScheduler()
scheduler.add_job(auto_post, "interval", hours=6)
scheduler.start()

# --- Ativar Webhook automaticamente ao subir ---
def set_webhook():
    if not WEBHOOK_URL or not TELEGRAM_TOKEN:
        print("WEBHOOK_URL ou TELEGRAM_TOKEN faltando")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook"
    full_url = f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}"
    try:
        r = requests.post(url, json={"url": full_url}, timeout=5)
        print("Webhook setado:", r.json())
    except Exception as e:
        print("Erro ao setar webhook:", e)

set_webhook()

# --- Webhook com regra educada ---
@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    data = request.json
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    chat_type = message.get("chat", {}).get("type")
    user_id = message.get("from", {}).get("id")

    if chat_type in ["group", "supergroup"]:
        group_ids.add(chat_id)
        if chat_id not in group_languages:
            group_languages[chat_id] = message.get("from", {}).get("language_code", "pt")

    if message.get("from", {}).get("is_bot"): return jsonify({"ok": True})

    if "text" in message:
        user_msg = message["text"].strip()
        should_reply = False
        msg_lower = user_msg.lower()

        # REGRA EDUCADA PRA GRUPO
        foi_mencionado = BOT_NAME.lower() in msg_lower or (BOT_USERNAME and BOT_USERNAME.lower() in msg_lower)
        foi_educado = any(p in msg_lower for p in ["por favor", "pfv", "obrigado", "obg", "pode"])
        eh_comando = user_msg.startswith(("/", "!", "."))
        respondeu_ele = message.get("reply_to_message", {}).get("from", {}).get("username", "").lower() == BOT_USERNAME.lower().replace("@", "")

        if chat_type == "private": should_reply = True
        elif (foi_mencionado and foi_educado) or respondeu_ele: should_reply = True
        elif eh_comando: should_reply = True

        if should_reply:
            try:
                if user_msg.lower().startswith("/piada"):
                    post = get_joke_api()
                    reply = groq_chat(user_id, f"Traduza e adapte para o idioma do usuário: {post}", chat_id)
                    send_telegram_message(chat_id, f"PIADA\n🤣 {reply}", message.get("message_id"))
                elif user_msg.lower().startswith("/fato"):
                    post = get_fact_api()
                    reply = groq_chat(user_id, f"Traduza e adapte para o idioma do usuário: {post}", chat_id)
                    send_telegram_message(chat_id, f"FATO CURIOSO\n📚 {reply}", message.get("message_id"))
                elif user_msg.lower().startswith("/quiz"):
                    user_lang = group_languages.get(chat_id, "pt")
                    send_translated_quiz(chat_id, user_id, user_lang)
                else:
                    reply = groq_chat(user_id, user_msg, chat_id)
                    send_telegram_message(chat_id, reply, message.get("message_id"))
            except Exception as e:
                print("Erro ao processar mensagem:", e)

    return jsonify({"ok": True})

# --- Favicon e Index ---
@app.route("/favicon.ico")
def favicon():
    ico_base64 = b"AAABAAEAEBAAAAEAIABoBAAAFgAAACgAAAAQAAAAIAAAAAEAIAAAAAAAAAAAAAAAA"
    return send_file(io.BytesIO(base64.b64decode(ico_base64)), mimetype="image/vnd.microsoft.icon")

@app.route("/")
def index(): return f"{BOT_NAME} rodando com memória em canal! Criado por {CREATOR_NAME} 🎭"
