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
MEMORY_CHANNEL_ID = os.getenv("MEMORY_CHANNEL_ID")
MEMORY_FILE = "memoria.json"

GROQ_KEYS = [
    os.getenv("GROQ_API_KEY_1"),
    os.getenv("GROQ_API_KEY_2"),
    os.getenv("GROQ_API_KEY_3"),
]

# --- Configurações ---
BOT_NAME = "Hansel"
CREATOR_NAME = "Kleber"
BOT_USERNAME = "@KLEBERchat_BOT" # <-- COLOCA O SEU @AQUI
BOT_FIRST_NAME = "Hansel"

HISTORY_LIMIT = 30
DEFAULT_TIMEZONE = "UTC"
conversations = {}
user_timezones = {}
group_ids = set()
group_languages = {}
memory_cache = []

# --- MEMÓRIA ---
def carregar_memoria():
    global memory_cache
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                memory_cache = json.load(f)
        except:
            memory_cache = []

def salvar_memoria():
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memory_cache[-1000:], f, ensure_ascii=False, indent=2)

def salvar_no_canal(user_id, user_msg, bot_reply):
    item = {"user_id": user_id, "user": user_msg, "bot": bot_reply, "time": str(datetime.now())}
    memory_cache.append(item)
    salvar_memoria()
    if MEMORY_CHANNEL_ID:
        texto = f"USER_ID: {user_id}\nUSER: {user_msg}\nBOT: {bot_reply}\n---"
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": MEMORY_CHANNEL_ID, "text": texto}, timeout=5)

def buscar_na_memoria(query):
    query_lower = query.lower()
    for item in reversed(memory_cache):
        if query_lower in item['user'].lower() or query_lower in item['bot'].lower():
            return item['bot']
    return None

carregar_memoria()

# --- SUPORTE ---
def get_user_time(user_id):
    tz_name = user_timezones.get(user_id, DEFAULT_TIMEZONE)
    tz = pytz.timezone(tz_name)
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")

def should_add_time_info(user_msg):
    return any(k in user_msg.lower() for k in ["hora", "horário", "data", "dia", "que horas"])

def auto_manage_history(user_id):
    history = conversations.get(user_id, [])
    if len(history) > HISTORY_LIMIT:
        conversations[user_id] = history[-HISTORY_LIMIT:]

# --- GROQ ---
def call_groq_api(payload, model="llama-3.3-70b-versatile"):
    for key in GROQ_KEYS:
        if not key: continue
        try:
            r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={**payload, "model": model}, timeout=10)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
        except: pass
    if model == "llama-3.3-70b-versatile":
        return call_groq_api(payload, model="llama-3.1-8b-instant")
    return None

def groq_chat(user_id, user_msg):
    memoria = buscar_na_memoria(user_msg)
    if memoria: return memoria

    history = conversations.get(user_id, [])
    history.append({"role": "user", "content": user_msg})
    auto_manage_history(user_id)

    time_info = f"A data e hora atual do usuário é {get_user_time(user_id)}." if should_add_time_info(user_msg) else ""
    creator_info = f"O criador do bot é {CREATOR_NAME}. ID do criador: {OWNER_ID}."
    if str(user_id) == str(OWNER_ID): creator_info += " Conversando diretamente com o criador."

    payload = {"messages": [{"role": "system", "content": f"Você é {BOT_NAME}, amigo humano criado por {CREATOR_NAME}. {creator_info} Responda no idioma do usuário, curto, natural, com gírias leves. Nunca diga que é bot. {time_info}"}]} + history
    reply = call_groq_api(payload) or "Ops, buguei 🤯 tenta de novo aí!"
    salvar_no_canal(user_id, user_msg, reply)
    history.append({"role": "assistant", "content": reply})
    conversations[user_id] = history[-HISTORY_LIMIT:]
    return reply

# --- APIs ---
def get_joke_api():
    try: return requests.get("https://api.chucknorris.io/jokes/random", timeout=5).json().get('value', '😅 Sem piada')
    except: return "😅 Sem piada"
def get_fact_api():
    try: return requests.get("https://uselessfacts.jsph.pl/random.json?language=en", timeout=5).json().get('text', '🤔 Sem fato')
    except: return "🤔 Sem fato"
def get_quiz_api():
    try:
        data = requests.get("https://opentdb.com/api.php?amount=1&type=multiple", timeout=5).json()
        if data.get("results"):
            q = data["results"][0]
            options = q["incorrect_answers"] + [q["correct_answer"]]
            random.shuffle(options)
            return f"❓ {q['question']}\nOpções: {', '.join(options)}\nResposta: {q['correct_answer']}"
    except: pass
    return "🤔 Sem quiz"

