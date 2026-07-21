import os
import requests
from flask import Flask, request, jsonify, send_file
from datetime import datetime, timedelta
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
import random
import io
import base64
import re
import json

app = Flask(__name__)

# --- Variáveis de ambiente ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OWNER_ID = os.getenv("OWNER_ID")
MEMORY_CHANNEL_ID = os.getenv("MEMORY_CHANNEL_ID") # Ex: -1001234567890
MEMORY_FILE = "memoria.json" # arquivo pra não perder memória

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
memory_cache = [] # cache em RAM

# --- FUNÇÕES DE MEMÓRIA COM JSON ---
def carregar_memoria():
    """Carrega memória do arquivo pro cache"""
    global memory_cache
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                memory_cache = json.load(f)
        except:
            memory_cache = []

def salvar_memoria():
    """Salva cache no arquivo"""
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memory_cache[-1000:], f, ensure_ascii=False, indent=2) # guarda últimas 1000

def salvar_no_canal(user_id, user_msg, bot_reply):
    """Salva no canal + no arquivo + no cache"""
    item = {"user_id": user_id, "user": user_msg, "bot": bot_reply, "time": str(datetime.now())}
    memory_cache.append(item)

    # Salva no arquivo
    salvar_memoria()

    # Salva no canal também pra backup
    if MEMORY_CHANNEL_ID:
        texto = f"USER_ID: {user_id}\nUSER: {user_msg}\nBOT: {bot_reply}\n---"
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": MEMORY_CHANNEL_ID, "text": texto}
        try:
            requests.post(url, json=payload, timeout=5)
        except Exception as e:
            print("Erro ao salvar no canal:", e)

def buscar_na_memoria(query):
    """Busca no cache em RAM"""
    query_lower = query.lower()
    # Busca das mais recentes pra mais antigas
    for item in reversed(memory_cache):
        if query_lower in item['user'].lower() or query_lower in item['bot'].lower():
            return item['bot']
    return None

# Carrega memória ao iniciar
carregar_memoria()

# --- Funções de suporte ---
def detect_timezone(ip):
    try:
        response = requests.get(f"https://ipapi.co/{ip}/timezone/", timeout=5)
        if response.status_code == 200:
            tz = response.text.strip()
            if tz:
                return tz
    except:
        pass
    return DEFAULT_TIMEZONE

def get_user_time(user_id):
    tz_name = user_timezones.get(user_id, DEFAULT_TIMEZONE)
    tz = pytz.timezone(tz_name)
    now = datetime.now(tz)
    return now.strftime("%Y-%m-%d %H:%M:%S %Z")

def should_add_time_info(user_msg):
    keywords = ["hora", "horário", "data", "dia", "que horas", "que dia"]
    msg_lower = user_msg.lower()
    return any(keyword in msg_lower for keyword in keywords)

def auto_manage_history(user_id):
    history = conversations.get(user_id, [])
    if len(history) > HISTORY_LIMIT:
        conversations[user_id] = history[-HISTORY_LIMIT:]

# --- Função Groq (IA) ---
def call_groq_api(payload, model="llama-3.3-70b-versatile"):
    for key in GROQ_KEYS:
        if not key:
            continue
        try:
            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={**payload, "model": model},
                timeout=10
            )
            if response.status_code == 200:
                return response.json()["choices"][0]["message"]["content"]
        except:
            pass
    if model == "llama-3.3-70b-versatile":
        return call_groq_api(payload, model="llama-3.1-8b-instant")
    return None

