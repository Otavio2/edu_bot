import os, time, json, base64, re, threading, requests, html
from collections import defaultdict, deque
from flask import Flask, request

# --- IDENTIDADE DO CRIADOR ---
SIGNATURE = "Kʆɛɓɛʀ"
DONO_NOME = "Kʆɛɓɛʀ"
DONO_ID = int(os.getenv("DONO_ID", "0"))
BOT_TOKEN = os.getenv("BOT_TOKEN","").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET","").strip()
API = f"https://api.telegram.org/bot{BOT_TOKEN}"
BOT_ID = int(BOT_TOKEN.split(':')[0]) if BOT_TOKEN and ":" in BOT_TOKEN else 0
RENDER_URL = (os.getenv("RENDER_EXTERNAL_URL","") or "https://edu-bot-6yfa.onrender.com").strip()
if not RENDER_URL.startswith("http"): RENDER_URL = f"https://{RENDER_URL}"
app = Flask(__name__)

PROVIDERS_RAW = {
    "gemini":{"key_env":"GEMINI_API_KEY","endpoint":"https://generativelanguage.googleapis.com/v1beta","format":"gemini","vision":True},
    "groq":{"key_env":"GROQ_API_KEY","endpoint":"https://api.groq.com/openai/v1","format":"openai","vision":True},
    "cerebras":{"key_env":"CEREBRAS_API_KEY","endpoint":"https://api.cerebras.ai/v1","format":"openai","vision":False},
    "openrouter":{"key_env":"OPENROUTER_API_KEY","endpoint":"https://openrouter.ai/api/v1","format":"openai","vision":True},
    "mistral":{"key_env":"MISTRAL_API_KEY","endpoint":"https://api.mistral.ai/v1","format":"openai","vision":True},
}
FALLBACK_MODELS = {
    "gemini":["gemini-2.0-flash","gemini-1.5-flash"],
    "groq":["llama-3.3-70b-versatile","llama-3.2-11b-vision-preview"],
    "cerebras":["llama-3.3-70b"],
    "openrouter":["meta-llama/llama-3.1-8b-instruct:free"],
    "mistral":["mistral-large-latest","pixtral-12b-2409"]
}
AI_BLACK = {}; AI_PROV_BLACK = {}
thread_local = threading.local()
def get_sess():
    if not hasattr(thread_local,"s"): thread_local.s = requests.Session()
    return thread_local.s

bio_cache = {}; admin_cache = {}
contexto_temporario = defaultdict(lambda: deque(maxlen=5))
infracoes_tmp = defaultdict(list)
CONFIANCA_MIN = 0.85

def tg(m,p):
    try:
        r=get_sess().post(f"{API}/{m}", json=p, timeout=12)
        return r.json()
    except: return {"ok":False}

def get_bio_real(cid, force=False):
    cid=str(cid); now=time.time()
    if not force and cid in bio_cache and now - bio_cache[cid]['t'] < 600: return bio_cache[cid]
    ch=tg("getChat",{"chat_id":int(cid)}).get("result",{}) or {}
    d={"name":ch.get("title",""),"bio":ch.get("description","") or "","t":now}
    bio_cache[cid]=d
    return d

def get_adms(cid):
    try:
        adms=tg("getChatAdministrators",{"chat_id":int(cid)}).get("result",[]) or []
        ids=[]; nomes=[]; owner=None; owner_name=""
        for a in adms:
            u=a.get("user",{}); uid=u.get("id")
            if not uid: continue
            nome=(u.get('first_name','') + (f" {u.get('last_name','')}" if u.get('last_name') else "")).strip() or str(uid)
            if u.get('username'): nome+=f" (@{u['username']})"
            ids.append(uid); nomes.append(nome)
            if a.get("status")=="creator": owner=uid; owner_name=nome
        admin_cache[str(cid)]={"owner":owner,"owner_name":owner_name,"adms":ids,"adms_nomes":nomes,"t":time.time()}
        return admin_cache[str(cid)]
    except: return admin_cache.get(str(cid),{"owner":None,"owner_name":"","adms":[],"adms_nomes":[],"t":0})

