import os, time, json, base64, logging, requests, re
from collections import defaultdict, deque
from flask import Flask, request

# === CONFIG OFICIAL Kʆɛɓɛʀ ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN não definido!")

CREATOR_ID = str(os.getenv("CREATOR_ID","8398287578"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET","")
SIGNATURE = "Kʆɛɓɛʀ"
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

try:
    MY_ID = int(os.getenv("BOT_ID","0"))
except:
    MY_ID = 0

PROVIDERS_RAW = {
    "groq": {"key_env": "GROQ_API_KEY", "endpoint": "https://api.groq.com/openai/v1"},
    "cerebras": {"key_env": "CEREBRAS_API_KEY", "endpoint": "https://api.cerebras.ai/v1"},
    "mistral": {"key_env": "MISTRAL_API_KEY", "endpoint": "https://api.mistral.ai/v1"},
    "openrouter": {"key_env": "OPENROUTER_API_KEY", "endpoint": "https://openrouter.ai/api/v1"},
    "gemini": {"key_env": "GEMINI_API_KEY", "endpoint": "https://generativelanguage.googleapis.com/v1beta"}
}
FALLBACK_MODELS = {
    "groq": ["llama-3.3-70b-versatile"],
    "cerebras": ["llama3.1-8b"],
    "mistral": ["mistral-large-latest"],
    "openrouter": ["meta-llama/llama-3.1-8b-instruct:free"],
    "gemini": ["gemini-1.5-flash"]
}

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
def log(m): print(f"[{SIGNATURE}] {m}", flush=True)

session = requests.Session()
session.headers.update({"User-Agent": f"OrbitADM/{SIGNATURE}"})
bot_perm_cache = {}
cfg_cache = {}
flood_cache = defaultdict(lambda: deque(maxlen=12))
TEXTO_EXPLICATIVO = f"🤖 ORBIT ALLIANCE ADM - {SIGNATURE}\nEu sou ADM com cérebro de IA.\n1️⃣ BIO: Se bio proíbe link/+18/política e Seguir Bio ON, eu protejo.\n2️⃣ 9 BOTÕES: Bio, Link, +18, Briga, Flert, Política, Flood, Venda, Boas-vindas.\nSó /painel. Trabalho com ADMs. Dev: {SIGNATURE}"

def send(cid, text, reply_to=None):
    try:
        if SIGNATURE not in text:
            text = f"{text}\n\n<i>{SIGNATURE}</i>"
        data={"chat_id":cid,"text":text[:4000],"parse_mode":"HTML","disable_web_page_preview":True}
        if reply_to: data["reply_to_message_id"]=reply_to
        session.post(f"{API}/sendMessage", json=data, timeout=8)
    except Exception as e:
        log(f"send erro: {e}")

def execute_delete(cid, mid):
    try:
        r=session.post(f"{API}/deleteMessage", json={"chat_id":cid,"message_id":mid}, timeout=8).json()
        return r.get("ok", False)
    except:
        return False

def is_admin(cid, uid):
    suid=str(uid)
    if suid==CREATOR_ID: return True
    if MY_ID and uid==MY_ID: return True
    try:
        key=f"adm_{cid}_{uid}"
        if key in bot_perm_cache:
            perms, ts = bot_perm_cache[key]
            if time.time()-ts < 300: return perms
        r=session.get(f"{API}/getChatMember", params={"chat_id":cid,"user_id":uid}, timeout=8).json()
        if not r.get("ok"): return False
        status=r["result"]["status"]
        is_adm = status in ["administrator","creator"]
        bot_perm_cache[key]=(is_adm, time.time())
        return is_adm
    except:
        return False

def get_cfg(cid):
    if cid not in cfg_cache:
        cfg_cache[cid]={"bio_rules":"Proibido link, +18, política, briga, venda","follow_bio":True,"anti_link":True,"anti_sensual":True,"anti_briga":True,"anti_flert":False,"anti_politica":True,"anti_flood":True,"anti_venda":True,"welcome_enabled":True}
    return cfg_cache[cid]

def send_panel(cid, mid=None):
    cfg=get_cfg(cid)
    txt=(f"🛡️ <b>ORBIT PAINEL - {SIGNATURE}</b>\n"
         f"Dono: {CREATOR_ID}\n"
         f"Bio: {cfg['bio_rules'][:90]}\n\n"
         f"📖 Bio:{'ON' if cfg['follow_bio'] else 'OFF'} | 🔗 Link:{'ON' if cfg['anti_link'] else 'OFF'} | 🔞 +18:{'ON' if cfg['anti_sensual'] else 'OFF'}\n"
         f"🤬 Briga:{'ON' if cfg['anti_briga'] else 'OFF'} | 🏛️ Pol:{'ON' if cfg['anti_politica'] else 'OFF'} | 📢 Flood:{'ON' if cfg['anti_flood'] else 'OFF'}\n"
         f"💬 Flert:{'ON' if cfg['anti_flert'] else 'OFF'} | 🛒 Venda:{'ON' if cfg['anti_venda'] else 'OFF'}\n\n"
         f"⚡ Dev: {SIGNATURE} | 100% ON")
    send(cid, txt, mid)

def is_duvida_sobre_bot(text):
    tl=text.lower()
    gatilhos=["o que voce faz","o que vc faz","como funciona","pra que serve","quem é voce","o que e esse bot","/ajuda","/help","/sobre","me explica"]
    return any(g in tl for g in gatilhos)

def gerar_explicacao_ia(pergunta):
    prompt=f"Pergunta: '{pergunta}'. Explique em 40 palavras ORBIT ADM by {SIGNATURE}, BIO+9 BOTOES. PT-BR curto."
    for prov in ["groq","cerebras","mistral"]:
        if prov not in PROVIDERS_RAW: continue
        key=os.getenv(PROVIDERS_RAW[prov]["key_env"])
        if not key: continue
        try:
            r=session.post(f"{PROVIDERS_RAW[prov]['endpoint']}/chat/completions", json={"model":FALLBACK_MODELS[prov][0],"messages":[{"role":"user","content":prompt}],"max_tokens":120}, headers={"Authorization":f"Bearer {key}"}, timeout=7)
            if r.status_code==200: return r.json()["choices"][0]["message"]["content"][:600]
        except: continue
    return TEXTO_EXPLICATIVO

def baixar_foto_telegram(file_id):
    try:
        r=session.get(f"{API}/getFile", params={"file_id":file_id}, timeout=8).json()
        if not r.get("ok"): return None
        fp=r["result"]["file_path"]
        url=f"https://api.telegram.org/file/bot{BOT_TOKEN}/{fp}"
        img=session.get(url, timeout=12).content
        if len(img) > 4_000_000: return None # evita estouro
        return base64.b64encode(img).decode()
    except Exception as e:
        log(f"baixar erro: {e}")
        return None

def ia_visao_porn(b64_image):
    if "gemini" not in PROVIDERS_RAW: return None
    key=os.getenv(PROVIDERS_RAW["gemini"]["key_env"])
    if not key: return None
    try:
        body={"contents":[{"parts":[{"text":"Essa imagem tem nudez explícita, pornografia, genitália, ato sexual, hentai? Responda só SIM ou NAO."},{"inline_data":{"mime_type":"image/jpeg","data":b64_image}}]}],"generationConfig":{"temperature":0.1}}
        r=session.post(f"{PROVIDERS_RAW['gemini']['endpoint']}/models/gemini-1.5-flash:generateContent?key={key}", json=body, timeout=12)
        if r.status_code==200:
            txt=r.json()["candidates"][0]["content"]["parts"][0]["text"].lower()
            return "sim" in txt
    except: return None
    return None

def call_ai_moderation(text, cfg):
    prompt=f"BIO:{cfg.get('bio_rules')} BOTOES:sensual={cfg.get('anti_sensual')} link={cfg.get('anti_link')} politica={cfg.get('anti_politica')} briga={cfg.get('anti_briga')} MSG:{text} JSON:{{\"violation\":bool,\"rule_key\":\"sensual|link|politica|briga|venda|flert|flood\",\"confidence\":0-100,\"reason\":\"curto\",\"bio_ref\":bool}}"
    for prov in ["groq","cerebras","mistral","gemini"]:
        if prov not in PROVIDERS_RAW: continue
        key=os.getenv(PROVIDERS_RAW[prov]["key_env"])
        if not key: continue
        try:
            if prov=="gemini":
                r=session.post(f"{PROVIDERS_RAW[prov]['endpoint']}/models/{FALLBACK_MODELS[prov][0]}:generateContent?key={key}", json={"contents":[{"parts":[{"text":prompt}]}]}, timeout=8)
                if r.status_code==200:
                    out=r.json()["candidates"][0]["content"]["parts"][0]["text"]
                    m=re.search(r'\{.*\}', out, re.DOTALL)
                    if m: return json.loads(m.group())
            else:
                r=session.post(f"{PROVIDERS_RAW[prov]['endpoint']}/chat/completions", json={"model":FALLBACK_MODELS[prov][0],"messages":[{"role":"user","content":prompt}],"temperature":0.2,"max_tokens":200}, headers={"Authorization":f"Bearer {key}"}, timeout=8)
                if r.status_code==200:
                    out=r.json()["choices"][0]["message"]["content"]
                    m=re.search(r'\{.*\}', out, re.DOTALL)
                    if m: return json.loads(m.group())
        except: continue
    return None

def active_rule_check(cfg, rule_key, bio_ref):
    if bio_ref and cfg.get("follow_bio"): return True
    if not rule_key: return False
    map_keys={"sensual":"anti_sensual","link":"anti_link","politica":"anti_politica","briga":"anti_briga","venda":"anti_venda","flert":"anti_flert","flood":"anti_flood"}
    return cfg.get(map_keys.get(rule_key,""), False)

def flood_hist_check(cid, uid, txt):
    key=f"{cid}_{uid}"; now=time.time(); dq=flood_cache[key]
    # limpa antigo >20s
    while dq and now - dq[0][0] > 20:
        dq.popleft()
    dq.append((now, txt))
    if len(dq)>=6: return True
    if len(dq)>=4 and txt and len(set([x[1] for x in dq]))==1: return True
    return False

def process_update(upd):
    msg=upd.get("message") or upd.get("edited_message")
    if not msg: return
    cid=msg["chat"]["id"]; mid=msg["message_id"]; uid=msg["from"]["id"]
    txt=(msg.get("text") or msg.get("caption") or "").strip()

    if str(uid)==CREATOR_ID:
        if txt.startswith(("/painel","/start","/help")): send_panel(cid,mid)
        return

    tem_foto="photo" in msg; tem_video="video" in msg; tem_gif="animation" in msg; tem_sticker="sticker" in msg
    tem_midia=tem_foto or tem_video or tem_gif or tem_sticker
    file_id=None
    if tem_foto: file_id=msg["photo"][-1]["file_id"]
    elif tem_video: file_id=msg["video"]["file_id"]
    elif tem_gif: file_id=msg["animation"]["file_id"]
    elif tem_sticker: file_id=msg["sticker"]["file_id"]

    if not txt and not tem_midia: return

    if txt and is_duvida_sobre_bot(txt):
        k=f"exp_{cid}"; last=bot_perm_cache.get(k,(None,0))[1] if k in bot_perm_cache else 0
        if time.time()-last>120:
            send(cid, gerar_explicacao_ia(txt), mid)
            bot_perm_cache[k]=(None,time.time())
        return

    if txt.startswith(("/painel","/start")):
        if not is_admin(cid,uid): send(cid,"⛔ Só ADM pode abrir painel.",mid); return
        send_panel(cid,mid); return

    if is_admin(cid,uid): return

    if msg.get("new_chat_members"):
        cfg=get_cfg(cid)
        if cfg.get("welcome_enabled"):
            for u in msg["new_chat_members"]:
                if MY_ID and u["id"]==MY_ID: continue
                send(cid,f"👋 Bem-vindo(a) {u.get('first_name','')}! Leia a bio 📖")
        return

    cfg=get_cfg(cid)

    if tem_midia and cfg.get("anti_sensual"):
        resultado_visao=None
        if file_id and (tem_foto or tem_video or tem_gif):
            b64=baixar_foto_telegram(file_id)
            if b64: resultado_visao=ia_visao_porn(b64)

        if resultado_visao is True:
            if execute_delete(cid,mid):
                send(cid,f"🔞 Mídia +18 apagada pela IA de visão.",mid)
            return
        elif resultado_visao is None: # 2ª BASE
            if (tem_foto or tem_video or tem_gif) and not txt:
                if execute_delete(cid,mid):
                    send(cid,"🔞 Mídia sem legenda bloqueada (Anti +18 ON - 2ª base).",mid)
                return
            if tem_sticker:
                set_name=msg.get("sticker",{}).get("set_name","").lower()
                if any(p in set_name for p in ["porn","hentai","nude","nsfw","sex","pack","18"]):
                    if execute_delete(cid,mid): send(cid,"🔞 Sticker +18 bloqueado.",mid)
                    return
        if tem_sticker and msg.get("sticker",{}).get("emoji","") in ["🍑","🍆","🔞"]:
            if execute_delete(cid,mid): send(cid,"🔞 Sticker +18.",mid)
            return

    if cfg.get("anti_flood"):
        if flood_hist_check(cid,uid,txt or "[midia]"):
            if execute_delete(cid,mid): send(cid,f"📢 Flood detectado.",mid)
            return

    txt_final = f"[{'foto' if tem_foto else 'video' if tem_video else 'gif' if tem_gif else 'sticker' if tem_sticker else ''}] {txt}".strip()
    if len(txt_final)<2: return
    chk=call_ai_moderation(txt_final, cfg)
    if chk and chk.get("violation") and chk.get("confidence",0)>=75:
        if active_rule_check(cfg, chk.get("rule_key"), chk.get("bio_ref")):
            if execute_delete(cid,mid):
                send(cid,f"⚠️ {chk.get('reason','Violação detectada')}",mid)

@app.route("/", methods=["POST"])
def webhook():
    if WEBHOOK_SECRET:
        sec = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if sec!= WEBHOOK_SECRET:
            return "forbidden", 403
    try:
        data=request.get_json(force=True)
        process_update(data)
    except Exception as e:
        log(f"Erro webhook: {e}")
    return "ok", 200

@app.route("/", methods=["GET"])
def home():
    return f"ORBIT ALLIANCE ADM ONLINE - {SIGNATURE} - Dono {CREATOR_ID} - OK", 200

if __name__=="__main__":
    port=int(os.getenv("PORT","10000"))
    log(f"Iniciando {SIGNATURE} na porta {port}")
    app.run(host="0.0.0.0", port=port)
