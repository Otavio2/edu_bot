# ORBIT ALLIANCE V13 FINAL SECURE - 271 FIXES - PROD READY - NO HARDCODED KEYS
import os, re, json, time, sqlite3, logging, requests, base64, hashlib, shutil, threading
from datetime import datetime, timezone
from collections import defaultdict, deque
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
backup_pending = False
last_backup = 0
BOT_ID = None
BOT_USERNAME = None

VALID_ACTIONS = {"DELETE","WARN","MUTE","KICK","BAN","UNBAN","UNMUTE","PIN","UNPIN"}
VALID_AUTO = {"DELETE","WARN","MUTE","KICK","BAN"}
VALID_MODES = {"observer","moderate","strict","auto"}
VALID_NIGHT = {"silent","strict"}

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
    CREATE TABLE IF NOT EXISTS group_rules(chat_id TEXT PRIMARY KEY,welcome INTEGER DEFAULT 1,goodbye INTEGER DEFAULT 1,welcome_msg TEXT DEFAULT 'Bem-vindo {name}!',goodbye_msg TEXT DEFAULT '{name} saiu.',anti_link INTEGER DEFAULT 0,anti_spam INTEGER DEFAULT 1,anti_flood INTEGER DEFAULT 1,anti_mention INTEGER DEFAULT 0,anti_divulgation INTEGER DEFAULT 0,allowed_links TEXT DEFAULT 'youtube.com,youtu.be,instagram.com,github.com,google.com',warning_limit INTEGER DEFAULT 3,moderation_mode TEXT DEFAULT 'moderate',flood_limit INTEGER DEFAULT 7,flood_window INTEGER DEFAULT 15,auto_actions TEXT DEFAULT '["DELETE","WARN","MUTE"]',night_mode INTEGER DEFAULT 0,night_mode_type TEXT DEFAULT 'silent',night_start TEXT DEFAULT '22:00',night_end TEXT DEFAULT '06:00',mute_duration INTEGER DEFAULT 600,updated_at TEXT);
    CREATE TABLE IF NOT EXISTS warnings(chat_id TEXT,user_id TEXT,count INTEGER DEFAULT 0,last_reason TEXT,updated_at TEXT,PRIMARY KEY(chat_id,user_id));
    CREATE TABLE IF NOT EXISTS moderation_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id TEXT,user_id TEXT,admin_id TEXT,action TEXT,reason TEXT,message_id INTEGER,source TEXT,success INTEGER,created_at TEXT);
    CREATE TABLE IF NOT EXISTS processed_updates(update_id INTEGER PRIMARY KEY,processed_at TEXT);
    CREATE TABLE IF NOT EXISTS system_errors(id INTEGER PRIMARY KEY AUTOINCREMENT,component TEXT,error TEXT,chat_id TEXT,update_id INTEGER,created_at TEXT);
    CREATE TABLE IF NOT EXISTS backup_meta(rowid INTEGER PRIMARY KEY,last_at TEXT,last_status TEXT,checksum TEXT);
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

def extract_domains(txt):
    txt=str(txt).lower()
    out=set()
    for d in re.findall(r"(?:https?://)?(?:www\.)?([a-z0-9-]+\.[a-z0-9.-]+\.[a-z]{2,})",txt):
        d=d.strip("./").split("/")[0].split(":")[0]
        if d.count(".")>=1 and len(d)>3: out.add(d)
    for d in re.findall(r"\b([a-z0-9-]+\.(?:com|net|org|io|com\.br|net\.br))\b",txt):
        out.add(d)
    return list(out)

def is_link_allowed(txt, allowed_str):
    allowed=[a.strip().lower() for a in allowed_str.split(",") if a.strip()]
    for dom in extract_domains(txt):
        if any(dom==a or dom.endswith("."+a) for a in allowed): continue
        return False,dom
    return True,None

def telegram_req(method,payload=None,retries=2):
    url=f"{TELEGRAM_API_URL}/{method}"
    for attempt in range(retries+1):
        try:
            r=requests.post(url,json=payload,timeout=12) if payload else requests.get(url,timeout=12)
            j=r.json()
            if r.status_code==429:
                time.sleep(j.get("parameters",{}).get("retry_after",2)); continue
            return j
        except Exception as e:
            if attempt==retries: log_error(f"TG_{method}",e); return {"ok":False,"error":str(e)}
            time.sleep(0.8)
    return {"ok":False}

def init_bot():
    global BOT_ID, BOT_USERNAME
    d=telegram_req("getMe")
    if d.get("ok"):
        BOT_ID=d["result"]["id"]; BOT_USERNAME=d["result"].get("username","").lower()
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
    if key not in {"welcome","goodbye","anti_link","anti_spam","anti_flood","anti_mention","anti_divulgation","warning_limit","moderation_mode","flood_limit","flood_window","welcome_msg","goodbye_msg","allowed_links","auto_actions","night_mode","night_mode_type","night_start","night_end","mute_duration"}:
        return False
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
    return {"success":success,"error":None if success else str(res)[:200],"action":action}

