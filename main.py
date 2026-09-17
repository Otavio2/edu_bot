# ORBIT ALLIANCE V17.3 HARDENED FINAL - BY Kʆɛɓɛʀ - RENDER READY
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
ORBIT_CORE = f"Orbit V17.3 HARDENED by {KLEBER_SIG}"

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
mem_texts = defaultdict(lambda: deque(maxlen=5))
mem_fight = defaultdict(lambda: deque(maxlen=20))
mem_join = defaultdict(lambda: deque())
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
        if not key:
            continue
        if name == "cloudflare":
            acc = os.getenv(cfg["account_env"])
            if not acc:
                continue
            provs[name] = {"key": key, "account_id": acc, "endpoint": cfg["endpoint"].format(account_id=acc), "format": cfg["format"]}
        else:
            provs[name] = {"key": key, "endpoint": cfg["endpoint"], "format": cfg["format"]}
    return provs

PROVIDERS = build_providers_dynamic()
ORDER_PREFERENCE = ["cloudflare", "groq", "gemini", "cerebras"]

SENSUAL_WORDS = {"sem cueca","sem calcinha","pelado","pelada","tesao","tesão","buceta","sexo","nudes","onlyfans","porno","punheta","siririca","de toalha","sem roupa"}
TOXIC_WORDS = {"lixo","burro","otario","otário","idiota","fdp","vsf","arrombado","desgraca","corno","vagabundo","vai se foder","fdp"," lixo"}
DIVULGA_WORDS = {"entra","ganhe","lucro","renda","grátis","gratis","promoção","promocao","vagas","dinheiro","pix","aposte","cassino","tigrinho","sorteio","grupo novo"}

DATABASE_PATH = os.getenv("DATABASE_PATH","Orbit.db")
# Corrige se usar /data sem Disk
if "/data" in DATABASE_PATH:
    try:
        os.makedirs("/data", exist_ok=True)
    except:
        DATABASE_PATH = "Orbit.db"

