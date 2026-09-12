# main.py V11.3 ADM COMPLETO + IA TOTAL + MODO NOTURNO DUAL + JSONBIN - FINAL CORRIGIDO
import os, re, time, json, sqlite3, logging, requests, random, base64
from datetime import datetime, timezone
from collections import defaultdict, deque
from flask import Flask, request
from concurrent.futures import ThreadPoolExecutor
import pytz

# ========= CONFIG =========
BOT_NAME = "Orbit Alliance"
CREATOR = "Kleber"
CREATOR_ID = "8398287578"
TIMEZONE = "America/Fortaleza"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN: raise RuntimeError("TELEGRAM_TOKEN não setado")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
DATABASE_PATH = os.getenv("DATABASE_PATH", "Orbit.db")
TZ = pytz.timezone(TIMEZONE)

# --- JSONBIN PERSISTENTE ---
JSONBIN_ID = os.getenv("JSONBIN_ID", "6aa4bd51ffd5d16053fcb4ac")
JSONBIN_KEY = os.getenv("JSONBIN_KEY", "$2a$10$9z0uXXR9IvpYYUNse/8tBehSItQytvdQTLkV6NSvwKhNZ557tfqH6")
JSONBIN_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_ID}"
JB_HEADERS = {"X-Master-Key": JSONBIN_KEY, "Content-Type": "application/json"}

def backup_db_to_jsonbin():
    try:
        if not os.path.exists(DATABASE_PATH): return
        with open(DATABASE_PATH, "rb") as f:
            b64 = base64.b64encode(f.read()).decode('utf-8')
        payload = {"db_base64": b64, "updated_at": datetime.now(timezone.utc).isoformat()}
        requests.put(JSONBIN_URL, json=payload, headers=JB_HEADERS, timeout=15)
    except Exception as e:
        logging.error(f"[BACKUP] Erro {e}")

def restore_db_from_jsonbin():
    try:
        r = requests.get(f"{JSONBIN_URL}/latest", headers=JB_HEADERS, timeout=15)
        if r.status_code == 200:
            record = r.json().get('record', {})
            b64 = record.get('db_base64')
            if b64:
                with open(DATABASE_PATH, "wb") as f:
                    f.write(base64.b64decode(b64))
                logging.info("[RESTORE] Banco restaurado!")
                return True
    except Exception as e:
        logging.error(f"[RESTORE] {e}")
    return False

restore_db_from_jsonbin()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s')
executor = ThreadPoolExecutor(max_workers=4)

