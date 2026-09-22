import os, time, json, base64, re, threading, requests
from collections import defaultdict, deque
from flask import Flask, request

SIGNATURE = "Kʆɛɓɛʀ"
BOT_NAME = "ADM"
BOT_TOKEN = os.getenv("BOT_TOKEN","").strip()
API = f"https://api.telegram.org/bot{BOT_TOKEN}"
MY_ID = int(os.getenv("BOT_ID","0")) if os.getenv("BOT_ID") else 0
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET","").strip()
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL","") or "https://edu-bot-6yfa.onrender.com"
if not RENDER_URL.startswith("http"): RENDER_URL = f"https://{RENDER_URL}"
app = Flask(__name__)

PROVIDERS_RAW = {
    "gemini":{"key_env":"GEMINI_API_KEY","endpoint":"https://generativelanguage.googleapis.com/v1beta","format":"gemini"},
    "groq":{"key_env":"GROQ_API_KEY","endpoint":"https://api.groq.com/openai/v1","format":"openai"},
    "cerebras":{"key_env":"CEREBRAS_API_KEY","endpoint":"https://api.cerebras.ai/v1","format":"openai"},
    "openrouter":{"key_env":"OPENROUTER_API_KEY","endpoint":"https://openrouter.ai/api/v1","format":"openai"},
    "mistral":{"key_env":"MISTRAL_API_KEY","endpoint":"https://api.mistral.ai/v1","format":"openai"},
}
FALLBACK_MODELS = {
    "gemini":["gemini-2.0-flash","gemini-1.5-flash"],
    "groq":["llama-3.3-70b-versatile"],
    "cerebras":["llama-3.3-70b"],
    "openrouter":["meta-llama/llama-3.1-8b-instruct:free"],
    "mistral":["mistral-large-latest"]
}
AI_BLACK = {}; AI_PROV_BLACK = {}
thread_local = threading.local()
def get_sess():
    if not hasattr(thread_local,"s"): thread_local.s = requests.Session()
    return thread_local.s

bio_cache = {}
admin_cache = {}
contexto_temporario = defaultdict(lambda: deque(maxlen=5))
CONFIANCA_MIN = 0.85

def tg(m,p):
    try: return get_sess().post(f"{API}/{m}", json=p, timeout=12).json()
    except: return {}

def get_bio_real(cid, force=False):
    cid=str(cid); now=time.time()
    if not force and cid in bio_cache and now - bio_cache[cid]['t'] < 600:
        return bio_cache[cid]
    r=tg("getChat",{"chat_id":int(cid)})
    ch=r.get("result",{})
    d={"name":ch.get("title",""),"bio":ch.get("description","") or "","t":now}
    bio_cache[cid]=d
    return d

def get_adms(cid):
    try:
        r=tg("getChatAdministrators",{"chat_id":int(cid)})
        adms=r.get("result",[])
        owner=None; owner_name=""; lista_ids=[]; lista_nomes=[]
        for a in adms:
            u=a['user']
            nome = u.get('first_name','') + (f" {u.get('last_name','')}" if u.get('last_name') else "")
            nome = nome.strip() or f"ID {u['id']}"
            if u.get('username'): nome += f" (@{u['username']})"
            if u.get('is_bot'): nome += " 🤖"
            lista_ids.append(u['id']); lista_nomes.append(nome)
            if a.get("status")=="creator": owner=u['id']; owner_name=nome
        admin_cache[str(cid)]={"owner":owner,"owner_name":owner_name,"adms":lista_ids,"adms_nomes":lista_nomes,"t":time.time()}
        return admin_cache[str(cid)]
    except: return admin_cache.get(str(cid),{"owner":None,"owner_name":"","adms":[],"adms_nomes":[],"t":0})

def is_admin_real(cid,uid):
    if MY_ID and uid==MY_ID: return True
    c=admin_cache.get(str(cid))
    if not c or time.time()-c['t']>600: c=get_adms(cid)
    return uid in c['adms'] or uid==c['owner']

def bot_tem_permissao_real(cid):
    bid=MY_ID or tg("getMe",{}).get("result",{}).get("id",0)
    r=tg("getChatMember",{"chat_id":int(cid),"user_id":bid})
    res=r.get("result",{})
    return res.get("can_delete_messages",False) is True

def send(cid,txt,mid=None):
    if SIGNATURE not in txt: txt=f"{txt}\n\n<i>{BOT_NAME} by {SIGNATURE}</i>"
    try: get_sess().post(f"{API}/sendMessage", json={"chat_id":cid,"text":txt[:3900],"parse_mode":"HTML","disable_web_page_preview":True,"reply_to_message_id":mid}, timeout=10)
    except: pass

def get_file_b64(fid):
    try:
        fp=tg("getFile",{"file_id":fid}).get("result",{}).get("file_path")
        if not fp: return None
        data=get_sess().get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{fp}", timeout=15).content
        if len(data)>4500000: return None
        return base64.b64encode(data).decode()
    except: return None