def is_admin_real(cid,uid):
    if uid == DONO_ID: return True
    if uid == BOT_ID: return True
    c=admin_cache.get(str(cid))
    if not c or time.time()-c['t']>600: c=get_adms(cid)
    return uid in c.get("adms",[]) or uid==c.get("owner")

def get_bot_perms(cid):
    res=tg("getChatMember",{"chat_id":int(cid),"user_id":BOT_ID}).get("result",{}) or {}
    status=res.get("status")
    if status=="creator": return {"delete":True,"restrict":True,"ban":True}
    return {"delete":res.get("can_delete_messages")==True,"restrict":res.get("can_restrict_members")==True,"ban":res.get("can_restrict_members")==True}

def send(cid,txt,mid=None):
    if SIGNATURE not in txt: txt=f"{txt}\n\n<i>ADM by {SIGNATURE}</i>"
    try: get_sess().post(f"{API}/sendMessage", json={"chat_id":cid,"text":txt[:3900],"parse_mode":"HTML","disable_web_page_preview":True,"reply_to_message_id":mid}, timeout=10)
    except: pass

def send_mention(cid,uid,nome,fala):
    send(cid,f'<a href="tg://user?id={uid}">{html.escape(nome or "usuário")}</a> {html.escape(fala or "")}')

def get_file_data(fid):
    try:
        fp=tg("getFile",{"file_id":fid}).get("result",{}).get("file_path")
        if not fp: return None,None
        mime="image/jpeg"
        if fp.lower().endswith(".png"): mime="image/png"
        elif fp.lower().endswith(".webp"): mime="image/webp"
        data=get_sess().get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{fp}", timeout=15).content
        if len(data)>5000000: return None,None
        return base64.b64encode(data).decode(), mime
    except: return None,None

def extrair_midia(msg):
    if msg.get("photo"):
        b,m=get_file_data(msg["photo"][-1]["file_id"])
        return b,m or "image/jpeg", "photo"
    if msg.get("sticker"):
        st=msg["sticker"]
        if st.get("is_animated") or st.get("is_video"): return None,None,None
        fid=st.get("thumbnail",{}).get("file_id") or st.get("file_id")
        b,m=get_file_data(fid) if fid else (None,None)
        return b,m or "image/webp", f"sticker {st.get('emoji','')}" if b else (None,None,None)
    if msg.get("animation"):
        fid=msg["animation"].get("thumbnail",{}).get("file_id")
        b,m=get_file_data(fid) if fid else (None,None)
        return b,m or "image/jpeg", "gif" if b else (None,None,None)
    if msg.get("video"):
        fid=msg["video"].get("thumbnail",{}).get("file_id")
        b,m=get_file_data(fid) if fid else (None,None)
        return b,m or "image/jpeg", "video" if b else (None,None,None)
    return None,None,None

def call_ia(prompt,b64=None,mime="image/jpeg"):
    for prov in ["gemini","groq","mistral","openrouter","cerebras"]:
        if AI_PROV_BLACK.get(prov,0)>time.time(): continue
        key=os.getenv(PROVIDERS_RAW[prov]["key_env"])
        if not key: continue
        if b64 and not PROVIDERS_RAW[prov]["vision"]: continue
        for model in FALLBACK_MODELS[prov]:
            if AI_BLACK.get(f"{prov}:{model}",0)>time.time(): continue
            try:
                s=get_sess()
                if PROVIDERS_RAW[prov]["format"]=="openai":
                    content=[{"type":"text","text":prompt}]
                    if b64: content.append({"type":"image_url","image_url":{"url":f"data:{mime};base64,{b64}"}})
                    r=s.post(f"{PROVIDERS_RAW[prov]['endpoint']}/chat/completions", headers={"Authorization":f"Bearer {key}"}, json={"model":model,"messages":[{"role":"user","content":content}],"temperature":0.1,"max_tokens":800}, timeout=15)
                    if r.status_code in [401,403]: AI_PROV_BLACK[prov]=time.time()+600; break
                    if r.status_code==429 or r.status_code>=500: AI_BLACK[f"{prov}:{model}"]=time.time()+180; continue
                    txt=r.json()["choices"][0]["message"]["content"]
                    if txt: return txt
                else:
                    parts=[{"text":prompt}]
                    if b64: parts.append({"inline_data":{"mime_type":mime,"data":b64}})
                    r=s.post(f"{PROVIDERS_RAW[prov]['endpoint']}/models/{model}:generateContent?key={key}", json={"contents":[{"parts":parts}]}, timeout=15)
                    if r.status_code in [401,403]: AI_PROV_BLACK[prov]=time.time()+600; break
                    if r.status_code==429 or r.status_code>=500: AI_BLACK[f"{prov}:{model}"]=time.time()+180; continue
                    txt=r.json()["candidates"][0]["content"]["parts"][0]["text"]
                    if txt: return txt
            except: AI_BLACK[f"{prov}:{model}"]=time.time()+120; continue
    return None

