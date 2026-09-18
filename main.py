# ORBIT ALLIANCE V17.6.3 FINAL - BY Kʆɛɓɛʀ - FIX PV + FILTRO -100%
import os, re, json, time, sqlite3, logging, requests, base64, shutil, threading
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
ORBIT_CORE = f"Orbit V17.6 PV by {KLEBER_SIG}"
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
    try:
        c=get_db()
        r=c.execute("SELECT * FROM group_rules WHERE chat_id=?",(str(chat_id),)).fetchone()
        c.close()
        if r: return dict(r)
    except: pass
    with db_lock:
        try:
            c=get_db()
            c.execute("INSERT OR IGNORE INTO group_rules(chat_id,updated_at) VALUES(?,?)",(str(chat_id),datetime.now(timezone.utc).isoformat()))
            c.commit()
            r=c.execute("SELECT * FROM group_rules WHERE chat_id=?",(str(chat_id),)).fetchone()
            c.close()
            if r: return dict(r)
        except: pass
    return {"chat_id":str(chat_id),"title":str(chat_id),"welcome":1,"goodbye":1,"welcome_msg":"Bem-vindo {name}!","goodbye_msg":"{name} saiu.","anti_link":0,"anti_spam":1,"anti_flood":1,"anti_divulgation":1,"anti_sensual":1,"sensual_mode":"moderate","allowed_links":"youtube.com","warning_limit":3,"flood_limit":7,"flood_window":15,"mute_duration":600,"lock_group":0}

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
        if now - ts < 300: return val
    if str(uid)==CREATOR_ID:
        admin_cache[key]=(True, now)
        return True
    try:
        r=requests.post(f"{TELEGRAM_API_URL}/getChatMember", json={"chat_id":chat_id,"user_id":uid}, timeout=5)
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
            for t in ["group_rules","custom_rules","pending_rules","user_rule_hits","warnings","moderation_logs"]:
                c.execute(f"DELETE FROM {t} WHERE chat_id=?",(str(chat_id),))
            c.commit()
            c.close()
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
        if any(k in low for k in ("proibido","proibida","banido","banida","vetado","vetada","nao pode","não pode","proibir","sem link","sem porno","sem politica","sem divulgação","sem divulgacao")) or low.startswith("proibido"):
            rules.append(l[:200])
    return rules[:20]

def get_keywords(rule_text):
    words=re.findall(r"\w{4,}",rule_text.lower())
    kws=[w for w in words if w not in STOP_RULE]
    return ",".join(kws[:8]) if kws else rule_text.lower()[:20]

def check_custom_rules(text,chat_id):
    try:
        c=get_db()
        rows=c.execute("SELECT id,rule_text,keywords FROM custom_rules WHERE chat_id=?",(str(chat_id),)).fetchall()
        c.close()
    except: return None
    tl=text.lower()
    for r in rows:
        kws=(r["keywords"] or "").split(",")
        for k in kws:
            k=k.strip()
            if len(k)>=4 and k in tl:
                return dict(r)
    return None

def handle_custom_violation(chat_id,user_id,rule,message_id):
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
    if cnt==1: send(chat_id,f"⚠️ *Regra:* {rule['rule_text']}\n1ª vez aviso",message_id)
    elif cnt==2: send(chat_id,f"⚠️ WARN por: {rule['rule_text']}",message_id)
    else:
        cfg=get_cfg(chat_id)
        telegram_req("restrictChatMember",{"chat_id":chat_id,"user_id":user_id,"permissions":{"can_send_messages":False},"until_date":int(time.time())+cfg.get("mute_duration",600)})
        send(chat_id,f"🔇 Mute 10min: {rule['rule_text']}",message_id)

def execute_action(chat_id,action,target_id=None,message_id=None,reason=""):
    if target_id and is_protected(chat_id,target_id): return {"success":False}
    res={"ok":False}
    try:
        if action=="DELETE" and message_id: res=telegram_req("deleteMessage",{"chat_id":chat_id,"message_id":message_id})
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

