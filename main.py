# ORBIT ALLIANCE V17.3 FINAL FIX - FUSÃO V16+V17.2 + RESTORE | Kʆɛɓɛʀ
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
ORBIT_CORE = f"Orbit Alliance V17.3 FINAL FIX by {KLEBER_SIG}"
KLEBER_CHECK = hashlib.sha256(KLEBER_SIG.encode()).hexdigest()[:12]

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
mem_context = defaultdict(lambda: deque(maxlen=10))
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
        else: provs[name] = {"key": key, "endpoint": cfg["endpoint"], "format": cfg["format"], "timeout": cfg["timeout"]}
    return provs
PROVIDERS = build_providers_dynamic()
ORDER_PREFERENCE = ["cloudflare","groq","gemini","cerebras"]

DIVULGA_WORDS = {"entra","ganhe","lucro","renda","grátis","gratis","promoção","promocao","vagas","dinheiro","pix","aposte","cassino","tigrinho","sorteio","grupo novo","link na bio"}
TOXIC_WORDS = {"lixo","burro","otario","otário","idiota","fdp","vsf","arrombado","desgraça","corno","vagabundo","vai se foder"}
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
    hs = ai_sensual_score(text); ht = ai_toxic_score(text); hd = ai_divulgacao_score(text); hsp = ai_spam_score(list(recent_texts))
    if not PROVIDERS: return {"toxic":ht,"divulg":hd,"sensual":hs,"spam":hsp}
    prompt = f"""Você é moderador BR. Retorne SOMENTE JSON {{"toxic":0-1,"divulg":0-1,"sensual":0-1,"spam":0-1}}. sensual=conteudo erotico. Msg:"{text[:300]}" JSON:"""
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
                        j["sensual"]=max(float(j.get("sensual",0)),hs); j["toxic"]=max(float(j.get("toxic",0)),ht); j["divulg"]=max(float(j.get("divulg",0)),hd); j["spam"]=max(float(j.get("spam",0)),hsp)
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
                            j["sensual"]=max(float(j.get("sensual",0)),hs); j["toxic"]=max(float(j.get("toxic",0)),ht); j["divulg"]=max(float(j.get("divulg",0)),hd); j["spam"]=max(float(j.get("spam",0)),hsp)
                            return j
        except: continue
    return {"toxic":ht,"divulg":hd,"sensual":hs,"spam":hsp}

def get_db():
    c=sqlite3.connect(DATABASE_PATH, check_same_thread=False, timeout=10)
    c.row_factory=sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL;")
    return c

# === FIX V17.3 - RESTORE + PROTECAO ===
def restore_safe():
    if not JSONBIN_URL:
        print("SEM JSONBIN")
        return False
    try:
        # Se banco local já existe e é grande, não restaura
        if os.path.exists(DATABASE_PATH) and os.path.getsize(DATABASE_PATH) > 5000:
            try:
                c=sqlite3.connect(DATABASE_PATH); chk=c.execute("PRAGMA quick_check").fetchone(); c.close()
                if chk and "ok" in str(chk[0]).lower():
                    print("Banco local OK, não restaurando")
                    return False
            except: pass
        print(f"[{KLEBER_SIG}] TENTANDO RESTORE {JSONBIN_ID}...")
        r=requests.get(f"{JSONBIN_URL}/latest", headers=JB_HEADERS, timeout=15)
        if r.status_code!=200:
            print(f"Restore status {r.status_code}")
            return False
        rec=r.json().get("record",{})
        b64=rec.get("db") or rec.get("db_base64") or rec.get("db_b64") or rec.get("b64")
        if not b64 or len(b64)<5000:
            print(f"[{KLEBER_SIG}] Backup vazio {len(b64) if b64 else 0}, ignorando")
            return False
        tmp=DATABASE_PATH+".restore"
        with open(tmp,"wb") as f: f.write(base64.b64decode(b64))
        shutil.move(tmp, DATABASE_PATH)
        print(f"[{KLEBER_SIG}] ✅ RESTORE OK {len(b64)} chars - REGRAS RECUPERADAS")
        return True
    except Exception as e:
        print(f"RESTORE FALHOU {e}")
        return False