def validar_literal(trecho,bio):
    if not trecho or not bio: return False
    return trecho.strip() in bio

def ia_analisa(cid,txt,b64,mime,tipo,hist):
    bio=get_bio_real(cid)
    bio_txt=bio['bio']
    if not bio_txt.strip(): return None
    prompt=f"""Você é ADM do grupo "{bio['name']}". BIO OFICIAL: "{bio_txt}" MENSAGEM: Texto="{(txt or '')[:1000]}" Midia={tipo} Historico={hist[-3:]} REGRAS: Entenda significado, não keyword. trecho_bio deve ser COPIADO LITERAL da BIO. Responda idioma usuario. JSON: {{"viola":bool,"regra":"resumo","trecho_bio":"literal da bio","fala":"curta humano","confianca":0-1,"punicao":{{"tipo":"none|mute|ban","quando":"none|reincidencia|imediato","duracao_segundos":0,"trecho_bio":"literal ou vazio"}}}}"""
    out=call_ia(prompt,b64,mime)
    if not out: return None
    try:
        m=re.search(r'\{.*\}',out,re.DOTALL)
        j=json.loads(m.group())
        if j.get("viola") and not validar_literal(j.get("trecho_bio",""), bio_txt): return None
        if j.get("punicao",{}).get("trecho_bio") and not validar_literal(j["punicao"]["trecho_bio"], bio_txt): j["punicao"]["trecho_bio"]=""
        return j
    except: return None

