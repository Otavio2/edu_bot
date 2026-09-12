# ORBIT ALLIANCE V16 - HANSEL + IA ADM MAX + PV LIVRE + LEARNING BEHAVIOR
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

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
JSONBIN_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_ID}" if JSONBIN_ID and JSONBIN_KEY else None
JB_HEADERS = {"X-Master-Key": JSONBIN_KEY, "Content-Type":"application/json"} if JSONBIN_KEY else {}
WEBHOOK_PATH = "/telegram/webhook"

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s')
executor = ThreadPoolExecutor(max_workers=6)
db_lock = threading.Lock()
backup_lock = threading.Lock()
mem_flood = defaultdict(lambda: deque())
mem_mention = defaultdict(lambda: deque())
mem_texts = defaultdict(lambda: deque(maxlen=5))
mem_warns_time = defaultdict(lambda: deque())
mem_context = defaultdict(lambda: deque(maxlen=5))
mem_join = deque(maxlen=20)
mem_fight = defaultdict(lambda: deque(maxlen=5))
backup_pending = False
last_backup = 0
BOT_ID = None
BOT_USERNAME = None

PROVIDERS_RAW = {
    "groq": {"key_env": "GROQ_API_KEY", "endpoint": "https://api.groq.com/openai/v1", "format": "openai", "timeout": 5},
    "gemini": {"key_env": "GEMINI_API_KEY", "endpoint": "https://generativelanguage.googleapis.com/v1beta", "format": "gemini", "timeout": 6},
    "cerebras": {"key_env": "CEREBRAS_API_KEY", "endpoint": "https://api.cerebras.ai/v1", "format": "openai", "timeout": 5},
}
FALLBACK_MODELS = {
    "groq": ["llama-3.1-8b-instant","llama-3.3-70b-versatile"],
    "gemini": ["gemini-1.5-flash","gemini-2.0-flash"],
    "cerebras": ["llama3.1-8b","llama-3.3-70b"],
}
PROVIDERS = {}
AI_MODEL_BLACKLIST = {}
AI_PROVIDER_BLACKLIST = {}
RECENT_LATENCY = defaultdict(lambda: deque(maxlen=20))
RECENT_ERRORS = defaultdict(lambda: deque(maxlen=50))
PROVIDER_CONCURRENCY = {"groq":2,"gemini":2,"cerebras":2}
PROVIDER_SEMAPHORES = {k: threading.Semaphore(v) for k,v in PROVIDER_CONCURRENCY.items()}
BLACKLIST_LOCK = threading.RLock()
PROVIDERS_LOCK = threading.RLock()
thread_local=threading.local()

def get_session():
    if not hasattr(thread_local,"session"): thread_local.session=requests.Session()
    return thread_local.session

def build_providers_dynamic():
    provs = {}
    for name, cfg in PROVIDERS_RAW.items():
        key = os.getenv(cfg["key_env"])
        if not key: continue
        provs[name] = {"key": key, "endpoint": cfg["endpoint"], "format": cfg["format"], "timeout": cfg["timeout"]}
    return provs
PROVIDERS = build_providers_dynamic()
ORDER_PREFERENCE = ["groq","gemini","cerebras"]

def is_model_blocked(prov, model_id):
    with BLACKLIST_LOCK:
        info = AI_MODEL_BLACKLIST.get((prov, model_id))
        if not info: return False
        if time.time() < info["until"]: return True
        AI_MODEL_BLACKLIST.pop((prov, model_id),None); return False
def is_provider_blocked(prov):
    with BLACKLIST_LOCK:
        info = AI_PROVIDER_BLACKLIST.get(prov)
        if not info: return False, None
        if time.time() < info["until"]: return True, info["reason"]
        AI_PROVIDER_BLACKLIST.pop(prov,None); return False, None
def block_model(prov, model_id, sec=300, reason="fail"):
    with BLACKLIST_LOCK: AI_MODEL_BLACKLIST[(prov, model_id)] = {"until": time.time()+sec, "reason": reason}
def block_provider(prov, sec, reason=""):
    with BLACKLIST_LOCK: AI_PROVIDER_BLACKLIST[prov] = {"until": time.time()+sec, "reason": reason}
def get_dynamic_priority():
    with PROVIDERS_LOCK: ativos = [p for p in ORDER_PREFERENCE if p in PROVIDERS] or list(PROVIDERS.keys())
    with BLACKLIST_LOCK: bl_copy = dict(AI_PROVIDER_BLACKLIST)
    def score(p):
        if bl_copy.get(p) and time.time() < bl_copy[p]["until"]: return 99999
        recent_err = len([e for e in RECENT_ERRORS[p] if time.time()-e < 600])
        recent_lat = sum(RECENT_LATENCY[p])/len(RECENT_LATENCY[p]) if RECENT_LATENCY[p] else 0
        return recent_err*5 + recent_lat/500.0
    return sorted(ativos, key=score)
def get_modelos_para_uso(prov):
    base = FALLBACK_MODELS.get(prov,[])
    disp = [m for m in base if not is_model_blocked(prov, m)]
    return disp or base

VALID_ACTIONS = {"DELETE","WARN","MUTE","KICK","BAN","UNBAN","UNMUTE","PIN","UNPIN"}
VALID_AUTO = {"DELETE","WARN","MUTE","KICK","BAN"}
VALID_MODES = {"observer","moderate","strict","auto"}
VALID_NIGHT = {"silent","strict"}

DIVULGA_WORDS = {"entra","ganhe","lucro","renda","grátis","gratis","promoção","promocao","vagas","dinheiro","pix","aposte","cassino","tigrinho","sorteio","grupo novo"}
TOXIC_WORDS = {"lixo","burro","otario","otário","idiota","fdp","vsf","arrombado","desgraça","vai se foder"}
INTENT_BAN = ["bane","banir","tira esse","remove esse","expulsa","manda embora","bana","bani"]
INTENT_MUTE = ["cala","muta","silencia","cala a boca","mutar","calar"]
INTENT_WARN = ["avisa","adverte","warn","advertir"]
INTENT_KICK = ["kicka","chuta","expulsa","tira"]

