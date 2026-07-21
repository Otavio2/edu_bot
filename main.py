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
import time

app = Flask(__name__)

# --- Variáveis de ambiente ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OWNER_ID = os.getenv("OWNER_ID")
MEMORY_CHANNEL_ID = os.getenv("MEMORY_CHANNEL_ID") # Ex: -1001234567890
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
BOT_ID = 123456789 # <-- PEGA SEU ID COM @userinfobot no Telegram

HISTORY_LIMIT = 30
DEFAULT_TIMEZONE = "UTC"
conversations = {}
user_timezones = {}
group_ids = set()
group_languages = {}
memory_cache = []

# --- MEMÓRIA INFINITA COM CANAL ---
def salvar_memoria():
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memory_cache[-3000:], f, ensure_ascii=False, indent=2)

def remover_duplicadas_antigas():
    """Remove repetições em sequência do histórico carregado"""
    global memory_cache
    if not memory_cache:
        return
        
    historico_limpo = []
    for item in memory_cache:
        if not historico_limpo:
            historico_limpo.append(item)
            continue
            
        ultimo_salvo = historico_limpo[-1]
        se_repetiu = (
            str(ultimo_salvo.get("user_id")) == str(item.get("user_id")) and
            ultimo_salvo.get("user") == item.get("user") and
            ultimo_salvo.get("bot") == item.get("bot")
        )
        
        if not se_repetiu:
            historico_limpo.append(item)
            
    if len(memory_cache) != len(historico_limpo):
        print(f"Limpeza de duplicadas antigas: {len(memory_cache) - len(historico_limpo)} itens removidos.")
        memory_cache = historico_limpo
        # Salva o arquivo local limpo sem mexer no canal antigo
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(memory_cache[-3000:], f, ensure_ascii=False, indent=2)

def carregar_memoria_do_canal():
    """Baixa todas as mensagens do canal e popula a memória"""
    global memory_cache
    if not MEMORY_CHANNEL_ID:
        return

    offset = 0
    limit = 100
    todos_itens = []
    print("Carregando memória do canal...")

    while True:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getChatHistory"
        try:
            r = requests.post(url, json={
                "chat_id": MEMORY_CHANNEL_ID,
                "limit": limit,
                "offset": offset
            }, timeout=15).json()

            if not r.get("ok"):
                break

            mensagens = r.get("result", {}).get("messages", [])
            if not mensagens:
                break

            for msg in mensagens:
                if "text" in msg and "USER_ID:" in msg["text"]:
                    try:
                        linhas = msg["text"].split("\n")
                        user_id = linhas[0].replace("USER_ID: ", "").strip()
                        user = linhas[1].replace("USER: ", "").strip()
                        bot = linhas[2].replace("BOT: ", "").strip()
                        todos_itens.append({"user_id": user_id, "user": user, "bot": bot, "time": msg["date"]})
                    except:
                        continue

            if len(mensagens) < limit:
                break
            offset += limit
            time.sleep(0.1)

        except Exception as e:
            print("Erro ao carregar canal:", e)
            break

    memory_cache = todos_itens[::-1]
    remover_duplicadas_antigas()  # Limpa o cache logo após puxar os dados
    salvar_memoria()
    print(f"Memória carregada e limpa: {len(memory_cache)} itens")

def carregar_memoria():
    global memory_cache
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                memory_cache = json.load(f)
        except:
            memory_cache = []
    carregar_memoria_do_canal()

def salvar_no_canal(user_id, user_msg, bot_reply):
    # 1. EVITA QUE NOVAS REPETIÇÕES SEJAM SALVAS OU ENVIADAS
    if memory_cache:
        ultima = memory_cache[-1]
        se_repetiu = (
            str(ultima.get("user_id")) == str(user_id) and 
            ultima.get("user") == user_msg and 
            ultima.get("bot") == bot_reply
        )
        if se_repetiu:
            return  # Corta a execução imediatamente

    # 2. SE FOR NOVA, SEGUE O PROCESSO DE SALVAMENTO
    item = {"user_id": user_id, "user": user_msg, "bot": bot_reply, "time": str(datetime.now())}
    memory_cache.append(item)
    salvar_memoria()
    if MEMORY_CHANNEL_ID:
        texto = f"USER_ID: {user_id}\nUSER: {user_msg}\nBOT: {bot_reply}\n---"
        try:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                          json={"chat_id": MEMORY_CHANNEL_ID, "text": texto}, timeout=5)
        except Exception as e:
            print("Erro ao save no canal:", e)

def gerar_resumo_usuario(user_id):
    infos = [item for item in memory_cache if str(item['user_id']) == str(user_id)]
    if not infos: return ""
    texto = "\n".join([f"Usuário: {i['user']}" for i in infos[-25:]])
    prompt = f"Resuma em 5 linhas o que você sabe sobre esta pessoa: {texto}"
    resumo = call_groq_api({"messages": [{"role": "user", "content": prompt}]}, model="llama-3.1-8b-instant")
    return f"MEMÓRIA DO USUÁRIO: {resumo}\n" if resumo else ""

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
                json={**payload, "model": model}, timeout=15)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
        except: pass
    if model == "llama-3.3-70b-versatile":
        return call_groq_api(payload, model="llama-3.1-8b-instant")
    return None

def groq_chat(user_id, user_msg):
    memoria_resumo = gerar_resumo_usuario(user_id)

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
                "content": f"Você é {BOT_NAME}, amigo humano criado por {CREATOR_NAME}. {creator_info} {memoria_resumo} Responda no idioma do usuário, curto, natural, com gírias leves. Nunca diga que é bot. {time_info}"
            }
        ] + history
    }
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
    
    # Trecho final que estava cortado: Correção do payload e do envio da enquete (Poll)
    payload = {
        "chat_id": chat_id, 
        "question": t_q, 
        "options": t_opts, 
        "is_anonymous": False
    }
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPoll", json=payload, timeout=5)
    