def backup_worker():
    global backup_pending,last_backup
    while True:
        time.sleep(60)
        try:
            with db_lock:
                c=get_db()
                c.execute("DELETE FROM pending_rules WHERE datetime(created_at) < datetime('now','-10 minutes')")
                c.commit()
                c.close()
        except: pass
        if not JSONBIN_URL or not backup_pending or time.time()-last_backup<300: continue
        with backup_lock:
            try:
                sz=os.path.getsize(DATABASE_PATH)
                if sz<5000: continue
                with open(DATABASE_PATH,"rb") as f: b64=base64.b64encode(f.read()).decode()
                requests.put(JSONBIN_URL,json={"db":b64,"updated":datetime.now(timezone.utc).isoformat(),"by":KLEBER_SIG},headers=JB_HEADERS,timeout=15)
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
    try:
        c=get_db()
        all_groups=c.execute("SELECT chat_id,title FROM group_rules WHERE chat_id LIKE '-100%' ORDER BY rowid DESC LIMIT 30").fetchall()
        c.close()
    except Exception as e:
        print(f"get_admin_groups DB erro: {e}")
        return []
    admin_groups=[]
    for g in all_groups:
        try:
            cid=str(g["chat_id"])
            if not cid.startswith("-100"): continue
            if is_admin(cid, user_id):
                admin_groups.append({"chat_id":cid,"title":g["title"] or cid})
                if len(admin_groups)>=10: break
        except: continue
    return admin_groups

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
        if cmd=="delregra":
            try:
                rid=int(args_text.strip())
                with db_lock:
                    c=get_db()
                    c.execute("DELETE FROM custom_rules WHERE chat_id=? AND id=?",(target_chat,rid))
                    c.commit()
                    c.close()
                backup_pending=True
                send(chat_id,f"✅ Regra {rid} apagada")
            except: send(chat_id,"Use: /delregra_-100xxx 2")
            return
        if cmd=="painel":
            cfg=get_cfg(target_chat)
            send(chat_id,f"⚙️ *Painel {target_chat}*\nLock: {'ON' if cfg.get('lock_group') else 'OFF'}")
            return
    if low in ["/start","/meusgrupos","/grupos","/painel"]:
        send(chat_id,"🔍 Buscando... já te respondo em 2s")
        def do_search():
            try:
                groups=get_admin_groups_for_user(uid)
                if not groups:
                    send(chat_id,"📭 Você não é ADM em nenhum grupo onde eu estou.\n1- Me adicione no grupo\n2- Me promova a ADM\n3- Mande /start aqui de novo")
                    return
                txt=f"🤖 *Seus grupos ({len(groups)})*\n\n"
                for g in groups:
                    cid=g["chat_id"]
                    txt+=f"📌 *{g['title']}*\n`{cid}`\n/regras_{cid} - ver regras\n/painel_{cid}\n\n"
                send(chat_id,txt)
            except Exception as e:
                import traceback
                print(f"do_search erro: {e}\n{traceback.format_exc()}")
                send(chat_id,f"Erro /start: {e}")
        threading.Thread(target=do_search, daemon=True).start()
        return
    if len(text)>=5 and not text.startswith("/"):
        rules=parse_rules_from_text(text)
        if rules:
            groups=get_admin_groups_for_user(uid)
            if not groups:
                send(chat_id,"📭 Sem grupos. Me coloque como ADM primeiro.")
                return
            txt=f"🤖 Detectei {len(rules)} regra(s):\n" + "\n".join([f"• {r}" for r in rules]) + f"\n\nEm qual salvar?\n"
            for g in groups[:10]:
                txt+=f"/salvar_{g['chat_id']} - {g['title']}\n"
            with db_lock:
                c=get_db()
                c.execute("INSERT OR REPLACE INTO pending_rules(chat_id,user_id,rules_json,created_at) VALUES(?,?,?,?)",(f"PV_{uid}",str(uid),json.dumps({"rules":rules,"groups":[g['chat_id'] for g in groups]}),datetime.now(timezone.utc).isoformat()))
                c.commit()
                c.close()
            send(chat_id,txt)
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
    send(chat_id,"📚 *PV CONFIG*\n/start - meus grupos\n/regras_-100xxx\n/painel_-100xxx")