# ========= DATABASE =========
def get_db():
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def init_database():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS groups (chat_id TEXT PRIMARY KEY, title TEXT, type TEXT, enabled INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT);
    CREATE TABLE IF NOT EXISTS group_rules (
        chat_id TEXT PRIMARY KEY,
        welcome INTEGER DEFAULT 1, goodbye INTEGER DEFAULT 1,
        welcome_msg TEXT DEFAULT '👋 Seja bem-vindo(a), {name}! Leia as regras e seja respeitoso.',
        goodbye_msg TEXT DEFAULT '👋 {name} saiu do grupo. Até mais!',
        anti_link INTEGER DEFAULT 0, anti_spam INTEGER DEFAULT 1, anti_flood INTEGER DEFAULT 1,
        anti_mention INTEGER DEFAULT 0, anti_divulgation INTEGER DEFAULT 0,
        allowed_links TEXT DEFAULT 'youtube.com,youtu.be,instagram.com,github.com,google.com',
        warning_limit INTEGER DEFAULT 3, moderation_mode TEXT DEFAULT 'moderate',
        flood_limit INTEGER DEFAULT 7, flood_window INTEGER DEFAULT 15,
        auto_actions TEXT DEFAULT '["delete","warn","mute"]',
        night_mode INTEGER DEFAULT 0,
        night_mode_type TEXT DEFAULT 'silent',
        night_start TEXT DEFAULT '22:00',
        night_end TEXT DEFAULT '06:00',
        updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS warnings (chat_id TEXT, user_id TEXT, count INTEGER DEFAULT 0, last_reason TEXT, updated_at TEXT, PRIMARY KEY(chat_id, user_id));
    CREATE TABLE IF NOT EXISTS moderation_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, user_id TEXT, action TEXT, reason TEXT, message_id INTEGER, source TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS flood_control (chat_id TEXT, user_id TEXT, timestamps TEXT, PRIMARY KEY(chat_id, user_id));
    CREATE TABLE IF NOT EXISTS mention_control (chat_id TEXT, user_id TEXT, timestamps TEXT, PRIMARY KEY(chat_id, user_id));
    """)
    conn.commit(); conn.close()
    try:
        conn=get_db()
        for col in ["night_mode INTEGER DEFAULT 0", "night_mode_type TEXT DEFAULT 'silent'", "night_start TEXT DEFAULT '22:00'", "night_end TEXT DEFAULT '06:00'"]:
            try: conn.execute(f"ALTER TABLE group_rules ADD COLUMN {col}")
            except: pass
        conn.commit(); conn.close()
    except: pass
    executor.submit(backup_db_to_jsonbin)
init_database()

def get_group_config(chat_id):
    conn = get_db(); cid=str(chat_id)
    row = conn.execute("SELECT * FROM group_rules WHERE chat_id=?", (cid,)).fetchone()
    if not row:
        now=datetime.now(timezone.utc).isoformat()
        conn.execute("INSERT OR IGNORE INTO groups(chat_id,created_at,updated_at) VALUES(?,?,?)", (cid, now, now))
        conn.execute("INSERT OR IGNORE INTO group_rules(chat_id,updated_at) VALUES(?,?)", (cid, now))
        conn.commit()
        row = conn.execute("SELECT * FROM group_rules WHERE chat_id=?", (cid,)).fetchone()
        conn.close()
        executor.submit(backup_db_to_jsonbin)
    else:
        conn.close()
    return dict(row) if row else {}

def set_rule(chat_id, rule, value):
    allowed=["welcome","goodbye","anti_link","anti_spam","anti_flood","anti_mention","anti_divulgation","warning_limit","moderation_mode","flood_limit","flood_window","welcome_msg","goodbye_msg","allowed_links","auto_actions","night_mode","night_mode_type","night_start","night_end"]
    if rule not in allowed: return False
    conn=get_db(); now=datetime.now(timezone.utc).isoformat()
    if rule in ["welcome_msg","goodbye_msg","allowed_links","auto_actions","moderation_mode","night_mode_type","night_start","night_end"]:
        conn.execute(f"UPDATE group_rules SET {rule}=?, updated_at=? WHERE chat_id=?", (str(value), now, str(chat_id)))
    else:
        if isinstance(value,bool): v=1 if value else 0
        elif str(value).lower() in ["on","true","1","sim","ativar","ativo","ligado"]: v=1
        elif str(value).lower() in ["off","false","0","nao","desativar","desativo","desligado"]: v=0
        else:
            try: v=int(value)
            except: v=value
        conn.execute(f"UPDATE group_rules SET {rule}=?, updated_at=? WHERE chat_id=?", (v, now, str(chat_id)))
    conn.commit(); conn.close()
    executor.submit(backup_db_to_jsonbin)
    return True

# ========= TELEGRAM API =========
def telegram_request(method, payload=None, timeout=10):
    try:
        url=f"{TELEGRAM_API_URL}/{method}"
        r=requests.post(url, json=payload, timeout=timeout) if payload else requests.get(url, timeout=timeout)
        data=r.json()
        if r.status_code==429:
            time.sleep(data.get("parameters",{}).get("retry_after",2))
            return telegram_request(method, payload, timeout)
        return data
    except Exception as e:
        logging.error(f"TG {method} {e}"); return {"ok":False}

BOT_ID=None; BOT_USERNAME=None
def init_bot_info():
    global BOT_ID, BOT_USERNAME
    d=telegram_request("getMe")
    if d.get("ok"): BOT_ID=d["result"]["id"]; BOT_USERNAME=d["result"]["username"].lower()
init_bot_info()

def send_message(chat_id, text, reply_to=None):
    if not text: return False
    payload={"chat_id":chat_id,"text":str(text)[:4096],"parse_mode":"Markdown"}
    if reply_to: payload["reply_to_message_id"]=reply_to
    return telegram_request("sendMessage", payload).get("ok", False)
def delete_message(chat_id, mid): return telegram_request("deleteMessage", {"chat_id":chat_id,"message_id":mid}).get("ok", False)
def restrict_user(chat_id, uid, sec=600): return telegram_request("restrictChatMember", {"chat_id":chat_id,"user_id":uid,"permissions":{"can_send_messages":False},"until_date":int(time.time())+sec}).get("ok",False)
def unrestrict_user(chat_id, uid): return telegram_request("restrictChatMember", {"chat_id":chat_id,"user_id":uid,"permissions":{"can_send_messages":True,"can_send_media_messages":True,"can_send_other_messages":True,"can_add_web_page_previews":True}}).get("ok",False)
def ban_user(chat_id, uid): return telegram_request("banChatMember", {"chat_id":chat_id,"user_id":uid}).get("ok",False)
def unban_user(chat_id, uid): return telegram_request("unbanChatMember", {"chat_id":chat_id,"user_id":uid}).get("ok",False)
def kick_user(chat_id, uid): ok=ban_user(chat_id,uid); (unban_user(chat_id,uid) if ok else None); return ok
def get_chat_member(chat_id, uid):
    r=telegram_request("getChatMember", {"chat_id":chat_id,"user_id":uid}); return r.get("result") if r.get("ok") else None

# ========= PERMISSION CHECKER =========
def is_bot_admin(chat_id): m=get_chat_member(chat_id,BOT_ID); return m and m["status"] in ["administrator","creator"]
def bot_can(chat_id, perm):
    m=get_chat_member(chat_id,BOT_ID)
    if not m: return False
    if m["status"]=="creator": return True
    return m.get(perm, False)
def is_target_admin(chat_id, uid): m=get_chat_member(chat_id,uid); return m and m["status"] in ["administrator","creator"]
def is_protected(chat_id, uid): return str(uid)==str(BOT_ID) or str(uid)==CREATOR_ID or is_target_admin(chat_id,uid)
def log_action(chat_id, uid, action, reason, mid=None, source="AUTO"):
    conn=get_db(); conn.execute("INSERT INTO moderation_logs(chat_id,user_id,action,reason,message_id,source,created_at) VALUES(?,?,?,?,?,?,?)",(str(chat_id),str(uid),action,reason,mid,source,datetime.now(timezone.utc).isoformat())); conn.commit(); conn.close()
    executor.submit(backup_db_to_jsonbin)

# ========= WARNINGS =========
def add_warning(chat_id, uid, reason=""):
    conn=get_db(); row=conn.execute("SELECT count FROM warnings WHERE chat_id=? AND user_id=?",(str(chat_id),str(uid))).fetchone(); c=(row["count"] if row else 0)+1
    conn.execute("INSERT OR REPLACE INTO warnings(chat_id,user_id,count,last_reason,updated_at) VALUES(?,?,?,?,?)",(str(chat_id),str(uid),c,reason,datetime.now(timezone.utc).isoformat())); conn.commit(); conn.close()
    executor.submit(backup_db_to_jsonbin)
    return c
def get_warnings(chat_id, uid):
    conn=get_db(); row=conn.execute("SELECT * FROM warnings WHERE chat_id=? AND user_id=?",(str(chat_id),str(uid))).fetchone(); conn.close(); return dict(row) if row else {"count":0}
def clear_warnings(chat_id, uid): conn=get_db(); conn.execute("DELETE FROM warnings WHERE chat_id=? AND user_id=?",(str(chat_id),str(uid))); conn.commit(); conn.close(); executor.submit(backup_db_to_jsonbin)

# ========= AI ENGINE V12 - PROVIDERS_RAW + FALLBACK =========
PROVIDERS_RAW = {
    "groq": {"key_env": "GROQ_API_KEY", "endpoint": "https://api.groq.com/openai/v1/chat/completions", "format": "openai", "timeout": 6},
    "gemini": {"key_env": "GEMINI_API_KEY", "endpoint": "https://generativelanguage.googleapis.com/v1beta", "format": "gemini", "timeout": 8},
    "cerebras": {"key_env": "CEREBRAS_API_KEY", "endpoint": "https://api.cerebras.ai/v1/chat/completions", "format": "openai", "timeout": 6},
    "openrouter": {"key_env": "OPENROUTER_API_KEY", "endpoint": "https://openrouter.ai/api/v1/chat/completions", "format": "openai", "timeout": 8},
}

FALLBACK_MODELS = {
    "groq": ["llama-3.3-70b-versatile","llama-3.1-8b-instant"],
    "gemini": ["gemini-2.0-flash","gemini-1.5-flash"],
    "cerebras": ["llama-3.3-70b","llama3.1-8b"],
    "openrouter": ["meta-llama/llama-3.1-8b-instruct:free"],
}

def build_providers():
    m={}
    for name,cfg in PROVIDERS_RAW.items():
        key=os.getenv(cfg["key_env"])
        if key: m[name]=cfg
    return m
PROVIDERS=build_providers()

def call_ai(prompt, timeout=7, max_tokens=250):
    if not PROVIDERS: return None
    for prov_name in ["groq","cerebras","gemini","openrouter"]:
        if prov_name not in PROVIDERS: continue
        cfg=PROVIDERS[prov_name]
        key=os.getenv(cfg["key_env"])
        for model in FALLBACK_MODELS.get(prov_name,[]):
            try:
                if cfg["format"]=="gemini":
                    url=f"{cfg['endpoint']}/models/{model}:generateContent?key={key}"
                    r=requests.post(url, json={"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"maxOutputTokens":max_tokens}}, timeout=timeout)
                    if r.status_code==200: return r.json()["candidates"][0]["content"]["parts"][0]["text"]
                else:
                    headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"}
                    payload={"model":model,"messages":[{"role":"user","content":prompt}],"temperature":0.2,"max_tokens":max_tokens}
                    r=requests.post(cfg["endpoint"], json=payload, headers=headers, timeout=timeout)
                    if r.status_code==200: return r.json()["choices"][0]["message"]["content"]
            except: continue
    return None

def ai_moderator_classify(text, rules):
    prompt=f"""Você é classificador de moderação. Analise mensagem de grupo Telegram.
REGRAS ATIVAS: anti_link={rules.get('anti_link')} anti_divulgation={rules.get('anti_divulgation')} modo={rules.get('moderation_mode')}
MENSAGEM: "{text[:600]}"
Responda SOMENTE JSON: {{"category":"spam|divulgacao|link|ofensa|normal","confidence":0.0-1.0,"action":"IGNORE|DELETE|WARN|MUTE|BAN","reason":"motivo_curto"}}
"""
    raw=call_ai(prompt, max_tokens=180)
    if not raw: return {"category":"normal","confidence":0.4,"action":"IGNORE","reason":"ia_off"}
    try:
        m=re.search(r"\{.*\}", raw, re.DOTALL)
        if not m: return {"category":"normal","confidence":0.4,"action":"IGNORE","reason":"parse_fail"}
        data=json.loads(m.group(0))
        if data.get("action") not in ["IGNORE","DELETE","WARN","MUTE","KICK","BAN"]: data["action"]="IGNORE"
        data["confidence"]=max(0.0,min(1.0,float(data.get("confidence",0.5))))
        return data
    except: return {"category":"normal","confidence":0.4,"action":"IGNORE","reason":"json_fail"}

def ai_natural_config(text):
    prompt=f"""Você é tradutor de comandos de BOT ADM. Converta pedido do ADM em JSON.
Pedido: "{text}"
REGRAS: welcome, goodbye, anti_link, anti_spam, anti_flood, anti_mention, anti_divulgation, night_mode, night_mode_type (silent/strict), moderation_mode (observer/moderate/strict/auto), warning_limit, flood_limit, flood_window, welcome_msg, goodbye_msg, allowed_links, night_start (22:00), night_end (06:00)
Exemplos: "ativa o anti link" -> {{"rule":"anti_link","value":true}} | "ativa modo noturno" -> {{"rule":"night_mode","value":true}} | "modo noturno silencioso" -> {{"rule":"night_mode_type","value":"silent"}}
Retorne SOMENTE JSON. Se não for config: {{"rule":null}}
"""
    raw=call_ai(prompt, timeout=6, max_tokens=180)
    if not raw: return None
    try:
        m=re.search(r"\{.*\}", raw, re.DOTALL)
        return json.loads(m.group(0)) if m else None
    except: return None

# ========= ANTI-ENGINES + NIGHT =========
LINK_RE=re.compile(r"(https?://|www\.|t\.me/|telegram\.me/|joinchat)",re.I)
MENTION_RE=re.compile(r"@\w+")
flood_cache=defaultdict(lambda: deque(maxlen=20))

def check_link(text, allowed_links_str):
    if not text or not LINK_RE.search(text): return False
    low=text.lower()
    allowed=[a.strip().lower() for a in (allowed_links_str or "").split(",") if a.strip()]
    for a in allowed:
        if a and a in low: return False
    return True

def check_flood(chat_id, user_id, limit, window):
    conn=get_db(); row=conn.execute("SELECT timestamps FROM flood_control WHERE chat_id=? AND user_id=?",(str(chat_id),str(user_id))).fetchone(); now=time.time()
    times=json.loads(row["timestamps"]) if row and row["timestamps"] else []; times=[t for t in times if now-t<window]; times.append(now)
    conn.execute("INSERT OR REPLACE INTO flood_control(chat_id,user_id,timestamps) VALUES(?,?,?)",(str(chat_id),str(user_id),json.dumps(times))); conn.commit(); conn.close()
    return len(times)>=limit

def check_mention_spam(chat_id, user_id, text, limit=5, window=30):
    if not text: return False
    count=len(MENTION_RE.findall(text))
    if count<3: return False
    conn=get_db(); row=conn.execute("SELECT timestamps FROM mention_control WHERE chat_id=? AND user_id=?",(str(chat_id),str(user_id))).fetchone(); now=time.time()
    times=json.loads(row["timestamps"]) if row and row["timestamps"] else []; times=[t for t in times if now-t<window]
    times.extend([now]*count)
    conn.execute("INSERT OR REPLACE INTO mention_control(chat_id,user_id,timestamps) VALUES(?,?,?)",(str(chat_id),str(user_id),json.dumps(times))); conn.commit(); conn.close()
    return len(times)>=limit

def check_spam(text, cache_key):
    if not text: return False
    q=flood_cache[cache_key]
    if len(q)>=3 and all(t==text for t in list(q)[-3:]): return True
    if len(text)>600 and len(set(text))<12: return True
    q.append(text); return False

def is_night_time(start_str, end_str):
    try:
        now = datetime.now(TZ)
        s_h, s_m = map(int, start_str.split(":"))
        e_h, e_m = map(int, end_str.split(":"))
        start = now.replace(hour=s_h, minute=s_m, second=0, microsecond=0)
        end = now.replace(hour=e_h, minute=e_m, second=0, microsecond=0)
        if start <= end: return start <= now <= end
        else: return now >= start or now <= end
    except: return False

def handle_night_mode(msg, rules):
    if not rules.get("night_mode"): return False
    if not is_night_time(rules.get("night_start","22:00"), rules.get("night_end","06:00")): return False
    chat_id=msg["chat"]["id"]; user_id=msg["from"]["id"]
    if is_protected(chat_id, user_id): return False
    n_type = rules.get("night_mode_type","silent")
    if n_type == "silent":
        if bot_can(chat_id,"can_delete_messages"):
            delete_message(chat_id, msg["message_id"])
            ck=f"night_warn_{chat_id}"
            if ck not in flood_cache or (len(flood_cache[ck])==0 or time.time()-flood_cache[ck][-1]>3600):
                send_message(chat_id,f"🌙 *Modo Noturno Silencioso ativo* 😴\nGrupo fechado até {rules.get('night_end','06:00')}. Só ADMs podem falar.")
                flood_cache[ck].append(time.time())
        return True
    elif n_type == "strict":
        text = msg.get("text") or ""
        if check_link(text, rules.get("allowed_links","")) or check_flood(chat_id, user_id, 3, 15):
            if bot_can(chat_id,"can_delete_messages"): delete_message(chat_id, msg["message_id"])
            return True
    return False

# ========= DECISION ENGINE =========
def make_decision(event_type, context, rules, ai_result=None):
    text=context.get("text",""); mode=rules.get("moderation_mode","moderate")
    try: auto_actions=json.loads(rules.get("auto_actions",'["delete","warn","mute"]'))
    except: auto_actions=["delete","warn","mute"]
    if mode=="observer": return "IGNORE","observer",1.0
    if event_type in ["NEW_MEMBER","LEFT_MEMBER"]: return "IGNORE","evento",1.0
    if rules.get("anti_flood") and check_flood(context["chat_id"], context["user_id"], rules.get("flood_limit",7), rules.get("flood_window",15)):
        act="MUTE" if "mute" in auto_actions else "WARN"
        return act,"flood",0.96
    if rules.get("anti_mention") and check_mention_spam(context["chat_id"], context["user_id"], text):
        return "MUTE","mention_spam",0.9
    if rules.get("anti_link") and check_link(text, rules.get("allowed_links","")):
        act="DELETE" if "delete" in auto_actions else "WARN"
        return act,"link_nao_permitido",0.92
    if rules.get("anti_spam") and check_spam(text, f"{context['chat_id']}_{context['user_id']}"):
        return "WARN","spam_repetitivo",0.85
    if rules.get("anti_divulgation") and len(text)>20:
        if not ai_result: ai_result=ai_moderator_classify(text, rules)
        if ai_result["category"] in ["divulgacao","spam"] and ai_result["confidence"]>=0.75:
            min_conf=0.65 if mode=="strict" else 0.75
            if ai_result["confidence"]>=min_conf:
                desired=ai_result["action"].lower()
                if desired in auto_actions or desired=="delete":
                    return ai_result["action"], ai_result["reason"], ai_result["confidence"]
                else:
                    return "DELETE", ai_result["reason"], ai_result["confidence"]
    if mode=="auto" and len(text)>15:
        if not ai_result: ai_result=ai_moderator_classify(text, rules)
        if ai_result["confidence"]>=0.88 and ai_result["action"]!="IGNORE":
            return ai_result["action"], ai_result["reason"], ai_result["confidence"]
    return "IGNORE","sem_violacao",1.0

# ========= WELCOME / GOODBYE =========
def handle_new_members(msg):
    chat_id=msg["chat"]["id"]; rules=get_group_config(chat_id)
    if not rules.get("welcome"): return
    for member in msg.get("new_chat_members",[]):
        if member["id"]==BOT_ID:
            send_message(chat_id,f"🪐 Orbit Alliance V12 online! Me promova a *ADM* com apagar e banir. Sou ADM com IA: entendo comandos e português natural. Use /help")
            continue
        name=member.get("first_name","pessoa")
        txt=rules.get("welcome_msg","👋 Seja bem-vindo(a), {name}!").format(name=name, title=msg["chat"].get("title",""))
        send_message(chat_id,txt)
        log_action(chat_id, member["id"], "WELCOME", "entrou", source="SYSTEM")

def handle_left_member(msg):
    chat_id=msg["chat"]["id"]; rules=get_group_config(chat_id)
    if not rules.get("goodbye"): return
    member=msg.get("left_chat_member")
    if not member or member["id"]==BOT_ID: return
    txt=rules.get("goodbye_msg","👋 {name} saiu.").format(name=member.get("first_name",""), title=msg["chat"].get("title",""))
    send_message(chat_id,txt)
    log_action(chat_id, member["id"], "GOODBYE", "saiu", source="SYSTEM")

# ========= COMANDOS =========
def parse_target(msg):
    if "reply_to_message" in msg and msg["reply_to_message"].get("from"): return msg["reply_to_message"]["from"]["id"]
    parts=(msg.get("text") or "").split()
    if len(parts)>=2 and parts[1].isdigit(): return int(parts[1])
    return None

def handle_command(msg):
    chat_id=msg["chat"]["id"]; user_id=msg["from"]["id"]; text=(msg.get("text") or "").strip()
    cmd=text.split()[0].lower().replace(f"@{BOT_USERNAME or ''}","").replace(f"@{BOT_NAME.lower()}","")

    if cmd=="/start":
        send_message(chat_id,f"🪐 *{BOT_NAME} - ADM Inteligente V12*\n\n*1️⃣ COMANDOS:* /antilink on /night silent /night 22:00 06:00\n*2️⃣ PT-BR (IA):* \"ativa o anti link\" \"ativa modo noturno das 22:00 as 06:00\"\nMe promova a ADM. Use /help")
        return

    if cmd=="/help":
        if is_target_admin(chat_id,user_id) or str(user_id)==CREATOR_ID:
            send_message(chat_id,"👮 *ORBIT ADM V12*\n\n*💬 FALE COMIGO (IA)*\n• \"liga o anti link\"\n• \"ativa modo noturno das 21:00 as 05:00\"\n• \"modo noturno silencioso / rigoroso\"\n• \"desativa modo noturno\"\n• \"ativa boas vindas\"\n\n*⌨️ COMANDOS*\n/warn /mute /ban /kick /delete\n/antilink /antispam /antiflood /antidivulga\n/night on/off/silent/strict /night 22:00 06:00\n/rules /status /logs")
        return

    if msg["chat"]["type"]!="private" and not is_target_admin(chat_id,user_id) and str(user_id)!=CREATOR_ID:
        return

    if cmd in ["/rules","/config"]:
        r=get_group_config(chat_id)
        send_message(chat_id,f"⚙️ *{msg['chat'].get('title','Grupo')} V12*\nWelcome:{'✅' if r.get('welcome') else '❌'} Anti-link:{'✅' if r.get('anti_link') else '❌'} Divulgação:{'✅' if r.get('anti_divulgation') else '❌'}\n🌙 Noturno:{'✅ '+r.get('night_mode_type') if r.get('night_mode') else '❌'} {r.get('night_start')}-{r.get('night_end')}"); return

    if cmd=="/status":
        r=get_group_config(chat_id); bot_adm=is_bot_admin(chat_id)
        perms=f"Del:{'✅' if bot_can(chat_id,'can_delete_messages') else '❌'} Ban:{'✅' if bot_can(chat_id,'can_restrict_members') else '❌'}"
        send_message(chat_id,f"🤖 *STATUS V12*\nBot ADM:{'✅' if bot_adm else '❌'}\n{perms}\nIA:{list(PROVIDERS.keys()) or 'regex'}\n🌙 Noturno:{'✅ '+r.get('night_mode_type') if r.get('night_mode') else '❌'} {r.get('night_start')}-{r.get('night_end')}"); return

    if cmd.startswith("/night"):
        parts=text.lower().split()
        times=re.findall(r"\d{1,2}:\d{2}", text)
        if len(times)>=2:
            set_rule(chat_id,"night_start",times[0]); set_rule(chat_id,"night_end",times[1]); set_rule(chat_id,"night_mode",True)
            send_message(chat_id,f"🌙 Horário noturno: {times[0]} às {times[1]} ✅"); return
        if "off" in parts or "desativar" in parts or "desliga" in parts:
            set_rule(chat_id,"night_mode",False); send_message(chat_id,"🌙 Modo noturno ❌ DESATIVADO"); return
        if "silent" in parts or "silencioso" in parts:
            set_rule(chat_id,"night_mode",True); set_rule(chat_id,"night_mode_type","silent"); send_message(chat_id,"🌙 Modo noturno *SILENCIOSO* ✅"); return
        if "strict" in parts or "rigoroso" in parts:
            set_rule(chat_id,"night_mode",True); set_rule(chat_id,"night_mode_type","strict"); send_message(chat_id,"🌙 Modo noturno *RIGOROSO* ✅"); return
        if "on" in parts or "ativar" in parts or "liga" in parts:
            set_rule(chat_id,"night_mode",True); send_message(chat_id,f"🌙 Modo noturno ✅ {get_group_config(chat_id).get('night_start')} às {get_group_config(chat_id).get('night_end')}"); return
        r=get_group_config(chat_id)
        send_message(chat_id,f"🌙 *Noturno:* {'✅ ON' if r.get('night_mode') else '❌ OFF'} {r.get('night_mode_type')} {r.get('night_start')}-{r.get('night_end')}\nUse /night on/off/silent/strict /night 22:00 06:00"); return

    if cmd.startswith("/warn"):
        tid=parse_target(msg)
        if not tid: send_message(chat_id,"Responda com /warn"); return
        if is_protected(chat_id,tid): send_message(chat_id,"⚠️ Protegido"); return
        reason=" ".join(text.split()[1:]) or "sem motivo"; c=add_warning(chat_id,tid,reason); lim=get_group_config(chat_id).get("warning_limit",3)
        send_message(chat_id,f"⚠️ {c}/{lim} - {reason}")
        if c>=lim and bot_can(chat_id,"can_restrict_members"): restrict_user(chat_id,tid,600)
        return
    if cmd.startswith("/unwarn"): tid=parse_target(msg); clear_warnings(chat_id,tid); send_message(chat_id,"✅ Warnings zerados"); return
    if cmd.startswith("/mute"):
        tid=parse_target(msg)
        if not tid or is_protected(chat_id,tid): return
        if not bot_can(chat_id,"can_restrict_members"): return
        restrict_user(chat_id,tid,3600); send_message(chat_id,"🔇 Silenciado 1h"); return
    if cmd.startswith("/unmute"): tid=parse_target(msg); unrestrict_user(chat_id,tid); send_message(chat_id,"🔊 Unmute"); return
    if cmd.startswith("/ban"):
        tid=parse_target(msg)
        if not tid or is_protected(chat_id,tid): return
        ban_user(chat_id,tid); send_message(chat_id,"🚫 Banido"); return
    if cmd.startswith("/unban"): tid=parse_target(msg); unban_user(chat_id,tid); send_message(chat_id,"✅ Desbanido"); return
    if cmd.startswith("/kick"):
        tid=parse_target(msg)
        if not tid or is_protected(chat_id,tid): return
        kick_user(chat_id,tid); send_message(chat_id,"👢 Kick"); return
    if cmd.startswith("/delete") or cmd.startswith("/del"):
        if "reply_to_message" in msg: delete_message(chat_id,msg["reply_to_message"]["message_id"]); delete_message(chat_id,msg["message_id"])
        return
    if cmd.startswith("/antilink"): val="on" in text.lower() or "ativar" in text.lower(); set_rule(chat_id,"anti_link",val); send_message(chat_id,f"Anti-link {'✅ ON' if val else '❌ OFF'}"); return
    if cmd.startswith("/antispam"): val="on" in text.lower(); set_rule(chat_id,"anti_spam",val); send_message(chat_id,f"Anti-spam {'✅' if val else '❌'}"); return
    if cmd.startswith("/antiflood"): val="on" in text.lower(); set_rule(chat_id,"anti_flood",val); send_message(chat_id,f"Anti-flood {'✅' if val else '❌'}"); return
    if cmd.startswith("/antimention"): val="on" in text.lower(); set_rule(chat_id,"anti_mention",val); send_message(chat_id,f"Anti-mention {'✅' if val else '❌'}"); return
    if cmd.startswith("/antidivulga"): val="on" in text.lower(); set_rule(chat_id,"anti_divulgation",val); send_message(chat_id,f"Anti-divulgação {'✅' if val else '❌'}"); return
    if cmd.startswith("/welcome"): val="on" in text.lower(); set_rule(chat_id,"welcome",val); send_message(chat_id,f"Welcome {'✅' if val else '❌'}"); return
    if cmd.startswith("/goodbye"): val="on" in text.lower(); set_rule(chat_id,"goodbye",val); send_message(chat_id,f"Goodbye {'✅' if val else '❌'}"); return
    if cmd.startswith("/setwelcome"):
        new_msg=text.replace("/setwelcome","").strip()
        set_rule(chat_id,"welcome_msg",new_msg); send_message(chat_id,f"✅ Welcome definido"); return
    if cmd.startswith("/setgoodbye"):
        new_msg=text.replace("/setgoodbye","").strip()
        set_rule(chat_id,"goodbye_msg",new_msg); send_message(chat_id,f"✅ Goodbye definido"); return
    if cmd.startswith("/mode"):
        m=text.split()[-1].lower(); mapa={"moderado":"moderate","rigido":"strict","observer":"observer","moderate":"moderate","strict":"strict","auto":"auto"}
        if m in mapa: set_rule(chat_id,"moderation_mode",mapa[m]); send_message(chat_id,f"✅ Modo {mapa[m]}")
        return
    if cmd.startswith("/allowlink"):
        link=text.replace("/allowlink","").strip().lower()
        cur=get_group_config(chat_id).get("allowed_links",""); new=cur+","+link if cur else link; set_rule(chat_id,"allowed_links",new); send_message(chat_id,f"✅ Link permitido: {link}"); return

    # ========= IA NATURAL TOTAL + REGEX HORÁRIO =========
    if len(text)>5:
        times=re.findall(r"\d{1,2}:\d{2}", text)
        if len(times)>=2 and "noturno" in text.lower():
            set_rule(chat_id,"night_start",times[0])
            set_rule(chat_id,"night_end",times[1])
            set_rule(chat_id,"night_mode",True)
            if "silenc" in text.lower(): set_rule(chat_id,"night_mode_type","silent")
            if "rigor" in text.lower(): set_rule(chat_id,"night_mode_type","strict")
            send_message(chat_id,f"🌙 *IA entendeu:* modo noturno {times[0]} às {times[1]} ✅\nVocê disse: \"{text}\"")
            return
        cfg=ai_natural_config(text)
        if cfg and cfg.get("rule"):
            rule=cfg["rule"]; val=cfg["value"]
            if rule in ["anti_link","anti_spam","anti_flood","anti_divulgation","anti_mention","welcome","goodbye","night_mode"]:
                set_rule(chat_id,rule,bool(val)); status="✅ ATIVADO" if val else "❌ DESATIVADO"
                send_message(chat_id,f"🤖 *IA entendeu:* {rule} = {status}\nVocê disse: \"{text}\""); return
            if rule in ["moderation_mode","night_mode_type"]:
                set_rule(chat_id,rule,val); send_message(chat_id,f"🤖 *IA entendeu:* {rule} = *{val}*\nVocê disse: \"{text}\""); return
            if rule in ["welcome_msg","goodbye_msg","night_start","night_end"]:
                set_rule(chat_id,rule,val); send_message(chat_id,f"🤖 *IA entendeu:* {rule} = {val}\nVocê disse: \"{text}\""); return
            if rule in ["warning_limit","flood_limit","flood_window"]:
                try: set_rule(chat_id,rule,int(val)); send_message(chat_id,f"🤖 *IA entendeu:* {rule} = {val}"); return
                except: pass
            if rule=="allowed_links":
                cur=get_group_config(chat_id).get("allowed_links",""); new=cur+","+str(val) if cur else str(val)
                set_rule(chat_id,"allowed_links",new); send_message(chat_id,f"🤖 *IA entendeu:* Liberei link *{val}*"); return

# ========= MESSAGE HANDLER =========
def handle_message(msg):
    if msg["from"].get("is_bot"): return
    chat_id=msg["chat"]["id"]; user_id=msg["from"]["id"]; text=msg.get("text") or msg.get("caption") or ""
    if text.startswith("/"): handle_command(msg); return
    if is_protected(chat_id,user_id):
        if len(text)>5 and ("ativa" in text.lower() or "desativa" in text.lower() or "liga" in text.lower() or "noturno" in text.lower() or "anti" in text.lower() or "modo" in text.lower() or "muda" in text.lower() or "libera" in text.lower()):
            handle_command(msg)
        return
    rules=get_group_config(chat_id)
    if not rules: return
    if handle_night_mode(msg, rules):
        log_action(chat_id,user_id,"NIGHT_MODE",f"bloqueio {rules.get('night_mode_type')}",msg["message_id"],"AUTO")
        return
    ctx={"chat_id":chat_id,"user_id":user_id,"text":text,"message_id":msg["message_id"]}
    action, reason, conf = make_decision("MESSAGE", ctx, rules)
    if conf<0.8 and action in ["BAN","KICK"]: action="WARN"
    if rules.get("moderation_mode")=="observer":
        if action!="IGNORE": log_action(chat_id,user_id,f"SIM_{action}",reason,msg["message_id"],"SIMULATION")
        return
    try: auto_actions=json.loads(rules.get("auto_actions",'["delete","warn","mute"]'))
    except: auto_actions=["delete","warn","mute"]
    if action=="IGNORE": return
    if action=="DELETE":
        if not bot_can(chat_id,"can_delete_messages"): return
        delete_message(chat_id,msg["message_id"])
    elif action=="WARN":
        c=add_warning(chat_id,user_id,reason); lim=rules.get("warning_limit",3)
        if bot_can(chat_id,"can_delete_messages"): delete_message(chat_id,msg["message_id"])
        send_message(chat_id,f"⚠️ Advertência {c}/{lim} - {reason}")
        if c>=lim and bot_can(chat_id,"can_restrict_members"): restrict_user(chat_id,user_id,600)
    elif action=="MUTE":
        if not bot_can(chat_id,"can_restrict_members"): return
        if bot_can(chat_id,"can_delete_messages"): delete_message(chat_id,msg["message_id"])
        restrict_user(chat_id,user_id,600); send_message(chat_id,f"🔇 Silenciado 10min - {reason}")
    elif action=="BAN":
        if not bot_can(chat_id,"can_restrict_members"): return
        if conf<0.92: restrict_user(chat_id,user_id,3600); return
        ban_user(chat_id,user_id); send_message(chat_id,f"🚫 Banido - {reason}")

def process_update(update):
    try:
        if "message" in update:
            m=update["message"]
            if "new_chat_members" in m: handle_new_members(m)
            elif "left_chat_member" in m: handle_left_member(m)
            else: handle_message(m)
        elif "edited_message" in update: handle_message(update["edited_message"])
        elif "my_chat_member" in update:
            chat=update["my_chat_member"]["chat"]; conn=get_db(); now=datetime.now(timezone.utc).isoformat()
            conn.execute("INSERT OR IGNORE INTO groups(chat_id,title,type,created_at,updated_at) VALUES(?,?,?,?,?)",(str(chat["id"]),chat.get("title",""),chat.get("type",""),now,now)); conn.commit(); conn.close(); get_group_config(chat["id"])
    except Exception as e: logging.exception(f"update {e}")

@app.route('/')
def index(): return f"{BOT_NAME} ADM V12 FINAL ONLINE | IA:{list(PROVIDERS.keys()) or 'regex'}",200
@app.route('/health')
def health(): return "ok",200
@app.route(f'/{TELEGRAM_TOKEN}', methods=['POST'])
def webhook():
    data=request.get_json()
    if data: executor.submit(process_update, data)
    return "ok",200

if __name__=='__main__': app.run(host='0.0.0.0', port=int(os.environ.get("PORT",8080)))