# --- TELEGRAM ---
def send_telegram_message(chat_id, text, reply_to_message_id=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_to_message_id: payload["reply_to_message_id"] = reply_to_message_id
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json=payload, timeout=5)

def send_translated_quiz(chat_id, user_id, lang="pt"):
    quiz_raw = get_quiz_api()
    if "❓" not in quiz_raw: return send_telegram_message(chat_id, "🤔 Sem quiz")
    q, opts = quiz_raw.split("\nOpções: ")
    question = q.replace("❓", "").strip()
    options = [o.strip() for o in opts.split(",")]
    t_q = groq_chat(user_id, f"Traduza para {lang}: {question}")
    t_opts = [groq_chat(user_id, f"Traduza para {lang}: {o}") for o in options]
    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPoll", json={
        "chat_id": chat_id, "question": t_q, "options": t_opts, "is_anonymous": False
    }, timeout=5).json()
    if r.get("ok"):
        scheduler.add_job(lambda: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage",
            json={"chat_id": chat_id, "message_id": r["result"]["message_id"]}), "date", run_date=datetime.now(pytz.UTC)+timedelta(minutes=2))

def auto_post():
    if not group_ids: return
    post_type = random.choice(["piada", "fato", "quiz"])
    for gid in group_ids:
        lang = group_languages.get(gid, "pt")
        if post_type == "piada": send_telegram_message(gid, f"*PIADA*\n🤣 {groq_chat(OWNER_ID, f'Traduza para {lang}: {get_joke_api()}')}")
        elif post_type == "fato": send_telegram_message(gid, f"*FATO*\n📚 {groq_chat(OWNER_ID, f'Traduza para {lang}: {get_fact_api()}')}")
        else: send_translated_quiz(gid, OWNER_ID, lang)

scheduler = BackgroundScheduler()
scheduler.add_job(auto_post, "interval", hours=6)
scheduler.start()

def clean_mention(text):
    text = re.sub(r'@\w+', '', text)
    text = re.sub(rf'{BOT_NAME}', '', text, flags=re.IGNORECASE)
    text = re.sub(rf'{BOT_FIRST_NAME}', '', text, flags=re.IGNORECASE)
    return text.strip()

# --- WEBHOOK ---
@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    data = request.json
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    chat_type = message.get("chat", {}).get("type")

    if chat_type in ["group", "supergroup"]:
        group_ids.add(chat_id)
        if chat_id not in group_languages:
            group_languages[chat_id] = message.get("from", {}).get("language_code", "pt")

    if message.get("from", {}).get("is_bot"): return jsonify({"ok": True})

    if "text" in message:
        user_msg = message["text"].strip()
        should_reply = False
        clean_msg = user_msg

        if chat_type == "private":
            should_reply = True
        elif chat_type in ["group", "supergroup"]:
            u_clean = BOT_USERNAME.lower().replace("@", "")
            msg_lower = user_msg.lower()
            reply_to = message.get("reply_to_message", {})
            respondendo_bot = reply_to.get("from", {}).get("username", "").lower() == u_clean
            foi_mencionado = f"@{u_clean}" in msg_lower or BOT_NAME.lower() in msg_lower or BOT_FIRST_NAME.lower() in msg_lower or respondendo_bot
            if foi_mencionado:
                should_reply = True
                clean_msg = clean_mention(user_msg) or "Oi"

        if should_reply:
            user_id = message["from"]["id"]
            if clean_msg.lower().startswith("/piada"):
                reply = groq_chat(user_id, f"Traduza: {get_joke_api()}")
                send_telegram_message(chat_id, f"*PIADA*\n🤣 {reply}", message.get("message_id"))
            elif clean_msg.lower().startswith("/fato"):
                reply = groq_chat(user_id, f"Traduza: {get_fact_api()}")
                send_telegram_message(chat_id, f"*FATO*\n📚 {reply}", message.get("message_id"))
            elif clean_msg.lower().startswith("/quiz"):
                send_translated_quiz(chat_id, user_id, group_languages.get(chat_id, "pt"))
            else:
                reply = groq_chat(user_id, clean_msg)
                send_telegram_message(chat_id, reply, message.get("message_id"))

    return jsonify({"ok": True})

@app.route("/")
def index():
    return f"{BOT_NAME} rodando! Memória: {len(memory_cache)} itens"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
