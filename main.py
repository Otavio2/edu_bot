# ORBIT ALLIANCE V17.8 ORIGINAL 1:1 - BY Kʆɛɓɛʀ - FIX BACKUP
import os, re, json, time, sqlite3, logging, requests, base64, shutil, threading, random
from datetime import datetime, timezone, timedelta
from collections import defaultdict, deque
from flask import Flask, request, abort
from concurrent.futures import ThreadPoolExecutor

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN: raise RuntimeError("TELEGRAM_TOKEN env missing")
CREATOR_ID = str(os.getenv("CREATOR_ID","8398287578"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
JSONBIN_ID = os.getenv("JSONBIN_ID")
JSONBIN_KEY = os.getenv("JSONBIN_KEY")
DATABASE_PATH = os.getenv("DATABASE_PATH","Orbit.db")
PORT = int(os.getenv("PORT",10000))

KLEBER_SIG = "Kʆɛɓɛʀ"
ORBIT_CORE = f"Orbit V17.8 FALANTE by {KLEBER_SIG}"
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
JSONBIN_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_ID}" if JSONBIN_ID and JSONBIN_KEY else None
JB_HEADERS = {"X-Master-Key": JSONBIN_KEY, "Content-Type":"application/json"} if JSONBIN_KEY else {}
WEBHOOK_PATH = "/telegram/webhook"

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s')
executor = ThreadPoolExecutor(max_workers=6)
db_lock = threading.Lock()
backup_lock = threading.Lock()
mem_flood = defaultdict(lambda: deque(maxlen=15))
mem_texts = defaultdict(lambda: deque(maxlen=5))
mem_fight = defaultdict(lambda: deque(maxlen=20))
mem_join = defaultdict(lambda: deque(maxlen=10))
admin_cache = {}
backup_pending = False
last_backup = 0
BOT_ID = None
BOT_USERNAME = None
thread_local = threading.local()
def get_session():
    if not hasattr(thread_local, "session"):
        thread_local.session = requests.Session()
    return thread_local.session

PROVIDERS_RAW = {
    "cloudflare": {"key_env": "CLOUDFLARE_API_TOKEN", "account_env": "CLOUDFLARE_ACCOUNT_ID", "endpoint": "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/meta/llama-3.1-8b-instruct", "format": "cloudflare"},
    "groq": {"key_env": "GROQ_API_KEY", "endpoint": "https://api.groq.com/openai/v1", "format": "openai"},
    "gemini": {"key_env": "GEMINI_API_KEY", "endpoint": "https://generativelanguage.googleapis.com/v1beta", "format": "gemini"},
    "cerebras": {"key_env": "CEREBRAS_API_KEY", "endpoint": "https://api.cerebras.ai/v1", "format": "openai"},
}
FALLBACK_MODELS = {"cloudflare":["@cf/meta/llama-3.1-8b-instruct"],"groq":["llama-3.1-8b-instant"],"gemini":["gemini-1.5-flash"],"cerebras":["llama3.1-8b"]}
def build_providers_dynamic():
    provs={}
    for name,cfg in PROVIDERS_RAW.items():
        key=os.getenv(cfg["key_env"])
        if not key: continue
        if name=="cloudflare":
            acc=os.getenv(cfg["account_env"])
            if not acc: continue
            provs[name]={"key":key,"account_id":acc,"endpoint":cfg["endpoint"].format(account_id=acc),"format":cfg["format"]}
        else: provs[name]={"key":key,"endpoint":cfg["endpoint"],"format":cfg["format"]}
    return provs
PROVIDERS=build_providers_dynamic()
ORDER_PREFERENCE=["cloudflare","groq","gemini","cerebras"]
SENSUAL_WORDS={"sem cueca","sem calcinha","pelado","pelada","tesao","tesão","buceta","sexo","nudes","onlyfans","porno","punheta","siririca","de toalha","sem roupa"}
TOXIC_WORDS={"lixo","burro","otario","otário","idiota","fdp","vsf","arrombado","desgraca","corno","vagabundo"}
DIVULGA_WORDS={"entra","ganhe","lucro","renda","grátis","gratis","promoção","promocao","vagas","dinheiro","pix","aposte","cassino","tigrinho","sorteio"}
STOP_RULE={"proibido","proibida","proibir","banido","banida","vetado","vetada","nao","não","pode","falar","sobre","fala","de","do","da","no","na","com","para","pra","é","e"}

if "/data" in DATABASE_PATH:
    try: os.makedirs("/data", exist_ok=True)
    except: DATABASE_PATH="Orbit.db"

def get_db():
    try: c=sqlite3.connect(DATABASE_PATH, check_same_thread=False, timeout=15)
    except: c=sqlite3.connect("Orbit.db", check_same_thread=False, timeout=15)
    c.row_factory=sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL;")
    c.execute("PRAGMA synchronous=NORMAL;")
    return c

def restore_safe():
    if not JSONBIN_URL: return False
    try:
        if os.path.exists(DATABASE_PATH) and os.path.getsize(DATABASE_PATH)>5000:
            try:
                c=sqlite3.connect(DATABASE_PATH)
                chk=c.execute("PRAGMA quick_check").fetchone()
                c.close()
                if chk and "ok" in str(chk[0]).lower(): return False
            except: pass
        r=requests.get(f"{JSONBIN_URL}/latest", headers=JB_HEADERS, timeout=15)
        if r.status_code!=200: return False
        rec=r.json().get("record",{})
        b64=rec.get("db") or rec.get("db_base64")
        if not b64 or len(b64)<5000: return False
        tmp=DATABASE_PATH+".restore"
        with open(tmp,"wb") as f: f.write(base64.b64decode(b64))
        shutil.move(tmp,DATABASE_PATH)
        return True
    except: return False

def init_db():
    if not os.path.exists(DATABASE_PATH) or os.path.getsize(DATABASE_PATH)<100:
        restore_safe()
    c=get_db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS group_rules(chat_id TEXT PRIMARY KEY,title TEXT,welcome INTEGER DEFAULT 1,goodbye INTEGER DEFAULT 1,welcome_msg TEXT DEFAULT 'Bem-vindo {name}! 🚀',goodbye_msg TEXT DEFAULT '{name} saiu.',anti_link INTEGER DEFAULT 0,anti_spam INTEGER DEFAULT 1,anti_flood INTEGER DEFAULT 1,anti_divulgation INTEGER DEFAULT 1,anti_sensual INTEGER DEFAULT 1,sensual_mode TEXT DEFAULT 'moderate',allowed_links TEXT DEFAULT 'youtube.com,youtu.be,instagram.com,github.com,cloudflare.com',warning_limit INTEGER DEFAULT 3,flood_limit INTEGER DEFAULT 7,flood_window INTEGER DEFAULT 15,mute_duration INTEGER DEFAULT 600,lock_group INTEGER DEFAULT 0,updated_at TEXT);
    CREATE TABLE IF NOT EXISTS warnings(chat_id TEXT,user_id TEXT,count INTEGER DEFAULT 0,last_reason TEXT,updated_at TEXT,PRIMARY KEY(chat_id,user_id));
    CREATE TABLE IF NOT EXISTS moderation_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id TEXT,user_id TEXT,action TEXT,reason TEXT,message_id INTEGER,success INTEGER,created_at TEXT);
    CREATE TABLE IF NOT EXISTS group_peak(chat_id TEXT, hour INTEGER, count INTEGER DEFAULT 0, PRIMARY KEY(chat_id,hour));
    CREATE TABLE IF NOT EXISTS user_reputation(chat_id TEXT, user_id TEXT, spam_score INTEGER DEFAULT 0, last_seen TEXT, PRIMARY KEY(chat_id,user_id));
    CREATE TABLE IF NOT EXISTS group_toxic_words(chat_id TEXT, word TEXT, count INTEGER DEFAULT 1, PRIMARY KEY(chat_id,word));
    CREATE TABLE IF NOT EXISTS custom_rules(id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id TEXT,rule_text TEXT,keywords TEXT,created_by TEXT,created_at TEXT);
    CREATE TABLE IF NOT EXISTS pending_rules(chat_id TEXT,user_id TEXT,rules_json TEXT,created_at TEXT,PRIMARY KEY(chat_id,user_id));
    CREATE TABLE IF NOT EXISTS user_rule_hits(chat_id TEXT,user_id TEXT,rule_id INTEGER,count INTEGER DEFAULT 1,last_at TEXT,PRIMARY KEY(chat_id,user_id,rule_id));
    """)
    c.commit()
    try:
        cols = [r[1] for r in c.execute("PRAGMA table_info(group_rules)").fetchall()]
        if "title" not in cols:
            c.execute("ALTER TABLE group_rules ADD COLUMN title TEXT")
            c.commit()
    except: pass
    c.close()
init_db()

def telegram_req(method,payload=None):
    url=f"{TELEGRAM_API_URL}/{method}"
    for _ in range(3):
        try:
            r=requests.post(url,json=payload,timeout=12) if payload else requests.get(url,timeout=12)
            if r.status_code==429: time.sleep(2); continue
            return r.json()
        except: time.sleep(1)
    return {"ok":False}

def init_bot():
    global BOT_ID,BOT_USERNAME
    d=telegram_req("getMe")
    if d.get("ok"):
        BOT_ID=d["result"]["id"]
        BOT_USERNAME=d["result"].get("username","")
        print(f"[{KLEBER_SIG}] {ORBIT_CORE} @{BOT_USERNAME} OK")
init_bot()

def send(chat_id,txt,reply=None,markup=None):
    p={"chat_id":chat_id,"text":str(txt)[:3900],"parse_mode":"Markdown"}
    if reply: p["reply_to_message_id"]=reply
    if markup: p["reply_markup"]=markup
    return telegram_req("sendMessage",p)

def get_cfg(chat_id):
    c=get_db()
    r=c.execute("SELECT * FROM group_rules WHERE chat_id=?",(str(chat_id),)).fetchone()
    c.close()
    if not r:
        with db_lock:
            c=get_db()
            c.execute("INSERT OR IGNORE INTO group_rules(chat_id,updated_at) VALUES(?,?)",(str(chat_id),datetime.now(timezone.utc).isoformat()))
            c.commit()
            c.close()
        return get_cfg(chat_id)
    return dict(r)

def set_cfg(chat_id,key,val):
    with db_lock:
        c=get_db()
        c.execute(f"UPDATE group_rules SET {key}=?, updated_at=? WHERE chat_id=?",(val,datetime.now(timezone.utc).isoformat(),str(chat_id)))
        c.commit()
        c.close()
    global backup_pending
    backup_pending=True

def is_admin(chat_id,uid):
    key=f"{chat_id}_{uid}"
    now=time.time()
    if key in admin_cache:
        val, ts = admin_cache[key]
        if now - ts < 300:
            return val
    if str(uid)==CREATOR_ID:
        try:
            c=get_db()
            exists=c.execute("SELECT 1 FROM group_rules WHERE chat_id=?",(str(chat_id),)).fetchone()
            c.close()
            if exists:
                admin_cache[key]=(True, now)
                return True
        except: pass
    try:
        url=f"{TELEGRAM_API_URL}/getChatMember"
        r=requests.post(url, json={"chat_id":chat_id,"user_id":uid}, timeout=4)
        j=r.json()
        ok=j.get("ok") and j.get("result",{}).get("status") in ("administrator","creator")
        admin_cache[key]=(ok, now)
        return ok
    except:
        admin_cache[key]=(False, now)
        return False

def is_protected(chat_id,uid):
    if str(uid)==str(BOT_ID) or str(uid)==CREATOR_ID: return True
    return is_admin(chat_id,uid)

def wipe_group_data(chat_id):
    try:
        with db_lock:
            c=get_db()
            for t in ["group_rules","custom_rules","pending_rules","user_rule_hits","warnings","moderation_logs","group_peak","user_reputation","group_toxic_words"]:
                c.execute(f"DELETE FROM {t} WHERE chat_id=?",(str(chat_id),))
            c.commit()
            c.close()
        for k in list(mem_flood.keys()):
            if str(chat_id) in str(k): del mem_flood[k]
        mem_fight.pop(str(chat_id),None)
        mem_join.pop(str(chat_id),None)
        global backup_pending
        backup_pending=True
    except: pass

def parse_rules_from_text(text):
    lines=[l.strip() for l in text.splitlines() if len(l.strip())>=5]
    if not lines and len(text.strip())>=8: lines=[text.strip()]
    rules=[]
    for l in lines:
        low=l.lower()
        if len(l)>250: continue
        if any(k in low for k in ("proibido","proibida","banido","banida","vetado","vetada","nao pode","não pode","proibir","sem link","sem porno","sem politica","sem divulgação","sem divulgacao","proibida a venda","proibido link")) or low.startswith("proibido") or low.startswith("banido"):
            rules.append(l[:200])
    return rules[:20]

def get_keywords(rule_text):
    words=re.findall(r"\w{4,}",rule_text.lower())
    kws=[w for w in words if w not in STOP_RULE]
    return ",".join(kws[:8]) if kws else rule_text.lower()[:20]

def check_custom_rules(text,chat_id):
    c=get_db()
    rows=c.execute("SELECT id,rule_text,keywords FROM custom_rules WHERE chat_id=?",(str(chat_id),)).fetchall()
    c.close()
    tl=text.lower()
    for r in rows:
        kws=(r["keywords"] or "").split(",")
        for k in kws:
            k=k.strip()
            if len(k)>=4 and k in tl:
                return dict(r)
    return None

def handle_custom_violation(chat_id,user_id,rule,message_id, user_name=""):
    rule_id=rule["id"]
    with db_lock:
        c=get_db()
        row=c.execute("SELECT count FROM user_rule_hits WHERE chat_id=? AND user_id=? AND rule_id=?",(str(chat_id),str(user_id),rule_id)).fetchone()
        cnt=(row["count"] if row else 0)+1
        c.execute("INSERT OR REPLACE INTO user_rule_hits(chat_id,user_id,rule_id,count,last_at) VALUES(?,?,?,?,?)",(str(chat_id),str(user_id),rule_id,cnt,datetime.now(timezone.utc).isoformat()))
        c.commit()
        c.close()
    global backup_pending
    backup_pending=True
    telegram_req("deleteMessage",{"chat_id":chat_id,"message_id":message_id})
    if cnt==1:
        aviso = gerar_aviso_ia(user_name or "amigo", f'regra: {rule["rule_text"]}', rule["rule_text"])
        send(chat_id, aviso, message_id)
    elif cnt==2:
        execute_action(chat_id,"WARN",user_id,None,f"regra: {rule['rule_text']}")
        send(chat_id,f"⚠️ WARN 1/3 por: {rule['rule_text']}",message_id)
    else:
        execute_action(chat_id,"MUTE",user_id,None,f"reincidente {rule['rule_text']}")
        send(chat_id,f"🔇 Mute 10min por reincidir: {rule['rule_text']}",message_id)

def execute_action(chat_id,action,target_id=None,message_id=None,reason=""):
    if target_id and is_protected(chat_id,target_id): return {"success":False}
    res={"ok":False}
    try:
        if action=="DELETE" and message_id: res=telegram_req("deleteMessage",{"chat_id":chat_id,"message_id":message_id})
        elif action=="WARN" and target_id:
            with db_lock:
                c=get_db()
                w=c.execute("SELECT count FROM warnings WHERE chat_id=? AND user_id=?",(str(chat_id),str(target_id))).fetchone()
                cnt=(w["count"] if w else 0)+1
                c.execute("INSERT OR REPLACE INTO warnings(chat_id,user_id,count,last_reason,updated_at) VALUES(?,?,?,?,?)",(str(chat_id),str(target_id),cnt,reason,datetime.now(timezone.utc).isoformat()))
                c.commit()
                c.close()
            res={"ok":True}
        elif action=="MUTE" and target_id:
            cfg=get_cfg(chat_id)
            res=telegram_req("restrictChatMember",{"chat_id":chat_id,"user_id":target_id,"permissions":{"can_send_messages":False},"until_date":int(time.time())+cfg.get("mute_duration",600)})
        elif action=="UNMUTE" and target_id: res=telegram_req("restrictChatMember",{"chat_id":chat_id,"user_id":target_id,"permissions":{"can_send_messages":True,"can_send_media_messages":True,"can_send_other_messages":True}})
        elif action=="BAN" and target_id: res=telegram_req("banChatMember",{"chat_id":chat_id,"user_id":target_id})
        elif action=="KICK" and target_id:
            telegram_req("banChatMember",{"chat_id":chat_id,"user_id":target_id})
            res=telegram_req("unbanChatMember",{"chat_id":chat_id,"user_id":target_id})
        elif action=="UNBAN" and target_id: res=telegram_req("unbanChatMember",{"chat_id":chat_id,"user_id":target_id})
    except: res={"ok":False}
    try:
        with db_lock:
            c=get_db()
            c.execute("INSERT INTO moderation_logs(chat_id,user_id,action,reason,message_id,success,created_at) VALUES(?,?,?,?,?,?,?)",(str(chat_id),str(target_id or ""),action,reason,message_id or 0,1 if res.get("ok") else 0,datetime.now(timezone.utc).isoformat()))
            c.commit()
            c.close()
    except: pass
    return {"success":bool(res.get("ok"))}

def call_moderation_ai(text):
    tl=text.lower()
    hs=0.85 if any(w in tl for w in ["sem cueca pela casa"]) else min(sum(0.45 for w in SENSUAL_WORDS if w in tl),1.0)
    ht=min(sum(0.35 for w in TOXIC_WORDS if w in tl),1.0)
    hd=min((0.4 if re.search(r"https?://|t\.me/|wa\.me|discord\.gg",tl) else 0)+sum(0.15 for w in DIVULGA_WORDS if w in tl),1.0)
    if not PROVIDERS: return {"toxic":ht,"divulg":hd,"sensual":hs}
    prompt=f'Retorne SOMENTE JSON {{"toxic":0-1,"divulg":0-1,"sensual":0-1}}. Msg:"{text[:200]}"'
    for prov in ORDER_PREFERENCE:
        if prov not in PROVIDERS: continue
        cfg=PROVIDERS[prov]
        try:
            sess=get_session()
            if cfg["format"]=="cloudflare":
                r=sess.post(cfg["endpoint"],json={"messages":[{"role":"user","content":prompt}]},headers={"Authorization":f"Bearer {cfg['key']}"},timeout=6)
                if r.status_code==200:
                    resp=r.json().get("result",{}).get("response","")
                    m=re.search(r"\{.*\}",resp,re.DOTALL)
                    if m:
                        j=json.loads(m.group())
                        return {"toxic":max(float(j.get("toxic",0)),ht),"divulg":max(float(j.get("divulg",0)),hd),"sensual":max(float(j.get("sensual",0)),hs)}
            elif cfg["format"]=="openai":
                model=FALLBACK_MODELS.get(prov,["llama-3.1-8b-instant"])[0]
                endpoint=cfg["endpoint"]
                if "groq.com" in endpoint: endpoint="https://api.groq.com/openai/v1/chat/completions"
                elif "cerebras" in endpoint: endpoint="https://api.cerebras.ai/v1/chat/completions"
                elif not endpoint.endswith("/chat/completions"): endpoint=endpoint.rstrip("/")+"/chat/completions"
                r=sess.post(endpoint,json={"model":model,"messages":[{"role":"user","content":prompt}],"temperature":0.1,"max_tokens":100},headers={"Authorization":f"Bearer {cfg['key']}","Content-Type":"application/json"},timeout=6)
                if r.status_code==200:
                    resp=r.json()["choices"][0]["message"]["content"]
                    m=re.search(r"\{.*\}",resp,re.DOTALL)
                    if m:
                        j=json.loads(m.group())
                        return {"toxic":max(float(j.get("toxic",0)),ht),"divulg":max(float(j.get("divulg",0)),hd),"sensual":max(float(j.get("sensual",0)),hs)}
            elif cfg["format"]=="gemini":
                model=FALLBACK_MODELS.get(prov,["gemini-1.5-flash"])[0]
                url=f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={cfg['key']}"
                r=sess.post(url,json={"contents":[{"parts":[{"text":prompt}]}]},headers={"Content-Type":"application/json"},timeout=6)
                if r.status_code==200:
                    resp=r.json().get("candidates",[{}])[0].get("content",{}).get("parts",[{}])[0].get("text","")
                    m=re.search(r"\{.*\}",resp,re.DOTALL)
                    if m:
                        j=json.loads(m.group())
                        return {"toxic":max(float(j.get("toxic",0)),ht),"divulg":max(float(j.get("divulg",0)),hd),"sensual":max(float(j.get("sensual",0)),hs)}
        except: continue
    return {"toxic":ht,"divulg":hd,"sensual":hs}

def gerar_aviso_ia(nome, motivo, texto_original):
    if not PROVIDERS: return f"⚠️ {nome}, isso não pode aqui no grupo. ({motivo})"
    prompt = f'Você é a moderadora Orbit, jovem, direta. O usuário {nome} mandou "{texto_original[:120]}" e foi bloqueado por {motivo}. Crie um aviso curto, 1 frase, max 18 palavras, em PT-BR informal, sem palavrão. Varie.'
    for prov in ORDER_PREFERENCE:
        if prov not in PROVIDERS: continue
        cfg=PROVIDERS[prov]
        try:
            sess=get_session()
            if cfg["format"]=="cloudflare":
                r=sess.post(cfg["endpoint"],json={"messages":[{"role":"user","content":prompt}]},headers={"Authorization":f"Bearer {cfg['key']}"},timeout=5)
                if r.status_code==200:
                    resp=r.json().get("result",{}).get("response","").strip()
                    if resp: return resp[:190]
            elif cfg["format"]=="openai":
                model=FALLBACK_MODELS.get(prov,["llama-3.1-8b-instant"])[0]
                endpoint=cfg["endpoint"]
                if "groq.com" in endpoint: endpoint="https://api.groq.com/openai/v1/chat/completions"
                elif "cerebras" in endpoint: endpoint="https://api.cerebras.ai/v1/chat/completions"
                elif not endpoint.endswith("/chat/completions"): endpoint=endpoint.rstrip("/")+"/chat/completions"
                r=sess.post(endpoint,json={"model":model,"messages":[{"role":"user","content":prompt}],"temperature":0.85,"max_tokens":60},headers={"Authorization":f"Bearer {cfg['key']}"},timeout=5)
                if r.status_code==200:
                    resp=r.json()["choices"][0]["message"]["content"].strip()
                    if resp: return resp[:190]
            elif cfg["format"]=="gemini":
                model=FALLBACK_MODELS.get(prov,["gemini-1.5-flash"])[0]
                url=f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={cfg['key']}"
                r=sess.post(url,json={"contents":[{"parts":[{"text":prompt}]}]},headers={"Content-Type":"application/json"},timeout=5)
                if r.status_code==200:
                    resp=r.json().get("candidates",[{}])[0].get("content",{}).get("parts",[{}])[0].get("text","").strip()
                    if resp: return resp[:190]
        except: continue
    return f"⚠️ {nome}, isso não pode aqui no grupo. ({motivo})"

def backup_worker():
    global backup_pending,last_backup
    while True:
        time.sleep(300)
        try:
            with db_lock:
                c=get_db()
                c.execute("DELETE FROM pending_rules WHERE datetime(created_at) < datetime('now','-10 minutes')")
                c.execute("DELETE FROM moderation_logs WHERE id NOT IN (SELECT id FROM moderation_logs ORDER BY id DESC LIMIT 500)")
                c.execute("DELETE FROM user_rule_hits WHERE datetime(last_at) < datetime('now','-7 days')")
                c.execute("DELETE FROM warnings WHERE datetime(updated_at) < datetime('now','-7 days')")
                c.commit()
                c.close()
        except: pass
        if not JSONBIN_URL or not backup_pending or time.time()-last_backup<900: continue
        with backup_lock:
            try:
                sz=os.path.getsize(DATABASE_PATH)
                if sz<5000: continue
                with open(DATABASE_PATH,"rb") as f: b64=base64.b64encode(f.read()).decode()
                requests.put(JSONBIN_URL,json={"db":b64,"db_base64":b64,"updated":datetime.now(timezone.utc).isoformat(),"by":KLEBER_SIG},headers=JB_HEADERS,timeout=15)
                last_backup=time.time()
                backup_pending=False
            except: pass
threading.Thread(target=backup_worker,daemon=True).start()

@app.route(WEBHOOK_PATH,methods=["POST"])
def webhook():
    if WEBHOOK_SECRET and request.headers.get("X-Telegram-Bot-Api-Secret-Token")!=WEBHOOK_SECRET: abort(403)
    data=request.get_json(force=True)
    executor.submit(process_update,data)
    return {"ok":True},200

@app.route("/",methods=["GET"])
def health():
    return {"status":ORBIT_CORE,"bot":BOT_USERNAME}

def get_admin_groups_for_user(user_id):
    c=get_db()
    all_groups=c.execute("SELECT chat_id,title FROM group_rules WHERE chat_id LIKE '-100%' ORDER BY rowid DESC LIMIT 30").fetchall()
    c.close()
    admin_groups=[]
    def check_one(g):
        try:
            cid = str(g["chat_id"])
            t = g["title"]
            if not t or t == "None" or t == cid or t.startswith("-100"):
                try:
                    info = telegram_req("getChat", {"chat_id": cid})
                    if info.get("ok"):
                        t = info["result"].get("title") or "Grupo"
                        set_cfg(cid, "title", t)
                except:
                    t = "Grupo"
            if is_admin(cid, user_id):
                return {"chat_id": cid, "title": t or "Grupo"}
        except: return None
    with ThreadPoolExecutor(max_workers=5) as ex:
        results=list(ex.map(check_one, all_groups))
    for r in results:
        if r:
            admin_groups.append(r)
            if len(admin_groups)>=10: break
    return admin_groups

def build_config_kb(chat_id):
    cfg=get_cfg(chat_id)
    return {"inline_keyboard":[
        [{"text":f"{'✅' if cfg.get('anti_divulgation') else '❌'} Anti-Divulg","callback_data":f"cfg|{chat_id}|anti_divulgation"},
         {"text":f"{'✅' if cfg.get('anti_sensual') else '❌'} Anti +18","callback_data":f"cfg|{chat_id}|anti_sensual"}],
        [{"text":f"{'✅' if cfg.get('anti_flood') else '❌'} Anti-Flood","callback_data":f"cfg|{chat_id}|anti_flood"},
         {"text":f"{'✅' if cfg.get('welcome') else '❌'} Boas-vindas","callback_data":f"cfg|{chat_id}|welcome"}],
        [{"text":f"{'🔒 Trancar' if not cfg.get('lock_group') else '🔓 Destrancar'} Grupo","callback_data":f"lock|{chat_id}"}],
        [{"text":"📜 Ver Regras","callback_data":f"regras|{chat_id}"},{"text":"🔄 Atualizar","callback_data":f"painel|{chat_id}"}]
    ]}

def handle_callback(cb):
    global backup_pending
    uid=str(cb["from"]["id"])
    data=cb.get("data","")
    chat_id_msg=cb["message"]["chat"]["id"]
    mid=cb["message"]["message_id"]
    parts=data.split("|")
    if len(parts)<2: return
    action=parts[0]; target_chat=parts[1]
    if not is_admin(target_chat,uid):
        telegram_req("answerCallbackQuery",{"callback_query_id":cb["id"],"text":"Você não é ADM desse grupo","show_alert":True}); return
    if action=="cfg":
        key=parts[2]
        cfg=get_cfg(target_chat)
        new_val=0 if cfg.get(key) else 1
        set_cfg(target_chat,key,new_val)
        telegram_req("answerCallbackQuery",{"callback_query_id":cb["id"],"text":f"{key} = {'ON' if new_val else 'OFF'}"})
        try: telegram_req("editMessageReplyMarkup",{"chat_id":chat_id_msg,"message_id":mid,"reply_markup":build_config_kb(target_chat)})
        except: pass
        return
    if action=="lock":
        cfg=get_cfg(target_chat)
        if cfg.get("lock_group"):
            set_cfg(target_chat,"lock_group",0)
            telegram_req("setChatPermissions",{"chat_id":target_chat,"permissions":{"can_send_messages":True,"can_send_media_messages":True,"can_send_other_messages":True}})
            telegram_req("answerCallbackQuery",{"callback_query_id":cb["id"],"text":"Grupo destrancado"})
        else:
            set_cfg(target_chat,"lock_group",1)
            telegram_req("setChatPermissions",{"chat_id":target_chat,"permissions":{"can_send_messages":False}})
            telegram_req("answerCallbackQuery",{"callback_query_id":cb["id"],"text":"Grupo trancado"})
        try: telegram_req("editMessageReplyMarkup",{"chat_id":chat_id_msg,"message_id":mid,"reply_markup":build_config_kb(target_chat)})
        except: pass
        return
    if action=="regras":
        c=get_db(); rows=c.execute("SELECT id,rule_text FROM custom_rules WHERE chat_id=? ORDER BY id",(target_chat,)).fetchall(); c.close()
        if not rows: send(chat_id_msg,f"📭 Nenhuma regra em *{get_cfg(target_chat).get('title')}*")
        else: send(chat_id_msg,f"📜 *{get_cfg(target_chat).get('title')} - Regras ({len(rows)}/20):*\n" + "\n".join([f"{r['id']}. {r['rule_text']}" for r in rows]))
    elif action=="painel":
        cfg=get_cfg(target_chat); c=get_db(); cr=c.execute("SELECT COUNT(*) as c FROM custom_rules WHERE chat_id=?",(target_chat,)).fetchone()["c"]; c.close()
        txt=f"⚙️ *Painel {cfg.get('title')}*\n\nRegras: {cr}/20\nLock: {'ON' if cfg.get('lock_group') else 'OFF'}\n\nToque pra ligar/desligar:"
        telegram_req("editMessageText",{"chat_id":chat_id_msg,"message_id":mid,"text":txt,"parse_mode":"Markdown","reply_markup":build_config_kb(target_chat)})
    elif action=="del":
        rid=parts[2] if len(parts)>2 else "1"
        try:
            with db_lock: c=get_db(); c.execute("DELETE FROM custom_rules WHERE chat_id=? AND id=?",(target_chat,int(rid))); c.commit(); c.close()
            backup_pending=True
            send(chat_id_msg,f"✅ Regra {rid} apagada de *{get_cfg(target_chat).get('title')}*")
        except: send(chat_id_msg,"Erro ao apagar")
    elif action=="salvar":
        c=get_db(); pend=c.execute("SELECT rules_json FROM pending_rules WHERE chat_id=? AND user_id=?",(f"PV_{uid}",uid)).fetchone(); c.close()
        if not pend: send(chat_id_msg,"Nenhuma pendente"); telegram_req("answerCallbackQuery",{"callback_query_id":cb["id"]}); return
        data=json.loads(pend["rules_json"]); rules=data.get("rules", data if isinstance(data,list) else [])
        with db_lock:
            c=get_db()
            for rtxt in rules: c.execute("INSERT INTO custom_rules(chat_id,rule_text,keywords,created_by,created_at) VALUES(?,?,?,?,?)",(target_chat,rtxt,get_keywords(rtxt),uid,datetime.now(timezone.utc).isoformat()))
            c.execute("DELETE FROM pending_rules WHERE chat_id=? AND user_id=?",(f"PV_{uid}",uid)); c.commit(); c.close()
        backup_pending=True
        send(chat_id_msg,f"✅ {len(rules)} regra(s) salva(s) em *{get_cfg(target_chat).get('title')}*!")
    telegram_req("answerCallbackQuery",{"callback_query_id":cb["id"],"text":"OK"})

def handle_private(msg):
    global backup_pending
    chat_id=msg["chat"]["id"]
    uid=msg["from"]["id"]
    text=(msg.get("text","") or "").strip()
    low=text.lower()
    m=re.match(r"/(\w+)_(-?\d+)(?:\s+(.*))?", text)
    if m:
        cmd=m.group(1).lower()
        target_chat=m.group(2)
        args_text=m.group(3) or ""
        if not is_admin(target_chat,uid):
            send(chat_id,"⚠️ Você não é ADM desse grupo.")
            return
        if cmd in ["regras","listregras"]:
            c=get_db()
            rows=c.execute("SELECT id,rule_text FROM custom_rules WHERE chat_id=? ORDER BY id",(target_chat,)).fetchall()
            c.close()
            if not rows: send(chat_id,f"📭 Nenhuma regra em {target_chat}")
            else: send(chat_id,f"📜 *Regras {target_chat} ({len(rows)}/20):*\n" + "\n".join([f"{r['id']}. {r['rule_text']}" for r in rows]))
            return
        if cmd in ["comoadd","tutorial"]:
            send(chat_id,f"📚 *Como add regra em {target_chat}*\nCole aqui no PV:\n`proibido politica`\nOu lista:\n`proibido link\nproibido briga`\nDepois responda SIM.")
            return
        if cmd=="delregra":
            try:
                rid=int(args_text.strip())
                with db_lock:
                    c=get_db()
                    c.execute("DELETE FROM custom_rules WHERE chat_id=? AND id=?",(target_chat,rid))
                    c.commit()
                    c.close()
                backup_pending=True
                send(chat_id,f"✅ Regra {rid} apagada de {target_chat}")
            except: send(chat_id,"Use: /delregra_-100123 2")
            return
        if cmd=="resetregras":
            with db_lock:
                c=get_db()
                c.execute("DELETE FROM custom_rules WHERE chat_id=?",(target_chat,))
                c.execute("DELETE FROM pending_rules WHERE chat_id=?",(target_chat,))
                c.commit()
                c.close()
            backup_pending=True
            send(chat_id,f"✅ Resetado {target_chat}")
            return
        if cmd=="lock":
            set_cfg(target_chat,"lock_group",1)
            telegram_req("setChatPermissions",{"chat_id":target_chat,"permissions":{"can_send_messages":False}})
            send(chat_id,f"🔒 Trancado {target_chat}")
            return
        if cmd=="unlock":
            set_cfg(target_chat,"lock_group",0)
            telegram_req("setChatPermissions",{"chat_id":target_chat,"permissions":{"can_send_messages":True,"can_send_media_messages":True,"can_send_other_messages":True}})
            send(chat_id,f"🔓 Destrancado {target_chat}")
            return
        if cmd=="painel":
            cfg=get_cfg(target_chat)
            c=get_db()
            rc=c.execute("SELECT COUNT(*) as c FROM custom_rules WHERE chat_id=?",(target_chat,)).fetchone()["c"]
            c.close()
            send(chat_id,f"⚙️ *Painel {target_chat}*\nRegras: {rc}/20\nLock: {'ON' if cfg.get('lock_group') else 'OFF'}\nSensual: {cfg.get('sensual_mode')}")
            return
    if low in ["/start","/meusgrupos","/grupos","/painel"]:
        send(chat_id,"🔍 Buscando seus grupos...")
        def do_search():
            try:
                groups=get_admin_groups_for_user(uid)
                if not groups:
                    send(chat_id,"📭 Você não é ADM em nenhum grupo onde eu estou.\n\n1- Me adicione no grupo\n2- Me promova a ADM\n3- Mande /start aqui de novo")
                    return
                send(chat_id,f"🤖 *Seus grupos ({len(groups)})* - toque nos botões:")
                for g in groups:
                    cid=g["chat_id"]
                    title=(g["title"] or "Grupo").strip()
                    if title.startswith("-100") or title.lower()=="none":
                        title="Grupo"
                    markup={"inline_keyboard":[
                        [{"text":f"📌 {title[:30]}","callback_data":f"painel|{cid}"}],
                        [{"text":"📜 Ver Regras","callback_data":f"regras|{cid}"},{"text":"⚙️ Painel","callback_data":f"painel|{cid}"}],
                        [{"text":"🗑️ Apagar 1","callback_data":f"del|{cid}|1"},{"text":"🗑️ Apagar 2","callback_data":f"del|{cid}|2"}]
                    ]}
                    send(chat_id,f"👇 Gerenciar *{title}*",markup=markup)
                c=get_db(); pend=c.execute("SELECT rules_json FROM pending_rules WHERE chat_id=? AND user_id=?",(f"PV_{uid}",str(uid))).fetchone(); c.close()
                if pend:
                    data=json.loads(pend["rules_json"])
                    rules=data.get("rules", data if isinstance(data,list) else [])
                    txt=f"📝 *Pendente {len(rules)} regra(s):*\n" + "\n".join([f"• {r}" for r in rules]) + "\n\nEscolha onde salvar:"
                    kb=[]
                    for g in groups[:10]: kb.append([{"text":f"💾 Salvar em {g['title'][:25]}","callback_data":f"salvar|{g['chat_id']}"}])
                    send(chat_id,txt,markup={"inline_keyboard":kb})
            except Exception as e:
                import traceback; print(traceback.format_exc())
                send(chat_id,f"Erro no /start: {e}")
        threading.Thread(target=do_search, daemon=True).start()
        return
    if len(text)>=5 and not text.startswith("/"):
        rules=parse_rules_from_text(text)
        if rules:
            groups=get_admin_groups_for_user(uid)
            if not groups:
                send(chat_id,"📭 Sem grupos. Me coloque como ADM primeiro.")
                return
            with db_lock:
                c=get_db()
                c.execute("INSERT OR REPLACE INTO pending_rules(chat_id,user_id,rules_json,created_at) VALUES(?,?,?,?)",(f"PV_{uid}",str(uid),json.dumps({"rules":rules,"groups":[g['chat_id'] for g in groups]}),datetime.now(timezone.utc).isoformat()))
                c.commit()
                c.close()
            txt=f"🤖 Detectei {len(rules)} regra(s):\n" + "\n".join([f"• {r}" for r in rules]) + f"\n\nEscolha onde salvar:"
            kb=[]
            for g in groups[:10]: kb.append([{"text":f"💾 {g['title'][:25]}","callback_data":f"salvar|{g['chat_id']}"}])
            send(chat_id,txt,markup={"inline_keyboard":kb})
            return
    if low.startswith("/salvar_"):
        try:
            target_chat=low.split("_")[1]
            c=get_db()
            pend=c.execute("SELECT rules_json FROM pending_rules WHERE chat_id=? AND user_id=?",(f"PV_{uid}",str(uid))).fetchone()
            c.close()
            if not pend:
                send(chat_id,"Nenhuma pendente.")
                return
            data=json.loads(pend["rules_json"])
            rules=data["rules"]
            with db_lock:
                c=get_db()
                for rtxt in rules:
                    c.execute("INSERT INTO custom_rules(chat_id,rule_text,keywords,created_by,created_at) VALUES(?,?,?,?,?)",(target_chat,rtxt,get_keywords(rtxt),str(uid),datetime.now(timezone.utc).isoformat()))
                c.execute("DELETE FROM pending_rules WHERE chat_id=? AND user_id=?",(f"PV_{uid}",str(uid)))
                c.commit()
                c.close()
            backup_pending=True
            send(chat_id,f"✅ {len(rules)} salva(s) em {target_chat}!")
        except Exception as e:
            send(chat_id,f"Erro {e}")
        return
    if low in ("sim","s","yes"):
        c=get_db()
        pend=c.execute("SELECT rules_json FROM pending_rules WHERE chat_id=? AND user_id=?",(f"PV_{uid}",str(uid))).fetchone()
        c.close()
        if pend:
            data=json.loads(pend["rules_json"])
            rules=data["rules"]
            target_chat=data["groups"][0]
            with db_lock:
                c=get_db()
                for rtxt in rules:
                    c.execute("INSERT INTO custom_rules(chat_id,rule_text,keywords,created_by,created_at) VALUES(?,?,?,?,?)",(target_chat,rtxt,get_keywords(rtxt),str(uid),datetime.now(timezone.utc).isoformat()))
                c.execute("DELETE FROM pending_rules WHERE chat_id=? AND user_id=?",(f"PV_{uid}",str(uid)))
                c.commit()
                c.close()
            backup_pending=True
            send(chat_id,f"✅ Salvo em {target_chat}: {rules}")
            return
    send(chat_id,"📚 *PV CONFIG - V17.8 FALANTE*\n`/start` - seus grupos com botões\nCole regras aqui e eu pergunto onde salvar.")

def process_update(update):
    global backup_pending
    if "callback_query" in update:
        handle_callback(update["callback_query"])
        return
    msg=update.get("message") or update.get("edited_message")
    if not msg: return
    chat_type=msg["chat"].get("type","group")
    if chat_type=="private":
        handle_private(msg)
        return
    chat_id=msg["chat"]["id"]
    uid=msg["from"]["id"]
    text=(msg.get("text","") or msg.get("caption","")).strip()
    mid=msg["message_id"]
    cfg=get_cfg(chat_id)
    try:
        title = msg["chat"].get("title")
        if title and title!= cfg.get("title"):
            set_cfg(chat_id, "title", title)
    except: pass
    if "new_chat_members" in msg:
        now=time.time()
        dq=mem_join[str(chat_id)]
        dq.append(now)
        while dq and now-dq[0]>10: dq.popleft()
        if len(dq)>=5:
            send(chat_id,"🚨 Anti-raid! Muitos joins - grupo trancado automaticamente")
            set_cfg(chat_id,"lock_group",1)
            dq.clear()
        if cfg.get("welcome"):
            for u in msg["new_chat_members"]:
                if str(u.get("id"))==str(BOT_ID): continue
                nome=u.get("first_name","")
                txt=cfg.get("welcome_msg","Bem-vindo {name}! 🚀")
                try: txt=txt.format(name=nome)
                except: pass
                send(chat_id,txt)
        return
    if "left_chat_member" in msg:
        left=msg["left_chat_member"]
        left_id=str(left.get("id"))
        if left_id==str(BOT_ID):
            threading.Thread(target=wipe_group_data,args=(chat_id,),daemon=True).start()
            return
        try:
            with db_lock:
                c=get_db()
                c.execute("DELETE FROM user_rule_hits WHERE chat_id=? AND user_id=?",(str(chat_id),left_id))
                c.commit()
                c.close()
            backup_pending=True
        except: pass
        if cfg.get("goodbye"):
            nome=left.get("first_name","Alguem")
            txt=cfg.get("goodbye_msg","{name} saiu.")
            try: txt=txt.format(name=nome)
            except: pass
            send(chat_id,txt)
        return
    if text.startswith("/"):
        parts=text.strip().split()
        cmd=parts[0].lower().split("@")[0]
        args=parts[1:]
        if cmd in ["/regras","/listregras","/comoadd","/tutorial"]:
            c=get_db()
            rows=c.execute("SELECT id,rule_text FROM custom_rules WHERE chat_id=? ORDER BY id",(str(chat_id),)).fetchall()
            c.close()
            if cmd in ["/comoadd","/tutorial"]:
                send(chat_id,"📚 *COMO ADD REGRA*\n\nCole uma frase que comece com proibido:\n`proibido falar de politica`\n\nOu lista:\n`proibido politica\nproibido link\nproibido divulgar`\n\nDepois responda SIM ou NAO\n\n`/regras` pra ver\n`/delregra 2` pra apagar\n`/resetregras` limpa tudo\n\nIsolado só aqui. Max 20.",mid)
                return
            if not rows: txt="📭 Nenhuma regra ainda. Use /comoadd"
            else: txt=f"📜 *Regras deste grupo ({len(rows)}/20):*\n" + "\n".join([f"{r['id']}. {r['rule_text']}" for r in rows])
            send(chat_id,txt,mid)
            return
        if cmd in ["/start","/help","/painel","/status"]:
            c=get_db()
            cr=c.execute("SELECT COUNT(*) as c FROM custom_rules WHERE chat_id=?",(str(chat_id),)).fetchone()["c"]
            c.close()
            if cmd in ["/painel","/help"]:
                send(chat_id,f"⚙️ *PAINEL V17.8 FALANTE By {KLEBER_SIG}*\nRegras: {cr}/20 | Lock: {'ON' if cfg.get('lock_group') else 'OFF'}\nAnti-Divulg: {'ON' if cfg.get('anti_divulgation') else 'OFF'} | Anti+18: {'ON' if cfg.get('anti_sensual') else 'OFF'}\n\n*REGRAS:* `/regras /comoadd /delregra /resetregras`\n*PV:* Fale comigo no privado /start pra gerenciar todos seus grupos\n*ADM:* /ban /kick /mute /unmute /warn /lock /unlock",mid,markup=build_config_kb(chat_id))
            else: send(chat_id,f"🤖 {ORBIT_CORE} | {cr}/20 regras | @{BOT_USERNAME}",mid)
            return
        if not is_admin(chat_id,uid):
            send(chat_id,"⚠️ Só ADM pode usar este comando.",mid)
            return
        if cmd=="/delregra":
            if args:
                try:
                    rid=int(args[0])
                    with db_lock:
                        c=get_db()
                        c.execute("DELETE FROM custom_rules WHERE chat_id=? AND id=?",(str(chat_id),rid))
                        c.commit()
                        c.close()
                    backup_pending=True
                    send(chat_id,f"✅ Regra {rid} apagada",mid)
                except: send(chat_id,"Use: /delregra 2",mid)
            else:
                send(chat_id,"Use: /delregra 2",mid)
            return
        if cmd=="/resetregras":
            with db_lock:
                c=get_db()
                c.execute("DELETE FROM custom_rules WHERE chat_id=?",(str(chat_id),))
                c.execute("DELETE FROM pending_rules WHERE chat_id=?",(str(chat_id),))
                c.commit()
                c.close()
            backup_pending=True
            send(chat_id,"✅ Todas as regras resetadas deste grupo",mid)
            return
        if cmd=="/ban":
            tgt=msg.get("reply_to_message",{}).get("from",{}).get("id")
            if tgt and not is_protected(chat_id,tgt):
                r=execute_action(chat_id,"BAN",tgt,None,"ban ADM")
                send(chat_id,"🚫 Banido" if r["success"] else "❌ Não consegui banir",mid)
            return
        elif cmd=="/kick":
            tgt=msg.get("reply_to_message",{}).get("from",{}).get("id")
            if tgt and not is_protected(chat_id,tgt):
                execute_action(chat_id,"KICK",tgt,None,"kick ADM")
                send(chat_id,"👢 Kick",mid)
            return
        elif cmd=="/mute":
            tgt=msg.get("reply_to_message",{}).get("from",{}).get("id")
            if tgt and not is_protected(chat_id,tgt):
                execute_action(chat_id,"MUTE",tgt,None,"mute ADM")
                send(chat_id,"🔇 Mutado 10min",mid)
            return
        elif cmd=="/unmute":
            tgt=msg.get("reply_to_message",{}).get("from",{}).get("id")
            if tgt:
                execute_action(chat_id,"UNMUTE",tgt,None,"unmute ADM")
                send(chat_id,"🔊 Desmutado",mid)
            return
        elif cmd=="/warn":
            tgt=msg.get("reply_to_message",{}).get("from",{}).get("id")
            if tgt and not is_protected(chat_id,tgt):
                execute_action(chat_id,"WARN",tgt,None,"warn ADM")
                send(chat_id,"⚠️ Warn aplicado",mid)
            return
        elif cmd=="/lock":
            set_cfg(chat_id,"lock_group",1)
            telegram_req("setChatPermissions",{"chat_id":chat_id,"permissions":{"can_send_messages":False}})
            send(chat_id,"🔒 Grupo trancado",mid)
            return
        elif cmd=="/unlock":
            set_cfg(chat_id,"lock_group",0)
            telegram_req("setChatPermissions",{"chat_id":chat_id,"permissions":{"can_send_messages":True,"can_send_media_messages":True,"can_send_other_messages":True}})
            send(chat_id,"🔓 Grupo destrancado",mid)
            return
        try: telegram_req("deleteMessage",{"chat_id":chat_id,"message_id":mid})
        except: pass
        return
    if is_admin(chat_id,uid):
        c=get_db()
        pend=c.execute("SELECT rules_json,created_at FROM pending_rules WHERE chat_id=? AND user_id=?",(str(chat_id),str(uid))).fetchone()
        c.close()
        if pend:
            try:
                ct=datetime.fromisoformat(pend["created_at"])
                if datetime.now(timezone.utc)-ct>timedelta(minutes=10):
                    with db_lock:
                        c=get_db()
                        c.execute("DELETE FROM pending_rules WHERE chat_id=? AND user_id=?",(str(chat_id),str(uid)))
                        c.commit()
                        c.close()
                    pend=None
            except: pass
        low=text.lower().strip()
        if pend and low in ("sim","s","yes","y","ok"):
            try: rules=json.loads(pend["rules_json"])
            except: rules=[]
            with db_lock:
                c=get_db()
                for rtxt in rules:
                    c.execute("INSERT INTO custom_rules(chat_id,rule_text,keywords,created_by,created_at) VALUES(?,?,?,?,?)",(str(chat_id),rtxt,get_keywords(rtxt),str(uid),datetime.now(timezone.utc).isoformat()))
                c.execute("DELETE FROM pending_rules WHERE chat_id=? AND user_id=?",(str(chat_id),str(uid)))
                c.commit()
                c.close()
            backup_pending=True
            send(chat_id,f"✅ {len(rules)} regra(s) salva(s) só aqui neste grupo!\n" + "\n".join([f"• {r}" for r in rules]),mid)
            return
        if pend and low in ("nao","não","n","cancelar"):
            with db_lock:
                c=get_db()
                c.execute("DELETE FROM pending_rules WHERE chat_id=? AND user_id=?",(str(chat_id),str(uid)))
                c.commit()
                c.close()
            send(chat_id,"❌ Cancelado",mid)
            return
        if len(text)>=5:
            rules=parse_rules_from_text(text)
            if rules:
                with db_lock:
                    c=get_db()
                    c.execute("INSERT OR REPLACE INTO pending_rules(chat_id,user_id,rules_json,created_at) VALUES(?,?,?,?)",(str(chat_id),str(uid),json.dumps(rules),datetime.now(timezone.utc).isoformat()))
                    c.commit()
                    c.close()
                backup_pending=True
                if len(rules)==1: send(chat_id,f"🤖 Detectei:\n`{rules[0]}`\nSalvar só aqui? Responda SIM ou NAO",mid)
                else: send(chat_id,f"🤖 Detectei {len(rules)} regras:\n" + "\n".join([f"{i+1}. {r}" for i,r in enumerate(rules)]) + "\nSalvar só aqui? SIM/NAO",mid)
                return
        return
    if uid==BOT_ID: return
    if cfg.get("lock_group"):
        execute_action(chat_id,"DELETE",None,mid,"lock")
        return
    if not text: return
    custom=check_custom_rules(text,chat_id)
    if custom:
        handle_custom_violation(chat_id,uid,custom,mid, msg["from"].get("first_name",""))
        return
    key=(str(chat_id),str(uid))
    now=time.time()
    dq=mem_flood[key]
    dq.append(now)
    while dq and now-dq[0]>cfg.get("flood_window",15): dq.popleft()
    if len(dq)>cfg.get("flood_limit",7):
        execute_action(chat_id,"MUTE",uid,mid,f"flood {len(dq)}")
        dq.clear()
        return
    mem_texts[key].append(text)
    if len(mem_texts[key])>=3 and len(set(mem_texts[key]))==1:
        execute_action(chat_id,"DELETE",None,mid,"spam repetido")
        return
    ai_res=call_moderation_ai(text)
    nome = msg["from"].get("first_name","amigo")
    if float(ai_res.get("divulg",0))>=0.75 and cfg.get("anti_divulgation"):
        allowed=[d.strip().lower() for d in (cfg.get("allowed_links","").split(",")) if d.strip()]
        if not any(d in text.lower() for d in allowed):
            execute_action(chat_id,"DELETE",None,mid,f"divulg {ai_res['divulg']:.2f}")
            aviso = gerar_aviso_ia(nome, "divulgação", text)
            send(chat_id, aviso)
            return
    if float(ai_res.get("toxic",0))>=0.80:
        execute_action(chat_id,"DELETE",None,mid,f"toxic {ai_res['toxic']:.2f}")
        aviso = gerar_aviso_ia(nome, "linguagem tóxica", text)
        send(chat_id, aviso)
        return
    if float(ai_res.get("sensual",0))>=0.85 and cfg.get("anti_sensual"):
        execute_action(chat_id,"DELETE",None,mid,f"+18 {ai_res['sensual']:.2f}")
        aviso = gerar_aviso_ia(nome, "conteúdo +18", text)
        send(chat_id, aviso)
        return

if __name__=="__main__":
    app.run(host="0.0.0.0",port=PORT)