def process_update(update):
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
        if cfg.get("welcome"):
            for u in msg["new_chat_members"]:
                if str(u.get("id"))==str(BOT_ID): continue
                send(chat_id,cfg.get("welcome_msg","Bem-vindo {name}!").format(name=u.get("first_name","")))
        return
    if "left_chat_member" in msg:
        if str(msg["left_chat_member"].get("id"))==str(BOT_ID):
            threading.Thread(target=wipe_group_data,args=(chat_id,),daemon=True).start()
            return
        return
    if text.startswith("/"):
        parts=text.strip().split()
        cmd=parts[0].lower().split("@")[0]
        if cmd in ["/regras","/listregras"]:
            c=get_db()
            rows=c.execute("SELECT id,rule_text FROM custom_rules WHERE chat_id=? ORDER BY id",(str(chat_id),)).fetchall()
            c.close()
            if not rows: txt="📭 Nenhuma regra. /comoadd"
            else: txt=f"📜 *Regras ({len(rows)}/20):*\n" + "\n".join([f"{r['id']}. {r['rule_text']}" for r in rows])
            send(chat_id,txt,mid)
            return
        if cmd in ["/painel","/start","/help","/status"]:
            c=get_db()
            cr=c.execute("SELECT COUNT(*) as c FROM custom_rules WHERE chat_id=?",(str(chat_id),)).fetchone()["c"]
            c.close()
            send(chat_id,f"⚙️ *PAINEL V17.6 PV By {KLEBER_SIG}*\nRegras: {cr}/20 | Lock: {'ON' if cfg.get('lock_group') else 'OFF'}\n/regras /painel",mid)
            return
        if not is_admin(chat_id,uid):
            return
        if cmd=="/ban":
            tgt=msg.get("reply_to_message",{}).get("from",{}).get("id")
            if tgt and not is_protected(chat_id,tgt): execute_action(chat_id,"BAN",tgt,None,"ban")
        elif cmd=="/kick":
            tgt=msg.get("reply_to_message",{}).get("from",{}).get("id")
            if tgt and not is_protected(chat_id,tgt): execute_action(chat_id,"KICK",tgt,None,"kick")
        elif cmd=="/mute":
            tgt=msg.get("reply_to_message",{}).get("from",{}).get("id")
            if tgt and not is_protected(chat_id,tgt): execute_action(chat_id,"MUTE",tgt,None,"mute")
        elif cmd=="/unmute":
            tgt=msg.get("reply_to_message",{}).get("from",{}).get("id")
            if tgt: execute_action(chat_id,"UNMUTE",tgt,None,"unmute")
        try: telegram_req("deleteMessage",{"chat_id":chat_id,"message_id":mid})
        except: pass
        return
    if is_admin(chat_id,uid):
        rules=parse_rules_from_text(text)
        if rules:
            with db_lock:
                c=get_db()
                c.execute("INSERT OR REPLACE INTO pending_rules(chat_id,user_id,rules_json,created_at) VALUES(?,?,?,?)",(str(chat_id),str(uid),json.dumps(rules),datetime.now(timezone.utc).isoformat()))
                c.commit()
                c.close()
            send(chat_id,f"🤖 Detectei: {rules[0]}\nSalvar? SIM/NAO",mid)
            return
        low=text.lower().strip()
        if low in ("sim","s","yes"):
            c=get_db()
            pend=c.execute("SELECT rules_json FROM pending_rules WHERE chat_id=? AND user_id=?",(str(chat_id),str(uid))).fetchone()
            c.close()
            if pend:
                rules=json.loads(pend["rules_json"])
                with db_lock:
                    c=get_db()
                    for rtxt in rules:
                        c.execute("INSERT INTO custom_rules(chat_id,rule_text,keywords,created_by,created_at) VALUES(?,?,?,?,?)",(str(chat_id),rtxt,get_keywords(rtxt),str(uid),datetime.now(timezone.utc).isoformat()))
                    c.execute("DELETE FROM pending_rules WHERE chat_id=? AND user_id=?",(str(chat_id),str(uid)))
                    c.commit()
                    c.close()
                send(chat_id,f"✅ {len(rules)} salva(s)!",mid)
            return
        return
    if uid==BOT_ID: return
    if cfg.get("lock_group"):
        execute_action(chat_id,"DELETE",None,mid,"lock")
        return
    if not text: return
    custom=check_custom_rules(text,chat_id)
    if custom:
        handle_custom_violation(chat_id,uid,custom,mid)
        return
    # flood check
    key=(str(chat_id),str(uid))
    now=time.time()
    dq=mem_flood[key]
    dq.append(now)
    while dq and now-dq[0]>cfg.get("flood_window",15): dq.popleft()
    if len(dq)>cfg.get("flood_limit",7):
        execute_action(chat_id,"MUTE",uid,mid,f"flood {len(dq)}")
        dq.clear()
        return
    ai_res=call_moderation_ai(text)
    if float(ai_res.get("sensual",0))>=0.85 and cfg.get("anti_sensual"):
        execute_action(chat_id,"DELETE",None,mid,f"+18 {ai_res['sensual']:.2f}")
        return

if __name__=="__main__":
    app.run(host="0.0.0.0",port=PORT)
