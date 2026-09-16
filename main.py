# ORBIT ALLIANCE V17 FINAL - 10/10 - COMPLETO
# CREATED BY: Kʆɛɓɛʀ | HANSEL CORE
# SIG: 4b2e-7a9f-KLEBER-ORBIT-V17 | CHECK: KLEBER-2026
# CLOUDFLARE WORKERS AI FREE DAILY + GROQ + GEMINI + CEREBRAS
# Kʆɛɓɛʀ - ORBIT ALLIANCE © 2026
import os, re, json, time, sqlite3, logging, requests, base64, hashlib, shutil, threading, difflib, random
from datetime import datetime, timezone
from collections import defaultdict, deque, Counter
from flask import Flask, request, abort
from concurrent.futures import ThreadPoolExecutor
import pytz

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN: raise RuntimeError("TELEGRAM_TOKEN env missing")
CREATOR_ID = str(os.getenv("CREATOR_ID","8398287578"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
JSONBIN_ID = os.getenv("JSONBIN_ID")
JSONBIN_KEY = os.getenv("JSONBIN_KEY")
DATABASE_PATH = os.getenv("DATABASE_PATH","Orbit.db")
PORT = int(os.getenv("PORT",10000))
TIMEZONE = "America/Fortaleza"
TZ = pytz.timezone(TIMEZONE)

KLEBER_SIG = "Kʆɛɓɛʀ"
ORBIT_CORE = f"Orbit Alliance V17 by {KLEBER_SIG}"
KLEBER_CHECK = hashlib.sha256(KLEBER_SIG.encode()).hexdigest()[:12]

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
JSONBIN_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_ID}" if JSONBIN_ID and JSONBIN_KEY else None
JB_HEADERS = {"X-Master-Key": JSONBIN_KEY, "Content-Type":"application/json"} if JSONBIN_KEY else {}
WEBHOOK_PATH = "/telegram/webhook"

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] [%(levelname)s] %(message)s')
executor = ThreadPoolExecutor(max_workers=10)
db_lock = threading.Lock()
backup_lock = threading.Lock()
mem_flood = defaultdict(lambda: deque())
mem_mention = defaultdict(lambda: deque())
mem_texts = defaultdict(lambda: deque(maxlen=5))
mem_warns_time = defaultdict(lambda: deque())
mem_context = defaultdict(lambda: deque(maxlen=10))
mem_fight = defaultdict(lambda: deque(maxlen=10))
backup_pending = False
last_backup = 0
BOT_ID = None
BOT_USERNAME = None

thread_local = threading.local()
def get_session():
    if not hasattr(thread_local,"session"):
        thread_local.session = requests.Session()
    return thread_local.session

PROVIDERS_RAW = {
    "cloudflare": {"key_env": "CLOUDFLARE_API_TOKEN", "account_env": "CLOUDFLARE_ACCOUNT_ID", "endpoint": "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/meta/llama-3.1-8b-instruct", "format": "cloudflare", "timeout": 6},
    "groq": {"key_env": "GROQ_API_KEY", "endpoint": "https://api.groq.com/openai/v1", "format": "openai", "timeout": 5},
    "gemini": {"key_env": "GEMINI_API_KEY", "endpoint": "https://generativelanguage.googleapis.com/v1beta", "format": "gemini", "timeout": 6},
    "cerebras": {"key_env": "CEREBRAS_API_KEY", "endpoint": "https://api.cerebras.ai/v1", "format": "openai", "timeout": 5},
}
FALLBACK_MODELS = {
    "cloudflare": ["@cf/meta/llama-3.1-8b-instruct"],
    "groq": ["llama-3.1-8b-instant","llama-3.3-70b-versatile"],
    "gemini": ["gemini-1.5-flash","gemini-2.0-flash"],
    "cerebras": ["llama3.1-8b","llama-3.3-70b"],
}