def extrair_midia(msg):
    if msg.get("photo"): return get_file_b64(msg["photo"][-1]["file_id"]), "photo"
    if msg.get("sticker"):
        st=msg["sticker"]; fid=st.get("thumbnail",{}).get("file_id") or st.get("file_id")
        return get_file_b64(fid), f"sticker {st.get('emoji','')}"
    if msg.get("animation"):
        anim=msg["animation"]; fid=anim.get("thumbnail",{}).get("file_id") or anim["file_id"]
        return get_file_b64(fid), "gif"
    if msg.get("video"):
        vid=msg["video"]; fid=vid.get("thumbnail",{}).get("file_id") or vid["file_id"]
        return get_file_b64(fid), "video"
    if msg.get("document"):
        doc=msg["document"]
        if "image" in doc.get("mime_type","") or "gif" in doc.get("mime_type",""):
            return get_file_b64(doc["file_id"]), "document"
    return None, None

def call_ia(prompt,b64=None):
    for prov in ["gemini","groq","cerebras","mistral","openrouter"]:
        if AI_PROV_BLACK.get(prov,0)>time.time(): continue
        key=os.getenv(PROVIDERS_RAW[prov]["key_env"])
        if not key: continue
        for model in FALLBACK_MODELS[prov]:
            if AI_BLACK.get(f"{prov}:{model}",0)>time.time(): continue
            try:
                s=get_sess()
                if PROVIDERS_RAW[prov]["format"]=="openai":
                    content=[{"type":"text","text":prompt}]
                    if b64: content.append({"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}"}})
                    url=PROVIDERS_RAW[prov]["endpoint"].rstrip("/")+"/chat/completions"
                    r=s.post(url, headers={"Authorization":f"Bearer {key}"}, json={"model":model,"messages":[{"role":"user","content":content}],"temperature":0.1,"max_tokens":600}, timeout=12).json()
                    return r["choices"][0]["message"]["content"]
                else:
                    parts=[{"text":prompt}]
                    if b64: parts.append({"inline_data":{"mime_type":"image/jpeg","data":b64}})
                    url=f"{PROVIDERS_RAW[prov]['endpoint']}/models/{model}:generateContent?key={key}"
                    r=s.post(url, json={"contents":[{"parts":parts}]}, timeout=12).json()
                    return r["candidates"][0]["content"]["parts"][0]["text"]
            except: AI_BLACK[f"{prov}:{model}"]=time.time()+120; continue
        AI_PROV_BLACK[prov]=time.time()+180
    return None

def ia_analisa_bio_100(cid,txt,b64,tipo,hist):
    bio=get_bio_real(cid)
    if not bio['bio']: return {"viola":False,"regra":"","motivo":"","fala":"","confianca":1.0,"lang":"pt"}
    prompt=f"""Você é ADM humano do grupo "{bio['name']}". Única fonte é BIO:
BIO: "{bio['bio']}"
MSG: "{txt or ''}" | MIDIA: {tipo} | HIST: {hist}
- NÃO crie regras. Só use BIO.
- Só viola=true se VIOLAÇÃO CLARA.
- Indique REGRA da BIO violada.
- Responda idioma usuário.
JSON: {{"viola":bool,"regra":"trecho BIO","motivo":"curto","fala":"curta humana","confianca":0.0-1.0,"lang":"pt/en/es"}}"""
    out=call_ia(prompt,b64)
    if out:
        try:
            m=re.search(r'\{.*\}',out,re.DOTALL)
            if m: return json.loads(m.group())
        except: pass
    return None