# --- Função de chat COM MEMÓRIA REAL ---
def groq_chat(user_id, user_msg):
    # 1. BUSCA NA MEMÓRIA PRIMEIRO
    memoria = buscar_na_memoria(user_msg)
    if memoria:
        return memoria

    # 2. SE NÃO ACHOU: USA IA
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
            {
                "role": "system",
                "content": (
                    f"Você é {BOT_NAME}, um amigo humano criado por {CREATOR_NAME}. {creator_info} "
                    "Responda sempre no idioma enviado pelo usuário, seja curto, natural, direto, como uma pessoa de verdade. "
                    "Use gírias leves, emojis quando fizer sentido. Nunca diga que é bot ou IA. "
                    "Seja educado e não encha o saco. "
                    f"{time_info}"
                )
            }
        ] + history
    }

    reply = call_groq_api(payload)
    if not reply:
        reply = "Ops, buguei 🤯 tenta de novo aí!"

    # 3. SALVA NA MEMÓRIA
    salvar_no_canal(user_id, user_msg, reply)

    history.append({"role": "assistant", "content": reply})
    conversations[user_id] = history[-HISTORY_LIMIT:]
    return reply

# --- Funções API ---
def get_joke_api():
    try:
        r = requests.get("https://api.chucknorris.io/jokes/random", timeout=5)
        return r.json().get('value', '😅 Não consegui pegar uma piada agora.')
    except:
        return "😅 Não consegui pegar uma piada agora."

def get_fact_api():
    try:
        r = requests.get("https://uselessfacts.jsph.pl/random.json?language=en", timeout=5)
        return r.json().get('text', '🤔 Não consegui achar um fato agora.')
    except:
        return "🤔 Não consegui achar um fato agora."

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
    except:
        return "🤔 Não consegui pegar um quiz agora."
    return "🤔 Não consegui pegar um quiz agora."

# --- Telegram ---
def send_telegram_message(chat_id, text, reply_to_message_id=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print("Erro Telegram:", e)

# --- Quiz com enquetes nativas ---
def send_translated_quiz(chat_id, user_id, lang="pt"):
    quiz_raw = get_quiz_api()
    if not quiz_raw or "❓" not in quiz_raw:
        send_telegram_message(chat_id, "🤔 Não consegui gerar um quiz agora.")
        return

    try:
        question_part, options_part = quiz_raw.split("\nOpções: ")
        question = question_part.replace("❓", "").strip()
        options = [opt.strip() for opt in options_part.split(",")]
    except:
        send_telegram_message(chat_id, "🤔 Erro ao preparar o quiz.")
        return

    translated_question = groq_chat(user_id, f"Traduza para {lang} apenas esta pergunta: {question}")
    translated_options = []
    for opt in options:
        t_opt = groq_chat(user_id, f"Traduza para {lang} apenas esta opção: {opt}")
        translated_options.append(t_opt)

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPoll"
    payload = {
        "chat_id": chat_id,
        "question": translated_question,
        "options": translated_options,
        "is_anonymous": False
    }
    try:
        resp = requests.post(url, json=payload, timeout=5).json()
        if resp.get("ok"):
            poll_message_id = resp["result"]["message_id"]
            scheduler.add_job(
                lambda: requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage",
                    json={"chat_id": chat_id, "message_id": poll_message_id},
                    timeout=5
                ),
                "date",
                run_date=datetime.now(pytz.UTC) + timedelta(minutes=2)
            )
    except Exception as e:
        print("Erro ao enviar quiz:", e)

# --- Postagens automáticas traduzidas ---
def auto_post():
    if not group_ids:
        return
    post_type = random.choice(["piada", "fato", "quiz"])
    for gid in group_ids:
        user_lang = group_languages.get(gid, "pt")
        if post_type == "piada":
            post = get_joke_api()
            post = groq_chat(OWNER_ID, f"Traduza e adapte para {user_lang}: {post}")
            post = f"*PIADA*\n🤣 {post}"
        elif post_type == "fato":
            post = get_fact_api()
            post = groq_chat(OWNER_ID, f"Traduza e adapte para {user_lang}: {post}")
            post = f"*FATO CURIOSO*\n📚 {post}"
        else:
            send_translated_quiz(gid, OWNER_ID, user_lang)
            continue
        send_telegram_message(gid, post)

scheduler = BackgroundScheduler()
scheduler.add_job(auto_post, "interval", hours=6)
scheduler.start()

# --- Limpar menção do texto ---
def clean_mention(text):
    text = re.sub(r'@\w+', '', text)
    text = re.sub(rf'{BOT_NAME}', '', text, flags=re.IGNORECASE)
    return text.strip()