def build_providers_dynamic():
    provs = {}
    for name, cfg in PROVIDERS_RAW.items():
        key = os.getenv(cfg["key_env"])
        if not key: continue
        if name == "cloudflare":
            acc = os.getenv(cfg["account_env"])
            if not acc: continue
            provs[name] = {"key": key, "account_id": acc, "endpoint": cfg["endpoint"].format(account_id=acc), "format": cfg["format"], "timeout": cfg["timeout"]}
        else:
            provs[name] = {"key": key, "endpoint": cfg["endpoint"], "format": cfg["format"], "timeout": cfg["timeout"]}
    return provs

PROVIDERS = build_providers_dynamic()
ORDER_PREFERENCE = ["cloudflare","groq","gemini","cerebras"]
AI_MODEL_BLACKLIST = {}
AI_PROVIDER_BLACKLIST = {}
RECENT_LATENCY = defaultdict(lambda: deque(maxlen=20))
RECENT_ERRORS = defaultdict(lambda: deque(maxlen=50))

DIVULGA_WORDS = {"entra","ganhe","lucro","renda","grátis","gratis","promoção","promocao","vagas","dinheiro","pix","aposte","cassino","tigrinho","sorteio","grupo novo","link na bio"}
TOXIC_WORDS = {"lixo","burro","otario","otário","idiota","fdp","vsf","arrombado","desgraça","corno","vagabundo"}
SENSUAL_WORDS = {"sem cueca","sem calcinha","pelado","pelada","tesão","tesao","pau","buceta","transar","sexo","gozar","nudes","onlyfans","xvideo","porno","punheta","siririca","de toalha","sem roupa","ficar pelado"}

def ai_sensual_score(text):
    tl=text.lower(); score=0
    for w in SENSUAL_WORDS:
        if w in tl: score+=0.45
    if "andar sem cueca" in tl or "sem cueca pela casa" in tl: score=0.85
    if "pelado" in tl and "casa" in tl: score=0.85
    return min(score,1.0)
def ai_toxic_score(text): return min(sum(0.35 for w in TOXIC_WORDS if w in text.lower()),1.0)
def ai_divulgacao_score(text):
    tl=text.lower(); sc=0
    if re.search(r"https?://|t\.me/|wa\.me|discord\.gg",tl): sc+=0.4
    for w in DIVULGA_WORDS:
        if w in tl: sc+=0.15
    return min(sc,1.0)
def ai_spam_score(texts):
    if len(texts)<3: return 0
    last=texts[-1].lower() if texts else ""
    count=sum(1 for t in texts if t.lower()==last)
    if count>=3: return 0.9
    return 0

def call_moderation_ai(text, recent_texts=[]):
    hs = ai_sensual_score(text)
    ht = ai_toxic_score(text)
    hd = ai_divulgacao_score(text)
    hsp = ai_spam_score(list(recent_texts))
    if not PROVIDERS:
        return {"toxic":ht,"divulg":hd,"sensual":hs,"spam":hsp,"intencao":"NADA"}
    prompt = f"""Você é moderador BR. Retorne SOMENTE JSON {{"toxic":0-1,"divulg":0-1,"sensual":0-1,"spam":0-1,"intencao":"NADA"}}. sensual=conteudo erotico/sexual/sem roupa. Msg:"{text[:300]}" JSON:"""
    for prov in ORDER_PREFERENCE:
        if prov not in PROVIDERS: continue
        cfg=PROVIDERS[prov]
        try:
            sess=get_session()
            if cfg["format"]=="cloudflare":
                r=sess.post(cfg["endpoint"], json={"messages":[{"role":"user","content":prompt}]}, headers={"Authorization":f"Bearer {cfg['key']}"}, timeout=6)
                if r.status_code==200:
                    resp = r.json().get("result",{}).get("response","")
                    m=re.search(r"\{.*\}",resp,re.DOTALL)
                    if m:
                        j=json.loads(m.group())
                        j["sensual"]=max(float(j.get("sensual",0)),hs)
                        j["toxic"]=max(float(j.get("toxic",0)),ht)
                        j["divulg"]=max(float(j.get("divulg",0)),hd)
                        j["spam"]=max(float(j.get("spam",0)),hsp)
                        return j
            else:
                for modelo in FALLBACK_MODELS.get(prov,[]):
                    url=f"{cfg['endpoint'].rstrip('/')}/chat/completions"
                    r=sess.post(url, json={"model":modelo,"messages":[{"role":"user","content":prompt}],"temperature":0.1,"max_tokens":100}, headers={"Authorization":f"Bearer {cfg['key']}"}, timeout=5)
                    if r.status_code==200:
                        cont=r.json()["choices"][0]["message"]["content"]
                        m=re.search(r"\{.*\}",cont,re.DOTALL)
                        if m:
                            j=json.loads(m.group())
                            j["sensual"]=max(float(j.get("sensual",0)),hs)
                            j["toxic"]=max(float(j.get("toxic",0)),ht)
                            j["divulg"]=max(float(j.get("divulg",0)),hd)
                            j["spam"]=max(float(j.get("spam",0)),hsp)
                            return j
        except Exception as e:
            continue
    return {"toxic":ht,"divulg":hd,"sensual":hs,"spam":hsp,"intencao":"NADA"}

