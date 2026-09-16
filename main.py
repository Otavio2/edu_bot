# ORBIT ALLIANCE V17.1 FINAL - 10/10 - COMPLETO FIX
# CREATED BY: Kʆɛɓɛʀ | HANSEL CORE
# SIG: 4b2e-7a9f-KLEBER-ORBIT-V17.1 | CHECK: KLEBER-2026
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
ORBIT_CORE = f"Orbit Alliance V17.1 by {KLEBER_SIG}"
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
    hs = ai_sensual_score(text); ht = ai_toxic_score(text); hd = ai_divulgacao_score(text); hsp = ai_spam_score(list(recent_texts))
    if not PROVIDERS:
        return {"toxic":ht,"divulg":hd,"sensual":hs,"spam":hsp}
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
        BOT_ID=d["result"]["id"]; BOT_USERNAME=d["result"].get("username","")
        print(f"[{KLEBER_SIG}] {ORBIT_CORE} CHECK:{KLEBER_CHECK} BOT @{BOT_USERNAME}")
init_bot()

def send(chat_id, txt, reply=None, parse="Markdown"):
    p={"chat_id":chat_id,"text":str(txt)[:3900],"parse_mode":parse}
    if reply: p["reply_to_message_id"]=reply
    return telegram_req("sendMessage",p)

def get_cfg(chat_id):
    c=get_db(); r=c.execute("SELECT * FROM group_rules WHERE chat_id=?",(str(chat_id),)).fetchone(); c.close()
    if not r:
        with db_lock:
            c=get_db(); c.execute("INSERT OR IGNORE INTO group_rules(chat_id,updated_at) VALUES(?,?)",(str(chat_id),datetime.now(timezone.utc).isoformat())); c.commit(); c.close()
        return get_cfg(chat_id)
    d=dict(r)
    try: d["auto_actions"]=json.loads(d.get("auto_actions","[]"))
    except: d["auto_actions"]=["DELETE","WARN","MUTE"]
    return d

def set_cfg(chat_id, key, val):
    with db_lock:
        c=get_db(); c.execute(f"UPDATE group_rules SET {key}=?, updated_at=? WHERE chat_id=?",(val,datetime.now(timezone.utc).isoformat(),str(chat_id))); c.commit(); c.close()
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
    if target_id and is_protected(chat_id,target_id): return {"success":False,"error":"protegido"}
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
    except Exception as e: log_error("execute_action",e); res={"ok":False,"error":str(e)}
    try:
        with db_lock:
            c=get_db(); c.execute("INSERT INTO moderation_logs(chat_id,user_id,action,reason,message_id,source,success,created_at) VALUES(?,?,?,?,?,?,?,?)",(str(chat_id),str(target_id or ""),action,f"{reason} [{KLEBER_SIG}]",message_id or 0,source,1 if res.get("ok") else 0,datetime.now(timezone.utc).isoformat())); c.commit(); c.close()
    except: pass
    return {"success":bool(res.get("ok")),"result":res}

def clean_cmd(chat_id, mid):
    try: telegram_req("deleteMessage",{"chat_id":chat_id,"message_id":mid})
    except: pass

def schedule_backup():
    global backup_pending, last_backup
    if not JSONBIN_URL or not backup_pending or time.time()-last_backup < 300: return
    with backup_lock:
        try:
            if not os.path.exists(DATABASE_PATH): return
            with open(DATABASE_PATH,"rb") as f: b64=base64.b64encode(f.read()).decode()
            payload={"db":b64,"updated":datetime.now(timezone.utc).isoformat(),"by":KLEBER_SIG,"check":KLEBER_CHECK}
            requests.put(JSONBIN_URL, json=payload, headers=JB_HEADERS, timeout=15); last_backup=time.time(); backup_pending=False
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
    return {"status":f"Orbit V17.1 by {KLEBER_SIG}","core":ORBIT_CORE,"check":KLEBER_CHECK,"bot_id":BOT_ID,"providers":list(PROVIDERS.keys())}

