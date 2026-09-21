import os, time, json, base64, re, threading, requests, random
from collections import defaultdict, deque
from flask import Flask, request

# === MARCA REGISTRADA ORIGINAL ===
SIGNATURE = "Kʆɛɓɛʀ"
BOT_NAME = "ADM"
CREATOR_ID = str(os.getenv("CREATOR_ID","8398287578"))

BOT_TOKEN = os.getenv("BOT_TOKEN","").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN não encontrado")
API = f"https://api.telegram.org/bot{BOT_TOKEN}"
MY_ID = int(os.getenv("BOT_ID","0")) if os.getenv("BOT_ID") else 0
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET","").strip()
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL","") or os.getenv("WEBHOOK_URL","") or "https://edu-bot-6yfa.onrender.com"
if not RENDER_URL.startswith("http"):
    RENDER_URL = f"https://{RENDER_URL}"

app = Flask(__name__)

PROVIDERS_RAW = {
    "groq":{"key_env":"GROQ_API_KEY","endpoint":"https://api.groq.com/openai/v1","format":"openai","timeout":6},
    "gemini":{"key_env":"GEMINI_API_KEY","endpoint":"https://generativelanguage.googleapis.com/v1beta","format":"gemini","timeout":8},
    "cerebras":{"key_env":"CEREBRAS_API_KEY","endpoint":"https://api.cerebras.ai/v1","format":"openai","timeout":6},
    "openrouter":{"key_env":"OPENROUTER_API_KEY","endpoint":"https://openrouter.ai/api/v1","format":"openai","timeout":8},
    "mistral":{"key_env":"MISTRAL_API_KEY","endpoint":"https://api.mistral.ai/v1","format":"openai","timeout":8},
}
FALLBACK_MODELS = {
    "groq":["llama-3.3-70b-versatile","llama-3.1-8b-instant"],
    "gemini":["gemini-2.0-flash","gemini-1.5-flash"],
    "cerebras":["llama-3.3-70b","llama3.1-8b"],
    "openrouter":["meta-llama/llama-3.1-8b-instruct:free"],
    "mistral":["mistral-large-latest","mistral-small-latest"]
}

AI_BLACK = {}; AI_PROV_BLACK = {}; AI_STATS = {"calls":0}
thread_local = threading.local()
def get_sess():
    if not hasattr(thread_local,"s"):
        thread_local.s = requests.Session()
    return thread_local.s

bio_cache = {}; perm_cache = {}; admin_cache = {}; flood_hist = defaultdict(lambda: deque(maxlen=10))

def tg(m,p):
    try:
        return get_sess().post(f"{API}/{m}", json=p, timeout=10).json()
    except:
        return {}

def get_bio(cid, force=False):
    cid = str(cid); now = time.time()
    if not force and cid in bio_cache and now - bio_cache[cid]['t'] < 600:
        return bio_cache[cid]
    r = tg("getChat",{"chat_id":int(cid) if cid.lstrip('-').isdigit() else cid})
    ch = r.get("result",{})
    d = {"name":ch.get("title","") or ch.get("first_name",""), "bio":ch.get("description","") or "", "t":now}
    bio_cache[cid] = d
    return d

def get_adms(cid):
    cid = str(cid)
    try:
        r = tg("getChatAdministrators",{"chat_id":int(cid)})
        adms = r.get("result",[]); owner = None; lista = []
        for a in adms:
            lista.append(a['user']['id'])
            if a.get("status") == "creator":
                owner = a['user']['id']
        admin_cache[cid] = {"owner":owner,"adms":lista,"t":time.time()}
        return admin_cache[cid]
    except:
        return admin_cache.get(cid,{"owner":None,"adms":[],"t":0})

def is_adm(cid,uid):
    if str(uid) == CREATOR_ID: return True
    if MY_ID and uid == MY_ID: return True
    c = admin_cache.get(str(cid))
    if not c or time.time() - c['t'] > 600:
        c = get_adms(cid)
    return uid in c['adms'] or uid == c['owner']