def extract_domains(txt):
    txt=str(txt).lower(); out=set()
    for d in re.findall(r"(?:https?://)?(?:www\.)?([a-z0-9-]+\.[a-z0-9.-]+\.[a-z]{2,})",txt):
        d=d.strip("./").split("/")[0].split(":")[0]
        if d.count(".")>=1 and len(d)>3: out.add(d)
    for d in re.findall(r"\b([a-z0-9-]+\.(?:com|net|org|io|com\.br|net\.br))\b",txt): out.add(d)
    return list(out)
def is_link_allowed(txt, allowed_str):
    allowed=[a.strip().lower() for a in allowed_str.split(",") if a.strip()]
    for dom in extract_domains(txt):
        if any(dom==a or dom.endswith("."+a) for a in allowed): continue
        return False,dom
    return True,None

def ai_divulgacao_score(text, domains):
    tl=text.lower(); score=0
    if domains: score+=0.4
    for w in DIVULGA_WORDS:
        if w in tl: score+=0.15
    if len(tl)>10 and sum(c.isupper() for c in text)/max(len(text),1)>0.5: score+=0.2
    if text.count("💰")+text.count("🔥")+text.count("👉")>=2: score+=0.2
    return min(score,1.0)
def ai_similarity_score(new_text, old_texts):
    if not old_texts: return 0
    best=0
    for old in old_texts:
        r=difflib.SequenceMatcher(None, new_text.lower(), old.lower()).ratio()
        if r>best: best=r
    return best
def ai_toxic_score(text):
    tl=text.lower(); score=0
    for w in TOXIC_WORDS:
        if w in tl: score+=0.35
    if len(text)>200 and text.count("!")>5: score+=0.2
    return min(score,1.0)

def ai_intent_pt(text):
    tl=text.lower()
    if any(w in tl for w in INTENT_BAN): return "BAN"
    if any(w in tl for w in INTENT_MUTE): return "MUTE"
    if any(w in tl for w in INTENT_KICK): return "KICK"
    if any(w in tl for w in INTENT_WARN): return "WARN"
    return None

def call_moderation_ai(text):
    if not PROVIDERS:
        return {"toxic": ai_toxic_score(text), "divulg": ai_divulgacao_score(text, extract_domains(text)), "spam": 0, "intencao": ai_intent_pt(text) or "NADA"}
    start=time.time()
    def time_left(): return 6 - (time.time()-start)
    prompt = f"""Analise msg Telegram. Retorne SOMENTE JSON {{"toxic":0-1,"divulg":0-1,"spam":0-1,"intencao":"BAN|MUTE|KICK|WARN|NADA"}} Msg:"{text[:300]}" JSON:"""
    prioridade=get_dynamic_priority()
    for prov in prioridade:
        if time_left()<=1: break
        if is_provider_blocked(prov)[0]: continue
        sem=PROVIDER_SEMAPHORES.get(prov)
        if sem and not sem.acquire(blocking=False): continue
        cfg=PROVIDERS.get(prov); modelos=get_modelos_para_uso(prov)
        try:
            for modelo in modelos:
                if is_model_blocked(prov, modelo): continue
                t0=time.time()
                try:
                    sess=get_session()
                    if cfg["format"]=="openai":
                        url=f"{cfg['endpoint'].rstrip('/')}/chat/completions"
                        r=sess.post(url, json={"model":modelo,"messages":[{"role":"user","content":prompt}],"temperature":0.1,"max_tokens":120}, headers={"Authorization":f"Bearer {cfg['key']}"}, timeout=min(5, max(1,int(time_left()-0.5))))
                        if r.status_code==200:
                            content=r.json()["choices"][0]["message"]["content"]
                            m=re.search(r"\{.*\}",content,re.DOTALL)
                            if m:
                                j=json.loads(m.group()); RECENT_LATENCY[prov].append(int((time.time()-t0)*1000)); return j
                        elif r.status_code==429: block_provider(prov,180,"429"); break
                        else: block_model(prov,modelo,180,f"{r.status_code}")
                except:
                    RECENT_ERRORS[prov].append(time.time()); block_model(prov,modelo,120,"exc"); continue
        finally:
            if sem:
                try: sem.release()
                except: pass
    return {"toxic": ai_toxic_score(text), "divulg": ai_divulgacao_score(text, extract_domains(text)), "spam": 0, "intencao": ai_intent_pt(text) or "NADA"}