def get_db():
    c=sqlite3.connect(DATABASE_PATH, check_same_thread=False, timeout=10)
    c.row_factory=sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL;")
    return c

def init_db():
    c=get_db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS groups(chat_id TEXT PRIMARY KEY,title TEXT,type TEXT,enabled INTEGER DEFAULT 1,updated_at TEXT);
    CREATE TABLE IF NOT EXISTS group_rules(chat_id TEXT PRIMARY KEY,welcome INTEGER DEFAULT 1,goodbye INTEGER DEFAULT 1,welcome_msg TEXT DEFAULT 'Bem-vindo {name}! 🚀',goodbye_msg TEXT DEFAULT '{name} saiu.',anti_link INTEGER DEFAULT 0,anti_spam INTEGER DEFAULT 1,anti_flood INTEGER DEFAULT 1,anti_divulgation INTEGER DEFAULT 1,anti_sensual INTEGER DEFAULT 1,sensual_mode TEXT DEFAULT 'moderate',allowed_links TEXT DEFAULT 'youtube.com,youtu.be,instagram.com,github.com,cloudflare.com',warning_limit INTEGER DEFAULT 3,moderation_mode TEXT DEFAULT 'moderate',flood_limit INTEGER DEFAULT 7,flood_window INTEGER DEFAULT 15,auto_actions TEXT DEFAULT '["DELETE","WARN","MUTE"]',mute_duration INTEGER DEFAULT 600,updated_at TEXT);
    CREATE TABLE IF NOT EXISTS warnings(chat_id TEXT,user_id TEXT,count INTEGER DEFAULT 0,last_reason TEXT,updated_at TEXT,PRIMARY KEY(chat_id,user_id));
    CREATE TABLE IF NOT EXISTS moderation_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id TEXT,user_id TEXT,action TEXT,reason TEXT,message_id INTEGER,source TEXT,success INTEGER,created_at TEXT);
    """)
    for col,typ in [("anti_sensual","INTEGER DEFAULT 1"),("sensual_mode","TEXT DEFAULT 'moderate'")]:
        try: c.execute(f"ALTER TABLE group_rules ADD COLUMN {col} {typ}")
        except: pass
    c.commit(); c.close()
init_db()

def log_error(comp, err): logging.error(f"[{KLEBER_SIG}] {comp}: {err}")

def telegram_req(method,payload=None):
    url=f"{TELEGRAM_API_URL}/{method}"
    try:
        r=requests.post(url,json=payload,timeout=12) if payload else requests.get(url,timeout=12)
        try: return r.json()
        except: return {"ok":False,"error":r.text[:200]}
    except Exception as e:
        log_error(f"telegram_req {method}", e)
        return {"ok":False,"error":str(e)}

def init_bot():
    global BOT_ID, BOT_USERNAME
    d=telegram_req("getMe")
    if d.get("ok"):
        BOT_ID=d["result"]["id"]
        BOT_USERNAME=d["result"].get("username","")
        print(f"[{KLEBER_SIG}] {ORBIT_CORE} | CHECK:{KLEBER_CHECK} | BOT @{BOT_USERNAME} ID:{BOT_ID} | PROVIDERS:{list(PROVIDERS.keys())}")
    else:
        print(f"[{KLEBER_SIG}] Falha getMe {d}")
init_bot()

def send(chat_id, txt, reply=None, parse="Markdown"):
    p={"chat_id":chat_id,"text":str(txt)[:3900],"parse_mode":parse}
    if reply: p["reply_to_message_id"]=reply
    return telegram_req("sendMessage",p)

def get_cfg(chat_id):
    c=get_db()
    r=c.execute("SELECT * FROM group_rules WHERE chat_id=?",(str(chat_id),)).fetchone()
    c.close()
    if not r:
        with db_lock:
            c=get_db()
            c.execute("INSERT OR IGNORE INTO group_rules(chat_id,updated_at) VALUES(?,?)",(str(chat_id),datetime.now(timezone.utc).isoformat()))
            c.commit(); c.close()
        return get_cfg(chat_id)
    d=dict(r)
    try: d["auto_actions"]=json.loads(d.get("auto_actions","[]"))
    except: d["auto_actions"]=["DELETE","WARN","MUTE"]
    return d

def set_cfg(chat_id, key, val):
    with db_lock:
        c=get_db()
        c.execute(f"UPDATE group_rules SET {key}=?, updated_at=? WHERE chat_id=?",(val,datetime.now(timezone.utc).isoformat(),str(chat_id)))
        c.commit(); c.close()
    global backup_pending; backup_pending=True
    return True

def is_admin(chat_id,uid):
    if str(uid)==CREATOR_ID: return True
    m=telegram_req("getChatMember",{"chat_id":chat_id,"user_id":uid})
    return m.get("ok") and m.get("result",{}).get("status") in ("administrator","creator")

def is_protected(chat_id,uid):
    if str(uid)==str(BOT_ID) or str(uid)==CREATOR_ID: return True
    return is_admin(chat_id,uid)

def execute_action(chat_id,action,target_id=None,reason="",message_id=None,source="AUTO",confidence=0):
    if target_id and is_protected(chat_id,target_id):
        return {"success":False,"error":"protegido"}
    res={"ok":False}
    try:
        if action=="DELETE" and message_id:
            res=telegram_req("deleteMessage",{"chat_id":chat_id,"message_id":message_id})
        elif action=="WARN" and target_id:
            with db_lock:
                c=get_db()
                w=c.execute("SELECT count FROM warnings WHERE chat_id=? AND user_id=?",(str(chat_id),str(target_id))).fetchone()
                cnt=(w["count"] if w else 0)+1
                c.execute("INSERT OR REPLACE INTO warnings(chat_id,user_id,count,last_reason,updated_at) VALUES(?,?,?,?,?)",(str(chat_id),str(target_id),cnt,reason,datetime.now(timezone.utc).isoformat()))
                c.commit(); c.close()
            res={"ok":True}
        elif action=="MUTE" and target_id:
            cfg=get_cfg(chat_id)
            res=telegram_req("restrictChatMember",{"chat_id":chat_id,"user_id":target_id,"permissions":{"can_send_messages":False},"until_date":int(time.time())+cfg.get("mute_duration",600)})
        elif action=="BAN" and target_id:
            res=telegram_req("banChatMember",{"chat_id":chat_id,"user_id":target_id})
        elif action=="KICK" and target_id:
            telegram_req("banChatMember",{"chat_id":chat_id,"user_id":target_id})
            res=telegram_req("unbanChatMember",{"chat_id":chat_id,"user_id":target_id})
        elif action=="UNMUTE" and target_id:
            res=telegram_req("restrictChatMember",{"chat_id":chat_id,"user_id":target_id,"permissions":{"can_send_messages":True,"can_send_media_messages":True,"can_send_other_messages":True}})
    except Exception as e:
        log_error("execute_action",e)
        res={"ok":False,"error":str(e)}
    # log
    try:
        with db_lock:
            c=get_db()
            c.execute("INSERT INTO moderation_logs(chat_id,user_id,action,reason,message_id,source,success,created_at) VALUES(?,?,?,?,?,?,?,?)",(str(chat_id),str(target_id or ""),action,f"{reason} [{KLEBER_SIG}]",message_id or 0,source,1 if res.get("ok") else 0,datetime.now(timezone.utc).isoformat()))
            c.commit(); c.close()
    except: pass
    return {"success":bool(res.get("ok")),"result":res}

def clean_cmd(chat_id, mid):
    try: telegram_req("deleteMessage",{"chat_id":chat_id,"message_id":mid})
    except: pass

def schedule_backup():
    global backup_pending, last_backup
    if not JSONBIN_URL: return
    if not backup_pending: return
    if time.time()-last_backup < 300: return
    with backup_lock:
        try:
            if not os.path.exists(DATABASE_PATH): return
            with open(DATABASE_PATH,"rb") as f:
                b64=base64.b64encode(f.read()).decode()
            payload={"db":b64,"updated":datetime.now(timezone.utc).isoformat(),"by":KLEBER_SIG,"check":KLEBER_CHECK}
            requests.put(JSONBIN_URL, json=payload, headers=JB_HEADERS, timeout=15)
            last_backup=time.time()
            backup_pending=False
            logging.info(f"[{KLEBER_SIG}] Backup JSONBIN ok")
        except Exception as e:
            log_error("backup",e)

def backup_worker():
    while True:
        time.sleep(60)
        schedule_backup()
threading.Thread(target=backup_worker, daemon=True).start()

@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    if WEBHOOK_SECRET and request.headers.get("X-Telegram-Bot-Api-Secret-Token")!=WEBHOOK_SECRET:
        abort(403)
    data=request.get_json(force=True)
    executor.submit(process_update, data)
    return {"ok":True},200

@app.route("/", methods=["GET"])
def health():
    return {"status":f"Orbit V17 by {KLEBER_SIG}","core":ORBIT_CORE,"check":KLEBER_CHECK,"bot_id":BOT_ID,"providers":list(PROVIDERS.keys()),"cloudflare": "cloudflare" in PROVIDERS}

def process_update(update):
    msg = update.get("message") or update.get("edited_message")
    if not msg: return
    chat_id = msg["chat"]["id"]
    chat_type = msg["chat"]["type"]
    uid = msg["from"]["id"]
    text = (msg.get("text","") or msg.get("caption","")).strip()
    mid = msg["message_id"]
    cfg = get_cfg(chat_id)

    # === PV LIVRE - KLEBER ===
    if chat_type=="private":
        if text.startswith("/"):
            cmd=text.split()[0].lower().split("@")[0]
            if cmd in ["/start","/help","/painel","/status"]:
                send(chat_id,f"🚀 *Orbit V17 by {KLEBER_SIG}*\n\nCore: `{ORBIT_CORE}`\nCheck: `{KLEBER_CHECK}`\n\n✅ Cloudflare Free Daily: {'ON' if 'cloudflare' in PROVIDERS else 'OFF - configure'}\n\nMe adiciona no grupo como ADM pra funcionar 100%.\n\nComandos no grupo:\n/painel\n/setsensual livre|moderate|restrito\n/status\n\n_By {KLEBER_SIG}_")
            clean_cmd(chat_id,mid)
        return

    # === COMANDOS ORGANIZADOS ===
    if text.startswith("/"):
        parts=text.strip().split()
        cmd=parts[0].lower().split("@")[0]
        args=parts[1:]
        # Só ADM
        if cmd in ["/ban","/kick","/mute","/unmute","/warn","/painel","/setsensual","/status","/antilink","/antiflood","/antispam","/antidivulg"]:
            if not is_admin(chat_id,uid):
                send(chat_id,"⚠️ Só ADM pode usar."); clean_cmd(chat_id,mid); return

        if cmd=="/painel":
            txt=f"""⚙️ *PAINEL ORBIT V17 - By {KLEBER_SIG}*
