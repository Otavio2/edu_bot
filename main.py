# ORBIT V17.3 FINAL FIX - RENDER READY - Kʆɛɓɛʀ
import os, re, json, time, sqlite3, logging, requests, base64, hashlib, shutil, threading, random
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
TZ = pytz.timezone("America/Fortaleza")

KLEBER_SIG = "Kʆɛɓɛʀ"
ORBIT_CORE = f"Orbit Alliance V17.3 FINAL FIX by {KLEBER_SIG}"

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
JSONBIN_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_ID}" if JSONBIN_ID and JSONBIN_KEY else None
JB_HEADERS = {"X-Master-Key": JSONBIN_KEY, "Content-Type":"application/json"} if JSONBIN_KEY else {}
WEBHOOK_PATH = "/telegram/webhook"

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
executor = ThreadPoolExecutor(max_workers=6)
db_lock = threading.Lock()
backup_lock = threading.Lock()
mem_flood = defaultdict(lambda: deque())
mem_texts = defaultdict(lambda: deque(maxlen=5))
mem_fight = defaultdict(lambda: deque(maxlen=10))
backup_pending = False
last_backup = 0
BOT_ID = None
BOT_USERNAME = None

thread_local = threading.local()
def get_session():
    if not hasattr(thread_local,"session"): thread_local.session = requests.Session()
    return thread_local.session