def get_perm(cid):
    bid = MY_ID or tg("getMe",{}).get("result",{}).get("id",0)
    r = tg("getChatMember",{"chat_id":int(cid),"user_id":bid})
    res = r.get("result",{})
    can = res.get("can_delete_messages",False) or res.get("status") in ["administrator","creator"]
    perm_cache[str(cid)] = {"can":can}
    return perm_cache[str(cid)]

def send(cid,txt,mid=None):
    footer = f"\n\n<i>{BOT_NAME} by {SIGNATURE}</i>"
    if SIGNATURE not in txt:
        txt = f"{txt}{footer}"
    try:
        get_sess().post(f"{API}/sendMessage", json={"chat_id":cid,"text":txt[:3500],"parse_mode":"HTML","disable_web_page_preview":True,"reply_to_message_id":mid}, timeout=10)
    except: pass

def delete_msg(cid,mid):
    if not perm_cache.get(str(cid),{}).get("can",False):
        if not get_perm(cid)["can"]: return False
    return tg("deleteMessage",{"chat_id":cid,"message_id":mid}).get("ok",False)

def baixar_b64(fid):
    try:
        fp = tg("getFile",{"file_id":fid}).get("result",{}).get("file_path")
        if not fp: return None
        data = get_sess().get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{fp}", timeout=10).content
        if len(data) > 3500000: return None
        return base64.b64encode(data).decode()
    except: return None

def call_ia(prompt,b64=None):
    for prov in ["groq","gemini","cerebras","mistral","openrouter"]:
        if AI_PROV_BLACK.get(prov,0) > time.time(): continue
        key = os.getenv(PROVIDERS_RAW[prov]["key_env"])
        if not key: continue
        for model in FALLBACK_MODELS[prov]:
            if AI_BLACK.get(f"{prov}:{model}",0) > time.time(): continue
            try:
                s = get_sess()
                if PROVIDERS_RAW[prov]["format"] == "openai":
                    url = PROVIDERS_RAW[prov]["endpoint"].rstrip("/")+"/chat/completions"
                    r = s.post(url, headers={"Authorization":f"Bearer {key}"}, json={"model":model,"messages":[{"role":"user","content":prompt}],"temperature":0.2,"max_tokens":400}, timeout=6).json()
                    AI_STATS["calls"]+=1
                    return r["choices"][0]["message"]["content"]
                else:
                    url = f"{PROVIDERS_RAW[prov]['endpoint']}/models/{model}:generateContent?key={key}"
                    parts = [{"text":prompt}]
                    if b64: parts.append({"inline_data":{"mime_type":"image/jpeg","data":b64}})
                    r = s.post(url, json={"contents":[{"parts":parts}],"generationConfig":{"temperature":0.2,"maxOutputTokens":400}}, timeout=8).json()
                    AI_STATS["calls"]+=1
                    return r["candidates"][0]["content"]["parts"][0]["text"]
            except:
                AI_BLACK[f"{prov}:{model}"] = time.time()+120
                continue
        AI_PROV_BLACK[prov] = time.time()+180
    return None

def ia_bio(cid,txt,b64,hist):
    bio = get_bio(cid)
    if not bio['bio']:
        return {"viola":False}
    prompt = f"""Você é {BOT_NAME} by {SIGNATURE}, ADM humano do grupo "{bio['name']}" há anos.
REGRAS DO GRUPO (é a bio mas NUNCA diga bio, diga 'regra do grupo'): "{bio['bio']}"
MENSAGEM: "{txt}"
HISTORICO: {hist[-5:]}

Analise como humano sábio: só apague se violar CLARAMENTE a regra do grupo. Zoeira leve libera.
RETORNE JSON: {{"viola":bool,"motivo":"curto","fala":"frase humana curta sem falar bio, ex: aqui não rola aposta mano","confianca":0.0-1.0}}"""
    out = call_ia(prompt,b64 if b64 else None)
    if out:
        try:
            m = re.search(r'\{.*\}',out,re.DOTALL)
            if m: return json.loads(m.group())
        except: pass
    return None