def init_db():
    # TENTA RESTAURAR ANTES DE CRIAR
    if not os.path.exists(DATABASE_PATH) or os.path.getsize(DATABASE_PATH) < 100:
        restore_safe()
    c=get_db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS groups(chat_id TEXT PRIMARY KEY,title TEXT,type TEXT,enabled INTEGER DEFAULT 1,updated_at TEXT);
    CREATE TABLE IF NOT EXISTS group_rules(chat_id TEXT PRIMARY KEY,welcome INTEGER DEFAULT 1,goodbye INTEGER DEFAULT 1,welcome_msg TEXT DEFAULT 'Bem-vindo {name}! 🚀',goodbye_msg TEXT DEFAULT '{name} saiu.',anti_link INTEGER DEFAULT 0,anti_spam INTEGER DEFAULT 1,anti_flood INTEGER DEFAULT 1,anti_divulgation INTEGER DEFAULT 1,anti_sensual INTEGER DEFAULT 1,sensual_mode TEXT DEFAULT 'moderate',allowed_links TEXT DEFAULT 'youtube.com,youtu.be,instagram.com,github.com,cloudflare.com',warning_limit INTEGER DEFAULT 3,moderation_mode TEXT DEFAULT 'moderate',flood_limit INTEGER DEFAULT 7,flood_window INTEGER DEFAULT 15,auto_actions TEXT DEFAULT '["DELETE","WARN","MUTE"]',mute_duration INTEGER DEFAULT 600,updated_at TEXT);
    CREATE TABLE IF NOT EXISTS warnings(chat_id TEXT,user_id TEXT,count INTEGER DEFAULT 0,last_reason TEXT,updated_at TEXT,PRIMARY KEY(chat_id,user_id));
    CREATE TABLE IF NOT EXISTS moderation_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id TEXT,user_id TEXT,action TEXT,reason TEXT,message_id INTEGER,source TEXT,success INTEGER,created_at TEXT);
    CREATE TABLE IF NOT EXISTS group_peak(chat_id TEXT, hour INTEGER, count INTEGER DEFAULT 0, PRIMARY KEY(chat_id,hour));
    CREATE TABLE IF NOT EXISTS user_reputation(chat_id TEXT, user_id TEXT, spam_score INTEGER DEFAULT 0, last_seen TEXT, PRIMARY KEY(chat_id,user_id));
    CREATE TABLE IF NOT EXISTS group_toxic_words(chat_id TEXT, word TEXT, count INTEGER DEFAULT 1, PRIMARY KEY(chat_id,word));
    """)
    c.commit(); c.close()
init_db()

def log_error(comp, err): logging.error(f"[{KLEBER_SIG}] {comp}: {err}")
def telegram_req(method,payload=None):
    url=f"{TELEGRAM_API_URL}/{method}"
    try:
        r=requests.post(url,json=payload,timeout=12) if payload else requests.get(url,timeout=12)
        try: return r.json()
        except: return {"ok":False}
    except Exception as e:
        log_error(f"telegram_req {method}", e); return {"ok":False}
def init_bot():
    global BOT_ID, BOT_USERNAME
    d=telegram_req("getMe")
    if d.get("ok"): BOT_ID=d["result"]["id"]; BOT_USERNAME=d["result"].get("username",""); print(f"[{KLEBER_SIG}] {ORBIT_CORE} BOT @{BOT_USERNAME}")
init_bot()
def send(chat_id, txt, reply=None, parse="Markdown"):
    p={"chat_id":chat_id,"text":str(txt)[:3900],"parse_mode":parse}
    if reply: p["reply_to_message_id"]=reply
    return telegram_req("sendMessage",p)
def get_cfg(chat_id):
    c=get_db(); r=c.execute("SELECT * FROM group_rules WHERE chat_id=?",(str(chat_id),)).fetchone(); c.close()
    if not r:
        with db_lock: c=get_db(); c.execute("INSERT OR IGNORE INTO group_rules(chat_id,updated_at) VALUES(?,?)",(str(chat_id),datetime.now(timezone.utc).isoformat())); c.commit(); c.close()
        return get_cfg(chat_id)
    d=dict(r)
    try: d["auto_actions"]=json.loads(d.get("auto_actions","[]"))
    except: d["auto_actions"]=["DELETE","WARN","MUTE"]
    return d
def set_cfg(chat_id, key, val):
    with db_lock: c=get_db(); c.execute(f"UPDATE group_rules SET {key}=?, updated_at=? WHERE chat_id=?",(val,datetime.now(timezone.utc).isoformat(),str(chat_id))); c.commit(); c.close()
    global backup_pending; backup_pending=True; return True
def is_admin(chat_id,uid):
    if str(uid)==CREATOR_ID: return True
    m=telegram_req("getChatMember",{"chat_id":chat_id,"user_id":uid})
    return m.get("ok") and m.get("result",{}).get("status") in ("administrator","creator")
def is_protected(chat_id,uid):
    if str(uid)==str(BOT_ID) or str(uid)==CREATOR_ID: return True
    return is_admin(chat_id,uid)
def execute_action(chat_id,action,target_id=None,reason="",message_id=None,source="AUTO",confidence=0):
    if target_id and is_protected(chat_id,target_id): return {"success":False}
    res={"ok":False}
    try:
        if action=="DELETE" and message_id: res=telegram_req("deleteMessage",{"chat_id":chat_id,"message_id":message_id})
        elif action=="WARN" and target_id:
            with db_lock:
                c=get_db(); w=c.execute("SELECT count FROM warnings WHERE chat_id=? AND user_id=?",(str(chat_id),str(target_id))).fetchone()
                cnt=(w["count"] if w else 0)+1
                c.execute("INSERT OR REPLACE INTO warnings(chat_id,user_id,count,last_reason,updated_at) VALUES(?,?,?,?,?)",(str(chat_id),str(target_id),cnt,reason,datetime.now(timezone.utc).isoformat())); c.commit(); c.close()
            res={"ok":True}
        elif action=="MUTE" and target_id:
            cfg=get_cfg(chat_id); res=telegram_req("restrictChatMember",{"chat_id":chat_id,"user_id":target_id,"permissions":{"can_send_messages":False},"until_date":int(time.time())+cfg.get("mute_duration",600)})
        elif action=="BAN" and target_id: res=telegram_req("banChatMember",{"chat_id":chat_id,"user_id":target_id})
        elif action=="KICK" and target_id:
            telegram_req("banChatMember",{"chat_id":chat_id,"user_id":target_id}); res=telegram_req("unbanChatMember",{"chat_id":chat_id,"user_id":target_id})
        elif action=="UNMUTE" and target_id: res=telegram_req("restrictChatMember",{"chat_id":chat_id,"user_id":target_id,"permissions":{"can_send_messages":True,"can_send_media_messages":True,"can_send_other_messages":True}})
        if target_id and action in ("DELETE","MUTE","BAN"):
            try:
                with db_lock:
                    c=get_db(); c.execute("INSERT INTO user_reputation(chat_id,user_id,spam_score,last_seen) VALUES(?,?,1,?) ON CONFLICT(chat_id,user_id) DO UPDATE SET spam_score=spam_score+1, last_seen=?",(str(chat_id),str(target_id),datetime.now(timezone.utc).isoformat(),datetime.now(timezone.utc).isoformat())); c.commit(); c.close()
            except: pass
    except Exception as e: log_error("execute_action",e); res={"ok":False}
    try:
        with db_lock: c=get_db(); c.execute("INSERT INTO moderation_logs(chat_id,user_id,action,reason,message_id,source,success,created_at) VALUES(?,?,?,?,?,?,?,?)",(str(chat_id),str(target_id or ""),action,f"{reason} [{KLEBER_SIG}]",message_id or 0,source,1 if res.get("ok") else 0,datetime.now(timezone.utc).isoformat())); c.commit(); c.close()
    except: pass
    return {"success":bool(res.get("ok"))}

def clean_cmd(chat_id, mid):
    try: telegram_req("deleteMessage",{"chat_id":chat_id,"message_id":mid})
    except: pass

def learn_peak_and_check(chat_id):
    try:
        hour = datetime.now(TZ).hour
        with db_lock:
            c=get_db(); c.execute("INSERT INTO group_peak(chat_id,hour,count) VALUES(?,?,1) ON CONFLICT(chat_id,hour) DO UPDATE SET count=count+1",(str(chat_id),hour)); rows=c.execute("SELECT count FROM group_peak WHERE chat_id=?",(str(chat_id),)).fetchall(); c.commit(); c.close()
        if len(rows) < 5: return False
        counts=[r["count"] for r in rows]; avg=sum(counts)/len(counts)
        c=get_db(); r=c.execute("SELECT count FROM group_peak WHERE chat_id=? AND hour=?",(str(chat_id),hour)).fetchone(); c.close()
        cur = r["count"] if r else 0
        return cur > avg*1.5 and cur > 10
    except: return False

def is_spammer_recurrente(chat_id, user_id):
    try: c=get_db(); row=c.execute("SELECT spam_score FROM user_reputation WHERE chat_id=? AND user_id=?",(str(chat_id),str(user_id))).fetchone(); c.close(); return row and row["spam_score"] >= 3
    except: return False

def check_group_toxic_word(chat_id, text):
    try:
        tl=text.lower(); c=get_db(); rows=c.execute("SELECT word,count FROM group_toxic_words WHERE chat_id=? AND count>=3 ORDER BY count DESC LIMIT 20",(str(chat_id),)).fetchall(); c.close()
        for r in rows:
            if r["word"] in tl: return r["word"], r["count"]
        return None,0
    except: return None,0

def learn_toxic_words_from_fight(chat_id, texts):
    try:
        words=[]
        for t in texts: words+=re.findall(r"\b\w{4,}\b", t.lower())
        common=[w for w,c in Counter(words).items() if c>=2 and w not in TOXIC_WORDS and len(w)>3][:5]
        if not common: return
        with db_lock:
            c=get_db()
            for w in common: c.execute("INSERT INTO group_toxic_words(chat_id,word,count) VALUES(?,?,1) ON CONFLICT(chat_id,word) DO UPDATE SET count=count+1",(str(chat_id),w))
            c.commit(); c.close()
    except: pass

def schedule_backup():
    global backup_pending, last_backup
    if not JSONBIN_URL or not backup_pending or time.time()-last_backup < 300: return
    with backup_lock:
        try:
            if not os.path.exists(DATABASE_PATH): return
            sz=os.path.getsize(DATABASE_PATH)
            if sz < 5000:
                print(f"[{KLEBER_SIG}] ⚠️ Banco pequeno {sz} bytes, NÃO enviando pra não apagar backup bom")
                return
            with open(DATABASE_PATH,"rb") as f: b64=base64.b64encode(f.read()).decode()
            payload={"db":b64,"db_base64":b64,"db_b64":b64,"updated":datetime.now(timezone.utc).isoformat(),"by":KLEBER_SIG,"check":KLEBER_CHECK}
            requests.put(JSONBIN_URL, json=payload, headers=JB_HEADERS, timeout=15); last_backup=time.time(); backup_pending=False
            print(f"[{KLEBER_SIG}] Backup OK {sz} bytes")
        except Exception as e: log_error("backup",e)
def backup_worker():
    while True: time.sleep(60); schedule_backup()
threading.Thread(target=backup_worker, daemon=True).start()

@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    if WEBHOOK_SECRET and request.headers.get("X-Telegram-Bot-Api-Secret-Token")!=WEBHOOK_SECRET: abort(403)
    data=request.get_json(force=True); executor.submit(process_update, data); return {"ok":True},200

@app.route("/", methods=["GET"])
def health():
    return {"status":f"Orbit V17.3 by {KLEBER_SIG}","core":ORBIT_CORE,"bot_id":BOT_ID,"providers":list(PROVIDERS.keys())}

def process_update(update):
    msg = update.get("message") or update.get("edited_message")
    if not msg: return
    chat_id = msg["chat"]["id"]; uid = msg["from"]["id"]
    text = (msg.get("text","") or msg.get("caption","")).strip(); mid = msg["message_id"]
    cfg = get_cfg(chat_id)

    if "new_chat_members" in msg:
        if cfg.get("welcome"):
            for u in msg["new_chat_members"]:
                if str(u.get("id")) == str(BOT_ID): continue
                nome = u.get("first_name","")
                txt_w = cfg.get("welcome_msg","Bem-vindo {name}! 🚀")
                try: txt_w = txt_w.format(name=nome)
                except: pass
                send(chat_id, txt_w)
        return
    if "left_chat_member" in msg:
        if cfg.get("goodbye"):
            nome = msg["left_chat_member"].get("first_name","Alguém")
            txt_b = cfg.get("goodbye_msg","{name} saiu.")
            try: txt_b = txt_b.format(name=nome)
            except: pass
            send(chat_id, txt_b)
        return

    if msg["chat"]["type"]=="private":
        if text.startswith("/"): send(chat_id,f"🚀 *Orbit V17.3 by {KLEBER_SIG}*\nUse /painel no grupo."); clean_cmd(chat_id,mid)
        return

    if text.startswith("/"):
        parts=text.strip().split(); cmd=parts[0].lower().split("@")[0]; args=parts[1:]
        if cmd in ["/start","/help","/painel","/status"]:
            if cmd in ["/painel","/help"]:
                is_peak = learn_peak_and_check(chat_id)
                c=get_db()
                try:
                    peak=c.execute("SELECT hour,count FROM group_peak WHERE chat_id=? ORDER BY count DESC LIMIT 1",(str(chat_id),)).fetchone()
                    rep=c.execute("SELECT COUNT(*) as c FROM user_reputation WHERE chat_id=? AND spam_score>=3",(str(chat_id),)).fetchone()
                    tox=c.execute("SELECT word,count FROM group_toxic_words WHERE chat_id=? ORDER BY count DESC LIMIT 3",(str(chat_id),)).fetchall()
                except: peak=None; rep=None; tox=[]
                c.close()
                peak_txt=f"{peak['hour']}h ({peak['count']})" if peak else "aprendendo"
                rep_txt=rep["c"] if rep else 0
                tox_txt=", ".join([f"{r['word']}({r['count']})" for r in tox]) if tox else "nenhuma"
                txt=f"""⚙️ *PAINEL ORBIT V17.3 - By {KLEBER_SIG}*
