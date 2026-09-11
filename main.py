# main.py V11.1 ADM COMPLETO - Matheus BOT ADM
# Base: docs/telegram_bot_adm_v1 + motor IA V10.2 reaproveitado
import os, re, time, json, sqlite3, logging, requests, random
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
DATABASE_PATH = os.getenv("DATABASE_PATH", "matheus.db")
TZ = pytz.timezone(TIMEZONE)

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
        auto_actions TEXT DEFAULT '["delete","warn","mute"]', updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS warnings (chat_id TEXT, user_id TEXT, count INTEGER DEFAULT 0, last_reason TEXT, updated_at TEXT, PRIMARY KEY(chat_id, user_id));
    CREATE TABLE IF NOT EXISTS moderation_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, user_id TEXT, action TEXT, reason TEXT, message_id INTEGER, source TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS flood_control (chat_id TEXT, user_id TEXT, timestamps TEXT, PRIMARY KEY(chat_id, user_id));
    CREATE TABLE IF NOT EXISTS mention_control (chat_id TEXT, user_id TEXT, timestamps TEXT, PRIMARY KEY(chat_id, user_id));
    """)
    conn.commit(); conn.close()
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
    return dict(row) if row else {}

def set_rule(chat_id, rule, value):
    allowed=["welcome","goodbye","anti_link","anti_spam","anti_flood","anti_mention","anti_divulgation","warning_limit","moderation_mode","flood_limit","flood_window","welcome_msg","goodbye_msg","allowed_links","auto_actions"]
    if rule not in allowed: return False
    conn=get_db(); now=datetime.now(timezone.utc).isoformat()
    if rule in ["welcome_msg","goodbye_msg","allowed_links","auto_actions","moderation_mode"]:
        conn.execute(f"UPDATE group_rules SET {rule}=?, updated_at=? WHERE chat_id=?", (str(value), now, str(chat_id)))
    else:
        if isinstance(value,bool): v=1 if value else 0
        elif str(value).lower() in ["on","true","1","sim","ativar"]: v=1
        elif str(value).lower() in ["off","false","0","nao","desativar"]: v=0
        else:
            try: v=int(value)
            except: v=value
        conn.execute(f"UPDATE group_rules SET {rule}=?, updated_at=? WHERE chat_id=?", (v, now, str(chat_id)))
    conn.commit(); conn.close(); return True

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

# ========= WARNINGS =========
def add_warning(chat_id, uid, reason=""):
    conn=get_db(); row=conn.execute("SELECT count FROM warnings WHERE chat_id=? AND user_id=?",(str(chat_id),str(uid))).fetchone(); c=(row["count"] if row else 0)+1
    conn.execute("INSERT OR REPLACE INTO warnings(chat_id,user_id,count,last_reason,updated_at) VALUES(?,?,?,?,?)",(str(chat_id),str(uid),c,reason,datetime.now(timezone.utc).isoformat())); conn.commit(); conn.close(); return c
def get_warnings(chat_id, uid):
    conn=get_db(); row=conn.execute("SELECT * FROM warnings WHERE chat_id=? AND user_id=?",(str(chat_id),str(uid))).fetchone(); conn.close(); return dict(row) if row else {"count":0}
def clear_warnings(chat_id, uid): conn=get_db(); conn.execute("DELETE FROM warnings WHERE chat_id=? AND user_id=?",(str(chat_id),str(uid))); conn.commit(); conn.close()

# ========= AI ENGINE V10.2 REAPROVEITADO =========
PROVIDERS={}
def build_providers():
    m={}
    if os.getenv("GROQ_API_KEY"): m["groq"]={"key":os.getenv("GROQ_API_KEY"),"endpoint":"https://api.groq.com/openai/v1/chat/completions","models":["llama-3.3-70b-versatile","llama-3.1-8b-instant"]}
    if os.getenv("GEMINI_API_KEY"): m["gemini"]={"key":os.getenv("GEMINI_API_KEY"),"endpoint":"gemini","models":["gemini-2.0-flash","gemini-1.5-flash"]}
    if os.getenv("OPENROUTER_API_KEY"): m["openrouter"]={"key":os.getenv("OPENROUTER_API_KEY"),"endpoint":"https://openrouter.ai/api/v1/chat/completions","models":["meta-llama/llama-3.1-8b-instruct:free","openai/gpt-oss-20b:free"]}
    if os.getenv("DEEPSEEK_API_KEY"): m["deepseek"]={"key":os.getenv("DEEPSEEK_API_KEY"),"endpoint":"https://api.deepseek.com/chat/completions","models":["deepseek-chat"]}
    return m
PROVIDERS=build_providers()

def call_ai(prompt, timeout=7, max_tokens=250):
    if not PROVIDERS: return None
    order=["groq","gemini","openrouter","deepseek"]
    for prov in order:
        if prov not in PROVIDERS: continue
        cfg=PROVIDERS[prov]
        try:
            if prov=="gemini":
                url=f"https://generativelanguage.googleapis.com/v1beta/models/{cfg['models'][0]}:generateContent?key={cfg['key']}"
                r=requests.post(url, json={"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"maxOutputTokens":max_tokens,"temperature":0.2}}, timeout=timeout)
                if r.status_code==200: return r.json()["candidates"][0]["content"]["parts"][0]["text"]
            else:
                headers={"Authorization":f"Bearer {cfg['key']}","Content-Type":"application/json"}
                payload={"model":cfg["models"][0],"messages":[{"role":"user","content":prompt}],"temperature":0.2,"max_tokens":max_tokens}
                r=requests.post(cfg["endpoint"], json=payload, headers=headers, timeout=timeout)
                if r.status_code==200: return r.json()["choices"][0]["message"]["content"]
        except: continue
    return None

# ========= AI MODERATOR =========
def ai_moderator_classify(text, rules):
    prompt=f"""Você é classificador de moderação. Analise mensagem de grupo Telegram.