def handle_message(msg):
    cid = msg["chat"]["id"]; mid = msg["message_id"]; uid = msg["from"]["id"]
    txt = (msg.get("text") or msg.get("caption") or "").strip()

    if str(cid) not in admin_cache and str(cid).startswith("-"):
        get_adms(cid)
    if str(cid).startswith("-") and is_adm(cid,uid):
        return
    if MY_ID and uid == MY_ID: return

    if txt.startswith(("/painel","/start","/menu","/adm")):
        if str(cid).startswith("-") and not is_adm(cid,uid):
            send(cid,"⛔ Só ADM pode usar."); return
        b = get_bio(cid,True); get_adms(cid); get_perm(cid)
        regras = b['bio'][:600] or "Sem regras definidas - libero tudo"
        ad = admin_cache.get(str(cid),{})
        send(cid,f"🤖 <b>ORBIT {BOT_NAME} by {SIGNATURE}</b>\n\nGrupo: {b['name']}\nRegras atuais:\n{regras}\n\nDono: {ad.get('owner','?')} | ADMs: {len(ad.get('adms',[]))} | Secret: {'ON' if WEBHOOK_SECRET else 'OFF'}",mid)
        return

    if "new_chat_members" in msg:
        b = get_bio(cid)
        if b['bio']:
            for u in msg["new_chat_members"]:
                if u["id"]!= MY_ID:
                    send(cid,f"👋 Bem-vindo(a) {u.get('first_name','')}! Dá uma olhada nas regras do grupo.")
        return

    k = f"{cid}_{uid}"; flood_hist[k].append(txt or "[midia]"); hist = list(flood_hist[k])
    fid = msg.get("photo",[{}])[-1].get("file_id") or msg.get("video",{}).get("file_id")
    b64 = baixar_b64(fid) if fid else None
    if not txt and not b64: return

    ia = ia_bio(cid,txt,b64,hist)
    if not ia or not ia.get("viola") or ia.get("confianca",0) < 0.78: return
    if not get_perm(cid)["can"]: return

    if delete_msg(cid,mid):
        send(cid,f"@{msg['from'].get('first_name','')} {ia.get('fala')}")

def keep_alive_24h():
    while True:
        time.sleep(300)
        try:
            if RENDER_URL: requests.get(RENDER_URL, timeout=5)
        except: pass

@app.route("/", methods=["POST"])
def webhook():
    if WEBHOOK_SECRET:
        if request.headers.get("X-Telegram-Bot-Api-Secret-Token","")!= WEBHOOK_SECRET:
            return "forbidden",403
    u = request.get_json(force=True)
    if "message" in u: handle_message(u["message"])
    elif "edited_message" in u: handle_message(u["edited_message"])
    return "ok",200

@app.route("/", methods=["GET"])
def home():
    return f"ORBIT {BOT_NAME} by {SIGNATURE} V25.6.1 SECRET ON | 24H | BIO-ONLY | Calls:{AI_STATS['calls']}",200

# SETUP WEBHOOK COM SECRET
try:
    url = f"{RENDER_URL.rstrip('/')}/"
    data = {"url":url,"allowed_updates":["message","edited_message"]}
    if WEBHOOK_SECRET:
        data["secret_token"] = WEBHOOK_SECRET
    tg("setWebhook",data)
    if not MY_ID:
        MY_ID = tg("getMe",{}).get("result",{}).get("id",0)
    print(f"[{BOT_NAME} by {SIGNATURE}] ONLINE SECRET={'ON' if WEBHOOK_SECRET else 'OFF'}")
except Exception as e:
    print(f"WEBHOOK ERR {e}")

threading.Thread(target=keep_alive_24h, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","10000")))