def get_db():
    c = sqlite3.connect(DATABASE_PATH, check_same_thread=False, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL;")
    c.execute("PRAGMA busy_timeout=5000;")
    return c

def init_db():
    c = get_db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS groups(chat_id TEXT PRIMARY KEY,title TEXT,type TEXT,enabled INTEGER DEFAULT 1,created_at TEXT,updated_at TEXT);
    CREATE TABLE IF NOT EXISTS group_rules(chat_id TEXT PRIMARY KEY,welcome INTEGER DEFAULT 1,goodbye INTEGER DEFAULT 1,welcome_msg TEXT DEFAULT 'Bem-vindo {name}!',goodbye_msg TEXT DEFAULT '{name} saiu.',anti_link INTEGER DEFAULT 0,anti_spam INTEGER DEFAULT 1,anti_flood INTEGER DEFAULT 1,anti_mention INTEGER DEFAULT 0,anti_divulgation INTEGER DEFAULT 1,allowed_links TEXT DEFAULT 'youtube.com,youtu.be,instagram.com,github.com,google.com',warning_limit INTEGER DEFAULT 3,moderation_mode TEXT DEFAULT 'moderate',flood_limit INTEGER DEFAULT 7,flood_window INTEGER DEFAULT 15,auto_actions TEXT DEFAULT '["DELETE","WARN","MUTE"]',night_mode INTEGER DEFAULT 0,night_mode_type TEXT DEFAULT 'silent',night_start TEXT DEFAULT '22:00',night_end TEXT DEFAULT '06:00',mute_duration INTEGER DEFAULT 600,updated_at TEXT);
    CREATE TABLE IF NOT EXISTS warnings(chat_id TEXT,user_id TEXT,count INTEGER DEFAULT 0,last_reason TEXT,updated_at TEXT,PRIMARY KEY(chat_id,user_id));
    CREATE TABLE IF NOT EXISTS moderation_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id TEXT,user_id TEXT,admin_id TEXT,action TEXT,reason TEXT,message_id INTEGER,source TEXT,success INTEGER,created_at TEXT);
    CREATE TABLE IF NOT EXISTS processed_updates(update_id INTEGER PRIMARY KEY,processed_at TEXT);
    CREATE TABLE IF NOT EXISTS system_errors(id INTEGER PRIMARY KEY AUTOINCREMENT,component TEXT,error TEXT,chat_id TEXT,update_id INTEGER,created_at TEXT);
    CREATE TABLE IF NOT EXISTS backup_meta(rowid INTEGER PRIMARY KEY,last_at TEXT,last_status TEXT,checksum TEXT);
    CREATE TABLE IF NOT EXISTS group_peak(chat_id TEXT, hour INTEGER, count INTEGER DEFAULT 0, PRIMARY KEY(chat_id,hour));
    CREATE TABLE IF NOT EXISTS user_reputation(chat_id TEXT, user_id TEXT, spam_score INTEGER DEFAULT 0, toxic_score INTEGER DEFAULT 0, last_seen TEXT, PRIMARY KEY(chat_id,user_id));
    CREATE TABLE IF NOT EXISTS group_toxic_words(chat_id TEXT, word TEXT, count INTEGER DEFAULT 1, PRIMARY KEY(chat_id,word));
    """)
    c.commit(); c.close()
init_db()

def log_error(comp, err, chat_id=None, upd=None):
    try:
        safe = re.sub(r"bot\d+:[\w-]+|sk-[\w-]+|x-master-key|Bearer [\w-]+","[REDACTED]",str(err),flags=re.I)[:800]
        c=get_db(); c.execute("INSERT INTO system_errors(component,error,chat_id,update_id,created_at) VALUES(?,?,?,?,?)",(comp,safe,str(chat_id) if chat_id else None,upd,datetime.now(timezone.utc).isoformat())); c.commit(); c.close()
    except: pass

def parse_bool(v):
    if isinstance(v,bool): return v
    s=str(v).lower().strip()
    if s in ("true","on","sim","ativar","ativo","1","yes"): return True
    if s in ("false","off","nao","não","desativar","desligado","0","no"): return False
    return None
def valid_hhmm(v):
    if not re.match(r"^\d{2}:\d{2}$",str(v)): return False
    try: h,m=map(int,str(v).split(":")); return 0<=h<=23 and 0<=m<=59
    except: return False
def valid_int(v,mn,mx):
    try: i=int(v); return (mn<=i<=mx,i)
    except: return (False,None)
def telegram_req(method,payload=None,retries=2):
    url=f"{TELEGRAM_API_URL}/{method}"
    for attempt in range(retries+1):
        try:
            r=requests.post(url,json=payload,timeout=12) if payload else requests.get(url,timeout=12)
            j=r.json()
            if r.status_code==429: time.sleep(j.get("parameters",{}).get("retry_after",2)); continue
            return j
        except Exception as e:
            if attempt==retries: log_error(f"TG_{method}",e); return {"ok":False,"error":str(e)}
            time.sleep(0.8)
    return {"ok":False}
def init_bot():
    global BOT_ID, BOT_USERNAME
    d=telegram_req("getMe")
    if d.get("ok"): BOT_ID=d["result"]["id"]; BOT_USERNAME=d["result"].get("username","").lower()
init_bot()
def send(chat_id, txt, reply=None):
    p={"chat_id":chat_id,"text":str(txt)[:4000]}
    if reply: p["reply_to_message_id"]=reply
    return telegram_req("sendMessage",p)
def get_cfg(chat_id):
    c=get_db(); r=c.execute("SELECT * FROM group_rules WHERE chat_id=?",(str(chat_id),)).fetchone(); c.close()
    if not r:
        c=get_db(); c.execute("INSERT OR IGNORE INTO group_rules(chat_id) VALUES(?)",(str(chat_id),)); c.commit(); c.close()
        return get_cfg(chat_id)
    return dict(r)
def set_cfg(chat_id, key, val):
    if key not in {"welcome","goodbye","anti_link","anti_spam","anti_flood","anti_mention","anti_divulgation","warning_limit","moderation_mode","flood_limit","flood_window","welcome_msg","goodbye_msg","allowed_links","auto_actions","night_mode","night_mode_type","night_start","night_end","mute_duration"}: return False
    if key in ("welcome","goodbye","anti_link","anti_spam","anti_flood","anti_mention","anti_divulgation","night_mode"):
        b=parse_bool(val)
        if b is None: return False
        val=1 if b else 0
    if key in ("night_start","night_end") and not valid_hhmm(val): return False
    if key=="night_mode_type" and str(val) not in VALID_NIGHT: return False
    if key=="moderation_mode" and str(val) not in VALID_MODES: return False
    if key=="warning_limit":
        ok,iv=valid_int(val,1,20)
        if not ok: return False
        val=iv
    if key=="flood_limit":
        ok,iv=valid_int(val,2,100)
        if not ok: return False
        val=iv
    if key=="flood_window":
        ok,iv=valid_int(val,1,300)
        if not ok: return False
        val=iv
    if key=="mute_duration":
        ok,iv=valid_int(val,60,86400)
        if not ok: return False
        val=iv
    if key=="auto_actions":
        try:
            arr=[str(x).upper() for x in (json.loads(val) if isinstance(val,str) else val)]
            if any(a not in VALID_AUTO for a in arr): return False
            val=json.dumps(arr)
        except: return False
    with db_lock:
        c=get_db(); c.execute(f"UPDATE group_rules SET {key}=?, updated_at=? WHERE chat_id=?",(val,datetime.now(timezone.utc).isoformat(),str(chat_id))); c.commit(); c.close()
    global backup_pending; backup_pending=True
    return True
def is_admin(chat_id,uid):
    m=telegram_req("getChatMember",{"chat_id":chat_id,"user_id":uid})
    return m.get("ok") and m.get("result",{}).get("status") in ("administrator","creator")
def is_protected(chat_id,uid):
    if str(uid)==str(BOT_ID): return True
    if str(uid)==CREATOR_ID: return True
    return is_admin(chat_id,uid)
def bot_can(chat_id,perm):
    m=telegram_req("getChatMember",{"chat_id":chat_id,"user_id":BOT_ID})
    r=m.get("result") if m.get("ok") else None
    if not r: return False
    if r["status"]=="creator": return True
    return bool(r.get(perm,False))
def authorize_action(chat_id,action,target_id=None,confidence=1.0,source="AUTO"):
    if action not in VALID_ACTIONS: return False,"acao_invalida"
    cfg=get_cfg(chat_id)
    if str(cfg.get("enabled",1))=="0": return False,"grupo_desabilitado"
    if source=="AUTO":
        try: allowed=[a.upper() for a in json.loads(cfg.get("auto_actions",'["DELETE","WARN","MUTE"]'))]
        except: allowed=["DELETE","WARN","MUTE"]
        if action not in allowed: return False,f"bloqueado_auto {allowed}"
    if target_id and is_protected(chat_id,target_id): return False,"alvo_protegido"
    if action in ("BAN","KICK","MUTE") and not bot_can(chat_id,"can_restrict_members"): return False,"sem_permissao_restrict"
    if action=="DELETE" and not bot_can(chat_id,"can_delete_messages"): return False,"sem_permissao_delete"
    if action=="PIN" and not bot_can(chat_id,"can_pin_messages"): return False,"sem_permissao_pin"
    if action=="BAN" and confidence<0.95 and source=="AUTO": return False,"confidence_ban_baixa"
    return True,"ok"
def log_action(chat_id,uid,action,reason,mid,source,success,admin_id=None):
    try:
        with db_lock:
            c=get_db(); c.execute("INSERT INTO moderation_logs(chat_id,user_id,admin_id,action,reason,message_id,source,success,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(str(chat_id),str(uid) if uid else None,str(admin_id) if admin_id else None,action,str(reason)[:200],mid,source,1 if success else 0,datetime.now(timezone.utc).isoformat())); c.commit(); c.close()
        global backup_pending; backup_pending=True
    except Exception as e: log_error("LOG",e,chat_id)
def execute_action(chat_id,action,target_id=None,reason="",message_id=None,admin_id=None,source="COMMAND",confidence=1.0):
    ok,why=authorize_action(chat_id,action,target_id,confidence,source)
    if not ok:
        log_action(chat_id,target_id,f"BLOCKED_{action}",why,message_id,source,False,admin_id)
        return {"success":False,"error":why,"action":action}
    res={"ok":False}
    if action=="DELETE": res=telegram_req("deleteMessage",{"chat_id":chat_id,"message_id":message_id})
    elif action=="MUTE":
        dur=get_cfg(chat_id).get("mute_duration",600); _,iv=valid_int(dur,60,86400); iv=iv if _ else 600
        res=telegram_req("restrictChatMember",{"chat_id":chat_id,"user_id":target_id,"permissions":{"can_send_messages":False},"until_date":int(time.time())+iv})
    elif action=="BAN": res=telegram_req("banChatMember",{"chat_id":chat_id,"user_id":target_id})
    elif action=="KICK":
        r1=telegram_req("banChatMember",{"chat_id":chat_id,"user_id":target_id})
        if not r1.get("ok"): return {"success":False,"error":"ban_fail","action":action}
        res=telegram_req("unbanChatMember",{"chat_id":chat_id,"user_id":target_id})
    elif action=="UNBAN": res=telegram_req("unbanChatMember",{"chat_id":chat_id,"user_id":target_id})
    elif action=="UNMUTE": res=telegram_req("restrictChatMember",{"chat_id":chat_id,"user_id":target_id,"permissions":{"can_send_messages":True,"can_send_media_messages":True,"can_send_other_messages":True}})
    elif action=="PIN": res=telegram_req("pinChatMessage",{"chat_id":chat_id,"message_id":message_id})
    elif action=="UNPIN": res=telegram_req("unpinChatMessage",{"chat_id":chat_id,"message_id":message_id})
    success=bool(res.get("ok"))
    log_action(chat_id,target_id,action,reason[:200],message_id,source,success,admin_id)
    if success and action in ("DELETE","MUTE","BAN") and target_id:
        try:
            with db_lock:
                c=get_db()
                c.execute("INSERT INTO user_reputation(chat_id,user_id,spam_score,last_seen) VALUES(?,?,1,?) ON CONFLICT(chat_id,user_id) DO UPDATE SET spam_score=spam_score+1, last_seen=?",(str(chat_id),str(target_id),datetime.now(timezone.utc).isoformat(),datetime.now(timezone.utc).isoformat()))
                c.commit(); c.close()
        except: pass
    return {"success":success,"error":None if success else str(res)[:200],"action":action}

# ===== LEARNING BEHAVIOR MODULE =====
def learn_peak_and_check(chat_id):
    try:
        hour = datetime.now(TZ).hour
        with db_lock:
            c=get_db()
            c.execute("INSERT INTO group_peak(chat_id,hour,count) VALUES(?,?,1) ON CONFLICT(chat_id,hour) DO UPDATE SET count=count+1",(str(chat_id),hour))
            rows=c.execute("SELECT count FROM group_peak WHERE chat_id=?",(str(chat_id),)).fetchall()
            c.commit(); c.close()
        if len(rows) < 5: return False
        counts=[r["count"] for r in rows]
        avg=sum(counts)/len(counts)
        c=get_db(); r=c.execute("SELECT count FROM group_peak WHERE chat_id=? AND hour=?",(str(chat_id),hour)).fetchone(); c.close()
        cur = r["count"] if r else 0
        return cur > avg*1.5 and cur > 10
    except: return False

def is_spammer_recurrente(chat_id, user_id):
    try:
        c=get_db(); row=c.execute("SELECT spam_score FROM user_reputation WHERE chat_id=? AND user_id=?",(str(chat_id),str(user_id))).fetchone(); c.close()
        return row and row["spam_score"] >= 3
    except: return False

def check_group_toxic_word(chat_id, text):
    try:
        tl=text.lower()
        c=get_db(); rows=c.execute("SELECT word,count FROM group_toxic_words WHERE chat_id=? AND count>=3 ORDER BY count DESC LIMIT 20",(str(chat_id),)).fetchall(); c.close()
        for r in rows:
            if r["word"] in tl:
                return r["word"], r["count"]
        return None,0
    except: return None,0

def learn_toxic_words_from_fight(chat_id, texts):
    try:
        words=[]
        for t in texts:
            words+=re.findall(r"\b\w{4,}\b", t.lower())
        common=[w for w,c in Counter(words).items() if c>=2 and w not in TOXIC_WORDS and len(w)>3][:5]
        if not common: return
        with db_lock:
            c=get_db()
            for w in common:
                c.execute("INSERT INTO group_toxic_words(chat_id,word,count) VALUES(?,?,1) ON CONFLICT(chat_id,word) DO UPDATE SET count=count+1",(str(chat_id),w))
            c.commit(); c.close()
    except: pass

def restore_safe():
    if not JSONBIN_URL: return
    if os.path.exists(DATABASE_PATH) and os.path.getsize(DATABASE_PATH) > 1024:
        try:
            c=sqlite3.connect(DATABASE_PATH); chk=c.execute("PRAGMA quick_check").fetchone(); c.close()
            if chk and "ok" in str(chk[0]).lower(): return
        except: pass
    try:
        r=requests.get(JSONBIN_URL+"/latest",headers=JB_HEADERS,timeout=12)
        if r.status_code!=200: return
        rec=r.json().get("record",{}); b64=rec.get("db_base64") or rec.get("b64")
        if not b64: return
        if rec.get("checksum"):
            calc=hashlib.sha256(b64.encode()).hexdigest()[:16]
            if calc!=rec.get("checksum"): log_error("RESTORE","checksum_mismatch"); return
        tmp=DATABASE_PATH+".restore"
        with open(tmp,"wb") as f: f.write(base64.b64decode(b64))
        c=sqlite3.connect(tmp); chk=c.execute("PRAGMA quick_check").fetchone(); c.close()
        if not chk or "ok" not in str(chk[0]).lower(): os.remove(tmp); log_error("RESTORE","quick_check_fail"); return
        shutil.move(tmp,DATABASE_PATH)
    except Exception as e: log_error("RESTORE",e)

def backup_worker():
    global backup_pending,last_backup
    while True:
        time.sleep(30)
        if not backup_pending or not JSONBIN_URL: continue
        if time.time()-last_backup<30: continue
        with backup_lock:
            try:
                tmp=DATABASE_PATH+".tmpcopy"; shutil.copy2(DATABASE_PATH,tmp)
                with open(tmp,"rb") as f: b64=base64.b64encode(f.read()).decode()
                chk=hashlib.sha256(b64.encode()).hexdigest()[:16]
                payload={"db_base64":b64,"updated_at":datetime.now(timezone.utc).isoformat(),"checksum":chk,"version":int(time.time())}
                r=requests.put(JSONBIN_URL,json=payload,headers=JB_HEADERS,timeout=15)
                if r.status_code==200:
                    last_backup=time.time(); backup_pending=False
                    c=get_db(); c.execute("INSERT OR REPLACE INTO backup_meta(rowid,last_at,last_status,checksum) VALUES(1,?,?,?)",(payload["updated_at"],"ok",chk)); c.commit(); c.close()
                try: os.remove(tmp)
                except: pass
            except Exception as e: log_error("BACKUP",e)

def cleanup_worker():
    while True:
        time.sleep(3600)
        try:
            c=get_db()
            c.execute("DELETE FROM processed_updates WHERE processed_at < datetime('now','-30 days')")
            c.execute("DELETE FROM moderation_logs WHERE created_at < datetime('now','-90 days')")
            c.execute("DELETE FROM system_errors WHERE created_at < datetime('now','-30 days')")
            c.commit(); c.close()
            mem_flood.clear(); mem_mention.clear()
        except Exception as e: log_error("CLEANUP",e)

threading.Thread(target=backup_worker,daemon=True).start()
threading.Thread(target=cleanup_worker,daemon=True).start()

@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    if WEBHOOK_SECRET:
        if request.headers.get("X-Telegram-Bot-Api-Secret-Token")!=WEBHOOK_SECRET: abort(403)
    try: data=request.get_json(force=True)
    except: log_error("WEBHOOK","payload_invalid"); return {"ok":True},200
    if not data or "update_id" not in data: return {"ok":True},200
    uid=data["update_id"]
    c=get_db()
    try: c.execute("INSERT INTO processed_updates(update_id,processed_at) VALUES(?,?)",(uid,datetime.now(timezone.utc).isoformat())); c.commit()
    except sqlite3.IntegrityError: c.close(); return {"ok":True},200
    except: pass
    finally:
        try: c.close()
        except: pass
    executor.submit(process_update_safe, data)
    return {"ok":True},200

@app.route("/", methods=["GET"])
def health():
    try: c=get_db(); c.execute("SELECT 1").fetchone(); c.close(); db="🟢"
    except: db="🔴"
    return {"status":"Orbit V16 LEARNING+PV LIVRE","bot_id":BOT_ID,"db":db,"providers": get_dynamic_priority(),"last_backup":last_backup}

@app.route("/setwebhook", methods=["GET"])
def setwebhook():
    url = request.args.get("url")
    if not url: return {"error":"?url=https://seu-app.onrender.com/telegram/webhook"},400
    r=telegram_req("setWebhook",{"url":url,"secret_token":WEBHOOK_SECRET} if WEBHOOK_SECRET else {"url":url})
    return r

def process_update_safe(upd):
    try: process_update(upd)
    except Exception as e: log_error("PROCESS",e,None,upd.get("update_id"))

def resolve_target(msg, args):
    if msg.get("reply_to_message"):
        return str(msg["reply_to_message"]["from"]["id"])
    if args and str(args[0]).isdigit():
        return str(args[0])
    txt = msg.get("text","") or msg.get("caption","") or ""
    m = re.search(r"@(\w+)", txt)
    if m:
        uname = m.group(1).lower()
        for ctx in list(mem_context.get(str(msg["chat"]["id"]), []))[-10:]:
            if uname in ctx.get("text","").lower():
                return ctx.get("uid")
        return None
    return None

def process_update(update):
    if "my_chat_member" in update:
        chat=update["my_chat_member"]["chat"]; cid=str(chat["id"])
        c=get_db(); c.execute("INSERT OR REPLACE INTO groups(chat_id,title,type,updated_at) VALUES(?,?,?,?)",(cid,chat.get("title",""),chat.get("type",""),datetime.now(timezone.utc).isoformat())); c.commit(); c.close()
        return
    if "chat_member" in update: return
    msg = update.get("message") or update.get("edited_message")
    if not msg: return
    is_edited = "edited_message" in update
    chat_id = msg["chat"]["id"]; chat_type = msg["chat"]["type"]
    uid = msg["from"]["id"]; text = msg.get("text","") or msg.get("caption","")
    mid = msg["message_id"]
    cfg = get_cfg(chat_id)
    c=get_db(); g=c.execute("SELECT enabled FROM groups WHERE chat_id=?",(str(chat_id),)).fetchone(); c.close()
    if g and g["enabled"]==0 and not text.startswith("/"): return
    c=get_db(); c.execute("INSERT OR REPLACE INTO groups(chat_id,title,type,updated_at) VALUES(?,?,?,?)",(str(chat_id),msg["chat"].get("title",""),chat_type,datetime.now(timezone.utc).isoformat())); c.commit(); c.close()

    mem_context[str(chat_id)].append({"uid":str(uid),"text":text,"time":time.time()})

    if "new_chat_members" in msg:
        if cfg.get("welcome"):
            for u in msg["new_chat_members"]:
                if str(u.get("id")) == str(BOT_ID): continue
                nome = u.get("first_name","")
                txt_w = cfg.get("welcome_msg","Bem-vindo {name}! 👋")
                try: txt_w = txt_w.format(name=nome)
                except: pass
                send(chat_id, txt_w)
        return
    if "left_chat_member" in msg:
        if cfg.get("goodbye"):
            left = msg["left_chat_member"]
            nome = left.get("first_name","Alguém")
            txt_b = cfg.get("goodbye_msg","{name} saiu. 👋")
            try: txt_b = txt_b.format(name=nome)
            except: pass
            send(chat_id, txt_b)
        return

    is_peak = learn_peak_and_check(chat_id) if chat_type!="private" else False

    if chat_type!="private" and not is_admin(chat_id,uid):
        if is_spammer_recurrente(chat_id, uid):
            execute_action(chat_id,"MUTE",uid,"spammer recorrente - observação",mid,source="AUTO",confidence=0.92)
            send(chat_id,f"🚨 {uid} em observação - mutado automaticamente.")
            return
        word,cnt = check_group_toxic_word(chat_id, text)
        if word:
            execute_action(chat_id,"DELETE",uid,f"palavra tóxica aprendida [{word}] x{cnt}",mid,source="AUTO",confidence=0.88)
            return

    if cfg.get("night_mode"):
        ns,ne=cfg.get("night_start","22:00"),cfg.get("night_end","06:00")
        if valid_hhmm(ns) and valid_hhmm(ne):
            now=datetime.now(TZ).strftime("%H:%M")
            in_night=(ns<=now or now<ne) if ns>ne else (ns<=now<ne)
            if in_night and not is_admin(chat_id,uid):
                if cfg.get("night_mode_type")=="silent": execute_action(chat_id,"DELETE",uid,"night_silent",mid,source="AUTO"); return
                elif cfg.get("night_mode_type")=="strict":
                    if len(text)>500: execute_action(chat_id,"DELETE",uid,"night_strict_long",mid,source="AUTO"); return

    if text and is_admin(chat_id,uid):
        intent = ai_intent_pt(text)
        if intent:
            t = resolve_target(msg, [])
            if not t and msg.get("reply_to_message"):
                t = str(msg["reply_to_message"]["from"]["id"])
            if t and t.isdigit():
                r=execute_action(chat_id,intent,t,f"intencao {intent} PT por {uid}",None,admin_id=uid,source="COMMAND",confidence=0.96)
                if r["success"]: send(chat_id,f"🤖 IA: {intent} em {t} ✅",mid); return

    if text.startswith("/"):
        parts=text.strip().split(); cmd=parts[0].split("@")[0].lower(); args=parts[1:] if len(parts)>1 else []
        if chat_type=="private":
            if cmd in ("/ban","/kick","/mute","/unmute","/unban","/delete","/warn","/unwarn","/pin","/unpin","/warnings","/logs"):
                send(chat_id,"⚠️ Esse comando só funciona em grupos. Me adicione num grupo.", mid); return
        else:
            if cmd in ("/ban","/kick","/mute","/unmute","/unban","/delete","/warn","/unwarn","/pin","/unpin","/logs","/warnings","/status","/resetwarnings","/allowlink","/resetai"):
                if not is_admin(chat_id,uid): send(chat_id,"⚠️ Só ADM."); return

        if cmd=="/ban":
            target=resolve_target(msg,args)
            if not target or not str(target).isdigit(): send(chat_id,"⚠️ Responda /ban", mid); return
            r=execute_action(chat_id,"BAN",target,f"ban por {uid}",None,admin_id=uid,source="COMMAND",confidence=1.0)
            send(chat_id,f"🚫 Banido {target}" if r["success"] else f"❌ {r['error']}", mid); return
        if cmd=="/unban":
            target=resolve_target(msg,args) or (args[0] if args else None)
            if not target or not str(target).isdigit(): send(chat_id,"⚠️ /unban <id>", mid); return
            r=execute_action(chat_id,"UNBAN",target,"unban",None,admin_id=uid,source="COMMAND")
            send(chat_id,"✅ Desbanido." if r["success"] else f"❌ {r['error']}", mid); return
        if cmd=="/kick":
            target=resolve_target(msg,args)
            if not target or not str(target).isdigit(): send(chat_id,"⚠️ Responda /kick", mid); return
            r=execute_action(chat_id,"KICK",target,f"kick por {uid}",None,admin_id=uid,source="COMMAND")
            send(chat_id,"👢 Expulso." if r["success"] else f"❌ {r['error']}", mid); return
        if cmd=="/mute":
            target=resolve_target(msg,args)
            if not target or not str(target).isdigit(): send(chat_id,"⚠️ Responda /mute", mid); return
            r=execute_action(chat_id,"MUTE",target,f"mute por {uid}",None,admin_id=uid,source="COMMAND")
            send(chat_id,"🔇 Mutado." if r["success"] else f"❌ {r['error']}", mid); return
        if cmd=="/unmute":
            target=resolve_target(msg,args)
            if not target or not str(target).isdigit(): send(chat_id,"⚠️ Responda /unmute", mid); return
            r=execute_action(chat_id,"UNMUTE",target,"unmute",None,admin_id=uid,source="COMMAND")
            send(chat_id,"🔊 Desmutado." if r["success"] else f"❌ {r['error']}", mid); return
        if cmd=="/delete":
            if not msg.get("reply_to_message"): send(chat_id,"⚠️ Responda /delete", mid); return
            r=execute_action(chat_id,"DELETE",None,"delete",msg["reply_to_message"]["message_id"],admin_id=uid,source="COMMAND")
            if not r["success"]: send(chat_id,f"❌ {r['error']}"); return
        if cmd=="/warn":
            target=resolve_target(msg,args)
            if not target or not str(target).isdigit(): send(chat_id,"⚠️ Responda /warn", mid); return
            if is_protected(chat_id,target): send(chat_id,"⚠️ Protegido"); return
            with db_lock:
                c=get_db(); row=c.execute("SELECT count FROM warnings WHERE chat_id=? AND user_id=?",(str(chat_id),str(target))).fetchone()
                cnt=(row["count"]+1) if row else 1
                c.execute("INSERT OR REPLACE INTO warnings(chat_id,user_id,count,last_reason,updated_at) VALUES(?,?,?,?,?)",(str(chat_id),str(target),cnt,f"warn por {uid}",datetime.now(timezone.utc).isoformat())); c.commit(); c.close()
                mem_warns_time[(str(chat_id),str(target))].append(time.time())
                dq=mem_warns_time[(str(chat_id),str(target))]
                while dq and time.time()-dq[0]>3600: dq.popleft()
                if len(dq)>=3:
                    execute_action(chat_id,"BAN",target,"3 warns em 1h - IA preditiva",None,admin_id=uid,source="AUTO",confidence=0.96)
                    send(chat_id,f"🤖 IA preditiva: {target} BAN por 3 warns/h", mid); return
            log_action(chat_id,target,"WARN",f"warn por {uid}",mid,"COMMAND",True,uid)
            send(chat_id,f"⚠️ {target} {cnt}/{get_cfg(chat_id).get('warning_limit',3)}", mid); return
        if cmd=="/unwarn":
            target=resolve_target(msg,args)
            if not target: send(chat_id,"⚠️ /unwarn", mid); return
            with db_lock: c=get_db(); c.execute("DELETE FROM warnings WHERE chat_id=? AND user_id=?",(str(chat_id),str(target))); c.commit(); c.close()
            mem_warns_time[(str(chat_id),str(target))].clear()
            send(chat_id,f"✅ Reset {target}", mid); return
        if cmd=="/warnings":
            target=resolve_target(msg,args) or str(uid)
            c=get_db(); row=c.execute("SELECT count,last_reason FROM warnings WHERE chat_id=? AND user_id=?",(str(chat_id),str(target))).fetchone(); c.close()
            if not row: send(chat_id,f"✅ {target} sem warns", mid)
            else: send(chat_id,f"⚠️ {target}: {row['count']} warns - {row['last_reason'][:100]}", mid); return
        if cmd=="/resetwarnings":
            with db_lock: c=get_db(); c.execute("DELETE FROM warnings WHERE chat_id=?",(str(chat_id),)); c.commit(); c.close()
            send(chat_id,"✅ Warnings resetados", mid); return
        if cmd=="/allowlink":
            if not args: send(chat_id,"Use: /allowlink dominio.com", mid); return
            dom=args[0].lower().strip(); cur=get_cfg(chat_id).get("allowed_links",""); lista=[a.strip() for a in cur.split(",") if a.strip()]
            if dom not in lista: lista.append(dom); set_cfg(chat_id,"allowed_links",",".join(lista))
            send(chat_id,f"✅ {dom} permitido.", mid); return
        if cmd=="/pin":
            if not msg.get("reply_to_message"): send(chat_id,"Responda /pin", mid); return
            r=execute_action(chat_id,"PIN",None,"pin",msg["reply_to_message"]["message_id"],admin_id=uid,source="COMMAND")
            send(chat_id,"📌 Fixado." if r["success"] else f"❌ {r['error']}", mid); return
        if cmd=="/unpin":
            r=execute_action(chat_id,"UNPIN",None,"unpin",None,admin_id=uid,source="COMMAND")
            send(chat_id,"📌 Desfixado." if r["success"] else f"❌ {r['error']}", mid); return
        if cmd=="/logs":
            c=get_db(); rows=c.execute("SELECT action,reason,created_at FROM moderation_logs WHERE chat_id=? ORDER BY id DESC LIMIT 20",(str(chat_id),)).fetchall(); c.close()
            out="\n".join([f"{r['created_at'][11:16]} {r['action']} {r['reason'][:30]}" for r in rows]) if rows else "Sem logs"
            send(chat_id,out[:3900], mid); return
        if cmd=="/resetai":
            with BLACKLIST_LOCK: AI_MODEL_BLACKLIST.clear(); AI_PROVIDER_BLACKLIST.clear()
            RECENT_LATENCY.clear(); RECENT_ERRORS.clear()
            global PROVIDERS; PROVIDERS=build_providers_dynamic()
            send(chat_id,f"♻️ IA reset: {get_dynamic_priority()}", mid); return
        if cmd=="/status":
            try: tg=telegram_req("getMe"); tgs="🟢" if tg.get("ok") else "🔴"
            except: tgs="🔴"
            try: c=get_db(); c.execute("SELECT 1").fetchone(); c.close(); dbs="🟢"
            except: dbs="🔴"
            jbs=f"🟢 {int(last_backup)}" if JSONBIN_URL else "⚪ OFF"
            try:
                c=get_db()
                peak=c.execute("SELECT hour,count FROM group_peak WHERE chat_id=? ORDER BY count DESC LIMIT 1",(str(chat_id),)).fetchone()
                rep=c.execute("SELECT COUNT(*) as c FROM user_reputation WHERE chat_id=? AND spam_score>=3",(str(chat_id),)).fetchone()
                tox=c.execute("SELECT word,count FROM group_toxic_words WHERE chat_id=? ORDER BY count DESC LIMIT 3",(str(chat_id),)).fetchall()
                c.close()
                peak_txt=f"{peak['hour']}h ({peak['count']} msgs)" if peak else "aprendendo"
                rep_txt=rep["c"] if rep else 0
                tox_txt=", ".join([f"{r['word']}({r['count']})" for r in tox]) if tox else "nenhuma"
            except: peak_txt="erro"; rep_txt=0; tox_txt="erro"
            send(chat_id,f"*Orbit V16 LEARNING*\nTG:{tgs} DB:{dbs} BIN:{jbs}\nOrdem IA: {get_dynamic_priority()}\nModo:{get_cfg(chat_id).get('moderation_mode')}\n\n📊 *Aprendizado:*\nPico: {peak_txt} {'🔥 RÍGIDO' if is_peak else ''}\nObservação: {rep_txt} users\nPalavras tóxicas do grupo: {tox_txt}", mid); return
        if cmd in ("/start","/help"):
            send(chat_id,"🚀 *Orbit IA MAX - LEARNING*\n\n ----- Criador: Kʆɛɓɛʀ -----\n\nComandos: /ban /kick /mute /unmute /delete /warn /unwarn /warnings /resetwarnings /allowlink /pin /unpin /logs /status /resetai\n\n🤖 *Bot autônomo IA:*\n• Aprende horário de pico e fica mais rígido\n• Spammer recorrente entra em observação (3 deletes = mute auto)\n• Aprende palavras que causam briga naquele grupo e apaga antes", mid); return

    if uid==BOT_ID: return
    if is_admin(chat_id,uid): return
    if not text: return
    if is_edited:
        c=get_db(); already=c.execute("SELECT id FROM moderation_logs WHERE chat_id=? AND message_id=? AND success=1 LIMIT 1",(str(chat_id),str(mid))).fetchone(); c.close()
        if already: return

    ai_res = call_moderation_ai(text)

    if ai_res.get("divulg",0) >= 0.7 and cfg.get("anti_divulgation"):
        r=execute_action(chat_id,"DELETE",uid,f"divulg IA {ai_res['divulg']:.2f}",mid,source="AUTO",confidence=ai_res['divulg'])
        if r["success"]:
            with db_lock:
                c=get_db(); row=c.execute("SELECT count FROM warnings WHERE chat_id=? AND user_id=?",(str(chat_id),str(uid))).fetchone()
                cnt=(row["count"]+1) if row else 1
                c.execute("INSERT OR REPLACE INTO warnings(chat_id,user_id,count,last_reason,updated_at) VALUES(?,?,?,?,?)",(str(chat_id),str(uid),cnt,f"divulg {ai_res['divulg']:.2f}",datetime.now(timezone.utc).isoformat())); c.commit(); c.close()
        return

    ok,dom=is_link_allowed(text,cfg.get("allowed_links",""))
    if cfg.get("anti_link") and not ok:
        r=execute_action(chat_id,"DELETE",uid,f"link {dom}",mid,source="AUTO",confidence=0.99)
        if r["success"]:
            with db_lock:
                c=get_db(); row=c.execute("SELECT count FROM warnings WHERE chat_id=? AND user_id=?",(str(chat_id),str(uid))).fetchone()
                cnt=(row["count"]+1) if row else 1
                c.execute("INSERT OR REPLACE INTO warnings(chat_id,user_id,count,last_reason,updated_at) VALUES(?,?,?,?,?)",(str(chat_id),str(uid),cnt,f"link {dom}",datetime.now(timezone.utc).isoformat())); c.commit(); c.close()
        return

    old=list(mem_texts[(str(chat_id),str(uid))])
    sim=ai_similarity_score(text, old)
    if cfg.get("anti_spam") and sim>=0.85 and len(text)>15:
        execute_action(chat_id,"DELETE",uid,f"spam similar {sim:.2f}",mid,source="AUTO",confidence=sim); return
    mem_texts[(str(chat_id),str(uid))].append(text)

    if ai_res.get("toxic",0) >= 0.7:
        r=execute_action(chat_id,"DELETE",uid,f"toxico IA {ai_res['toxic']:.2f}",mid,source="AUTO",confidence=ai_res['toxic'])
        if ai_res.get("toxic",0)>=0.9: execute_action(chat_id,"MUTE",uid,"toxico grave",mid,source="AUTO",confidence=ai_res['toxic'])
        mem_fight[str(chat_id)].append((str(uid),time.time(),ai_res.get("toxic",0),text))
        recent=[f for f in mem_fight[str(chat_id)] if time.time()-f[1]<30]
        if len(recent)>=2 and len(set(u for u,_,_,_ in recent))>=2 and all(s>=0.7 for _,_,_,s in recent):
            learn_toxic_words_from_fight(chat_id, [t for _,_,_,t in recent])
            for u,_,_,_ in recent: execute_action(chat_id,"MUTE",u,"briga IA - aprendizado",None,source="AUTO",confidence=0.85)
            send(chat_id,"🤖 IA: briga detectada, calma galera. Mute 10min. Aprendi palavras dessa briga.")
            mem_fight[str(chat_id)].clear()
        return

    if cfg.get("anti_flood"):
        dq=mem_flood[(str(chat_id),str(uid))]; now=time.time(); dq.append(now)
        while dq and now-dq[0]>cfg.get("flood_window",15): dq.popleft()
        limit = cfg.get("flood_limit",7)
        if is_peak: limit = max(3, limit-3)
        if len(dq)>limit:
            r=execute_action(chat_id,"MUTE",uid,f"flood pico={is_peak}",mid,source="AUTO",confidence=0.9)
            if r["success"]: mem_flood[(str(chat_id),str(uid))].clear()
            return
    if cfg.get("anti_mention"):
        mentions=len(re.findall(r"@\w+",text)); mw=mem_mention[(str(chat_id),str(uid))]; mw.append((time.time(),mentions))
        now=time.time()
        while mw and now-mw[0][0]>30: mw.popleft()
        total=sum(m for _,m in mw)
        if mentions>=5 or total>=8:
            execute_action(chat_id,"DELETE",uid,f"mention {mentions}",mid,source="AUTO",confidence=0.9); return

restore_safe()
if __name__=="__main__":
    app.run(host="0.0.0.0", port=PORT)