`Check: {KLEBER_CHECK}`

*Grupo:* {msg['chat'].get('title','')}
*Modo:* `{cfg.get('moderation_mode')}`
*IA:* `{', '.join(PROVIDERS.keys()) or 'Heurística'}`
*+18:* `{cfg.get('sensual_mode')}` | Anti: {'ON' if cfg.get('anti_sensual') else 'OFF'}
*Links:* {'ON' if cfg.get('anti_link') else 'OFF'} | *Flood:* {cfg.get('flood_limit')}/{cfg.get('flood_window')}s

*— +18 RESENHA (NOVO) —*
`/setsensual livre` → libera "sem cueca" etc
`/setsensual moderate` → ⭐ RECOMENDADO pra resenha
`/setsensual restrito` → apaga tudo +18

*— MODERAÇÃO —*
`/ban` `/kick` `/mute` `/unmute` (responda msg)
`/status` - status IA + Cloudflare
`/antilink on/off`
`/antiflood 7 15`

_Core by {KLEBER_SIG} - Cloudflare FREE DAILY ✅_
"""
            send(chat_id,txt,mid); clean_cmd(chat_id,mid); return

        if cmd=="/setsensual":
            if not args:
                send(chat_id,"Use: /setsensual livre | moderate | restrito",mid); clean_cmd(chat_id,mid); return
            mode=args[0].lower()
            if mode=="moderado": mode="moderate"
            if mode not in ["livre","moderate","restrito"]:
                send(chat_id,"Modos: livre, moderate, restrito",mid); clean_cmd(chat_id,mid); return
            set_cfg(chat_id,"sensual_mode",mode)
            set_cfg(chat_id,"anti_sensual", 0 if mode=="livre" else 1)
            send(chat_id,f"✅ +18 alterado para *{mode}* - by {KLEBER_SIG}\n{'Perfeito pra resenha!' if mode=='moderate' else ''}",mid); clean_cmd(chat_id,mid); return

        if cmd=="/status":
            cf = "✅ ON - Grátis diária" if "cloudflare" in PROVIDERS else "❌ OFF - configure CLOUDFLARE_API_TOKEN e ACCOUNT_ID"
            send(chat_id,f"🤖 *Orbit V17*\nBy: {KLEBER_SIG}\nCheck: {KLEBER_CHECK}\n\n*IA:*\nCloudflare: {cf}\nProviders: {list(PROVIDERS.keys())}\n\n*Grupo:*\n+18: {cfg.get('sensual_mode')}\nFlood: {cfg.get('flood_limit')} msgs / {cfg.get('flood_window')}s\nAntiLink: {cfg.get('anti_link')}\nAntiDivulg: {cfg.get('anti_divulgation')}",mid); clean_cmd(chat_id,mid); return

        if cmd in ["/antilink"]:
            if not args: send(chat_id,"/antilink on/off",mid); clean_cmd(chat_id,mid); return
            set_cfg(chat_id,"anti_link",1 if args[0].lower()=="on" else 0)
            send(chat_id,f"✅ AntiLink {args[0]}",mid); clean_cmd(chat_id,mid); return
        if cmd in ["/antiflood"]:
            if len(args)>=2:
                try:
                    set_cfg(chat_id,"flood_limit",int(args[0])); set_cfg(chat_id,"flood_window",int(args[1]))
                    send(chat_id,f"✅ Flood {args[0]}/{args[1]}s",mid)
                except: pass
            clean_cmd(chat_id,mid); return

        if cmd=="/ban":
            tgt=msg.get("reply_to_message",{}).get("from",{}).get("id")
            if not tgt: send(chat_id,"Responda a mensagem com /ban",mid); clean_cmd(chat_id,mid); return
            r=execute_action(chat_id,"BAN",tgt,"ban ADM")
            send(chat_id,"🚫 Banido" if r["success"] else f"❌ {r.get('error','sem permissão')}",mid); clean_cmd(chat_id,mid); return
        if cmd=="/kick":
            tgt=msg.get("reply_to_message",{}).get("from",{}).get("id")
            if not tgt: send(chat_id,"Responda com /kick",mid); clean_cmd(chat_id,mid); return
            r=execute_action(chat_id,"KICK",tgt,"kick ADM")
            send(chat_id,"👢 Kickado" if r["success"] else "❌ Erro",mid); clean_cmd(chat_id,mid); return
        if cmd=="/mute":
            tgt=msg.get("reply_to_message",{}).get("from",{}).get("id")
            if not tgt: send(chat_id,"Responda com /mute",mid); clean_cmd(chat_id,mid); return
            r=execute_action(chat_id,"MUTE",tgt,"mute ADM")
            send(chat_id,"🔇 Mutado 10min" if r["success"] else "❌ Erro",mid); clean_cmd(chat_id,mid); return
        if cmd=="/unmute":
            tgt=msg.get("reply_to_message",{}).get("from",{}).get("id")
            if not tgt: send(chat_id,"Responda com /unmute",mid); clean_cmd(chat_id,mid); return
            r=execute_action(chat_id,"UNMUTE",tgt,"unmute")
            send(chat_id,"🔊 Desmutado" if r["success"] else "❌ Erro",mid); clean_cmd(chat_id,mid); return

    # IGNORA ADM/BOT
    if uid==BOT_ID or is_admin(chat_id,uid):
        return
    if not text: return

    # LEARNING - KLEBER
    mem_texts[(str(chat_id),str(uid))].append(text)
    mem_context[str(chat_id)].append(text)

    # IA
    ai_res = call_moderation_ai(text, mem_texts[(str(chat_id),str(uid))])

    # === FILTRO +18 INTELIGENTE - KLEBER V17 ===
    sensual = float(ai_res.get("sensual",0))
    if sensual >= 0.65 and cfg.get("anti_sensual"):
        mode = cfg.get("sensual_mode","moderate")
        if mode=="restrito":
            execute_action(chat_id,"DELETE",None,f"+18 restrito {sensual:.2f} by {KLEBER_SIG}",mid)
            send(chat_id,f"🔞 +18 não permitido aqui.",mid)
            return
        elif mode=="moderate":
            if sensual >= 0.92: # PESADO
                execute_action(chat_id,"DELETE",None,f"+18 pesado {sensual:.2f}",mid)
                send(chat_id,f"⚠️ Pesado demais @{msg['from'].get('first_name','')}! Resenha tem limite 😅",mid)
                return
            else: # LEVE - 0.65 a 0.91 - "andar sem cueca" cai aqui
                if random.random() < 0.30:
                    send(chat_id,f"😏 Eita @{msg['from'].get('first_name','')} tá ousado hein... vou deixar passar 👀",mid)
                return
        # livre = passa tudo

    # FILTRO DIVULGAÇÃO
    if float(ai_res.get("divulg",0)) >= 0.75 and cfg.get("anti_divulgation"):
        execute_action(chat_id,"DELETE",None,f"divulg {ai_res['divulg']:.2f}",mid)
        return

    # FILTRO TOXIC
    if float(ai_res.get("toxic",0)) >= 0.85:
        execute_action(chat_id,"DELETE",None,f"toxic {ai_res['toxic']:.2f}",mid)
        if float(ai_res.get("toxic",0))>=0.93:
            execute_action(chat_id,"MUTE",uid,"toxico grave")
        return

    # FLOOD
    key=(str(chat_id),str(uid)); now=time.time(); dq=mem_flood[key]; dq.append(now)
    while dq and now-dq[0] > cfg.get("flood_window",15): dq.popleft()
    if len(dq) > cfg.get("flood_limit",7):
        execute_action(chat_id,"MUTE",uid,f"flood {len(dq)}",mid)
        dq.clear()
        send(chat_id,f"🔇 @{msg['from'].get('first_name','')} floodou, mute 10min")
        return

if __name__=="__main__":
    print(f"[{KLEBER_SIG}] START {ORBIT_CORE} CHECK:{KLEBER_CHECK}")
    app.run(host="0.0.0.0", port=PORT)
