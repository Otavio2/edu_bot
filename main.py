# ORBIT ALLIANCE V17.9 HARDENED - PV INTELIGENTE + ADM COMPLETO
# FIX: 30+ PONTOS AUDIT | Kʆɛɓɛʀ | HANSEL CORE - BASE V17.8
import os, re, json, time, sqlite3, logging, requests, threading
from datetime import datetime, timezone, timedelta
from collections import defaultdict, deque
from urllib.parse import urlparse
from flask import Flask, request, abort
from concurrent.futures import ThreadPoolExecutor

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CREATOR_ID = str(os.getenv("CREATOR_ID","8398287578"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
DATABASE_PATH = os.getenv("DATABASE_PATH","Orbit.db")
PORT = int(os.getenv("PORT",10000))
ORBIT_CORE = "Orbit Alliance V17.9 HARDENED"
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
WEBHOOK_PATH = "/telegram/webhook"

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
executor = ThreadPoolExecutor(max_workers=8)
db_lock = threading.Lock()
chat_locks = defaultdict(threading.Lock)
mem_flood = {}
mem_texts = defaultdict(lambda: deque(maxlen=5))
admin_cache = {}
circuit_breaker = {}
processed_updates = deque(maxlen=2000)
thread_local = threading.local()
BOT_ID = None

def get_session():
    if not hasattr(thread_local,"session"): thread_local.session = requests.Session()
    return thread_local.session
def clamp01(v):
    try: return max(0.0, min(1.0, float(v)))
    except: return 0.0
def escape_prompt(s): return s.replace('"""','"')[:800]
def normalize_text(text):
    t=text.lower().replace("3","e").replace("4","a").replace("0","o").replace("1","i").replace("5","s")
    t=re.sub(r"[^a-z0-9\s]"," ",t); return re.sub(r"\s+"," ",t).strip()

PROVIDERS_RAW={
    "groq":{"key_env":"GROQ_API_KEY","endpoint":"https://api.groq.com/openai/v1","format":"openai","timeout":3},
    "cloudflare":{"key_env":"CLOUDFLARE_API_TOKEN","account_env":"CLOUDFLARE_ACCOUNT_ID","endpoint":"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/meta/llama-3.1-8b-instruct","format":"cloudflare","timeout":4},
    "gemini":{"key_env":"GEMINI_API_KEY","endpoint":"https://generativelanguage.googleapis.com/v1beta","format":"gemini","timeout":4},
    "cerebras":{"key_env":"CEREBRAS_API_KEY","endpoint":"https://api.cerebras.ai/v1","format":"openai","timeout":3},
}
FALLBACK_MODELS={"cloudflare":["@cf/meta/llama-3.1-8b-instruct"],"groq":["llama-3.3-70b-versatile"],"gemini":["gemini-2.0-flash"],"cerebras":["llama-3.3-70b"]}
def build_providers():
    provs={}
    for n,cfg in PROVIDERS_RAW.items():
        k=os.getenv(cfg["key_env"])
        if not k: continue
        if n=="cloudflare":
            acc=os.getenv(cfg["account_env"])
            if not acc: continue
            provs[n]={"key":k,"account_id":acc,"endpoint":cfg["endpoint"].format(account_id=acc),"format":cfg["format"],"timeout":cfg["timeout"]}
        else: provs[n]={"key":k,"endpoint":cfg["endpoint"],"format":cfg["format"],"timeout":cfg["timeout"]}
    return provs
PROVIDERS=build_providers()
ORDER=["groq","cloudflare","gemini","cerebras"]
DIVULGA_WORDS={"entra","ganhe","lucro","renda","gratis","promocao","vagas","dinheiro","pix","aposte","cassino","tigrinho","sorteio"}
TOXIC_WORDS={"lixo","burro","otario","idiota","fdp","vsf","arrombado","corno","vagabundo"}
SENSUAL_WORDS={"sem cueca","sem calcinha","pelado","pelada","tesao","buceta","transar","sexo","nudes","onlyfans","xvideo","porno"}

def ai_sensual_score(t): return min(sum(0.45 for w in SENSUAL_WORDS if w in normalize_text(t)),1.0)
def ai_toxic_score(t): return min(sum(0.35 for w in TOXIC_WORDS if w in normalize_text(t)),1.0)
def ai_divulgacao_score(t):
    sc=0.5 if re.search(r"https?://|t\.me/|wa\.me|discord\.gg",t.lower()) else 0
    sc+=sum(0.15 for w in DIVULGA_WORDS if w in t.lower()); return min(sc,1.0)
def ai_spam_score(texts):
    if len(texts)<3: return 0
    last=normalize_text(texts[-1]); return 0.9 if sum(1 for x in texts if normalize_text(x)==last)>=3 else 0

def call_moderation_ai(text, recent=[], chat_id=None):
    hs=ai_sensual_score(text); ht=ai_toxic_score(text); hd=ai_divulgacao_score(text); hsp=ai_spam_score(list(recent))
    fallback={"toxic":ht,"divulg":hd,"sensual":hs,"spam":hsp,"rule_violation":0}
    rules_ctx=""
    if chat_id:
        try: rules_ctx=escape_prompt(get_cfg(chat_id).get("rules_msg",""))
        except: pass
    if not PROVIDERS: return fallback
    prompt=f'JSON toxic divulg sensual spam rule_violation 0-1 Regras:{rules_ctx[:500]} Msg:{normalize_text(text)[:300]}'
    start=time.time()
    for prov in ORDER:
        if time.time()-start>4: break
        if prov not in PROVIDERS or circuit_breaker.get(prov,0)>time.time(): continue
        cfg=PROVIDERS[prov]
        try:
            sess=get_session()
            if cfg["format"]=="cloudflare":
                r=sess.post(cfg["endpoint"],json={"messages":[{"role":"user","content":prompt}]},headers={"Authorization":f"Bearer {cfg['key']}"},timeout=cfg["timeout"])
                if r.status_code==200:
                    resp=r.json().get("result",{}).get("response",""); m=re.search(r"\{.*\}",resp,re.DOTALL)
                    if m: j=json.loads(m.group()); return {k: clamp01(max(float(j.get(k,0)), fallback.get(k,0))) for k in fallback}
                else: circuit_breaker[prov]=time.time()+120
            else:
                url=f"{cfg['endpoint'].rstrip('/')}/chat/completions"
                r=sess.post(url,json={"model":FALLBACK_MODELS[prov][0],"messages":[{"role":"user","content":prompt}],"temperature":0.1,"max_tokens":100},headers={"Authorization":f"Bearer {cfg['key']}"},timeout=cfg["timeout"])
                if r.status_code==200:
                    cont=r.json()["choices"][0]["message"]["content"]; m=re.search(r"\{.*\}",cont,re.DOTALL)
                    if m: j=json.loads(m.group()); return {k: clamp01(max(float(j.get(k,0)), fallback.get(k,0))) for k in fallback}
                elif r.status_code>=500: circuit_breaker[prov]=time.time()+120
        except: circuit_breaker[prov]=time.time()+60
    return fallback

def get_db():
    c=sqlite3.connect(DATABASE_PATH,check_same_thread=False,timeout=10); c.row_factory=sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL;"); c.execute("PRAGMA busy_timeout=5000;"); return c
def init_db():
    c=get_db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS group_rules(chat_id TEXT PRIMARY KEY,welcome INTEGER DEFAULT 1,goodbye INTEGER DEFAULT 1,welcome_msg TEXT DEFAULT 'Bem-vindo {name}! 🚀',goodbye_msg TEXT DEFAULT '{name} saiu.',rules_msg TEXT DEFAULT '📜 REGRAS',anti_link INTEGER DEFAULT 0,anti_spam INTEGER DEFAULT 1,anti_flood INTEGER DEFAULT 1,anti_divulgation INTEGER DEFAULT 1,anti_sensual INTEGER DEFAULT 1,anti_mention INTEGER DEFAULT 1,night_mode INTEGER DEFAULT 0,sensual_mode TEXT DEFAULT 'moderate',allowed_links TEXT DEFAULT 'youtube.com,instagram.com,github.com',warning_limit INTEGER DEFAULT 3,moderation_mode TEXT DEFAULT 'moderate',flood_limit INTEGER DEFAULT 7,flood_window INTEGER DEFAULT 15,mention_limit INTEGER DEFAULT 5,auto_actions TEXT DEFAULT '["DELETE","WARN","MUTE"]',mute_duration INTEGER DEFAULT 600,updated_at TEXT);
    CREATE TABLE IF NOT EXISTS warnings(chat_id TEXT,user_id TEXT,count INTEGER DEFAULT 0,last_reason TEXT,updated_at TEXT,PRIMARY KEY(chat_id,user_id));
    CREATE TABLE IF NOT EXISTS moderation_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id TEXT,user_id TEXT,action TEXT,reason TEXT,message_id INTEGER,source TEXT,success INTEGER,created_at TEXT);
    CREATE TABLE IF NOT EXISTS user_reputation(chat_id TEXT,user_id TEXT,spam_score INTEGER DEFAULT 0,last_seen TEXT,PRIMARY KEY(chat_id,user_id));
    CREATE TABLE IF NOT EXISTS pending_rules(id TEXT PRIMARY KEY,target_chat TEXT,text_proposed TEXT,key_type TEXT,created_at TEXT);
    """); c.commit(); c.close()
init_db()

def telegram_req(method,payload=None):
    url=f"{TELEGRAM_API_URL}/{method}"
    try:
        r=requests.post(url,json=payload,timeout=10) if payload else requests.get(url,timeout=10)
        return r.json()
    except: return {"ok":False}
def init_bot():
    global BOT_ID
    d=telegram_req("getMe")
    if d.get("ok"): BOT_ID=d["result"]["id"]
init_bot()
def send(chat_id,txt,reply=None,parse="Markdown",markup=None):
    p={"chat_id":chat_id,"text":str(txt)[:3900],"parse_mode":parse}
    if reply: p["reply_to_message_id"]=reply
    if markup: p["reply_markup"]=markup
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
def set_cfg(chat_id,key,val):
    allowed={"welcome","goodbye","welcome_msg","goodbye_msg","rules_msg","anti_link","anti_spam","anti_flood","anti_divulgation","anti_sensual","anti_mention","night_mode","sensual_mode","allowed_links","warning_limit","moderation_mode","flood_limit","flood_window","mention_limit","mute_duration","auto_actions"}
    if key not in allowed: return False
    with db_lock: c=get_db(); c.execute(f"UPDATE group_rules SET {key}=?, updated_at=? WHERE chat_id=?",(val if not isinstance(val,list) else json.dumps(val),datetime.now(timezone.utc).isoformat(),str(chat_id))); c.commit(); c.close()
    return True
def is_admin(chat_id,uid):
    if str(uid)==CREATOR_ID: return True
    now=time.time()
    if str(chat_id) in admin_cache and str(uid) in admin_cache[str(chat_id)]:
        is_adm,ts=admin_cache[str(chat_id)][str(uid)]
        if now-ts<300: return is_adm
    m=telegram_req("getChatMember",{"chat_id":chat_id,"user_id":uid})
    is_adm=m.get("ok") and m["result"]["status"] in ("administrator","creator")
    admin_cache.setdefault(str(chat_id),{})[str(uid)]=(is_adm,now); return is_adm
def can_bot(chat_id,need):
    if not BOT_ID: return False
    m=telegram_req("getChatMember",{"chat_id":chat_id,"user_id":BOT_ID})
    if not m.get("ok"): return False
    r=m["result"]
    if r["status"]=="creator": return True
    if need=="ban": return r.get("can_restrict_members",False)
    if need=="delete": return r.get("can_delete_messages",False)
    if need=="pin": return r.get("can_pin_messages",False)
    return False
def is_protected(chat_id,uid): return str(uid)==str(BOT_ID) or str(uid)==CREATOR_ID or is_admin(chat_id,uid)
def is_allowed_action(chat_id,act):
    cfg=get_cfg(chat_id); allowed=cfg.get("auto_actions",[])
    if act in ("BAN","KICK") and "BAN" not in allowed: return False
    if act=="MUTE" and "MUTE" not in allowed: return False
    return True
def extract_domains(text):
    doms=[]
    for u in re.findall(r"https?://[^\s]+",text):
        try: doms.append(urlparse(u).netloc.lower().replace("www.",""))
        except: pass
    return doms
def check_allowed_link(text,allowed):
    doms=extract_domains(text)
    if not doms: return False
    for d in doms:
        for a in allowed:
            a=a.strip().lower().replace("www.","")
            if d==a or d.endswith("."+a): return True
    return False
def inc_spam(chat_id,uid):
    with db_lock: c=get_db(); c.execute("INSERT INTO user_reputation(chat_id,user_id,spam_score,last_seen) VALUES(?,?,1,?) ON CONFLICT(chat_id,user_id) DO UPDATE SET spam_score=spam_score+1,last_seen=?",(str(chat_id),str(uid),datetime.now(timezone.utc).isoformat(),datetime.now(timezone.utc).isoformat())); c.commit(); c.close()
def get_pending(pid):
    c=get_db(); r=c.execute("SELECT * FROM pending_rules WHERE id=?",(pid,)).fetchone(); c.close()
    if not r: return None
    try:
        if (datetime.now(timezone.utc)-datetime.fromisoformat(r["created_at"])).total_seconds()>600:
            c=get_db(); c.execute("DELETE FROM pending_rules WHERE id=?",(pid,)); c.commit(); c.close(); return None
    except: pass
    return dict(r)
def set_pending(pid,target,text,key):
    with db_lock: c=get_db(); c.execute("INSERT OR REPLACE INTO pending_rules(id,target_chat,text_proposed,key_type,created_at) VALUES(?,?,?,?,?)",(pid,target,text,key,datetime.now(timezone.utc).isoformat())); c.commit(); c.close()
def del_pending(pid):
    with db_lock: c=get_db(); c.execute("DELETE FROM pending_rules WHERE id=?",(pid,)); c.commit(); c.close()
def execute_action(chat_id,action,target_id=None,reason="",message_id=None,source="AUTO",duration=None):
    if target_id and is_protected(chat_id,target_id): return {"success":False,"error":"protected"}
    if source.startswith("AUTO") and not is_allowed_action(chat_id,action): return {"success":False,"error":"blocked"}
    need="ban" if action in ("BAN","KICK","MUTE","UNMUTE","UNBAN") else "delete" if action=="DELETE" else "pin" if action in ("PIN","UNPIN") else None
    if need and not can_bot(chat_id,need): return {"success":False,"error":"bot no perm"}
    res={"ok":False}
    try:
        if action=="DELETE" and message_id: res=telegram_req("deleteMessage",{"chat_id":chat_id,"message_id":message_id})
        elif action=="WARN" and target_id:
            with db_lock: c=get_db(); w=c.execute("SELECT count FROM warnings WHERE chat_id=? AND user_id=?",(str(chat_id),str(target_id))).fetchone(); cnt=(w["count"] if w else 0)+1; c.execute("INSERT OR REPLACE INTO warnings(chat_id,user_id,count,last_reason,updated_at) VALUES(?,?,?,?,?)",(str(chat_id),str(target_id),cnt,reason,datetime.now(timezone.utc).isoformat())); c.commit(); c.close()
            inc_spam(chat_id,target_id); res={"ok":True}
            cfg=get_cfg(chat_id)
            if cnt>=cfg.get("warning_limit",3) and is_allowed_action(chat_id,"BAN"):
                execute_action(chat_id,"BAN",target_id,f"{cnt} warns",None,"AUTO+ESCALA")
        elif action=="MUTE" and target_id:
            d=duration or get_cfg(chat_id).get("mute_duration",600); res=telegram_req("restrictChatMember",{"chat_id":chat_id,"user_id":target_id,"permissions":{"can_send_messages":False},"until_date":int(time.time())+d})
        elif action=="UNMUTE" and target_id: res=telegram_req("restrictChatMember",{"chat_id":chat_id,"user_id":target_id,"permissions":{"can_send_messages":True,"can_send_media_messages":True,"can_send_other_messages":True,"can_send_polls":True}})
        elif action=="BAN" and target_id:
            if duration: res=telegram_req("banChatMember",{"chat_id":chat_id,"user_id":target_id,"until_date":int(time.time())+duration})
            else: res=telegram_req("banChatMember",{"chat_id":chat_id,"user_id":target_id})
        elif action=="UNBAN" and target_id: res=telegram_req("unbanChatMember",{"chat_id":chat_id,"user_id":target_id})
        elif action=="KICK" and target_id: telegram_req("banChatMember",{"chat_id":chat_id,"user_id":target_id}); res=telegram_req("unbanChatMember",{"chat_id":chat_id,"user_id":target_id})
        elif action=="PIN" and message_id: res=telegram_req("pinChatMessage",{"chat_id":chat_id,"message_id":message_id})
        elif action=="UNPIN": res=telegram_req("unpinAllChatMessages",{"chat_id":chat_id})
    except Exception as e: logging.error(e); res={"ok":False}
    try:
        with db_lock: c=get_db(); c.execute("INSERT INTO moderation_logs(chat_id,user_id,action,reason,message_id,source,success,created_at) VALUES(?,?,?,?,?,?,?,?)",(str(chat_id),str(target_id or ""),action,reason,message_id or 0,source,1 if res.get("ok") else 0,datetime.now(timezone.utc).isoformat())); c.execute("DELETE FROM moderation_logs WHERE created_at < ?",((datetime.now(timezone.utc)-timedelta(days=30)).isoformat(),)); c.commit(); c.close()
    except: pass
    return {"success":bool(res.get("ok"))}

@app.route(WEBHOOK_PATH,methods=["POST"])
def webhook():
    if WEBHOOK_SECRET and request.headers.get("X-Telegram-Bot-Api-Secret-Token")!=WEBHOOK_SECRET: abort(403)
    try: data=request.get_json(force=True)
    except: return {"ok":True},200
    if not data or len(str(data))>20000: return {"ok":True},200
    uid=data.get("update_id")
    if uid and uid in processed_updates: return {"ok":True},200
    if uid: processed_updates.append(uid)
    executor.submit(process_update,data); return {"ok":True},200
@app.route("/",methods=["GET"])
def health(): return {"status":ORBIT_CORE,"bot_id":BOT_ID}

def extract_rule_intent(text):
    tl = normalize_text(text)
    triggers = ["proibido","proibida","sem ","nao pode","para de mandar","chega de","vou proibir","ta proibido"]
    if not any(t in tl for t in triggers): return None
    if "link" in tl: return {"key":"anti_link","text":"🚫 Sem Links","search":"link"}
    if "flood" in tl or "figurinha" in tl: return {"key":"anti_flood","text":"🛰️ Sem Flood","search":"flood figurinha"}
    if "putaria" in tl or "porno" in tl or "nude" in tl: return {"key":"anti_sensual","text":"🛑 Conteúdo Limpo","search":"porn putaria nude"}
    clean=text.strip()[:80]
    return {"key":"custom","text":f"🚫 {clean.capitalize()}","search":normalize_text(clean)}
def rule_exists(rules_msg, search):
    if not rules_msg: return False
    tl=normalize_text(rules_msg); terms=search.split()
    return sum(1 for t in terms if t in tl) >= max(1, len(terms)*0.6)

def process_update(update):
    if "callback_query" in update:
        cb=update["callback_query"]; uid=cb["from"]["id"]; chat_id=cb["message"]["chat"]["id"]; data=cb.get("data","")
        if data.startswith("cfg_"):
            target=data.replace("cfg_","")
            if is_admin(target,uid):
                cfg=get_cfg(target); telegram_req("answerCallbackQuery",{"callback_query_id":cb["id"],"text":"Selecionado!"})
                send(chat_id,f"⚙️ Configurando: {target}\n\nRegras:\n{cfg.get('rules_msg','')}\n\nDigite no PV a nova regra completa.")
                set_pending(f"pv_{uid}",target,"","custom")
                try: telegram_req("editMessageReplyMarkup",{"chat_id":chat_id,"message_id":cb["message"]["message_id"],"reply_markup":{"inline_keyboard":[]}})
                except: pass
            else: telegram_req("answerCallbackQuery",{"callback_query_id":cb["id"],"text":"Sem ADM","show_alert":True})
        return
    msg=update.get("message") or update.get("edited_message")
    if not msg: return
    chat_id=msg["chat"]["id"]; uid=msg["from"]["id"]; text=(msg.get("text","") or msg.get("caption","")).strip(); mid=msg["message_id"]
    with chat_locks[str(chat_id)]:
        cfg=get_cfg(chat_id)
        if "new_chat_members" in msg and cfg.get("welcome"):
            for u in msg["new_chat_members"]:
                if str(u["id"])!=str(BOT_ID):
                    try: send(chat_id,cfg.get("welcome_msg","Bem-vindo {name}! 🚀").format(name=u.get("first_name","")))
                    except: send(chat_id,cfg.get("welcome_msg","Bem-vindo!"))
            return
        if "left_chat_member" in msg and cfg.get("goodbye"):
            try: send(chat_id,cfg.get("goodbye_msg","{name} saiu.").format(name=msg["left_chat_member"].get("first_name","")))
            except: send(chat_id,cfg.get("goodbye_msg","Saiu."))
            return
        if msg["chat"]["type"]=="private":
            pend=get_pending(f"pv_{uid}")
            if pend and not text.startswith("/") and len(text)>3:
                set_cfg(pend["target_chat"],"rules_msg",text); del_pending(f"pv_{uid}")
                send(chat_id,f"✅ Regras do {pend['target_chat']} atualizadas:\n{text}")
                try: send(int(pend["target_chat"]),f"📜 Regras atualizadas via PV:\n{text}")
                except: pass
                return
            if text.startswith(("/meusgrupos","/grupos","/painel","/start")):
                c=get_db(); rows=c.execute("SELECT chat_id FROM group_rules").fetchall(); c.close(); botoes=[]
                for r in rows:
                    gid=r["chat_id"]
                    if is_admin(gid,uid):
                        try: info=telegram_req("getChat",{"chat_id":gid}); nome=info["result"]["title"] if info.get("ok") else gid
                        except: nome=gid
                        botoes.append({"text":nome[:30],"callback_data":f"cfg_{gid}"})
                if not botoes: send(chat_id,"❌ Nenhum grupo com você e eu como ADM."); return
                send(chat_id,f"🫡 {len(botoes)} grupos:",markup={"inline_keyboard":[[b] for b in botoes]}); return
            send(chat_id,f"🚀 {ORBIT_CORE}\n/meusgrupos - configura no PV"); return
        if text.startswith("/"):
            parts=text.split(); cmd=parts[0].lower().split("@")[0]; args=parts[1:]
            if cmd in ("/regras","/rules"): send(chat_id,f"📜 REGRAS:\n{cfg.get('rules_msg','Sem regras')}",mid); return
            if cmd in ("/painel","/help"): send(chat_id,f"⚙️ PAINEL V17.9 HARDENED\nAuto:{cfg.get('auto_actions')} Warn:{cfg.get('warning_limit')}\nBotPerms ban:{can_bot(chat_id,'ban')} del:{can_bot(chat_id,'delete')} pin:{can_bot(chat_id,'pin')}\n/regras /setrules /addrule /removerule /ban /unban /kick /mute /unmute /delete /warn /unwarn /warnings /resetwarnings /pin /unpin /allowlink /logs /resetai /setwelcome /setgoodbye /setnight /setmention /status",mid); return
            if cmd=="/status": send(chat_id,f"🤖 {ORBIT_CORE}\nCircuit:{circuit_breaker}\nFloodKeys:{len(mem_flood)}",mid); return
            if not is_admin(chat_id,uid): send(chat_id,"⚠️ Só ADM.",mid); return
            if cmd=="/ban":
                tgt=msg.get("reply_to_message",{}).get("from",{}).get("id"); dur=int(args[0])*60 if args and args[0].isdigit() else None
                r=execute_action(chat_id,"BAN",tgt,"ban ADM",None,"ADM",duration=dur); send(chat_id,"🚫 Banido" if r["success"] else f"❌ {r.get('error')}",mid)
            elif cmd=="/unban" and args:
                try: r=execute_action(chat_id,"UNBAN",int(args[0]),"unban ADM",None,"ADM"); send(chat_id,"✅ Desbanido" if r["success"] else f"❌ {r.get('error')}",mid)
                except: send(chat_id,"❌ ID inválido",mid)
            elif cmd=="/kick":
                tgt=msg.get("reply_to_message",{}).get("from",{}).get("id"); r=execute_action(chat_id,"KICK",tgt,"kick ADM",None,"ADM"); send(chat_id,"👢 Kickado" if r["success"] else f"❌ {r.get('error')}",mid)
            elif cmd=="/mute":
                tgt=msg.get("reply_to_message",{}).get("from",{}).get("id"); dur=int(args[0])*60 if args and args[0].isdigit() else None
                r=execute_action(chat_id,"MUTE",tgt,"mute ADM",None,"ADM",duration=dur); send(chat_id,"🔇 Mutado" if r["success"] else f"❌ {r.get('error')}",mid)
            elif cmd=="/unmute":
                tgt=msg.get("reply_to_message",{}).get("from",{}).get("id"); r=execute_action(chat_id,"UNMUTE",tgt,"unmute ADM",None,"ADM"); send(chat_id,"🔊 Desmutado" if r["success"] else "❌",mid)
            elif cmd=="/delete":
                tgt_mid=msg.get("reply_to_message",{}).get("message_id"); r=execute_action(chat_id,"DELETE",None,"del ADM",tgt_mid,"ADM") if tgt_mid else {"success":False}; send(chat_id,"🗑️ Apagada" if r["success"] else "❌",mid)
            elif cmd=="/warn":
                tgt=msg.get("reply_to_message",{}).get("from",{}).get("id")
                if tgt: execute_action(chat_id,"WARN",tgt,"warn ADM",None,"ADM"); c=get_db(); w=c.execute("SELECT count FROM warnings WHERE chat_id=? AND user_id=?",(str(chat_id),str(tgt))).fetchone(); c.close(); send(chat_id,f"⚠️ {w['count'] if w else 1}/{cfg.get('warning_limit',3)}",mid)
            elif cmd=="/unwarn":
                tgt=msg.get("reply_to_message",{}).get("from",{}).get("id")
                if tgt:
                    with db_lock: c=get_db(); c.execute("DELETE FROM warnings WHERE chat_id=? AND user_id=?",(str(chat_id),str(tgt))); c.commit(); c.close()
                    send(chat_id,"✅ Warns zerados",mid)
            elif cmd=="/warnings":
                tgt=msg.get("reply_to_message",{}).get("from",{}).get("id") or uid
                c=get_db(); w=c.execute("SELECT count FROM warnings WHERE chat_id=? AND user_id=?",(str(chat_id),str(tgt))).fetchone(); c.close(); send(chat_id,f"📊 Warns: {w['count'] if w else 0}",mid)
            elif cmd=="/resetwarnings":
                with db_lock: c=get_db(); c.execute("DELETE FROM warnings WHERE chat_id=?",(str(chat_id),)); c.commit(); c.close(); send(chat_id,"🗑️ Warns zerados",mid)
            elif cmd=="/pin":
                tgt_mid=msg.get("reply_to_message",{}).get("message_id")
                if tgt_mid: r=execute_action(chat_id,"PIN",None,"pin ADM",tgt_mid,"ADM"); send(chat_id,"📌 Fixado" if r["success"] else f"❌ {r.get('error')}",mid)
            elif cmd=="/unpin": r=execute_action(chat_id,"UNPIN",None,"unpin ADM",None,"ADM"); send(chat_id,"📌 Desfixado" if r["success"] else "❌",mid)
            elif cmd=="/allowlink" and args:
                cur=cfg.get("allowed_links",""); new=cur+","+args[0] if cur else args[0]; set_cfg(chat_id,"allowed_links",new); send(chat_id,f"🔗 Liberado: {args[0]}",mid)
            elif cmd=="/logs":
                c=get_db(); rows=c.execute("SELECT action,reason,created_at,success FROM moderation_logs WHERE chat_id=? ORDER BY id DESC LIMIT 10",(str(chat_id),)).fetchall(); c.close()
                txt="\n".join([f"{'✅' if r['success'] else '❌'} {r['action']} {r['reason'][:25]} {r['created_at'][11:16]}" for r in rows]) or "Sem logs"; send(chat_id,f"📜 Logs:\n{txt}",mid)
            elif cmd=="/resetai": admin_cache.clear(); circuit_breaker.clear(); mem_flood.clear(); send(chat_id,"🧠 Reset total feito",mid)
            elif cmd=="/setrules":
                new_msg=text[len(cmd):].strip()
                if new_msg: set_cfg(chat_id,"rules_msg",new_msg); send(chat_id,"✅ Regras salvas!",mid)
            elif cmd=="/addrule":
                new_rule=text[len(cmd):].strip()
                if new_rule: cur=cfg.get("rules_msg",""); set_cfg(chat_id,"rules_msg",cur+f"\n{new_rule}"); send(chat_id,"✅ Adicionada",mid)
            elif cmd=="/removerule" and args and args[0].isdigit():
                idx=int(args[0])-1; rules=cfg.get("rules_msg","").split("\n")
                if 0<=idx<len(rules): removida=rules.pop(idx); set_cfg(chat_id,"rules_msg","\n".join(rules)); send(chat_id,f"🗑️ Removida: {removida}",mid)
            elif cmd=="/setwelcome": new_msg=text[len(cmd):].strip(); set_cfg(chat_id,"welcome_msg",new_msg); send(chat_id,"👋 Welcome setado",mid)
            elif cmd=="/setgoodbye": new_msg=text[len(cmd):].strip(); set_cfg(chat_id,"goodbye_msg",new_msg); send(chat_id,"👋 Goodbye setado",mid)
            elif cmd=="/setnight" and args: set_cfg(chat_id,"night_mode",1 if args[0].lower()=="on" else 0); send(chat_id,f"🌙 Night {args[0]}",mid)
            elif cmd=="/setmention" and args and args[0].isdigit(): set_cfg(chat_id,"mention_limit",int(args[0])); send(chat_id,f"@ Limite {args[0]}",mid)
            return
        pend=get_pending(str(chat_id))
        if pend and is_admin(chat_id,uid):
            tl=normalize_text(text)
            if tl in ["sim","s","yes","adiciona","confirma"]:
                cur=cfg.get("rules_msg",""); set_cfg(chat_id,"rules_msg",cur+f"\n{pend['text_proposed']}"); del_pending(str(chat_id))
                if pend["key_type"]=="anti_link": set_cfg(chat_id,"anti_link",1)
                send(chat_id,f"✅ Adicionei: {pend['text_proposed']}"); return
            if tl in ["nao","não","n","cancela"]: del_pending(str(chat_id)); send(chat_id,"👌 Cancelado"); return
        if is_admin(chat_id,uid) and not text.startswith("/"):
            intent=extract_rule_intent(text)
            if intent and not rule_exists(cfg.get("rules_msg",""),intent["search"]):
                set_pending(str(chat_id),str(chat_id),intent["text"],intent["key"])
                send(chat_id,f"🫡 Entendi: {text}\nAdicionar {intent['text']}?\nResponda SIM ou NÃO"); return
        if uid==BOT_ID or is_admin(chat_id,uid): return
        if not text and not msg.get("sticker"): return
        if cfg.get("anti_flood"):
            now=time.time(); key=(str(chat_id),str(uid)); dq=mem_flood.get(key)
            if not dq: dq=deque(); mem_flood[key]=dq
            dq.append(now)
            while dq and now-dq[0]>cfg.get("flood_window",15): dq.popleft()
            if len(mem_flood)>800:
                for k in list(mem_flood.keys())[:200]:
                    if time.time()-mem_flood[k][-1]>600: del mem_flood[k]
            if len(dq)>cfg.get("flood_limit",7): execute_action(chat_id,"MUTE",uid,"flood",mid,"AUTO+FLOOD"); dq.clear(); return
        mem_texts[(str(chat_id),str(uid))].append(text)
        ai=call_moderation_ai(text,mem_texts[(str(chat_id),str(uid))],chat_id)
        allowed=[d.strip() for d in cfg.get("allowed_links","").split(",") if d.strip()]
        doms=extract_domains(text)
        if doms and not check_allowed_link(text,allowed):
            if cfg.get("anti_link") or float(ai.get("divulg",0))>=0.7:
                execute_action(chat_id,"DELETE",uid,f"link/divulg {ai.get('divulg')}",mid,"AUTO+DIVULG"); return
        if float(ai.get("rule_violation",0))>=0.75: execute_action(chat_id,"DELETE",uid,f"custom {ai.get('rule_violation')}",mid,"AUTO+CUSTOM"); return
        if float(ai.get("toxic",0))>=0.70: execute_action(chat_id,"DELETE",uid,f"toxic {ai.get('toxic')}",mid,"AUTO+TOXIC"); return
        if float(ai.get("sensual",0))>=0.65 and cfg.get("anti_sensual"): execute_action(chat_id,"DELETE",uid,f"sensual {ai.get('sensual')}",mid,"AUTO+SENSUAL"); return

if __name__=="__main__":
    print(f"START {ORBIT_CORE}"); app.run(host="0.0.0.0",port=PORT)