def handle_message(msg):
    cid=msg["chat"]["id"]; mid=msg["message_id"]; uid=msg["from"]["id"]
    txt=(msg.get("text") or msg.get("caption") or "").strip()
    if MY_ID and uid==MY_ID: return
    if str(cid).startswith("-") and str(cid) not in admin_cache: get_adms(cid)

    # === COMANDOS COM AUTO-LIMPEZA ===
    if txt.startswith("/"):
        cmd=txt.split()[0].lower().split("@")[0]

        # Apaga o comando do usuário pra não poluir - thread 1s depois pra dar tempo do Telegram processar
        def apaga_comando():
            time.sleep(1.2)
            if bot_tem_permissao_real(cid):
                tg("deleteMessage",{"chat_id":cid,"message_id":mid})
        threading.Thread(target=apaga_comando, daemon=True).start()

        # Se for comando que não é nosso, só apaga e some (limpeza)
        if cmd not in ["/start","/regras","/status","/ping","/id","/reload"]:
            return

        if not str(cid).startswith("-"):
            send(cid,
f"""🌍👁️ <b>ORBIT {BOT_NAME} by {SIGNATURE} - ADM BIO 100%</b>

Como eu funciono:

1️⃣ Me adiciona no grupo
2️⃣ Me dá ADM com <b>Apagar mensagens</b>
3️⃣ Escreve as REGRAS na Descrição do grupo
4️⃣ Dá /start no grupo - eu leio a bio e começo a trabalhar

Eu NÃO tenho regras próprias. A BIO REAL é minha única lei.
Ex bio: "Proibido política, porno, spam, divulgação e brigas."

Se mudar a bio, dou /reload e mudo junto.
Vision + Hyperglota ON.
""")
            return

        b=get_bio_real(cid,True); ad=get_adms(cid); pode=bot_tem_permissao_real(cid)

        if cmd=="/start":
            nomes = "\n".join([f"• {n}" for n in ad.get('adms_nomes',[])]) or "• Nenhum"
            owner_linha = ad.get('owner_name') or "Desconhecido"
            bloco_bio = b['bio'][:800] if b['bio'] else "⚠️ SEM BIO - Escreve regras em Editar Grupo > Descrição. Enquanto vazio, não apago nada."
            perm_txt = "SIM ✅ - Pronto pra proteger" if pode else "NÃO ❌ - Me promove com 'Apagar mensagens'"
            send(cid,
f"""🤖 <b>{BOT_NAME} by {SIGNATURE} V27.5 AUTO-LIMPEZA</b> ✅

📛 <b>{b['name']}</b>
📜 <b>Regras que estou lendo agora (bio real):</b>
{bloco_bio}

🛡️ Apagar: {perm_txt}

👑 Dono:
- {owner_linha}
👥 ADMs ({len(ad.get('adms',[]))}):
{nomes}

Uso: Edita bio e /reload
Eu apago só violação CLARA da bio, no idioma da pessoa.
""",mid)
            return
        if cmd=="/regras": send(cid,f"📜 <b>Bio real:</b>\n{b['bio'] or 'Sem bio definida'}",mid); return
        if cmd=="/status": send(cid,f"🟢 ONLINE V27.5 AUTO-LIMPEZA\nBio: {'OK ✅' if b['bio'] else 'Vazia ⚠️'}\nPerm: {'OK ✅' if pode else 'SEM ❌'}\nDono: {ad.get('owner_name')}",mid); return
        if cmd=="/ping": send(cid,f"🏓 Pong V27.5 by {SIGNATURE}",mid); return
        if cmd=="/id": send(cid,f"🆔 Você: {uid} | Chat: {cid}",mid); return
        if cmd=="/reload":
            if not is_admin_real(cid,uid): send(cid,"⛔ Só ADM",mid); return
            get_bio_real(cid,True); get_adms(cid)
            send(cid,f"🔄 Recarregado!\nBio:\n{get_bio_real(cid)['bio'][:600] or 'Vazia'}",mid); return

    # === MODERAÇÃO BIO 100% ===
    if str(cid).startswith("-") and is_admin_real(cid,uid): return
    bio=get_bio_real(cid)
    if not bio['bio']: return
    k=f"{cid}_{uid}"; contexto_temporario[k].append(txt or "[midia]"); hist=list(contexto_temporario[k])
    b64, tipo = extrair_midia(msg)
    if not txt and not b64: return
    ia=ia_analisa_bio_100(cid,txt,b64,tipo,hist)
    if not ia or not isinstance(ia,dict): return
    if not ia.get("viola") is True: return
    if ia.get("confianca",0) < CONFIANCA_MIN: return
    regra=ia.get("regra","").strip()
    if not regra: return
    bio_lower=bio['bio'].lower()
    palavras=[p for p in re.findall(r'\w+', regra.lower()) if len(p)>3][:4]
    if palavras and not any(p in bio_lower for p in palavras): return
    if not bot_tem_permissao_real(cid): return
    resp=tg("deleteMessage",{"chat_id":cid,"message_id":mid})
    if not resp.get("ok"): return
    send(cid,f"@{msg['from'].get('first_name','')} {ia.get('fala')}")

def keep_alive():
    while True:
        time.sleep(300)
        try: requests.get(RENDER_URL, timeout=5)
        except: pass

@app.route("/", methods=["POST"])
def webhook():
    if WEBHOOK_SECRET and request.headers.get("X-Telegram-Bot-Api-Secret-Token","")!=WEBHOOK_SECRET: return "forbidden",403
    u=request.get_json(force=True)
    if "message" in u: handle_message(u["message"])
    elif "edited_message" in u: handle_message(u["edited_message"])
    return "ok",200
@app.route("/", methods=["GET"])
def home(): return f"ORBIT {BOT_NAME} by {SIGNATURE} V27.5 AUTO-LIMPEZA ONLINE",200

try:
    d={"url":f"{RENDER_URL.rstrip('/')}/","allowed_updates":["message","edited_message"]}
    if WEBHOOK_SECRET: d["secret_token"]=WEBHOOK_SECRET
    tg("setWebhook",d)
    if not MY_ID: MY_ID=tg("getMe",{}).get("result",{}).get("id",0)
    print(f"[{BOT_NAME} by {SIGNATURE}] V27.5 AUTO-LIMPEZA LACRADO")
except Exception as e: print(f"WEBHOOK ERR {e}")
threading.Thread(target=keep_alive, daemon=True).start()
if __name__=="__main__": app.run(host="0.0.0.0", port=int(os.getenv("PORT","10000")))
