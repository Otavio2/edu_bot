import os
import requests
from flask import Flask, request, jsonify
from datetime import datetime, timedelta
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
import random
import re
import json
import time

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
BOT_USERNAME = "@Group_klbBot"
BOT_FIRST_NAME = "Hansel"
BOT_ID = 123456789 # <-- PEGA SEU ID COM @userinfobot

HISTORY_LIMIT = 30
DEFAULT_TIMEZONE = "UTC"
conversations = {}
user_timezones = {}
group_ids = set()
group_languages = {}
memory_cache = []

# --- FILTRO ANTI CONTEÚDO +18 ---
PALAVRAS_BLOQUEADAS = [
    "porn", "sexo", "puta", "pinto", "buceta", "fuder", "gozar", "nudes", "hentai", "xxx"
]
def contem_conteudo_bloqueado(texto):
    texto_lower = texto.lower()
    return any(p in texto_lower for p in PALAVRAS_BLOQUEADAS)

# --- MEMÓRIA INFINITA CORRIGIDA ---
def salvar_memoria():
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memory_cache[-5000:], f, ensure_ascii=False, indent=2) # Aumentei pra 5000

def ja_existe_na_memoria(user_id, user_msg, bot_reply):
    if not memory_cache:
        return False
    ultimas = [i for i in memory_cache if str(i['user_id']) == str(user_id)][-5:]
    for item in ultimas:
        if item['user'].strip().lower() == user_msg.strip().lower() and item['bot'].strip().lower() == bot_reply.strip().lower():
            return True
    return False

def carregar_memoria():
    global memory_cache
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                memory_cache = json.load(f)
            print(f"Memória carregada do arquivo: {len(memory_cache)} itens")
        except:
            memory_cache = []
            print("Erro ao carregar memoria.json")
    else:
        print("Arquivo de memória não existe, começando do zero")

def salvar_no_canal(user_id, user_msg, bot_reply, importante=False, chat_id=None):
    if contem_conteudo_bloqueado(user_msg) or contem_conteudo_bloqueado(bot_reply):
        print(f"Mensagem bloqueada, não salva: {user_id}")
        return
    if ja_existe_na_memoria(user_id, user_msg, bot_reply):
        print(f"Mensagem repetida, ignorada: {user_id}")
        return

    item = {"user_id": user_id, "user": user_msg, "bot": bot_reply, "time": str(datetime.now()), "importante": importante, "chat_id": chat_id}
    memory_cache.append(item)
    salvar_memoria() # Salva no arquivo primeiro

    # Envia pro canal como backup
    if MEMORY_CHANNEL_ID:
        tag = "\nIMPORTANTE" if importante else ""
        texto = f"USER_ID: {user_id}\nUSER: {user_msg}\nBOT: {bot_reply}{tag}\n---"
        try:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                          json={"chat_id": MEMORY_CHANNEL_ID, "text": texto}, timeout=5)
        except Exception as e:
            print("Erro ao salvar no canal:", e)

def gerar_resumo_usuario(user_id):
    infos = [item for item in memory_cache if str(item['user_id']) == str(user_id)]
    if not infos: return ""
    # Prioriza mensagens importantes
    importantes = [i for i in infos if i.get("importante")]
    normais = [i for i in infos if not i.get("importante")][-15:]
    texto = "\n".join([f"IMPORTANTE: {i['user']}" for i in importantes[-10:]] + [f"Usuário: {i['user']}" for i in normais])
    if not texto: return ""
    prompt = f"Resuma em 5 linhas o que você sabe sobre esta pessoa. Foque no que está marcado como IMPORTANTE: {texto}"
    resumo = call_groq_api({"messages": [{"role": "user", "content": prompt}]}, model="llama-3.1-8b-instant")
    return f"MEMÓRIA DO USUÁRIO: {resumo}\n" if resumo else ""

carregar_memoria() # Agora só carrega do arquivo

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

# --- GROQ COM TEMPERATURE ALTA = MAIS CRIATIVO ---
def call_groq_api(payload, model="llama-3.3-70b-versatile", temperature=0.9):
    for key in GROQ_KEYS:
        if not key: continue
        try:
            r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={**payload, "model": model, "temperature": temperature}, timeout=15)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
        except: pass
    if model == "llama-3.3-70b-versatile":
        return call_groq_api(payload, model="llama-3.1-8b-instant", temperature=temperature)
    return None