# --- Webhook ---
@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    data = request.json
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    chat_type = message.get("chat", {}).get("type")

    if chat_type in ["group", "supergroup"]:
        group_ids.add(chat_id)
        if chat_id not in group_languages:
            lang_code = message.get("from", {}).get("language_code", "pt")
            group_languages[chat_id] = lang_code

    if message.get("from", {}).get("is_bot"):
        return jsonify({"ok": True})

    if "text" in message:
        user_msg = message["text"].strip()
        should_reply = False
        clean_msg = user_msg

        # REGRA 1: PV responde tudo
        if chat_type == "private":
            should_reply = True

        # REGRA 2: GRUPO só responde se mencionar
        elif chat_type in ["group", "supergroup"]:
            username_clean = BOT_USERNAME.lower().replace("@", "")
            msg_lower = user_msg.lower()

            foi_mencionado = (
                f"@{username_clean}" in msg_lower or
                BOT_NAME.lower() in msg_lower or
                message.get("reply_to_message", {}).get("from", {}).get("username", "").lower() == username_clean
            )

            if foi_mencionado:
                should_reply = True
                clean_msg = clean_mention(user_msg)
                if clean_msg == "":
                    clean_msg = "Oi"

        if should_reply:
            try:
                user_id = message["from"]["id"]

                if clean_msg.lower().startswith("/piada"):
                    post = get_joke_api()
                    reply = groq_chat(user_id, f"Traduza e adapte para o idioma do usuário: {post}")
                    send_telegram_message(chat_id, f"*PIADA*\n🤣 {reply}", reply_to_message_id=message.get("message_id"))
                    scheduler.add_job(lambda: requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage",
                        json={"chat_id": chat_id, "message_id": message.get("message_id")},
                        timeout=5
                    ), "date", run_date=datetime.now(pytz.UTC) + timedelta(seconds=5))
                    return jsonify({"ok": True})

                elif clean_msg.lower().startswith("/fato"):
                    post = get_fact_api()
                    reply = groq_chat(user_id, f"Traduza e adapte para o idioma do usuário: {post}")
                    send_telegram_message(chat_id, f"*FATO CURIOSO*\n📚 {reply}", reply_to_message_id=message.get("message_id"))
                    scheduler.add_job(lambda: requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage",
                        json={"chat_id": chat_id, "message_id": message.get("message_id")},
                        timeout=5
                    ), "date", run_date=datetime.now(pytz.UTC) + timedelta(seconds=5))
                    return jsonify({"ok": True})

                elif clean_msg.lower().startswith("/quiz"):
                    user_lang = group_languages.get(chat_id, "pt")
                    send_translated_quiz(chat_id, user_id, user_lang)
                    scheduler.add_job(lambda: requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage",
                        json={"chat_id": chat_id, "message_id": message.get("message_id")},
                        timeout=5
                    ), "date", run_date=datetime.now(pytz.UTC) + timedelta(seconds=5))
                    return jsonify({"ok": True})

                else:
                    reply = groq_chat(user_id, clean_msg)
                    send_telegram_message(chat_id, reply, reply_to_message_id=message.get("message_id"))

            except Exception as e:
                print("Erro ao processar mensagem:", e)
                reply = groq_chat(message["from"]["id"], clean_msg)
                send_telegram_message(chat_id, reply, reply_to_message_id=message.get("message_id"))

    return jsonify({"ok": True})

# --- Favicon ---
@app.route("/favicon.ico")
def favicon():
    ico_base64 = b"AAABAAEAEBAAAAEAIABoBAAAFgAAACgAAAAQAAAAIAAAAAEAIAAAAAAAAAAAAAAAA"
    ico_bytes = base64.b64decode(ico_base64)
    return send_file(io.BytesIO(ico_bytes), mimetype="image/vnd.microsoft.icon")

# --- Index ---
@app.route("/")
def index():
    return f"{BOT_NAME} rodando! Criado por {CREATOR_NAME} 🎭 | Memória: {len(memory_cache)} itens"

# --- Main ---
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