REGRAS ATIVAS: anti_link={rules.get('anti_link')} anti_divulgation={rules.get('anti_divulgation')} modo={rules.get('moderation_mode')}
MENSAGEM: "{text[:600]}"
Responda SOMENTE JSON: {{"category":"spam|divulgacao|link|ofensa|normal","confidence":0.0-1.0,"action":"IGNORE|DELETE|WARN|MUTE|BAN","reason":"motivo_curto"}}
Regras: se normal, action=IGNORE confidence <0.65. Se divulgação de grupo/canal/venda, category=divulgacao. Se link suspeito, category=link. NÃO invente.
"""
    raw=call_ai(prompt, max_tokens=180)
    if not raw: return {"category":"normal","confidence":0.4,"action":"IGNORE","reason":"ia_off"}
    try:
        m=re.search(r"\{.*\}", raw, re.DOTALL)
        if not m: return {"category":"normal","confidence":0.4,"action":"IGNORE","reason":"parse_fail"}
        data=json.loads(m.group(0))
        if data.get("action") not in ["IGNORE","DELETE","WARN","MUTE","KICK","BAN","NOTIFY_ADMIN"]: data["action"]="IGNORE"
        data["confidence"]=max(0.0,min(1.0,float(data.get("confidence",0.5))))
        return data
    except: return {"category":"normal","confidence":0.4,"action":"IGNORE","reason":"json_fail"}

def ai_natural_config(text):
    prompt=f"""Converta pedido em JSON de config de bot ADM.