def groq_chat(user_id, user_msg, chat_id=None):
    if contem_conteudo_bloqueado(user_msg):
        return "Opa, prefiro não falar sobre isso 😅 Bora mudar de assunto?"

    memoria_resumo = gerar_resumo_usuario(user_id) # Agora ele realmente lê do arquivo
    history = conversations.get(user_id, [])
    history.append({"role": "user", "content": user_msg})
    auto_manage_history(user_id)

    time_info = f"A data e hora atual do usuário é {get_user_time(user_id)}." if should_add_time_info(user_msg) else ""
    creator_info = f"O criador do bot é {CREATOR_NAME}. ID do criador: {OWNER_ID}."
    if str(user_id) == str(OWNER_ID): creator_info += " Conversando diretamente com o criador."

    payload = {
        "messages": [
            {
                "role": "system",
                "content": f"Você é {BOT_NAME}, amigo humano criado por {CREATOR_NAME}. {creator_info} {memoria_resumo} Responda no idioma do usuário, curto, natural, com gírias leves, bem-humorado e CRIATIVO. Varie suas respostas, não seja repetitivo. NUNCA fale de conteúdo sexual, pornográfico ou +18. Se pedirem, mude de assunto educadamente. {time_info}"
            }
        ] + history
    }
    reply = call_groq_api(payload, temperature=0.9) or "Ops, buguei 🤯 tenta de novo aí!"

    if contem_conteudo_bloqueado(reply):
        reply = "Prefiro não entrar nesse assunto 😅 Quer conversar sobre outra coisa?"

    salvar_no_canal(user_id, user_msg, reply, chat_id=chat_id)
    history.append({"role": "assistant", "content": reply})
    conversations[user_id] = history[-HISTORY_LIMIT:]
    return reply

# --- LIDAR COM REAÇÕES/LIKES ---
def marcar_como_importante(chat_id, message_id):
    for item in reversed(memory_cache):
        if str(item.get('chat_id')) == str(chat_id):
            item['importante'] = True
            salvar_memoria()
            salvar_no_canal(item['user_id'], item['user'], item['bot'], importante=True, chat_id=chat_id)
            print(f"Mensagem marcada como IMPORTANTE: {item['user'][:20]}")
            break

# --- APIs ---
def get_joke_api():
    try: return requests.get("https://api.chucknorris.io/jokes/random", timeout=5).json().get('value', '😅 Sem piada')
    except: return "😅 Sem piada"
def get_fact_api():
    try: return requests.get("https://uselessfacts.jsph.pl/random.json?language=en", timeout=5).json().get('text', '🤔 Sem fato')
    except: return "🤔 Sem fato"

def send_telegram_message(chat_id, text, reply_to_message_id=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_to_message_id: payload["reply_to_message_id"] = reply_to_message_id
    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json=payload, timeout=5)
    return r.json().get("result", {}).get("message_id")

def auto_post(): pass
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

    # 1. LIDAR COM REAÇÕES
    if "message_reaction" in data:
        reaction = data["message_reaction"]
        if any(r['emoji'] == '❤️' for r in reaction.get('new_reaction', [])):
            marcar_como_importante(reaction['chat']['id'], reaction['message_id'])
        return jsonify({"ok": True})

    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    chat_type = message.get("chat", {}).get("type")

    if chat_type in ["group", "supergroup"]: group_ids.add(chat_id)
    if message.get("from", {}).get("is_bot"): return jsonify({"ok": True})

    if "text" in message:
        user_msg = message["text"].strip()
        should_reply = False
        clean_msg = user_msg

        if chat_type == "private": should_reply = True
        elif chat_type in ["group", "supergroup"]:
            u_clean = BOT_USERNAME.lower().replace("@", "")
            msg_lower = user_msg.lower()
            reply_to = message.get("reply_to_message", {})
            respondendo_bot = False
            if reply_to:
                reply_from = reply_to.get("from", {})
                respondendo_bot = str(reply_from.get("id")) == str(BOT_ID)
            foi_mencionado = f"@{u_clean}" in msg_lower or BOT_NAME.lower() in msg_lower or BOT_FIRST_NAME.lower() in msg_lower or respondendo_bot
            if foi_mencionado:
                should_reply = True
                clean_msg = clean_mention(user_msg) or "Oi"

        if should_reply:
            user_id = message["from"]["id"]
            reply = groq_chat(user_id, clean_msg, chat_id)
            msg_id = send_telegram_message(chat_id, reply, message.get("message_id"))
            # Atualiza o último item com message_id
            if memory_cache:
                memory_cache[-1]['message_id'] = msg_id
                salvar_memoria()

    return jsonify({"ok": True})

@app.route("/")
def index():
    return f"{BOT_NAME} rodando! Memória: {len(memory_cache)} itens | Criador: {CREATOR_NAME}"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