PROVIDERS_RAW = {
    "cloudflare": {"key_env": "CLOUDFLARE_API_TOKEN", "account_env": "CLOUDFLARE_ACCOUNT_ID", "endpoint": "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/meta/llama-3.1-8b-instruct", "format": "cloudflare", "timeout": 6},
    "groq": {"key_env": "GROQ_API_KEY", "endpoint": "https://api.groq.com/openai/v1", "format": "openai", "timeout": 5},
    "gemini": {"key_env": "GEMINI_API_KEY", "endpoint": "https://generativelanguage.googleapis.com/v1beta", "format": "gemini", "timeout": 6},
    "cerebras": {"key_env": "CEREBRAS_API_KEY", "endpoint": "https://api.cerebras.ai/v1", "format": "openai", "timeout": 5},
}
FALLBACK_MODELS = {
    "cloudflare": ["@cf/meta/llama-3.1-8b-instruct"],
    "groq": ["llama-3.1-8b-instant"],
    "gemini": ["gemini-1.5-flash"],
    "cerebras": ["llama3.1-8b"],
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

SENSUAL_WORDS = {"sem cueca","sem calcinha","pelado","pelada","tesao","tesão","buceta","sexo","nudes","onlyfans","porno","punheta"}
TOXIC_WORDS = {"lixo","burro","otario","idiota","fdp","vsf","arrombado","corno","vagabundo"}
DIVULGA_WORDS = {"entra","ganhe","lucro","renda","grátis","promocao","pix","tigrinho","sorteio"}

def ai_sensual_score(text):
    tl=text.lower()
    return 0.85 if any(w in tl for w in ["sem cueca pela casa","andar sem cueca","pelado em casa"]) else min(sum(0.45 for w in SENSUAL_WORDS if w in tl),1.0)

def call_moderation_ai(text, recent_texts=[]):
    hs = ai_sensual_score(text)
    ht = min(sum(0.35 for w in TOXIC_WORDS if w in text.lower()),1.0)
    hd = min(0.4 if re.search(r"https?://|t\.me/",text.lower()) else 0 + sum(0.15 for w in DIVULGA_WORDS if w in text.lower()),1.0)
    if not PROVIDERS:
        return {"toxic":ht,"divulg":hd,"sensual":hs,"spam":0}
    prompt = f'Retorne SOMENTE JSON {{"toxic":0-1,"divulg":0-1,"sensual":0-1}}. Msg:"{text[:200]}"'
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
                        return {"toxic":max(float(j.get("toxic",0)),ht),"divulg":max(float(j.get("divulg",0)),hd),"sensual":max(float(j.get("sensual",0)),hs),"spam":0}
        except: continue
    return {"toxic":ht,"divulg":hd,"sensual":hs,"spam":0}

def get_db():
    c=sqlite3.connect(DATABASE_PATH, check_same_thread=False, timeout=10)
    c.row_factory=sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL;")
    return c

def restore_safe():
    if not JSONBIN_URL: return False
    try:
        if os.path.exists(DATABASE_PATH) and os.path.getsize(DATABASE_PATH) > 5000:
            return False
        r=requests.get(f"{JSONBIN_URL}/latest", headers=JB_HEADERS, timeout=15)
        if r.status_code!=200: return False
        rec=r.json().get("record",{})
        b64=rec.get("db") or rec.get("db_base64")
        if not b64 or len(b64)<5000: return False
        tmp=DATABASE_PATH+".restore"
        with open(tmp,"wb") as f: f.write(base64.b64decode(b64))
        shutil.move(tmp, DATABASE_PATH)
        return True
    except: return False

def init_db():
    if not os.path.exists(DATABASE_PATH) or os.path.getsize(DATABASE_PATH) < 100:
        restore_safe()
    c=get_db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS group_rules(chat_id TEXT PRIMARY KEY,welcome INTEGER DEFAULT 1,goodbye INTEGER DEFAULT 1,welcome_msg TEXT DEFAULT 'Bem-vindo {name}!',goodbye_msg TEXT DEFAULT '{name} saiu.',anti_divulgation INTEGER DEFAULT 1,anti_sensual INTEGER DEFAULT 1,sensual_mode TEXT DEFAULT 'moderate',allowed_links TEXT DEFAULT 'youtube.com,github.com',warning_limit INTEGER DEFAULT 3,flood_limit INTEGER DEFAULT 7,flood_window INTEGER DEFAULT 15,mute_duration INTEGER DEFAULT 600,updated_at TEXT);
    CREATE TABLE IF NOT EXISTS warnings(chat_id TEXT,user_id TEXT,count INTEGER DEFAULT 0,last_reason TEXT,updated_at TEXT,PRIMARY KEY(chat_id,user_id));
    CREATE TABLE IF NOT EXISTS group_peak(chat_id TEXT, hour INTEGER, count INTEGER DEFAULT 0, PRIMARY KEY(chat_id,hour));
    CREATE TABLE IF NOT EXISTS user_reputation(chat_id TEXT, user_id TEXT, spam_score INTEGER DEFAULT 0, last_seen TEXT, PRIMARY KEY(chat_id,user_id));
    CREATE TABLE IF NOT EXISTS group_toxic_words(chat_id TEXT, word TEXT, count INTEGER DEFAULT 1, PRIMARY KEY(chat_id,word));
    """)
    c.commit(); c.close()
init_db()

def telegram_req(method,payload=None):
    url=f"{TELEGRAM_API_URL}/{method}"
    try:
        r=requests.post(url,json=payload,timeout=12) if payload else requests.get(url,timeout=12)
        return r.json()
    except: return {"ok":False}

def init_bot():
    global BOT_ID, BOT_USERNAME
    d=telegram_req("getMe")
    if d.get("ok"):
        BOT_ID=d["result"]["id"]; BOT_USERNAME=d["result"].get("username","")
        print(f"[{KLEBER_SIG}] BOT @{BOT_USERNAME} OK")
init_bot()

def send(chat_id, txt, reply=None):
    p={"chat_id":chat_id,"text":str(txt)[:3900],"parse_mode":"Markdown"}
    if reply: p["reply_to_message_id"]=reply
    return telegram_req("sendMessage",p)

def get_cfg(chat_id):
    c=get_db(); r=c.execute("SELECT * FROM group_rules WHERE chat_id=?",(str(chat_id),)).fetchone(); c.close()
    if not r:
        with db_lock:
            c=get_db(); c.execute("INSERT OR IGNORE INTO group_rules(chat_id,updated_at) VALUES(?,?)",(str(chat_id),datetime.now(timezone.utc).isoformat())); c.commit(); c.close()
        return get_cfg(chat_id)
    return dict(r)

def set_cfg(chat_id, key, val):
    with db_lock:
        c=get_db(); c.execute(f"UPDATE group_rules SET {key}=?, updated_at=? WHERE chat_id=?",(val,datetime.now(timezone.utc).isoformat(),str(chat_id))); c.commit(); c.close()
    global backup_pending; backup_pending=True

def is_admin(chat_id,uid):
    if str(uid)==CREATOR_ID: return True
    m=telegram_req("getChatMember",{"chat_id":chat_id,"user_id":uid})
    return m.get("ok") and m.get("result",{}).get("status") in ("administrator","creator")

def execute_action(chat_id,action,target_id=None,message_id=None):
    if target_id and (str(target_id)==str(BOT_ID) or str(target_id)==CREATOR_ID): return {"success":False}
    res={"ok":False}
    if action=="DELETE" and message_id:
        res=telegram_req("deleteMessage",{"chat_id":chat_id,"message_id":message_id})
    elif action=="MUTE" and target_id:
        cfg=get_cfg(chat_id)
        res=telegram_req("restrictChatMember",{"chat_id":chat_id,"user_id":target_id,"permissions":{"can_send_messages":False},"until_date":int(time.time())+cfg.get("mute_duration",600)})
    elif action=="BAN" and target_id:
        res=telegram_req("banChatMember",{"chat_id":chat_id,"user_id":target_id})
    return {"success":bool(res.get("ok"))}

def schedule_backup():
    global backup_pending, last_backup
    if not JSONBIN_URL or not backup_pending or time.time()-last_backup < 300: return
    try:
        if os.path.getsize(DATABASE_PATH) < 5000: return
        with open(DATABASE_PATH,"rb") as f: b64=base64.b64encode(f.read()).decode()
        payload={"db":b64,"db_base64":b64,"updated":datetime.now(timezone.utc).isoformat()}
        requests.put(JSONBIN_URL, json=payload, headers=JB_HEADERS, timeout=15)
        last_backup=time.time(); backup_pending=False
    except: pass

def backup_worker():
    while True: time.sleep(60); schedule_backup()
threading.Thread(target=backup_worker, daemon=True).start()

@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    if WEBHOOK_SECRET and request.headers.get("X-Telegram-Bot-Api-Secret-Token")!=WEBHOOK_SECRET: abort(403)
    data=request.get_json(force=True); executor.submit(process_update, data); return {"ok":True},200

@app.route("/", methods=["GET"])
def health():
    return {"status":f"Orbit V17.3 by {KLEBER_SIG}","providers":list(PROVIDERS.keys())}

def process_update(update):
    msg = update.get("message") or update.get("edited_message")
    if not msg: return
    chat_id = msg["chat"]["id"]; uid = msg["from"]["id"]
    text = (msg.get("text","") or msg.get("caption","")).strip()
    mid = msg["message_id"]
    cfg = get_cfg(chat_id)

    if "new_chat_members" in msg:
        if cfg.get("welcome"):
            for u in msg["new_chat_members"]:
                if str(u.get("id")) == str(BOT_ID): continue
                send(chat_id, cfg.get("welcome_msg","Bem-vindo {name}!").format(name=u.get("first_name","")))
        return
    if "left_chat_member" in msg:
        if cfg.get("goodbye"):
            send(chat_id, cfg.get("goodbye_msg","Saiu").format(name=msg["left_chat_member"].get("first_name","")))
        return
    if text.startswith("/"):
        parts=text.strip().split(); cmd=parts[0].lower().split("@")[0]; args=parts[1:]
        if cmd in ["/start","/help","/painel","/status"]:
            send(chat_id,f"⚙️ PAINEL V17.3 - IA {list(PROVIDERS.keys())} | +18 {cfg.get('sensual_mode')} | By {KLEBER_SIG}",mid)
            try: telegram_req("deleteMessage",{"chat_id":chat_id,"message_id":mid})
            except: pass
            return
        if not is_admin(chat_id,uid): return
        if cmd=="/ban":
            tgt=msg.get("reply_to_message",{}).get("from",{}).get("id")
            execute_action(chat_id,"BAN",tgt)
        elif cmd=="/mute":
            tgt=msg.get("reply_to_message",{}).get("from",{}).get("id")
            execute_action(chat_id,"MUTE",tgt)
        return
    if uid==BOT_ID or is_admin(chat_id,uid): return
    if not text: return
    ai_res = call_moderation_ai(text, mem_texts[(str(chat_id),str(uid))])
    if float(ai_res.get("sensual",0)) >= 0.75 and cfg.get("anti_sensual"):
        execute_action(chat_id,"DELETE",None,mid); return
    if float(ai_res.get("divulg",0)) >= 0.75 and cfg.get("anti_divulgation"):
        allowed = [d.strip().lower() for d in cfg.get("allowed_links","").split(",")]
        if not any(d in text.lower() for d in allowed):
            execute_action(chat_id,"DELETE",None,mid); return

if __name__=="__main__":
    print(f"[{KLEBER_SIG}] START {ORBIT_CORE}")
    app.run(host="0.0.0.0", port=PORT)