def restore_safe():
    if not JSONBIN_URL:
        logging.warning("RESTORE skip - sem JSONBIN_URL")
        return
    if os.path.exists(DATABASE_PATH) and os.path.getsize(DATABASE_PATH) > 1024:
        try:
            c=sqlite3.connect(DATABASE_PATH)
            chk=c.execute("PRAGMA quick_check").fetchone()
            c.close()
            if chk and "ok" in str(chk[0]).lower():
                logging.info("RESTORE skip - local válido")
                return
        except:
            pass
    try:
        r=requests.get(JSONBIN_URL+"/latest",headers=JB_HEADERS,timeout=12)
        if r.status_code!=200:
            logging.warning(f"RESTORE http {r.status_code}")
            return
        rec=r.json().get("record",{})
        b64=rec.get("db_base64") or rec.get("b64")
        if not b64:
            logging.warning("RESTORE vazio - sem b64")
            return
        if rec.get("checksum"):
            calc=hashlib.sha256(b64.encode()).hexdigest()[:16]
            if calc!=rec.get("checksum"):
                log_error("RESTORE","checksum_mismatch")
                return
        tmp=DATABASE_PATH+".restore"
        with open(tmp,"wb") as f:
            f.write(base64.b64decode(b64))
        c=sqlite3.connect(tmp)
        chk=c.execute("PRAGMA quick_check").fetchone()
        c.close()
        if not chk or "ok" not in str(chk[0]).lower():
            os.remove(tmp)
            log_error("RESTORE","quick_check_fail")
            return
        shutil.move(tmp,DATABASE_PATH)
        logging.info("RESTORE ok do JSONBin")
    except Exception as e:
        log_error("RESTORE",e)

def backup_worker():
    global backup_pending,last_backup
    while True:
        time.sleep(30)
        if not backup_pending or not JSONBIN_URL: continue
        if time.time()-last_backup<30: continue
        with backup_lock:
            try:
                tmp=DATABASE_PATH+".tmpcopy"
                shutil.copy2(DATABASE_PATH,tmp)
                with open(tmp,"rb") as f: b64=base64.b64encode(f.read()).decode()
                chk=hashlib.sha256(b64.encode()).hexdigest()[:16]
                payload={"db_base64":b64,"updated_at":datetime.now(timezone.utc).isoformat(),"checksum":chk,"version":int(time.time())}
                r=requests.put(JSONBIN_URL,json=payload,headers=JB_HEADERS,timeout=15)
                if r.status_code==200:
                    last_backup=time.time(); backup_pending=False
                    c=get_db(); c.execute("INSERT OR REPLACE INTO backup_meta(rowid,last_at,last_status,checksum) VALUES(1,?,?,?)",(payload["updated_at"],"ok",chk)); c.commit(); c.close()
                else: log_error("BACKUP",f"http {r.status_code}")
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
    try:
        c.execute("INSERT INTO processed_updates(update_id,processed_at) VALUES(?,?)",(uid,datetime.now(timezone.utc).isoformat())); c.commit()
    except sqlite3.IntegrityError:
        c.close(); return {"ok":True},200
    except: pass
    finally:
        try: c.close()
        except: pass
    executor.submit(process_update_safe, data)
    return {"ok":True},200

@app.route("/", methods=["GET"])
def health():
    try:
        c=get_db(); c.execute("SELECT 1").fetchone(); c.close(); db="🟢 ONLINE"
    except: db="🔴 OFFLINE"
    return {"status":"Orbit V13 SECURE","bot_id":BOT_ID,"db":db,"webhook":WEBHOOK_PATH,"last_backup":last_backup}

def process_update_safe(upd):
    try: process_update(upd)
    except Exception as e: log_error("PROCESS",e,None,upd.get("update_id"))