def handle_message(msg):
    cid=msg["chat"]["id"]; mid=msg.get("message_id"); uid=msg["from"]["id"]
    txt=(msg.get("text") or msg.get("caption") or "").strip()
    if uid in [BOT_ID, DONO_ID]: return
    if str(cid).startswith("-") and str(cid) not in admin_cache: get_adms(cid)

    if txt.startswith("/"):
        cmd=txt.split()[0].lower().split("@")[0]
        if cmd in ["/start","/regras","/status","/ping","/id","/reload"]:
            b=get_bio_real(cid,True); ad=get_adms(cid); perms=get_bot_perms(cid)
            if cmd=="/start":
                send(cid,f"🤖 <b>ADM by {SIGNATURE} V28</b>\n<b>{html.escape(b['name'])}</b>\nBio: {html.escape(b['bio'][:800]) or 'Vazia'}\n\nDono: {DONO_NOME} ID:{DONO_ID}\nDel:{'✅' if perms['delete'] else '❌'}",mid); return
            if cmd=="/regras": send(cid,f"📜 {html.escape(b['bio'])}",mid); return
            if cmd=="/status": send(cid,f"🟢 V28 by {SIGNATURE}\nBio:{'✅' if b['bio'] else '❌'}\nIA:✅\nDono:{DONO_NOME}",mid); return
            if cmd=="/ping": send(cid,f"🏓 V28 Pong by {SIGNATURE}",mid); return
            if cmd=="/id": send(cid,f"Você:{uid} Chat:{cid} Dono:{DONO_ID}",mid); return
            if cmd=="/reload" and is_admin_real(cid,uid): get_bio_real(cid,True); get_adms(cid); send(cid,"🔄 Reload by "+SIGNATURE,mid); return
        return

    if str(cid).startswith("-") and is_admin_real(cid,uid): return
    bio=get_bio_real(cid)
    if not bio['bio'].strip(): return
    k=f"{cid}_{uid}"; contexto_temporario[k].append(txt or "[midia]"); hist=list(contexto_temporario[k])
    b64,mime,tipo=extrair_midia(msg)
    if not txt and not b64: return
    ia=ia_analisa(cid,txt,b64,mime or "image/jpeg",tipo,hist)
    if not ia or ia.get("viola") is not True or ia.get("confianca",0) < CONFIANCA_MIN: return
    if not validar_literal(ia.get("trecho_bio",""), bio['bio']): return
    perms=get_bot_perms(cid)
    if not perms["delete"]: return
    if not tg("deleteMessage",{"chat_id":cid,"message_id":mid}).get("ok"): return
    nome=msg['from'].get('first_name',''); fala=ia.get('fala') or "Respeite as regras"
    pun=ia.get("punicao",{}) or {}
    if pun.get("tipo") in ["ban","mute"] and pun.get("trecho_bio"):
        agora=time.time()
        infracoes_tmp[(str(cid),uid)]=[t for t in infracoes_tmp[(str(cid),uid)] if agora-t<86400]
        infracoes_tmp[(str(cid),uid)].append(agora)
        if pun.get("quando")=="reincidencia" and len(infracoes_tmp[(str(cid),uid)])<2:
            send_mention(cid,uid,nome,fala); return
        if pun["tipo"]=="ban" and perms["ban"]:
            tg("banChatMember",{"chat_id":cid,"user_id":uid}); send_mention(cid,uid,nome,f"{fala} - ban (bio: {pun['trecho_bio'][:60]})"); return
        if pun["tipo"]=="mute" and perms["restrict"] and pun.get("duracao_segundos",0)>0:
            tg("restrictChatMember",{"chat_id":cid,"user_id":uid,"permissions":{"can_send_messages":False},"until_date":int(agora)+pun["duracao_segundos"]})
            send_mention(cid,uid,nome,f"{fala} - mute {pun['duracao_segundos']//60}min"); return
    send_mention(cid,uid,nome,fala)

def keep_alive():
    while True:
        time.sleep(300)
        try:
            requests.get(RENDER_URL, timeout=5)
            agora=time.time()
            for k in list(infracoes_tmp.keys()):
                infracoes_tmp[k]=[t for t in infracoes_tmp[k] if agora-t<86400]
                if not infracoes_tmp[k]: del infracoes_tmp[k]
        except: pass

@app.route("/", methods=["POST"])
def webhook():
    if WEBHOOK_SECRET and request.headers.get("X-Telegram-Bot-Api-Secret-Token","")!=WEBHOOK_SECRET: return "forbidden",403
    u=request.get_json(force=True, silent=True) or {}
    if "message" in u: threading.Thread(target=handle_message, args=(u["message"],), daemon=True).start()
    elif "edited_message" in u: threading.Thread(target=handle_message, args=(u["edited_message"],), daemon=True).start()
    return "ok",200

@app.route("/", methods=["GET"])
def home(): return f"ORBIT ADM by {SIGNATURE} V28 | Dono:{DONO_NOME} ID:{DONO_ID} | BOT_ID:{BOT_ID} ONLINE",200

try:
    tg("setWebhook",{"url":f"{RENDER_URL.rstrip('/')}/","allowed_updates":["message","edited_message"],"secret_token":WEBHOOK_SECRET} if WEBHOOK_SECRET else {"url":f"{RENDER_URL.rstrip('/')}/","allowed_updates":["message","edited_message"]})
    print(f"[ADM by {SIGNATURE}] V28 ONLINE Dono:{DONO_NOME} ID:{DONO_ID} BOT_ID:{BOT_ID}")
except Exception as e: print(f"WEBHOOK ERR {e}")
threading.Thread(target=keep_alive, daemon=True).start()
if __name__=="__main__": app.run(host="0.0.0.0", port=int(os.getenv("PORT","10000")))