Pedido: "{text}"
Regras válidas: welcome,goodbye,anti_link,anti_spam,anti_flood,anti_mention,anti_divulgation,warning_limit,moderation_mode (observer/moderate/strict/auto)
Retorne SOMENTE JSON: {{"rule":"anti_link","value":true}} ou {{"rule":"moderation_mode","value":"strict"}}. Se não entender: {{"rule":null}}
"""
    raw=call_ai(prompt, timeout=6, max_tokens=80)
    if not raw: return None
    try:
        m=re.search(r"\{.*\}", raw, re.DOTALL); return json.loads(m.group(0)) if m else None
    except: return None

# ========= ANTI-ENGINES =========
LINK_RE=re.compile(r"(https?://|www\.|t\.me/|telegram\.me/|joinchat)",re.I)
MENTION_RE=re.compile(r"@\w+")
flood_cache=defaultdict(lambda: deque(maxlen=20))
mention_cache=defaultdict(lambda: deque(maxlen=20))

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

# ========= DECISION ENGINE =========
def make_decision(event_type, context, rules, ai_result=None):
    text=context.get("text","")
    mode=rules.get("moderation_mode","moderate")
    try: auto_actions=json.loads(rules.get("auto_actions",'["delete","warn","mute"]'))
    except: auto_actions=["delete","warn","mute"]

    if mode=="observer": return "IGNORE","observer",1.0
    if event_type in ["NEW_MEMBER","LEFT_MEMBER"]: return "IGNORE","evento",1.0

    # 1. Flood alta prioridade
    if rules.get("anti_flood") and check_flood(context["chat_id"], context["user_id"], rules.get("flood_limit",7), rules.get("flood_window",15)):
        act="MUTE" if "mute" in auto_actions else "WARN"
        return act,"flood",0.96

    # 2. Mention spam
    if rules.get("anti_mention") and check_mention_spam(context["chat_id"], context["user_id"], text):
        return "MUTE","mention_spam",0.9

    # 3. Anti-link
    if rules.get("anti_link") and check_link(text, rules.get("allowed_links","")):
        act="DELETE" if "delete" in auto_actions else "WARN"
        return act,"link_nao_permitido",0.92

    # 4. Anti-spam repetitivo
    if rules.get("anti_spam"):
        if check_spam(text, f"{context['chat_id']}_{context['user_id']}"):
            return "WARN","spam_repetitivo",0.85

    # 5. Anti-divulgação com IA (só se ativado)
    if rules.get("anti_divulgation") and len(text)>20:
        if not ai_result: ai_result=ai_moderator_classify(text, rules)
        if ai_result["category"] in ["divulgacao","spam"] and ai_result["confidence"]>=0.75:
            # se modo strict, conf mínima menor
            min_conf=0.65 if mode=="strict" else 0.75
            if ai_result["confidence"]>=min_conf:
                desired=ai_result["action"].lower()
                if desired in auto_actions or desired=="delete":
                    return ai_result["action"], ai_result["reason"], ai_result["confidence"]
                else:
                    return "DELETE", ai_result["reason"], ai_result["confidence"]

    # 6. Modo auto - deixa IA decidir tudo com confiança alta
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
            send_message(chat_id,f"🪐 Orbit Alliance online! Me promova a *ADM* com permissões de apagar e banir pra eu proteger o grupo. Use /help")
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

    if cmd=="/start": send_message(chat_id,f"🤖 *{BOT_NAME} ADM V11.1*\nSou ADM com IA.\n\nMe promova a ADM com: Apagar msgs + Banir usuários\n\nComandos:\n/rules - ver regras\n/status - status do bot\n/help - ajuda ADM"); return
    if cmd=="/help":
        if is_target_admin(chat_id,user_id) or str(user_id)==CREATOR_ID:
            send_message(chat_id,"👮 *COMANDOS ADM:*\n\n*Punições:*\n/warn (reply) - advertir\n/unwarn - limpar warns\n/warnings (reply) - ver warns\n/mute /unmute /kick /ban /unban\n/delete (reply) - apagar\n\n*Proteção:*\n/antilink on/off\n/antispam on/off\n/antiflood on/off\n/antidivulga on/off - usa IA\n/antimention on/off\n\n*Sistema:*\n/welcome on/off\n/goodbye on/off\n/setwelcome texto - define msg\n/setgoodbye texto\n/mode observer/moderate/strict/auto\n/rules /status /logs\n\nEx: /setwelcome Bem vindo {name}!")
        else: send_message(chat_id,"Comandos: /rules /status"); return

    if msg["chat"]["type"]!="private" and not is_target_admin(chat_id,user_id) and str(user_id)!=CREATOR_ID:
        return

    if cmd in ["/rules","/config"]:
        r=get_group_config(chat_id)
        send_message(chat_id,f"⚙️ *{msg['chat'].get('title','Grupo')}*\n\nWelcome:{'✅' if r.get('welcome') else '❌'} Goodbye:{'✅' if r.get('goodbye') else '❌'}\nAnti-link:{'✅' if r.get('anti_link') else '❌'}\nAnti-spam:{'✅' if r.get('anti_spam') else '❌'}\nFlood:{'✅' if r.get('anti_flood') else '❌'} ({r.get('flood_limit')}/{r.get('flood_window')}s)\nMention:{'✅' if r.get('anti_mention') else '❌'}\nDivulgação IA:{'✅' if r.get('anti_divulgation') else '❌'}\nModo:{r.get('moderation_mode')}\nWarn limite:{r.get('warning_limit')}\nLinks permitidos:{r.get('allowed_links')[:60]}"); return

    if cmd=="/status":
        r=get_group_config(chat_id); bot_adm=is_bot_admin(chat_id)
        perms=f"Del:{'✅' if bot_can(chat_id,'can_delete_messages') else '❌'} Ban:{'✅' if bot_can(chat_id,'can_restrict_members') else '❌'} Pin:{'✅' if bot_can(chat_id,'can_pin_messages') else '❌'}"
        send_message(chat_id,f"🤖 *STATUS V11.1*\nBot ADM:{'✅' if bot_adm else '❌ NÃO SOU ADM - ME PROMOVA'}\n{perms}\nIA:{list(PROVIDERS.keys()) or 'nenhuma - só regex'}\nModo:{r.get('moderation_mode')}\nDB:{DATABASE_PATH}"); return

    if cmd.startswith("/warn"):
        tid=parse_target(msg)
        if not tid: send_message(chat_id,"Responda a mensagem da pessoa com /warn + motivo"); return
        if is_protected(chat_id,tid): send_message(chat_id,"⚠️ Não posso punir ADM/bot/criador"); return
        reason=" ".join(text.split()[1:]) or "sem motivo"; c=add_warning(chat_id,tid,reason); lim=get_group_config(chat_id).get("warning_limit",3)
        send_message(chat_id,f"⚠️ Advertência {c}/{lim}\nMotivo: {reason}"); log_action(chat_id,tid,"WARN",reason,msg["message_id"],"ADMIN")
        if c>=lim:
            if bot_can(chat_id,"can_restrict_members"): restrict_user(chat_id,tid,600); send_message(chat_id,f"🔇 Mute automático - limite {c}/{lim}"); log_action(chat_id,tid,"MUTE",f"limite_warn {c}/{lim}",source="AUTO")
        return
    if cmd.startswith("/unwarn") or cmd.startswith("/clearwarn"): tid=parse_target(msg); clear_warnings(chat_id,tid); send_message(chat_id,"✅ Warnings zerados"); return
    if cmd=="/warnings": tid=parse_target(msg); w=get_warnings(chat_id,tid); send_message(chat_id,f"Warnings: {w.get('count',0)} motivo: {w.get('last_reason','-')}"); return
    if cmd.startswith("/mute"):
        tid=parse_target(msg)
        if not tid or is_protected(chat_id,tid): send_message(chat_id,"⚠️ Protegido"); return
        if not bot_can(chat_id,"can_restrict_members"): send_message(chat_id,"❌ Preciso ser ADM com permissão de restringir"); return
        restrict_user(chat_id,tid,3600); send_message(chat_id,"🔇 Silenciado 1h"); log_action(chat_id,tid,"MUTE","cmd_admin",msg["message_id"],"ADMIN"); return
    if cmd.startswith("/unmute"): tid=parse_target(msg); unrestrict_user(chat_id,tid); send_message(chat_id,"🔊 Unmute ok"); log_action(chat_id,tid,"UNMUTE","cmd",source="ADMIN"); return
    if cmd.startswith("/ban"):
        tid=parse_target(msg)
        if not tid or is_protected(chat_id,tid): send_message(chat_id,"⚠️ Protegido"); return
        if not bot_can(chat_id,"can_restrict_members"): send_message(chat_id,"❌ Sem permissão ban"); return
        ban_user(chat_id,tid); send_message(chat_id,"🚫 Banido"); log_action(chat_id,tid,"BAN","cmd_admin",source="ADMIN"); return
    if cmd.startswith("/unban"):
        tid=parse_target(msg)
        if not tid and len(text.split())>=2 and text.split()[1].isdigit(): tid=int(text.split()[1])
        if not tid: send_message(chat_id,"Use /unban ID ou reply"); return
        unban_user(chat_id,tid); send_message(chat_id,"✅ Desbanido"); log_action(chat_id,tid,"UNBAN","cmd",source="ADMIN"); return
    if cmd.startswith("/kick"):
        tid=parse_target(msg)
        if not tid or is_protected(chat_id,tid): return
        if not bot_can(chat_id,"can_restrict_members"): send_message(chat_id,"❌ Sem permissão"); return
        kick_user(chat_id,tid); send_message(chat_id,"👢 Kick - removido"); log_action(chat_id,tid,"KICK","cmd",source="ADMIN"); return
    if cmd.startswith("/delete") or cmd.startswith("/del"):
        if "reply_to_message" in msg: delete_message(chat_id,msg["reply_to_message"]["message_id"]); delete_message(chat_id,msg["message_id"]); log_action(chat_id,user_id,"DELETE","cmd",source="ADMIN")
        else: send_message(chat_id,"Responda uma mensagem com /delete"); return
    if cmd.startswith("/antilink"): val="on" in text.lower() or "ativar" in text.lower() or "1" in text.split(); set_rule(chat_id,"anti_link",val); send_message(chat_id,f"Anti-link {'✅ ON' if val else '❌ OFF'}"); return
    if cmd.startswith("/antispam"): val="on" in text.lower(); set_rule(chat_id,"anti_spam",val); send_message(chat_id,f"Anti-spam {'✅' if val else '❌'}"); return
    if cmd.startswith("/antiflood"): val="on" in text.lower(); set_rule(chat_id,"anti_flood",val); send_message(chat_id,f"Anti-flood {'✅' if val else '❌'}"); return
    if cmd.startswith("/antimention"): val="on" in text.lower(); set_rule(chat_id,"anti_mention",val); send_message(chat_id,f"Anti-mention {'✅' if val else '❌'}"); return
    if cmd.startswith("/antidivulga"): val="on" in text.lower(); set_rule(chat_id,"anti_divulgation",val); send_message(chat_id,f"Anti-divulgação IA {'✅' if val else '❌'} - agora usa IA V10.2"); return
    if cmd.startswith("/welcome"):
        if "on" in text.lower() or "off" in text.lower(): val="on" in text.lower(); set_rule(chat_id,"welcome",val); send_message(chat_id,f"Welcome {'✅' if val else '❌'}")
        return
    if cmd.startswith("/goodbye"): val="on" in text.lower(); set_rule(chat_id,"goodbye",val); send_message(chat_id,f"Goodbye {'✅' if val else '❌'}"); return
    if cmd.startswith("/setwelcome"):
        new_msg=text.replace("/setwelcome","").strip()
        if not new_msg: send_message(chat_id,"Use: /setwelcome Bem vindo {name} ao {title}!"); return
        set_rule(chat_id,"welcome_msg",new_msg); send_message(chat_id,f"✅ Welcome definido:\n{new_msg}"); return
    if cmd.startswith("/setgoodbye"):
        new_msg=text.replace("/setgoodbye","").strip()
        if not new_msg: send_message(chat_id,"Use: /setgoodbye Tchau {name}!"); return
        set_rule(chat_id,"goodbye_msg",new_msg); send_message(chat_id,f"✅ Goodbye definido:\n{new_msg}"); return
    if cmd.startswith("/mode"):
        parts=text.split(); m=parts[-1].lower() if len(parts)>1 else ""
        mapa={"moderado":"moderate","rigido":"strict","observer":"observer","moderate":"moderate","strict":"strict","auto":"auto","auto-ia":"auto"}
        if m in mapa: set_rule(chat_id,"moderation_mode",mapa[m]); send_message(chat_id,f"✅ Modo alterado para {mapa[m]}\nobserver=só observa\nmoderate=padrão\nstrict=rigoroso\n auto=IA decide tudo")
        else: send_message(chat_id,"Use: /mode observer / moderate / strict / auto"); return
    if cmd.startswith("/logs"):
        conn=get_db(); rows=conn.execute("SELECT user_id,action,reason,source,created_at FROM moderation_logs WHERE chat_id=? ORDER BY id DESC LIMIT 15",(str(chat_id),)).fetchall(); conn.close()
        if not rows: send_message(chat_id,"Sem logs ainda"); return
        txt="\n".join([f"{r['created_at'][11:16]} {r['action']} uid:{str(r['user_id'])[-4:]} {r['reason'][:20]} [{r['source']}]" for r in rows]); send_message(chat_id,f"📋 *Últimos 15 logs:*\n{txt}"); return
    if cmd.startswith("/allowlink"):
        link=text.replace("/allowlink","").strip().lower()
        if not link: send_message(chat_id,f"Permitidos: {get_group_config(chat_id).get('allowed_links')}"); return
        cur=get_group_config(chat_id).get("allowed_links",""); new=cur+","+link if cur else link; set_rule(chat_id,"allowed_links",new); send_message(chat_id,f"✅ Link permitido adicionado: {link}"); return

    if len(text)>10:
        cfg=ai_natural_config(text)
        if cfg and cfg.get("rule"):
            rule=cfg["rule"]; val=cfg["value"]
            if rule in ["anti_link","anti_spam","anti_flood","anti_divulgation","anti_mention","welcome","goodbye"]:
                set_rule(chat_id,rule,bool(val)); send_message(chat_id,f"✅ Entendi: {rule} = {val}"); return
            if rule=="moderation_mode": set_rule(chat_id,rule,val); send_message(chat_id,f"✅ Modo = {val}"); return

# ========= MESSAGE HANDLER =========
def handle_message(msg):
    if msg["from"].get("is_bot"): return
    chat_id=msg["chat"]["id"]; user_id=msg["from"]["id"]; text=msg.get("text") or msg.get("caption") or ""
    if text.startswith("/"): handle_command(msg); return
    if is_protected(chat_id,user_id): return
    rules=get_group_config(chat_id)
    if not rules: return
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
        if not bot_can(chat_id,"can_delete_messages"): log_action(chat_id,user_id,"FAIL_DEL","sem_perm",msg["message_id"],"SYSTEM"); return
        delete_message(chat_id,msg["message_id"]); log_action(chat_id,user_id,"DELETE",reason,msg["message_id"],"AUTO")
    elif action=="WARN":
        if "warn" not in auto_actions and "delete" in auto_actions:
            if bot_can(chat_id,"can_delete_messages"): delete_message(chat_id,msg["message_id"])
            return
        c=add_warning(chat_id,user_id,reason); lim=rules.get("warning_limit",3)
        if bot_can(chat_id,"can_delete_messages"): delete_message(chat_id,msg["message_id"])
        send_message(chat_id,f"⚠️ Advertência {c}/{lim} - {reason}"); log_action(chat_id,user_id,"WARN",reason,msg["message_id"],"AUTO")
        if c>=lim and bot_can(chat_id,"can_restrict_members"): restrict_user(chat_id,user_id,600); send_message(chat_id,f"🔇 Mute automático {c}/{lim}"); log_action(chat_id,user_id,"MUTE",f"limite_warn",source="AUTO")
    elif action=="MUTE":
        if not bot_can(chat_id,"can_restrict_members"): return
        if bot_can(chat_id,"can_delete_messages"): delete_message(chat_id,msg["message_id"])
        restrict_user(chat_id,user_id,600); send_message(chat_id,f"🔇 Silenciado 10min - {reason}"); log_action(chat_id,user_id,"MUTE",reason,msg["message_id"],"AUTO")
    elif action=="KICK":
        if "kick" not in auto_actions:
            if bot_can(chat_id,"can_restrict_members"): restrict_user(chat_id,user_id,600); send_message(chat_id,f"🔇 Mute (kick bloqueado) - {reason}")
            return
        if not bot_can(chat_id,"can_restrict_members"): return
        kick_user(chat_id,user_id); send_message(chat_id,f"👢 Removido - {reason}"); log_action(chat_id,user_id,"KICK",reason,msg["message_id"],"AUTO")
    elif action=="BAN":
        if "ban" not in auto_actions:
            if bot_can(chat_id,"can_restrict_members"): restrict_user(chat_id,user_id,3600); send_message(chat_id,f"🔇 Mute (ban bloqueado) - {reason}")
            return
        if not bot_can(chat_id,"can_restrict_members"): return
        if conf<0.92:
            restrict_user(chat_id,user_id,3600); send_message(chat_id,f"🔇 Mute (confiança baixa pra ban {conf:.2f}) - {reason}"); return
        ban_user(chat_id,user_id); send_message(chat_id,f"🚫 Banido - {reason}"); log_action(chat_id,user_id,"BAN",reason,msg["message_id"],"AUTO")

# ========= PROCESS UPDATE =========
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

# ========= FLASK =========
@app.route('/')
def index(): return f"{BOT_NAME} ADM V11.1 COMPLETO ONLINE | DB:{DATABASE_PATH} | IA:{list(PROVIDERS.keys()) or 'regex'} | Groups SQLite",200
@app.route('/health')
def health(): return "ok",200
@app.route(f'/{TELEGRAM_TOKEN}', methods=['POST'])
def webhook():
    data=request.get_json()
    if data: executor.submit(process_update, data)
    return "ok",200

if __name__=='__main__': app.run(host='0.0.0.0', port=int(os.environ.get("PORT",8080)))