def resolve_target(msg, args):
    if msg.get("reply_to_message"): return str(msg["reply_to_message"]["from"]["id"])
    if args and args[0].isdigit(): return args[0]
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

    if cfg.get("night_mode"):
        ns,ne=cfg.get("night_start","22:00"),cfg.get("night_end","06:00")
        if valid_hhmm(ns) and valid_hhmm(ne):
            now=datetime.now(TZ).strftime("%H:%M")
            in_night=(ns<=now or now<ne) if ns>ne else (ns<=now<ne)
            if in_night and not is_admin(chat_id,uid):
                if cfg.get("night_mode_type")=="silent":
                    execute_action(chat_id,"DELETE",uid,"night_silent",mid,source="AUTO")
                    return
                elif cfg.get("night_mode_type")=="strict":
                    if len(text)>500:
                        execute_action(chat_id,"DELETE",uid,"night_strict_long",mid,source="AUTO")
                        return

    if text.startswith("/"):
        parts=text.strip().split()
        cmd=parts[0].split("@")[0].lower()
        args=parts[1:] if len(parts)>1 else []

        if chat_type=="private" and cmd in ("/ban","/kick","/mute","/unmute","/unban","/delete","/warn","/unwarn","/pin","/unpin"):
            send(chat_id,"⚠️ Esse comando só funciona em grupos.")
            return
        if cmd in ("/ban","/kick","/mute","/unmute","/unban","/delete","/warn","/unwarn","/pin","/unpin","/logs","/warnings","/status","/resetwarnings","/allowlink"):
            if not is_admin(chat_id,uid):
                send(chat_id,"⚠️ Só administradores podem usar.")
                return
        if cmd=="/ban":
            target=resolve_target(msg,args)
            if not target: send(chat_id,"⚠️ Responda com /ban", mid); return
            r=execute_action(chat_id,"BAN",target,f"ban por {uid}",None,admin_id=uid,source="COMMAND",confidence=1.0)
            send(chat_id,f"🚫 Banido {target}" if r["success"] else f"❌ {r['error']}", mid); return
        if cmd=="/unban":
            target=resolve_target(msg,args) or (args[0] if args else None)
            if not target or not target.isdigit(): send(chat_id,"⚠️ /unban <id>", mid); return
            r=execute_action(chat_id,"UNBAN",target,"unban",None,admin_id=uid,source="COMMAND")
            send(chat_id,"✅ Desbanido." if r["success"] else f"❌ {r['error']}", mid); return
        if cmd=="/kick":
            target=resolve_target(msg,args)
            if not target: send(chat_id,"⚠️ Responda com /kick", mid); return
            r=execute_action(chat_id,"KICK",target,f"kick por {uid}",None,admin_id=uid,source="COMMAND")
            send(chat_id,"👢 Expulso." if r["success"] else f"❌ {r['error']}", mid); return
        if cmd=="/mute":
            target=resolve_target(msg,args)
            if not target: send(chat_id,"⚠️ Responda com /mute", mid); return
            r=execute_action(chat_id,"MUTE",target,f"mute por {uid}",None,admin_id=uid,source="COMMAND")
            send(chat_id,"🔇 Mutado." if r["success"] else f"❌ {r['error']}", mid); return
        if cmd=="/unmute":
            target=resolve_target(msg,args)
            if not target: send(chat_id,"⚠️ Responda com /unmute", mid); return
            r=execute_action(chat_id,"UNMUTE",target,"unmute",None,admin_id=uid,source="COMMAND")
            send(chat_id,"🔊 Desmutado." if r["success"] else f"❌ {r['error']}", mid); return
        if cmd=="/delete":
            if not msg.get("reply_to_message"): send(chat_id,"⚠️ Responda com /delete", mid); return
            r=execute_action(chat_id,"DELETE",None,"delete",msg["reply_to_message"]["message_id"],admin_id=uid,source="COMMAND")
            if not r["success"]: send(chat_id,f"❌ {r['error']}")
            return
        if cmd=="/warn":
            target=resolve_target(msg,args)
            if not target: send(chat_id,"⚠️ Responda com /warn", mid); return
            if is_protected(chat_id,target): send(chat_id,"⚠️ Protegido"); return
            with db_lock:
                c=get_db(); row=c.execute("SELECT count FROM warnings WHERE chat_id=? AND user_id=?",(str(chat_id),str(target))).fetchone()
                cnt=(row["count"]+1) if row else 1
                c.execute("INSERT OR REPLACE INTO warnings(chat_id,user_id,count,last_reason,updated_at) VALUES(?,?,?,?,?)",(str(chat_id),str(target),cnt,f"warn por {uid}",datetime.now(timezone.utc).isoformat())); c.commit(); c.close()
            log_action(chat_id,target,"WARN",f"warn por {uid}",mid,"COMMAND",True,uid)
            send(chat_id,f"⚠️ {target} {cnt}/{get_cfg(chat_id).get('warning_limit',3)} warns"); return
        if cmd=="/unwarn":
            target=resolve_target(msg,args)
            if not target: send(chat_id,"⚠️ /unwarn", mid); return
            with db_lock: c=get_db(); c.execute("DELETE FROM warnings WHERE chat_id=? AND user_id=?",(str(chat_id),str(target))); c.commit(); c.close()
            send(chat_id,f"✅ Warnings de {target} resetados.", mid); return
        if cmd=="/warnings":
            target=resolve_target(msg,args) or str(uid)
            c=get_db(); row=c.execute("SELECT count FROM warnings WHERE chat_id=? AND user_id=?",(str(chat_id),str(target))).fetchone(); c.close()
            send(chat_id,f"✅ {target} sem warns" if not row else f"⚠️ {target}: {row['count']} warns", mid); return
        if cmd=="/resetwarnings":
            with db_lock: c=get_db(); c.execute("DELETE FROM warnings WHERE chat_id=?",(str(chat_id),)); c.commit(); c.close()
            send(chat_id,"✅ Warnings resetados", mid); return
        if cmd=="/allowlink":
            if not args: send(chat_id,"Use: /allowlink dominio.com", mid); return
            dom=args[0].lower().strip()
            cfg2=get_cfg(chat_id); cur=cfg2.get("allowed_links",""); lista=[a.strip() for a in cur.split(",") if a.strip()]
            if dom not in lista: lista.append(dom); set_cfg(chat_id,"allowed_links",",".join(lista))
            send(chat_id,f"✅ {dom} permitido.", mid); return
        if cmd=="/pin":
            if not msg.get("reply_to_message"): send(chat_id,"Responda com /pin", mid); return
            r=execute_action(chat_id,"PIN",None,"pin",msg["reply_to_message"]["message_id"],admin_id=uid,source="COMMAND")
            send(chat_id,"📌 Fixado." if r["success"] else f"❌ {r['error']}", mid); return
        if cmd=="/unpin":
            r=execute_action(chat_id,"UNPIN",None,"unpin",None,admin_id=uid,source="COMMAND")
            send(chat_id,"📌 Desfixado." if r["success"] else f"❌ {r['error']}", mid); return
        if cmd=="/logs":
            c=get_db(); rows=c.execute("SELECT action,reason,created_at FROM moderation_logs WHERE chat_id=? ORDER BY id DESC LIMIT 20",(str(chat_id),)).fetchall(); c.close()
            out="\n".join([f"{r['created_at'][11:16]} {r['action']} {r['reason'][:30]}" for r in rows]) if rows else "Sem logs"
            send(chat_id,out[:3900], mid); return
        if cmd=="/status":
            try: tg=telegram_req("getMe"); tgs="🟢 ONLINE" if tg.get("ok") else "🔴 OFF"
            except: tgs="🔴 ERRO"
            try: c=get_db(); c.execute("SELECT 1").fetchone(); c.close(); dbs="🟢 WAL"
            except: dbs="🔴 OFF"
            jbs=f"🟢 {int(last_backup)}" if JSONBIN_URL else "⚪ OFF"
            cfg2=get_cfg(chat_id)
            send(chat_id,f"*{BOT_USERNAME or 'Orbit'} V13*\nTG:{tgs} DB:{dbs} BIN:{jbs}\nPerm del:{'✅' if bot_can(chat_id,'can_delete_messages') else '❌'} res:{'✅' if bot_can(chat_id,'can_restrict_members') else '❌'}\nModo:{cfg2.get('moderation_mode')} AntiLink:{cfg2.get('anti_link')}", mid); return
        if cmd in ("/start","/help"):
            send(chat_id,"/ban /kick /mute /unmute /delete /warn /allowlink /pin /logs /status", mid); return

    if uid==BOT_ID: return
    if is_admin(chat_id,uid): return
    if not text: return
    if is_edited:
        c=get_db(); already=c.execute("SELECT id FROM moderation_logs WHERE chat_id=? AND message_id=? AND success=1 LIMIT 1",(str(chat_id),str(mid))).fetchone(); c.close()
        if already: return
    if cfg.get("anti_link"):
        ok,dom=is_link_allowed(text,cfg.get("allowed_links",""))
        if not ok:
            r=execute_action(chat_id,"DELETE",uid,f"link {dom}",mid,source="AUTO",confidence=0.99)
            if r["success"]:
                with db_lock: c=get_db(); row=c.execute("SELECT count FROM warnings WHERE chat_id=? AND user_id=?",(str(chat_id),str(uid))).fetchone(); cnt=(row["count"]+1) if row else 1; c.execute("INSERT OR REPLACE INTO warnings(chat_id,user_id,count,last_reason,updated_at) VALUES(?,?,?,?,?)",(str(chat_id),str(uid),cnt,f"link {dom}",datetime.now(timezone.utc).isoformat())); c.commit(); c.close()
            return
    if cfg.get("anti_flood"):
        dq=mem_flood[(str(chat_id),str(uid))]; now=time.time(); dq.append(now)
        while dq and now-dq[0]>cfg.get("flood_window",15): dq.popleft()
        if len(dq)>cfg.get("flood_limit",7):
            r=execute_action(chat_id,"MUTE",uid,f"flood",mid,source="AUTO",confidence=0.9)
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