def get_db():
    try:
        c = sqlite3.connect(DATABASE_PATH, check_same_thread=False, timeout=15)
    except sqlite3.OperationalError:
        # fallback local
        c = sqlite3.connect("Orbit.db", check_same_thread=False, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL;")
    c.execute("PRAGMA synchronous=NORMAL;")
    return c

def restore_safe():
    if not JSONBIN_URL:
        return False
    try:
        if os.path.exists(DATABASE_PATH) and os.path.getsize(DATABASE_PATH) > 5000:
            try:
                c = sqlite3.connect(DATABASE_PATH)
                chk = c.execute("PRAGMA quick_check").fetchone()
                c.close()
                if chk and "ok" in str(chk[0]).lower():
                    return False
            except:
                pass
        print(f"[{KLEBER_SIG}] Tentando restore...")
        r = requests.get(f"{JSONBIN_URL}/latest", headers=JB_HEADERS, timeout=15)
        if r.status_code!= 200:
            return False
        rec = r.json().get("record", {})
        b64 = rec.get("db") or rec.get("db_base64") or rec.get("db_b64")
        if not b64 or len(b64) < 5000:
            print(f"[{KLEBER_SIG}] Backup vazio, ignorando")
            return False
        tmp = DATABASE_PATH + ".restore"
        with open(tmp, "wb") as f:
            f.write(base64.b64decode(b64))
        shutil.move(tmp, DATABASE_PATH)
        print(f"[{KLEBER_SIG}] RESTORE OK")
        return True
    except Exception as e:
        print(f"Restore falhou {e}")
        return False

def init_db():
    if not os.path.exists(DATABASE_PATH) or os.path.getsize(DATABASE_PATH) < 100:
        restore_safe()
    c = get_db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS group_rules(chat_id TEXT PRIMARY KEY,title TEXT,welcome INTEGER DEFAULT 1,goodbye INTEGER DEFAULT 1,welcome_msg TEXT DEFAULT 'Bem-vindo {name}! 🚀',goodbye_msg TEXT DEFAULT '{name} saiu.',anti_link INTEGER DEFAULT 0,anti_spam INTEGER DEFAULT 1,anti_flood INTEGER DEFAULT 1,anti_divulgation INTEGER DEFAULT 1,anti_sensual INTEGER DEFAULT 1,sensual_mode TEXT DEFAULT 'moderate',allowed_links TEXT DEFAULT 'youtube.com,youtu.be,instagram.com,github.com,cloudflare.com',warning_limit INTEGER DEFAULT 3,flood_limit INTEGER DEFAULT 7,flood_window INTEGER DEFAULT 15,mute_duration INTEGER DEFAULT 600,lock_group INTEGER DEFAULT 0,updated_at TEXT);
    CREATE TABLE IF NOT EXISTS warnings(chat_id TEXT,user_id TEXT,count INTEGER DEFAULT 0,last_reason TEXT,updated_at TEXT,PRIMARY KEY(chat_id,user_id));
    CREATE TABLE IF NOT EXISTS moderation_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id TEXT,user_id TEXT,action TEXT,reason TEXT,message_id INTEGER,success INTEGER,created_at TEXT);
    CREATE TABLE IF NOT EXISTS group_peak(chat_id TEXT, hour INTEGER, count INTEGER DEFAULT 0, PRIMARY KEY(chat_id,hour));
    CREATE TABLE IF NOT EXISTS user_reputation(chat_id TEXT, user_id TEXT, spam_score INTEGER DEFAULT 0, last_seen TEXT, PRIMARY KEY(chat_id,user_id));
    CREATE TABLE IF NOT EXISTS group_toxic_words(chat_id TEXT, word TEXT, count INTEGER DEFAULT 1, PRIMARY KEY(chat_id,word));
    CREATE TABLE IF NOT EXISTS action_outbox(action_key TEXT PRIMARY KEY,chat_id TEXT,user_id TEXT,action TEXT,payload_json TEXT,status TEXT,attempts INTEGER DEFAULT 0,next_attempt_at TEXT,created_at TEXT);
    """)
    c.commit()
    c.close()

init_db()

def telegram_req(method, payload=None):
    url = f"{TELEGRAM_API_URL}/{method}"
    for _ in range(3):
        try:
            r = requests.post(url, json=payload, timeout=12) if payload else requests.get(url, timeout=12)
            if r.status_code == 429:
                time.sleep(2)
                continue
            return r.json()
        except:
            time.sleep(1)
    return {"ok": False}

def init_bot():
    global BOT_ID, BOT_USERNAME
    d = telegram_req("getMe")
    if d.get("ok"):
        BOT_ID = d["result"]["id"]
        BOT_USERNAME = d["result"].get("username", "")
        print(f"[{KLEBER_SIG}] {ORBIT_CORE} @{BOT_USERNAME} OK")
init_bot()

def send(chat_id, txt, reply=None):
    p = {"chat_id": chat_id, "text": str(txt)[:3900], "parse_mode": "Markdown"}
    if reply:
        p["reply_to_message_id"] = reply
    return telegram_req("sendMessage", p)

def get_cfg(chat_id):
    c = get_db()
    r = c.execute("SELECT * FROM group_rules WHERE chat_id=?", (str(chat_id),)).fetchone()
    c.close()
    if not r:
        with db_lock:
            c = get_db()
            c.execute("INSERT OR IGNORE INTO group_rules(chat_id,updated_at) VALUES(?,?)", (str(chat_id), datetime.now(timezone.utc).isoformat()))
            c.commit()
            c.close()
        return get_cfg(chat_id)
    return dict(r)

def set_cfg(chat_id, key, val):
    with db_lock:
        c = get_db()
        c.execute(f"UPDATE group_rules SET {key}=?, updated_at=? WHERE chat_id=?", (val, datetime.now(timezone.utc).isoformat(), str(chat_id)))
        c.commit()
        c.close()
    global backup_pending
    backup_pending = True

def is_admin(chat_id, uid):
    if str(uid) == CREATOR_ID:
        return True
    m = telegram_req("getChatMember", {"chat_id": chat_id, "user_id": uid})
    return m.get("ok") and m.get("result", {}).get("status") in ("administrator", "creator")

def is_protected(chat_id, uid):
    if str(uid) == str(BOT_ID) or str(uid) == CREATOR_ID:
        return True
    return is_admin(chat_id, uid)

def enqueue_action(chat_id, user_id, action, payload, action_key=None):
    try:
        if not action_key:
            action_key = f"{chat_id}_{user_id}_{action}_{int(time.time())}_{random.randint(1000,9999)}"
        with db_lock:
            c = get_db()
            c.execute("INSERT OR IGNORE INTO action_outbox(action_key,chat_id,user_id,action,payload_json,status,attempts,next_attempt_at,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                      (action_key, str(chat_id), str(user_id), action, json.dumps(payload), "pending", 0, datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()))
            c.commit()
            c.close()
        global backup_pending
        backup_pending = True
        return True
    except Exception as e:
        print(f"OUTBOX ERRO {e}")
        return False

def execute_action(chat_id, action, target_id=None, message_id=None, reason="", source="AUTO"):
    if target_id and is_protected(chat_id, target_id):
        return {"success": False, "reason": "protected"}
    res = {"ok": False}
    try:
        if action == "DELETE" and message_id:
            res = telegram_req("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
        elif action == "WARN" and target_id:
            with db_lock:
                c = get_db()
                w = c.execute("SELECT count FROM warnings WHERE chat_id=? AND user_id=?", (str(chat_id), str(target_id))).fetchone()
                cnt = (w["count"] if w else 0) + 1
                c.execute("INSERT OR REPLACE INTO warnings(chat_id,user_id,count,last_reason,updated_at) VALUES(?,?,?,?,?)",
                          (str(chat_id), str(target_id), cnt, reason, datetime.now(timezone.utc).isoformat()))
                c.commit()
                c.close()
            res = {"ok": True}
        elif action == "MUTE" and target_id:
            cfg = get_cfg(chat_id)
            res = telegram_req("restrictChatMember", {"chat_id": chat_id, "user_id": target_id, "permissions": {"can_send_messages": False}, "until_date": int(time.time()) + cfg.get("mute_duration", 600)})
        elif action == "UNMUTE" and target_id:
            res = telegram_req("restrictChatMember", {"chat_id": chat_id, "user_id": target_id, "permissions": {"can_send_messages": True, "can_send_media_messages": True, "can_send_other_messages": True}})
        elif action == "BAN" and target_id:
            res = telegram_req("banChatMember", {"chat_id": chat_id, "user_id": target_id})
        elif action == "KICK" and target_id:
            telegram_req("banChatMember", {"chat_id": chat_id, "user_id": target_id})
            res = telegram_req("unbanChatMember", {"chat_id": chat_id, "user_id": target_id})
        elif action == "UNBAN" and target_id:
            res = telegram_req("unbanChatMember", {"chat_id": chat_id, "user_id": target_id})
        if target_id and action in ("MUTE", "BAN", "DELETE"):
            try:
                with db_lock:
                    c = get_db()
                    c.execute("INSERT INTO user_reputation(chat_id,user_id,spam_score,last_seen) VALUES(?,?,1,?) ON CONFLICT(chat_id,user_id) DO UPDATE SET spam_score=spam_score+1, last_seen=?",
                              (str(chat_id), str(target_id), datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()))
                    c.commit()
                    c.close()
            except:
                pass
    except Exception as e:
        print(f"EXECUTE ERRO {e}")
        res = {"ok": False}
    try:
        with db_lock:
            c = get_db()
            c.execute("INSERT INTO moderation_logs(chat_id,user_id,action,reason,message_id,success,created_at) VALUES(?,?,?,?,?,?,?)",
                      (str(chat_id), str(target_id or ""), action, reason, message_id or 0, 1 if res.get("ok") else 0, datetime.now(timezone.utc).isoformat()))
            c.commit()
            c.close()
    except:
        pass
    return {"success": bool(res.get("ok"))}

def call_moderation_ai(text):
    tl = text.lower()
    hs = 0.85 if any(w in tl for w in ["sem cueca pela casa", "andar sem cueca", "pelado em casa"]) else min(sum(0.45 for w in SENSUAL_WORDS if w in tl), 1.0)
    ht = min(sum(0.35 for w in TOXIC_WORDS if w in tl), 1.0)
    hd = min((0.4 if re.search(r"https?://|t\.me/|wa\.me|discord\.gg", tl) else 0) + sum(0.15 for w in DIVULGA_WORDS if w in tl), 1.0)
    if not PROVIDERS:
        return {"toxic": ht, "divulg": hd, "sensual": hs}
    prompt = f'Retorne SOMENTE JSON {{"toxic":0-1,"divulg":0-1,"sensual":0-1}}. Msg:"{text[:200]}"'
    for prov in ORDER_PREFERENCE:
        if prov not in PROVIDERS:
            continue
        cfg = PROVIDERS[prov]
        try:
            sess = get_session()
            if cfg["format"] == "cloudflare":
                r = sess.post(cfg["endpoint"], json={"messages": [{"role": "user", "content": prompt}]}, headers={"Authorization": f"Bearer {cfg['key']}"}, timeout=6)
                if r.status_code == 200:
                    resp = r.json().get("result", {}).get("response", "")
                    m = re.search(r"\{.*\}", resp, re.DOTALL)
                    if m:
                        j = json.loads(m.group())
                        return {"toxic": max(float(j.get("toxic", 0)), ht), "divulg": max(float(j.get("divulg", 0)), hd), "sensual": max(float(j.get("sensual", 0)), hs)}
            elif cfg["format"] == "openai":
                model = FALLBACK_MODELS.get(prov, ["llama-3.1-8b-instant"])[0]
                headers = {"Authorization": f"Bearer {cfg['key']}", "Content-Type": "application/json"}
                endpoint = cfg["endpoint"]
                if "groq.com" in endpoint:
                    endpoint = "https://api.groq.com/openai/v1/chat/completions"
                elif "cerebras" in endpoint:
                    endpoint = "https://api.cerebras.ai/v1/chat/completions"
                elif not endpoint.endswith("/chat/completions"):
                    endpoint = endpoint.rstrip("/") + "/chat/completions"
                r = sess.post(endpoint, json={"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1, "max_tokens": 100}, headers=headers, timeout=6)
                if r.status_code == 200:
                    resp = r.json()["choices"][0]["message"]["content"]
                    m = re.search(r"\{.*\}", resp, re.DOTALL)
                    if m:
                        j = json.loads(m.group())
                        return {"toxic": max(float(j.get("toxic", 0)), ht), "divulg": max(float(j.get("divulg", 0)), hd), "sensual": max(float(j.get("sensual", 0)), hs)}
            elif cfg["format"] == "gemini":
                model = FALLBACK_MODELS.get(prov, ["gemini-1.5-flash"])[0]
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={cfg['key']}"
                r = sess.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, headers={"Content-Type": "application/json"}, timeout=6)
                if r.status_code == 200:
                    resp = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                    m = re.search(r"\{.*\}", resp, re.DOTALL)
                    if m:
                        j = json.loads(m.group())
                        return {"toxic": max(float(j.get("toxic", 0)), ht), "divulg": max(float(j.get("divulg", 0)), hd), "sensual": max(float(j.get("sensual", 0)), hs)}
        except Exception as e:
            print(f"[{KLEBER_SIG}] IA {prov} falhou: {e}")
            continue
    return {"toxic": ht, "divulg": hd, "sensual": hs}

def schedule_backup():
    global backup_pending, last_backup
    if not JSONBIN_URL or not backup_pending or time.time() - last_backup < 300:
        return
    with backup_lock:
        try:
            if not os.path.exists(DATABASE_PATH):
                return
            sz = os.path.getsize(DATABASE_PATH)
            if sz < 5000:
                print(f"[{KLEBER_SIG}] Banco pequeno {sz}, nao enviando")
                return
            with open(DATABASE_PATH, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            payload = {"db": b64, "db_base64": b64, "updated": datetime.now(timezone.utc).isoformat(), "by": KLEBER_SIG}
            requests.put(JSONBIN_URL, json=payload, headers=JB_HEADERS, timeout=15)
            last_backup = time.time()
            backup_pending = False
            print(f"[{KLEBER_SIG}] Backup OK {sz}")
        except Exception as e:
            print(f"Backup erro {e}")

def backup_worker():
    while True:
        time.sleep(60)
        schedule_backup()
threading.Thread(target=backup_worker, daemon=True).start()

@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    if WEBHOOK_SECRET and request.headers.get("X-Telegram-Bot-Api-Secret-Token")!= WEBHOOK_SECRET:
        abort(403)
    data = request.get_json(force=True)
    executor.submit(process_update, data)
    return {"ok": True}, 200

@app.route("/", methods=["GET"])
def health():
    return {"status": f"Orbit V17.3 HARDENED by {KLEBER_SIG}", "bot": BOT_USERNAME, "providers": list(PROVIDERS.keys())}

def process_update(update):
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    chat_id = msg["chat"]["id"]
    uid = msg["from"]["id"]
    text = (msg.get("text", "") or msg.get("caption", "")).strip()
    mid = msg["message_id"]
    cfg = get_cfg(chat_id)

    if "new_chat_members" in msg:
        now = time.time()
        dq = mem_join[str(chat_id)]
        dq.append(now)
        while dq and now - dq[0] > 10:
            dq.popleft()
        if len(dq) >= 5:
            send(chat_id, "🚨 Anti-raid ativado! Entradas em massa detectadas.")
            set_cfg(chat_id, "lock_group", 1)
            dq.clear()
        if cfg.get("welcome"):
            for u in msg["new_chat_members"]:
                if str(u.get("id")) == str(BOT_ID):
                    continue
                nome = u.get("first_name", "")
                txt_w = cfg.get("welcome_msg", "Bem-vindo {name}! 🚀")
                try:
                    txt_w = txt_w.format(name=nome)
                except:
                    pass
                send(chat_id, txt_w)
        return

    if "left_chat_member" in msg:
        if cfg.get("goodbye"):
            nome = msg["left_chat_member"].get("first_name", "Alguem")
            txt_b = cfg.get("goodbye_msg", "{name} saiu.")
            try:
                txt_b = txt_b.format(name=nome)
            except:
                pass
            send(chat_id, txt_b)
        return

    if text.startswith("/"):
        parts = text.strip().split()
        cmd = parts[0].lower().split("@")[0]
        args = parts[1:]
        if cmd in ["/start", "/help", "/painel", "/status"]:
            c = get_db()
            try:
                peak = c.execute("SELECT hour,count FROM group_peak WHERE chat_id=? ORDER BY count DESC LIMIT 1", (str(chat_id),)).fetchone()
                rep = c.execute("SELECT COUNT(*) as c FROM user_reputation WHERE chat_id=? AND spam_score>=3", (str(chat_id),)).fetchone()
                tox = c.execute("SELECT word,count FROM group_toxic_words WHERE chat_id=? ORDER BY count DESC LIMIT 3", (str(chat_id),)).fetchall()
            except:
                peak = None
                rep = None
                tox = []
            c.close()
            peak_txt = f"{peak['hour']}h ({peak['count']})" if peak else "aprendendo"
            rep_txt = rep["c"] if rep else 0
            tox_txt = ", ".join([f"{r['word']}({r['count']})" for r in tox]) if tox else "nenhuma"
            if cmd in ["/painel", "/help"]:
                send(chat_id, f"⚙️ *PAINEL ORBIT V17.3 HARDENED - By {KLEBER_SIG}*\n*IA:* {list(PROVIDERS.keys())} | *+18:* {cfg.get('sensual_mode')}\n*Flood:* {cfg.get('flood_limit')}/{cfg.get('flood_window')}s | *Pico:* {peak_txt}\n*Lock:* {'ON' if cfg.get('lock_group') else 'OFF'}\n\n*APRENDIZADO:*\nObservacao: {rep_txt} users\nToxicas: {tox_txt}\n\n*+18:* `/setsensual livre/moderate/restrito`\n*ADM:* `/ban /unban /kick /mute /unmute /delete /warn /unwarn /warnings /resetwarnings /pin /unpin /allowlink /setwelcome /setgoodbye /lock /unlock /logs`\n_Salva tudo ✅_", mid)
            else:
                send(chat_id, f"🤖 *Orbit V17.3 HARDENED By {KLEBER_SIG}*\nProviders: {list(PROVIDERS.keys())}\nBackup: {'ON' if JSONBIN_URL else 'OFF'}", mid)
            try:
                telegram_req("deleteMessage", {"chat_id": chat_id, "message_id": mid})
            except:
                pass
            return

        if not is_admin(chat_id, uid):
            send(chat_id, "⚠️ So ADM.", mid)
            try:
                telegram_req("deleteMessage", {"chat_id": chat_id, "message_id": mid})
            except:
                pass
            return

        if cmd == "/ban":
            tgt = msg.get("reply_to_message", {}).get("from", {}).get("id")
            if tgt and not is_protected(chat_id, tgt):
                r = execute_action(chat_id, "BAN", tgt, None, "ban ADM")
                send(chat_id, "🚫 Banido" if r["success"] else "❌ Falha", mid)
            else:
                send(chat_id, "❌ Nao posso banir ADM/bot", mid)
        elif cmd == "/unban":
            if args:
                try:
                    telegram_req("unbanChatMember", {"chat_id": chat_id, "user_id": int(args[0])})
                    send(chat_id, f"✅ Desbanido {args[0]}", mid)
                except:
                    send(chat_id, "❌ Erro ID", mid)
        elif cmd == "/kick":
            tgt = msg.get("reply_to_message", {}).get("from", {}).get("id")
            if tgt and not is_protected(chat_id, tgt):
                r = execute_action(chat_id, "KICK", tgt, None, "kick")
                send(chat_id, "👢 Kickado" if r["success"] else "❌", mid)
        elif cmd == "/mute":
            tgt = msg.get("reply_to_message", {}).get("from", {}).get("id")
            if tgt and not is_protected(chat_id, tgt):
                r = execute_action(chat_id, "MUTE", tgt, None, "mute")
                send(chat_id, "🔇 Mutado" if r["success"] else "❌", mid)
        elif cmd == "/unmute":
            tgt = msg.get("reply_to_message", {}).get("from", {}).get("id")
            r = execute_action(chat_id, "UNMUTE", tgt, None, "unmute")
            send(chat_id, "🔊 Desmutado" if r["success"] else "❌", mid)
        elif cmd == "/delete":
            tgt_mid = msg.get("reply_to_message", {}).get("message_id")
            if tgt_mid:
                telegram_req("deleteMessage", {"chat_id": chat_id, "message_id": tgt_mid})
                send(chat_id, "🗑️ Apagada", mid)
        elif cmd == "/warn":
            tgt = msg.get("reply_to_message", {}).get("from", {}).get("id")
            if tgt and not is_protected(chat_id, tgt):
                execute_action(chat_id, "WARN", tgt, None, "warn ADM")
                c = get_db()
                w = c.execute("SELECT count FROM warnings WHERE chat_id=? AND user_id=?", (str(chat_id), str(tgt))).fetchone()
                c.close()
                cnt = w["count"] if w else 1
                send(chat_id, f"⚠️ Warn {cnt}/{cfg.get('warning_limit',3)}", mid)
                if cnt >= cfg.get("warning_limit", 3):
                    execute_action(chat_id, "BAN", tgt, None, "3 warns")
                    send(chat_id, "🚫 Ban por 3 warns", mid)
        elif cmd == "/unwarn":
            tgt = msg.get("reply_to_message", {}).get("from", {}).get("id")
            if tgt:
                with db_lock:
                    c = get_db()
                    c.execute("DELETE FROM warnings WHERE chat_id=? AND user_id=?", (str(chat_id), str(tgt)))
                    c.commit()
                    c.close()
                send(chat_id, "✅ Zerado", mid)
        elif cmd == "/warnings":
            tgt = msg.get("reply_to_message", {}).get("from", {}).get("id") or uid
            c = get_db()
            w = c.execute("SELECT count FROM warnings WHERE chat_id=? AND user_id=?", (str(chat_id), str(tgt))).fetchone()
            c.close()
            send(chat_id, f"Warns: {w['count'] if w else 0}", mid)
        elif cmd == "/resetwarnings":
            with db_lock:
                c = get_db()
                c.execute("DELETE FROM warnings WHERE chat_id=?", (str(chat_id),))
                c.commit()
                c.close()
            send(chat_id, "✅ Todos warns resetados", mid)
        elif cmd == "/pin":
            tgt_mid = msg.get("reply_to_message", {}).get("message_id")
            if tgt_mid:
                telegram_req("pinChatMessage", {"chat_id": chat_id, "message_id": tgt_mid})
                send(chat_id, "📌 Fixada", mid)
        elif cmd == "/unpin":
            telegram_req("unpinAllChatMessages", {"chat_id": chat_id})
            send(chat_id, "📌 Desfixadas", mid)
        elif cmd == "/allowlink":
            if args:
                cur = cfg.get("allowed_links", "")
                new = cur + "," + args[0] if cur else args[0]
                set_cfg(chat_id, "allowed_links", new)
                send(chat_id, f"✅ Liberado: {args[0]}", mid)
        elif cmd == "/setwelcome":
            if args:
                set_cfg(chat_id, "welcome_msg", " ".join(args))
                send(chat_id, f"✅ Welcome set: {' '.join(args)}", mid)
        elif cmd == "/setgoodbye":
            if args:
                set_cfg(chat_id, "goodbye_msg", " ".join(args))
                send(chat_id, f"✅ Goodbye set", mid)
        elif cmd == "/setsensual":
            if args:
                mode = args[0].lower()
                if mode == "moderado":
                    mode = "moderate"
                set_cfg(chat_id, "sensual_mode", mode)
                set_cfg(chat_id, "anti_sensual", 0 if mode == "livre" else 1)
                send(chat_id, f"✅ +18 = {mode}", mid)
        elif cmd == "/lock":
            set_cfg(chat_id, "lock_group", 1)
            send(chat_id, "🔒 Grupo trancado - so ADM fala", mid)
            telegram_req("setChatPermissions", {"chat_id": chat_id, "permissions": {"can_send_messages": False}})
        elif cmd == "/unlock":
            set_cfg(chat_id, "lock_group", 0)
            send(chat_id, "🔓 Grupo destrancado", mid)
            telegram_req("setChatPermissions", {"chat_id": chat_id, "permissions": {"can_send_messages": True, "can_send_media_messages": True, "can_send_other_messages": True}})
        elif cmd == "/logs":
            c = get_db()
            rows = c.execute("SELECT action,user_id,reason FROM moderation_logs WHERE chat_id=? ORDER BY id DESC LIMIT 10", (str(chat_id),)).fetchall()
            c.close()
            txt = "📜 *Logs:*\n" + "\n".join([f"{r['action']} {r['user_id']} {r['reason'][:30]}" for r in rows]) if rows else "Sem logs"
            send(chat_id, txt, mid)
        try:
            telegram_req("deleteMessage", {"chat_id": chat_id, "message_id": mid})
        except:
            pass
        return

    if uid == BOT_ID or is_admin(chat_id, uid):
        return
    if cfg.get("lock_group"):
        execute_action(chat_id, "DELETE", None, mid, "lock")
        return
    if not text:
        return

    key = (str(chat_id), str(uid))
    now = time.time()
    dq = mem_flood[key]
    dq.append(now)
    while dq and now - dq[0] > cfg.get("flood_window", 15):
        dq.popleft()
    if len(dq) > cfg.get("flood_limit", 7):
        execute_action(chat_id, "MUTE", uid, mid, f"flood {len(dq)}")
        dq.clear()
        return

    mem_texts[key].append(text)
    if len(mem_texts[key]) >= 3 and len(set(mem_texts[key])) == 1:
        execute_action(chat_id, "DELETE", None, mid, "spam repetido")
        return

    ai_res = call_moderation_ai(text)
    sensual = float(ai_res.get("sensual", 0))
    if sensual >= 0.65 and cfg.get("anti_sensual"):
        mode = cfg.get("sensual_mode", "moderate")
        if mode == "restrito" or sensual >= 0.85:
            execute_action(chat_id, "DELETE", None, mid, f"+18 {sensual:.2f}")
            return
        if mode == "moderate" and sensual >= 0.92:
            execute_action(chat_id, "DELETE", None, mid, f"+18 pesado {sensual:.2f}")
            return

    if float(ai_res.get("divulg", 0)) >= 0.75 and cfg.get("anti_divulgation"):
        allowed = [d.strip().lower() for d in (cfg.get("allowed_links", "").split(",")) if d.strip()]
        if not any(d in text.lower() for d in allowed):
            execute_action(chat_id, "DELETE", None, mid, f"divulg {ai_res['divulg']:.2f}")
            return

    if float(ai_res.get("toxic", 0)) >= 0.80:
        execute_action(chat_id, "DELETE", None, mid, f"toxic {ai_res['toxic']:.2f}")
        mem_fight[str(chat_id)].append((str(uid), time.time(), text))
        recent = [f for f in mem_fight[str(chat_id)] if time.time() - f[1] < 30]
        if len(recent) >= 3 and len(set(u for u, _, _ in recent)) >= 2:
            for u, _, _ in recent:
                try:
                    execute_action(chat_id, "MUTE", u, None, "briga")
                except:
                    pass
            send(chat_id, "🤖 Briga detectada! Calma - mute 10min")
            mem_fight[str(chat_id)].clear()
        return

if __name__ == "__main__":
    print(f"[{KLEBER_SIG}] START {ORBIT_CORE}")
    app.run(host="0.0.0.0", port=PORT)
