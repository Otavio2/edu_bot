import os, time, json, base64, re, threading, requests
from collections import defaultdict, deque
from flask import Flask, request

def get_token():
    for k in ["BOT_TOKEN","BOT _TOKEN","TELEGRAM_TOKEN","TOKEN"]:
        v=os.getenv(k)
        if v and v.strip():
            print(f"[Kʆɛɓɛʀ] TOKEN ACHADO EM: '{k}'")
            return v.strip()
    for k,v in os.environ.items():
        if k.replace(" ","").replace("_","").upper() in ["BOTTOKEN","TELEGRAMTOKEN"] and v.strip():
            print(f"[Kʆɛɓɛʀ] TOKEN ACHADO POR SCAN: '{k}'")
            return v.strip()
    return ""
BOT_TOKEN=get_token()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN / BOT _TOKEN não encontrado")
API = f"https://api.telegram.org/bot{BOT_TOKEN}"
CREATOR_ID = str(os.getenv("CREATOR_ID","8398287578"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET","")
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL","") or os.getenv("WEBHOOK_URL","") or os.getenv("RENDER_EXTERNAL_HOSTNAME","") or "https://edu-bot-6yfa.onrender.com"
if not RENDER_URL.startswith("http"):
    RENDER_URL = f"https://{RENDER_URL}"
SIGNATURE = "Kʆɛɓɛʀ"
MY_ID = int(os.getenv("BOT_ID","0")) if os.getenv("BOT_ID") else 0

app = Flask(__name__)

PROVIDERS_RAW = {
    "groq": {"key_env": "GROQ_API_KEY", "endpoint": "https://api.groq.com/openai/v1", "format": "openai", "timeout": 6},
    "gemini": {"key_env": "GEMINI_API_KEY", "endpoint": "https://generativelanguage.googleapis.com/v1beta", "format": "gemini", "timeout": 8},
    "cerebras": {"key_env": "CEREBRAS_API_KEY", "endpoint": "https://api.cerebras.ai/v1", "format": "openai", "timeout": 6},
    "openrouter": {"key_env": "OPENROUTER_API_KEY", "endpoint": "https://openrouter.ai/api/v1", "format": "openai", "timeout": 8},
    "cloudflare": {"key_env": "CLOUDFLARE_API_TOKEN", "endpoint": f"https://api.cloudflare.com/client/v4/accounts/{os.getenv('CLOUDFLARE_ACCOUNT_ID')}/ai/run/", "format": "cloudflare", "timeout": 8, "requires": ["CLOUDFLARE_ACCOUNT_ID"]},
    "mistral": {"key_env": "MISTRAL_API_KEY", "endpoint": "https://api.mistral.ai/v1", "format": "openai", "timeout": 8},
}
FALLBACK_MODELS = {
    "groq": ["llama-3.3-70b-versatile","llama-3.1-8b-instant"],
    "gemini": ["gemini-2.0-flash","gemini-1.5-flash"],
    "cerebras": ["llama-3.3-70b","llama3.1-8b"],
    "openrouter": ["meta-llama/llama-3.1-8b-instruct:free"],
    "cloudflare": ["@cf/meta/llama-3.1-8b-instruct"],
    "mistral": ["mistral-large-latest","mistral-small-latest"]
}
MODEL_CACHE = {}; AI_MODEL_BLACKLIST = {}; AI_PROVIDER_BLACKLIST = {}
AI_STATS = {"fallbacks":0,"total_calls":0}
PROVIDER_CONCURRENCY = {"groq":2,"gemini":2,"mistral":2,"cerebras":3,"openrouter":2,"cloudflare":2}
PROVIDER_SEMAPHORES = {k: threading.Semaphore(v) for k,v in PROVIDER_CONCURRENCY.items()}
thread_local=threading.local()
def get_session():
    if not hasattr(thread_local,"session"): thread_local.session=requests.Session()
    return thread_local.session

DEFAULT_CFG = {"seguir_bio":True,"anti_link":True,"anti_18":True,"anti_briga":True,"anti_flert":True,"anti_politica":True,"anti_flood":True,"anti_venda":True,"welcome":True}
cfg_db = {}; bio_cache = {}; perm_cache = {}; flood_hist = defaultdict(lambda: deque(maxlen=15))
grupos_conhecidos = set()

def tg(m,p):
    try: return get_session().post(f"{API}/{m}", json=p, timeout=10).json()
    except: return {}

def auto_setup():
    global MY_ID
    if not MY_ID:
        try:
            me=tg("getMe",{}).get("result",{})
            MY_ID=me.get("id",0)
        except: pass
        print(f"[{SIGNATURE}] BOT ID AUTO: {MY_ID}")
    if RENDER_URL:
        url=f"{RENDER_URL.rstrip('/')}/"
        data={"url":url, "allowed_updates":["message","edited_message","callback_query","chat_member","my_chat_member"]}
        if WEBHOOK_SECRET: data["secret_token"]=WEBHOOK_SECRET
        r=tg("setWebhook",data)
        print(f"[{SIGNATURE}] WEBHOOK AUTO: {url} -> {r}")
    ativas = sum(1 for v in PROVIDERS_RAW.values() if os.getenv(v["key_env"]))
    print(f"[{SIGNATURE}] IAs ATIVAS: {ativas} -> {list(k for k,v in PROVIDERS_RAW.items() if os.getenv(v['key_env']))}")
    def bio_auto_refresh():
        while True:
            time.sleep(600)
            for cid in list(bio_cache.keys()):
                try: get_real_bio(cid, force=True)
                except: pass
    threading.Thread(target=bio_auto_refresh, daemon=True).start()
    print(f"[{SIGNATURE}] AUTO TUDO ATIVO")

def get_cfg(cid):
    if cid not in cfg_db: cfg_db[cid]=DEFAULT_CFG.copy()
    return cfg_db[cid]

def get_real_bio(cid, force=False):
    now=time.time()
    if not force and cid in bio_cache and now-bio_cache[cid]['updated_at']<600: return bio_cache[cid]
    r=tg("getChat",{"chat_id":cid}); ch=r.get("result",{})
    d={"name":ch.get("title","") or ch.get("first_name",""),"bio":ch.get("description",""),"updated_at":now}
    bio_cache[cid]=d; return d

def get_bot_perm(cid, force=False):
    now=time.time()
    if not force and cid in perm_cache and now-perm_cache[cid]['updated_at']<300: return perm_cache[cid]
    bid=MY_ID or tg("getMe",{}).get("result",{}).get("id",0)
    r=tg("getChatMember",{"chat_id":cid,"user_id":bid}); res=r.get("result",{})
    can=res.get("can_delete_messages",False) or res.get("status") in ["administrator","creator"]
    d={"can_delete":can,"state":"ONLINE" if can else "DEGRADED","updated_at":now}
    perm_cache[cid]=d; return d

def is_admin(cid,uid):
    if str(uid)==CREATOR_ID: return True
    if MY_ID and uid==MY_ID: return True
    try:
        return tg("getChatMember",{"chat_id":cid,"user_id":uid}).get("result",{}).get("status") in ["administrator","creator"]
    except: return False

def find_grupo_comum(uid):
    for cid in list(grupos_conhecidos):
        try:
            if get_bot_perm(cid)['can_delete'] and is_admin(cid, uid):
                return cid
        except: continue
    return None

def send(cid,text,mid=None,kb=None):
    if SIGNATURE not in text: text=f"{text}\n\n<i>{SIGNATURE}</i>"
    d={"chat_id":cid,"text":text[:3500],"parse_mode":"HTML","disable_web_page_preview":True}
    if mid: d["reply_to_message_id"]=mid
    if kb: d["reply_markup"]=json.dumps(kb)
    get_session().post(f"{API}/sendMessage", json=d, timeout=10)

def delete_msg(cid,mid):
    if not get_bot_perm(cid)["can_delete"]: return False
    return tg("deleteMessage",{"chat_id":cid,"message_id":mid}).get("ok",False)

def baixar_b64(fid):
    try:
        fp=tg("getFile",{"file_id":fid}).get("result",{}).get("file_path")
        if not fp: return None
        data=get_session().get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{fp}", timeout=10).content
        if len(data)>3500000: return None
        return base64.b64encode(data).decode()
    except: return None

def call_provider(provider, prompt, b64=None):
    if AI_PROVIDER_BLACKLIST.get(provider,0)>time.time(): return None
    cfg=PROVIDERS_RAW.get(provider)
    if not cfg: return None
    key=os.getenv(cfg["key_env"])
    if not key: return None
    for req in cfg.get("requires",[]):
        if not os.getenv(req): return None
    sem=PROVIDER_SEMAPHORES.get(provider)
    for model in FALLBACK_MODELS.get(provider,[]):
        if AI_MODEL_BLACKLIST.get(f"{provider}:{model}",0)>time.time(): continue
        if sem: sem.acquire()
        try:
            s=get_session()
            if cfg["format"]=="openai":
                url=cfg["endpoint"].rstrip("/")+"/chat/completions"
                r=s.post(url, headers={"Authorization":f"Bearer {key}"}, json={"model":model,"messages":[{"role":"user","content":prompt}],"temperature":0.1,"max_tokens":400}, timeout=cfg["timeout"]).json()
                AI_STATS["total_calls"]+=1
                return r["choices"][0]["message"]["content"]
            elif cfg["format"]=="gemini":
                url=f"{cfg['endpoint']}/models/{model}:generateContent?key={key}"
                parts=[{"text":prompt}]
                if b64: parts.append({"inline_data":{"mime_type":"image/jpeg","data":b64}})
                r=s.post(url, json={"contents":[{"parts":parts}],"generationConfig":{"temperature":0.1,"maxOutputTokens":400}}, timeout=cfg["timeout"]).json()
                AI_STATS["total_calls"]+=1
                return r["candidates"][0]["content"]["parts"][0]["text"]
            elif cfg["format"]=="cloudflare":
                url=cfg["endpoint"].rstrip("/")+f"{model}"
                r=s.post(url, headers={"Authorization":f"Bearer {key}"}, json={"prompt":prompt}, timeout=cfg["timeout"]).json()
                AI_STATS["total_calls"]+=1
                return r.get("result",{}).get("response")
        except:
            AI_MODEL_BLACKLIST[f"{provider}:{model}"]=time.time()+120
            continue
        finally:
            if sem: sem.release()
    AI_PROVIDER_BLACKLIST[provider]=time.time()+180
    AI_STATS["fallbacks"]+=1
    return None

def ia_analisa(cid,txt,b64,hist):
    cfg=get_cfg(cid); bio=get_real_bio(cid)
    prompt=f"""Você é ORBIT V24 AUTO. GRUPO:{bio['name']} BIO:"{bio['bio']}" BOTOES:{json.dumps(cfg)} SEGUIR_BIO:{'ON' if cfg['seguir_bio'] else 'OFF'} HIST:{hist[-5:]} MSG:"{txt}" MIDIA:{'SIM' if b64 else 'NAO'} Analise intenção. Só viola se botão ON ou bio ON. RETORNE JSON: {{"viola":bool,"motivo":"curto","origem":"botao/bio/nenhuma","regra":"anti_link|anti_18|anti_briga|anti_flert|anti_politica|anti_venda|anti_flood|bio","confianca":0.0-1.0}}"""
    for prov in ["groq","gemini","cerebras","mistral","openrouter","cloudflare"]:
        out=call_provider(prov, prompt, b64 if prov=="gemini" else None)
        if out:
            try:
                m=re.search(r'\{.*\}',out,re.DOTALL)
                if m: return json.loads(m.group())
            except: continue
    return None

def painel_kb(cfg):
    def ic(v): return "✅" if v else "❌"
    return {"inline_keyboard":[
        [{"text":f"{ic(cfg['seguir_bio'])} 📖 Seguir Bio: {'ON' if cfg['seguir_bio'] else 'OFF'}","callback_data":"t:seguir_bio"}],
        [{"text":f"{ic(cfg['anti_link'])} Anti-Link","callback_data":"t:anti_link"}, {"text":f"{ic(cfg['anti_18'])} Anti +18","callback_data":"t:anti_18"}],
        [{"text":f"{ic(cfg['anti_briga'])} Anti-Briga","callback_data":"t:anti_briga"}, {"text":f"{ic(cfg['anti_flert'])} Anti-Flert","callback_data":"t:anti_flert"}],
        [{"text":f"{ic(cfg['anti_politica'])} Anti-Política","callback_data":"t:anti_politica"}, {"text":f"{ic(cfg['anti_flood'])} Anti-Flood","callback_data":"t:anti_flood"}],
        [{"text":f"{ic(cfg['anti_venda'])} Anti-Venda","callback_data":"t:anti_venda"}, {"text":f"{ic(cfg['welcome'])} 👋 Boas-vindas","callback_data":"t:welcome"}],
        [{"text":"🔄 Ler Bio Agora","callback_data":"refresh_bio"}]
    ]}

def painel_txt(cid, uid=None):
    target_cid=cid
    is_pv = not str(cid).startswith("-")
    if is_pv:
        if uid:
            comum=find_grupo_comum(uid)
            if comum:
                target_cid=comum
            else:
                return f"🤖 <b>ORBIT ADM V24.5.3</b>\nVocê e eu não somos ADM juntos em nenhum grupo.\nMe adicione como ADM em um grupo e use /painel lá primeiro.\n{SIGNATURE} | IA: {sum(1 for v in PROVIDERS_RAW.values() if os.getenv(v['key_env']))}"
        else:
            target_cid=cid
    bio=get_real_bio(target_cid); perm=get_bot_perm(target_cid); cfg=get_cfg(target_cid)
    ativos=sum([cfg['anti_link'],cfg['anti_18'],cfg['anti_briga'],cfg['anti_flert'],cfg['anti_politica'],cfg['anti_flood'],cfg['anti_venda']])
    ias_ativas = sum(1 for v in PROVIDERS_RAW.values() if os.getenv(v["key_env"]))
    origem = "PV->Grupo" if target_cid!=cid else "GRUPO" if not is_pv else "PV"
    return f"🤖 <b>ORBIT ADM V24.5.3 [{origem}]</b>\nGrupo: {bio['name']}\nBio: {(bio['bio'][:90] or 'sem bio')}...\nEstado: {perm['state']} | {ativos}/7 | {SIGNATURE} | IA: {ias_ativas} | Calls: {AI_STATS['total_calls']}"

def send_painel(cid, uid=None, mid=None):
    target_cid=cid
    if not str(cid).startswith("-") and uid:
        comum=find_grupo_comum(uid)
        if comum: target_cid=comum
    txt=painel_txt(cid, uid)
    has_panel = "Você e eu não somos ADM" not in txt
    kb=painel_kb(get_cfg(target_cid)) if has_panel else None
    send(cid, txt, mid, kb)

def handle_message(msg):
    cid=msg["chat"]["id"]; mid=msg["message_id"]; uid=msg["from"]["id"]
    txt=(msg.get("text") or msg.get("caption") or "").strip()
    if str(cid).startswith("-"):
        grupos_conhecidos.add(cid)
    if MY_ID and uid==MY_ID: return
    if txt.startswith(("/painel","/start","/menu")):
        if not str(cid).startswith("-"): # PV
            send_painel(cid, uid, mid)
            return
        if not is_admin(cid,uid): send(cid,"⛔ Só ADM."); return
        get_real_bio(cid,True); get_bot_perm(cid,True); send_painel(cid, uid, mid); return
    if "new_chat_members" in msg:
        if get_cfg(cid)["welcome"]:
            for u in msg["new_chat_members"]:
                if u["id"]!=MY_ID: send(cid,f"👋 Bem-vindo(a) {u.get('first_name','')}! Leia a descrição.")
        return
    if is_admin(cid,uid): return
    k=f"{cid}_{uid}"; flood_hist[k].append((time.time(), txt or "[midia]")); hist=[h[1] for h in flood_hist[k]]
    fid=msg.get("photo",[{}])[-1].get("file_id") or msg.get("video",{}).get("file_id") or msg.get("sticker",{}).get("file_id")
    b64=baixar_b64(fid) if fid and get_cfg(cid)["anti_18"] else None
    if not txt and not b64: return
    ia=ia_analisa(cid,txt,b64,hist)
    if not ia or not ia.get("viola") or ia.get("confianca",0)<0.65: return
    cfg=get_cfg(cid); regra=ia.get("regra",""); origem=ia.get("origem","")
    if origem=="botao" and not cfg.get(regra,False): return
    if origem=="bio" and not cfg.get("seguir_bio",False): return
    if delete_msg(cid,mid): send(cid,f"⚠️ Removido: {ia.get('motivo','violação')} [{regra}]")

def handle_callback(q):
    cid=q["message"]["chat"]["id"]; uid=q["from"]["id"]; mid=q["message"]["message_id"]
    target_cid=cid
    if not str(cid).startswith("-"):
        comum=find_grupo_comum(uid)
        if comum: target_cid=comum
    if not is_admin(target_cid,uid): tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":"Só ADM","show_alert":True}); return
    d=q["data"]; cfg=get_cfg(target_cid)
    if d.startswith("t:"):
        k=d[2:]
        if k in cfg: cfg[k]=not cfg[k]
        tg("editMessageText",{"chat_id":cid,"message_id":mid,"text":painel_txt(cid, uid)+f"\n\n<i>{SIGNATURE}</i>","parse_mode":"HTML","reply_markup":painel_kb(cfg)})
        tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":f"{k} {'ON' if cfg[k] else 'OFF'}"})
    elif d=="refresh_bio":
        get_real_bio(target_cid,True); get_bot_perm(target_cid,True)
        tg("editMessageText",{"chat_id":cid,"message_id":mid,"text":painel_txt(cid, uid)+f"\n\n<i>{SIGNATURE}</i>","parse_mode":"HTML","reply_markup":painel_kb(get_cfg(target_cid))})
        tg("answerCallbackQuery",{"callback_query_id":q["id"],"text":"Bio atualizada AUTO!"})

@app.route("/", methods=["POST"])
def webhook():
    if WEBHOOK_SECRET and request.headers.get("X-Telegram-Bot-Api-Secret-Token")!=WEBHOOK_SECRET: return "forbidden",403
    u=request.get_json(force=True)
    if "callback_query" in u: handle_callback(u["callback_query"])
    elif "message" in u: handle_message(u["message"])
    elif "edited_message" in u: handle_message(u["edited_message"])
    return "ok",200

@app.route("/", methods=["GET"])
def home(): return f"ORBIT V24.5.3 AUTO {SIGNATURE} ONLINE | IA:{sum(1 for v in PROVIDERS_RAW.values() if os.getenv(v['key_env']))}",200

auto_setup()
if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","10000")))