*IA:* `{', '.join(PROVIDERS.keys())}` | *+18:* `{cfg.get('sensual_mode')}`
*Flood:* {cfg.get('flood_limit')}/{cfg.get('flood_window')}s | *Pico:* {peak_txt} {'🔥' if is_peak else ''}

*— APRENDIZADO V16 —*
Observação: {rep_txt} users
Toxicas aprendidas: {tox_txt}

*— +18 —*
`/setsensual livre/moderate/restrito`

*— ADM —*
`/ban /kick /mute /unmute /delete /warn /unwarn /warnings /pin /allowlink /logs`

_Salva tudo ✅ By {KLEBER_SIG}_
"""
                send(chat_id,txt,mid)
            elif cmd=="/status":
                cf = "✅ ON" if "cloudflare" in PROVIDERS else "❌ OFF"
                send(chat_id,f"🤖 *Orbit V17.3 By {KLEBER_SIG}*\nCloudflare: {cf}\nProviders: {list(PROVIDERS.keys())}\n+18: {cfg.get('sensual_mode')}\nLearning: ON\nBackup: {'ON' if JSONBIN_URL else 'OFF'}",mid)
            clean_cmd(chat_id,mid); return
        if not is_admin(chat_id,uid):
            send(chat_id,"⚠️ Só ADM."); clean_cmd(chat_id,mid); return
        if cmd=="/ban":
            tgt=msg.get("reply_to_message",{}).get("from",{}).get("id")
            r=execute_action(chat_id,"BAN",tgt,"ban ADM"); send(chat_id,"🚫 Banido" if r["success"] else "❌",mid)
        elif cmd=="/unban":
            if args:
                try: telegram_req("unbanChatMember",{"chat_id":chat_id,"user_id":int(args[0])}); send(chat_id,f"✅ Desbanido {args[0]}",mid)
                except: send(chat_id,"❌ Erro",mid)
        elif cmd=="/kick":
            tgt=msg.get("reply_to_message",{}).get("from",{}).get("id")
            r=execute_action(chat_id,"KICK",tgt,"kick"); send(chat_id,"👢 Kickado" if r["success"] else "❌",mid)
        elif cmd=="/mute":
            tgt=msg.get("reply_to_message",{}).get("from",{}).get("id")
            r=execute_action(chat_id,"MUTE",tgt,"mute"); send(chat_id,"🔇 Mutado" if r["success"] else "❌",mid)
        elif cmd=="/unmute":
            tgt=msg.get("reply_to_message",{}).get("from",{}).get("id")
            r=execute_action(chat_id,"UNMUTE",tgt,"unmute"); send(chat_id,"🔊 Desmutado" if r["success"] else "❌",mid)
        elif cmd=="/delete":
            tgt_mid=msg.get("reply_to_message",{}).get("message_id")
            if tgt_mid: telegram_req("deleteMessage",{"chat_id":chat_id,"message_id":tgt_mid}); send(chat_id,"🗑️ Apagada",mid)
        elif cmd=="/warn":
            tgt=msg.get("reply_to_message",{}).get("from",{}).get("id")
            if tgt:
                execute_action(chat_id,"WARN",tgt,"warn ADM"); c=get_db(); w=c.execute("SELECT count FROM warnings WHERE chat_id=? AND user_id=?",(str(chat_id),str(tgt))).fetchone(); c.close(); cnt=w["count"] if w else 1
                send(chat_id,f"⚠️ Warn {cnt}/{cfg.get('warning_limit',3)}",mid)
                if cnt>=cfg.get("warning_limit",3): execute_action(chat_id,"BAN",tgt,"3 warns"); send(chat_id,"🚫 Ban 3 warns",mid)
        elif cmd=="/unwarn":
            tgt=msg.get("reply_to_message",{}).get("from",{}).get("id")
            if tgt:
                with db_lock: c=get_db(); c.execute("DELETE FROM warnings WHERE chat_id=? AND user_id=?",(str(chat_id),str(tgt))); c.commit(); c.close()
                send(chat_id,"✅ Zerado",mid)
        elif cmd=="/warnings":
            tgt=msg.get("reply_to_message",{}).get("from",{}).get("id") or uid
            c=get_db(); w=c.execute("SELECT count FROM warnings WHERE chat_id=? AND user_id=?",(str(chat_id),str(tgt))).fetchone(); c.close()
            send(chat_id,f"Warns: {w['count'] if w else 0}",mid)
        elif cmd=="/resetwarnings":
            with db_lock: c=get_db(); c.execute("DELETE FROM warnings WHERE chat_id=?",(str(chat_id),)); c.commit(); c.close()
            send(chat_id,"✅ Todos warns reset",mid)
        elif cmd=="/pin":
            tgt_mid=msg.get("reply_to_message",{}).get("message_id")
            if tgt_mid: telegram_req("pinChatMessage",{"chat_id":chat_id,"message_id":tgt_mid}); send(chat_id,"📌 Fixada",mid)
        elif cmd=="/unpin":
            telegram_req("unpinAllChatMessages",{"chat_id":chat_id}); send(chat_id,"📌 Desfixado",mid)
        elif cmd=="/allowlink":
            if args:
                cur=cfg.get("allowed_links",""); new=cur+","+args[0] if cur else args[0]
                set_cfg(chat_id,"allowed_links",new); send(chat_id,f"✅ Liberado: {args[0]}",mid)
        elif cmd=="/logs":
            c=get_db(); rows=c.execute("SELECT action,user_id,reason FROM moderation_logs WHERE chat_id=? ORDER BY id DESC LIMIT 10",(str(chat_id),)).fetchall(); c.close()
            txt="📜 *Logs:*\n"+"\n".join([f"{r['action']} {r['user_id']} {r['reason'][:30]}" for r in rows]) if rows else "Sem logs"
            send(chat_id,txt,mid)
        elif cmd=="/resetai": send(chat_id,f"✅ IAs reset by {KLEBER_SIG}",mid)
        elif cmd=="/setsensual":
            if args:
                mode=args[0].lower()
                if mode=="moderado": mode="moderate"
                set_cfg(chat_id,"sensual_mode",mode); set_cfg(chat_id,"anti_sensual",0 if mode=="livre" else 1)
                send(chat_id,f"✅ +18 = {mode}",mid)
        clean_cmd(chat_id,mid); return

    if uid==BOT_ID or is_admin(chat_id,uid): return
    if not text: return

    is_peak = learn_peak_and_check(chat_id)
    if is_spammer_recurrente(chat_id, uid):
        execute_action(chat_id,"MUTE",uid,"spammer recorrente",mid,source="AUTO+REP"); send(chat_id,f"🚨 @{msg['from'].get('first_name','')} em observação",mid); return
    word,cnt = check_group_toxic_word(chat_id, text)
    if word:
        execute_action(chat_id,"DELETE",uid,f"palavra toxica aprendida [{word}]",mid,source="AUTO+LEARN"); return

    mem_texts[(str(chat_id),str(uid))].append(text)
    ai_res = call_moderation_ai(text, mem_texts[(str(chat_id),str(uid))])

    sensual = float(ai_res.get("sensual",0))
    if sensual >= 0.65 and cfg.get("anti_sensual"):
        mode = cfg.get("sensual_mode","moderate")
        if mode=="restrito":
            execute_action(chat_id,"DELETE",None,f"+18 restrito {sensual:.2f}",mid,source="AUTO+SENSUAL",confidence=sensual); return
        elif mode=="moderate":
            if sensual >= 0.92:
                execute_action(chat_id,"DELETE",None,f"+18 pesado {sensual:.2f}",mid,source="AUTO+SENSUAL",confidence=sensual)
                send(chat_id,f"⚠️ Pesado demais @{msg['from'].get('first_name','')}!",mid); return
            else: return

    if float(ai_res.get("divulg",0)) >= 0.75 and cfg.get("anti_divulgation"):
        allowed = [d.strip().lower() for d in (cfg.get("allowed_links","").split(",")) if d.strip()]
        if not any(d in text.lower() for d in allowed):
            execute_action(chat_id,"DELETE",None,f"divulg {ai_res['divulg']:.2f}",mid,source="AUTO+DIVULG",confidence=float(ai_res.get("divulg",0))); return

    if float(ai_res.get("toxic",0)) >= 0.70:
        execute_action(chat_id,"DELETE",None,f"toxic {ai_res['toxic']:.2f}",mid,source="AUTO+TOXIC",confidence=float(ai_res.get("toxic",0)))
        mem_fight[str(chat_id)].append((str(uid),time.time(),ai_res.get("toxic",0),text))
        recent=[f for f in mem_fight[str(chat_id)] if time.time()-f[1]<30]
        if len(recent)>=2 and len(set(u for u,_,_,_ in recent))>=2 and all(s>=0.7 for _,_,s,_ in recent):
            learn_toxic_words_from_fight(chat_id, [t for _,_,_,t in recent])
            for u,_,_,_ in recent: execute_action(chat_id,"MUTE",u,"briga IA",None,source="AUTO+FIGHT")
            send(chat_id,"🤖 Briga detectada! Calma galera - mute 10min. Aprendi palavras dessa briga.")
            mem_fight[str(chat_id)].clear()
        elif float(ai_res.get("toxic",0))>=0.90:
            execute_action(chat_id,"MUTE",uid,"toxico grave",mid,source="AUTO+TOXIC")
        return

    if cfg.get("anti_flood"):
        key=(str(chat_id),str(uid)); now=time.time(); dq=mem_flood[key]; dq.append(now)
        while dq and now-dq[0] > cfg.get("flood_window",15): dq.popleft()
        limit = cfg.get("flood_limit",7)
        if is_peak: limit = max(3, limit-3)
        if len(dq) > limit:
            execute_action(chat_id,"MUTE",uid,f"flood pico={is_peak}",mid,source="AUTO+FLOOD"); dq.clear()
            send(chat_id,f"🔇 @{msg['from'].get('first_name','')} floodou",mid); return

if __name__=="__main__":
    print(f"[{KLEBER_SIG}] START {ORBIT_CORE} CHECK:{KLEBER_CHECK}")
    app.run(host="0.0.0.0", port=PORT)