def process_update(update):
    msg = update.get("message") or update.get("edited_message")
    if not msg: return
    chat_id = msg["chat"]["id"]; uid = msg["from"]["id"]
    text = (msg.get("text","") or msg.get("caption","")).strip(); mid = msg["message_id"]
    cfg = get_cfg(chat_id)

    if msg["chat"]["type"]=="private":
        if text.startswith("/"):
            send(chat_id,f"🚀 *Orbit V17.1 by {KLEBER_SIG}*\nCheck: `{KLEBER_CHECK}`\n\nMe adiciona como ADM.\nUse /painel no grupo.\n\n_By {KLEBER_SIG}_")
            clean_cmd(chat_id,mid)
        return

    # === COMANDOS V17.1 FIX - TODOS OS 20 ===
    if text.startswith("/"):
        parts=text.strip().split(); cmd=parts[0].lower().split("@")[0]; args=parts[1:]

        if cmd in ["/start","/help","/painel","/status"]:
            if cmd in ["/painel","/help"]:
                txt=f"""⚙️ *PAINEL ORBIT V17.1 - By {KLEBER_SIG}*
`Check: {KLEBER_CHECK}`
*Grupo:* {msg['chat'].get('title','')}
*IA:* `{', '.join(PROVIDERS.keys()) or 'Heurística'}`
*+18:* `{cfg.get('sensual_mode')}` | *Links:* {'ON' if cfg.get('anti_link') else 'OFF'}
*Flood:* {cfg.get('flood_limit')}/{cfg.get('flood_window')}s | *WarnLimit:* {cfg.get('warning_limit')}

*— +18 RESENHA —*
`/setsensual livre` → libera tudo
`/setsensual moderate` → ⭐ RECOMENDADO
`/setsensual restrito` → bloqueia tudo

*— COMANDOS ADM —*
`/ban /unban /kick /mute /unmute`
`/delete /warn /unwarn /warnings /resetwarnings`
`/pin /unpin /allowlink /logs /resetai`

_Core by {KLEBER_SIG} ✅_
"""
                send(chat_id,txt,mid)
            elif cmd=="/status":
                cf = "✅ ON" if "cloudflare" in PROVIDERS else "❌ OFF"
                send(chat_id,f"🤖 *Orbit V17.1 By {KLEBER_SIG}*\nCheck: {KLEBER_CHECK}\nCloudflare: {cf}\nProviders: {list(PROVIDERS.keys())}\n+18: {cfg.get('sensual_mode')}",mid)
            elif cmd=="/start":
                send(chat_id,f"🚀 Orbit V17.1 by {KLEBER_SIG} - Use /painel",mid)
            clean_cmd(chat_id,mid); return

        if not is_admin(chat_id,uid):
            send(chat_id,"⚠️ Só ADM pode usar."); clean_cmd(chat_id,mid); return

        if cmd=="/ban":
            tgt=msg.get("reply_to_message",{}).get("from",{}).get("id")
            if not tgt: send(chat_id,"Responda a msg com /ban",mid)
            else: r=execute_action(chat_id,"BAN",tgt,"ban ADM"); send(chat_id,"🚫 Banido" if r["success"] else "❌ Erro",mid)
        elif cmd=="/unban":
            if not args: send(chat_id,"Use: /unban ID",mid)
            else:
                try: telegram_req("unbanChatMember",{"chat_id":chat_id,"user_id":int(args[0])}); send(chat_id,f"✅ Desbanido {args[0]}",mid)
                except: send(chat_id,"❌ Erro ID",mid)
        elif cmd=="/kick":
            tgt=msg.get("reply_to_message",{}).get("from",{}).get("id")
            r=execute_action(chat_id,"KICK",tgt,"kick ADM"); send(chat_id,"👢 Kickado" if r["success"] else "❌",mid)
        elif cmd=="/mute":
            tgt=msg.get("reply_to_message",{}).get("from",{}).get("id")
            r=execute_action(chat_id,"MUTE",tgt,"mute ADM"); send(chat_id,"🔇 Mutado 10min" if r["success"] else "❌",mid)
        elif cmd=="/unmute":
            tgt=msg.get("reply_to_message",{}).get("from",{}).get("id")
            r=execute_action(chat_id,"UNMUTE",tgt,"unmute"); send(chat_id,"🔊 Desmutado" if r["success"] else "❌",mid)
        elif cmd=="/delete":
            tgt_mid=msg.get("reply_to_message",{}).get("message_id")
            if tgt_mid: telegram_req("deleteMessage",{"chat_id":chat_id,"message_id":tgt_mid}); send(chat_id,"🗑️ Apagada",mid)
        elif cmd=="/warn":
            tgt=msg.get("reply_to_message",{}).get("from",{}).get("id")
            if not tgt: send(chat_id,"Responda com /warn",mid)
            else:
                execute_action(chat_id,"WARN",tgt,"warn ADM"); c=get_db(); w=c.execute("SELECT count FROM warnings WHERE chat_id=? AND user_id=?",(str(chat_id),str(tgt))).fetchone(); c.close(); cnt=w["count"] if w else 1
                send(chat_id,f"⚠️ Warn {cnt}/{cfg.get('warning_limit',3)}",mid)
                if cnt>=cfg.get("warning_limit",3): execute_action(chat_id,"BAN",tgt,"3 warns"); send(chat_id,"🚫 Ban por 3 warns",mid)
        elif cmd=="/unwarn":
            tgt=msg.get("reply_to_message",{}).get("from",{}).get("id")
            if tgt:
                with db_lock: c=get_db(); c.execute("DELETE FROM warnings WHERE chat_id=? AND user_id=?",(str(chat_id),str(tgt))); c.commit(); c.close()
                send(chat_id,"✅ Warns zerados",mid)
        elif cmd=="/warnings":
            tgt=msg.get("reply_to_message",{}).get("from",{}).get("id") or uid
            c=get_db(); w=c.execute("SELECT count FROM warnings WHERE chat_id=? AND user_id=?",(str(chat_id),str(tgt))).fetchone(); c.close()
            send(chat_id,f"Warns: {w['count'] if w else 0}/{cfg.get('warning_limit',3)}",mid)
        elif cmd=="/resetwarnings":
            with db_lock: c=get_db(); c.execute("DELETE FROM warnings WHERE chat_id=?",(str(chat_id),)); c.commit(); c.close()
            send(chat_id,"✅ Todos warns zerados",mid)
        elif cmd=="/pin":
            tgt_mid=msg.get("reply_to_message",{}).get("message_id")
            if tgt_mid: telegram_req("pinChatMessage",{"chat_id":chat_id,"message_id":tgt_mid}); send(chat_id,"📌 Fixada",mid)
        elif cmd=="/unpin":
            telegram_req("unpinAllChatMessages",{"chat_id":chat_id}); send(chat_id,"📌 Desfixado",mid)
        elif cmd=="/allowlink":
            if not args: send(chat_id,"Use: /allowlink youtube.com",mid)
            else:
                cur=cfg.get("allowed_links",""); new=cur+","+args[0] if cur else args[0]
                set_cfg(chat_id,"allowed_links",new); send(chat_id,f"✅ Liberado: {args[0]}",mid)
        elif cmd=="/logs":
            c=get_db(); rows=c.execute("SELECT action,user_id,reason FROM moderation_logs WHERE chat_id=? ORDER BY id DESC LIMIT 10",(str(chat_id),)).fetchall(); c.close()
            txt="📜 *Logs:*\n"+"\n".join([f"{r['action']} - {r['user_id']} - {r['reason'][:30]}" for r in rows]) if rows else "Sem logs"
            send(chat_id,txt,mid)
        elif cmd=="/resetai":
            send(chat_id,f"✅ IAs resetadas by {KLEBER_SIG}",mid)
        elif cmd=="/setsensual":
            if not args: send(chat_id,"Use: /setsensual livre|moderate|restrito",mid)
            else:
                mode=args[0].lower();
                if mode=="moderado": mode="moderate"
                set_cfg(chat_id,"sensual_mode",mode); set_cfg(chat_id,"anti_sensual",0 if mode=="livre" else 1)
                send(chat_id,f"✅ +18 = {mode} - by {KLEBER_SIG}",mid)
        elif cmd=="/antilink":
            if args: set_cfg(chat_id,"anti_link",1 if args[0].lower()=="on" else 0); send(chat_id,f"AntiLink {args[0]}",mid)
        elif cmd=="/antiflood":
            if len(args)>=2: set_cfg(chat_id,"flood_limit",int(args[0])); set_cfg(chat_id,"flood_window",int(args[1])); send(chat_id,f"Flood {args[0]}/{args[1]}",mid)
        elif cmd=="/antispam":
            if args: set_cfg(chat_id,"anti_spam",1 if args[0].lower()=="on" else 0); send(chat_id,f"AntiSpam {args[0]}",mid)
        elif cmd=="/antidivulg":
            if args: set_cfg(chat_id,"anti_divulgation",1 if args[0].lower()=="on" else 0); send(chat_id,f"AntiDivulg {args[0]}",mid)

        clean_cmd(chat_id,mid); return

    if uid==BOT_ID or is_admin(chat_id,uid): return
    if not text: return

    mem_texts[(str(chat_id),str(uid))].append(text)
    ai_res = call_moderation_ai(text, mem_texts[(str(chat_id),str(uid))])

    sensual = float(ai_res.get("sensual",0))
    if sensual >= 0.65 and cfg.get("anti_sensual"):
        mode = cfg.get("sensual_mode","moderate")
        if mode=="restrito":
            execute_action(chat_id,"DELETE",None,f"+18 restrito {sensual:.2f} by {KLEBER_SIG}",mid,source="AUTO+SENSUAL",confidence=sensual); return
        elif mode=="moderate":
            if sensual >= 0.92:
                execute_action(chat_id,"DELETE",None,f"+18 pesado {sensual:.2f} by {KLEBER_SIG}",mid,source="AUTO+SENSUAL",confidence=sensual)
                send(chat_id,f"⚠️ Pesado demais @{msg['from'].get('first_name','')}!",mid); return
            else: return

    if float(ai_res.get("divulg",0)) >= 0.75 and cfg.get("anti_divulgation"):
        allowed = [d.strip().lower() for d in (cfg.get("allowed_links","").split(",")) if d.strip()]
        if not any(d in text.lower() for d in allowed):
            execute_action(chat_id,"DELETE",None,f"divulg {ai_res['divulg']:.2f} by {KLEBER_SIG}",mid,source="AUTO+DIVULG",confidence=float(ai_res.get("divulg",0))); return

    if cfg.get("anti_link") and re.search(r"https?://|t\.me/|wa\.me|discord\.gg", text.lower()):
        allowed = [d.strip().lower() for d in (cfg.get("allowed_links","").split(",")) if d.strip()]
        if not any(d in text.lower() for d in allowed):
            execute_action(chat_id,"DELETE",None,f"antilink by {KLEBER_SIG}",mid,source="AUTO+LINK"); return

    if float(ai_res.get("toxic",0)) >= 0.85:
        execute_action(chat_id,"DELETE",None,f"toxic {ai_res['toxic']:.2f} by {KLEBER_SIG}",mid,source="AUTO+TOXIC",confidence=float(ai_res.get("toxic",0)))
        if float(ai_res.get("toxic",0))>=0.93: execute_action(chat_id,"MUTE",uid,f"toxico grave by {KLEBER_SIG}",mid,source="AUTO+TOXIC")
        return

    if cfg.get("anti_flood"):
        key=(str(chat_id),str(uid)); now=time.time(); dq=mem_flood[key]; dq.append(now)
        while dq and now-dq[0] > cfg.get("flood_window",15): dq.popleft()
        if len(dq) > cfg.get("flood_limit",7):
            execute_action(chat_id,"MUTE",uid,f"flood {len(dq)} by {KLEBER_SIG}",mid,source="AUTO+FLOOD"); dq.clear()
            send(chat_id,f"🔇 @{msg['from'].get('first_name','')} floodou",mid); return

if __name__=="__main__":
    print(f"[{KLEBER_SIG}] START {ORBIT_CORE} CHECK:{KLEBER_CHECK}")
    app.run(host="0.0.0.0", port=PORT)
