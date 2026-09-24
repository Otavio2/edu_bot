import os, time, json, base64, re, threading, requests, html, random
from flask import Flask, request

SIGNATURE = "Kʆɛɓɛʀ"
DONO_NOME = "Kleber"
DONO_ID = int(os.getenv("DONO_ID","8398287578"))
BOT_TOKEN = os.getenv("BOT_TOKEN","").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET","").strip()
API = f"https://api.telegram.org/bot{BOT_TOKEN}"
BOT_ID = int(BOT_TOKEN.split(':')[0]) if ":" in BOT_TOKEN else 0
RENDER_URL = (os.getenv("RENDER_EXTERNAL_URL","") or "https://edu-bot-6yfa.onrender.com").strip()
if RENDER_URL and not RENDER_URL.startswith("http"): RENDER_URL = f"https://{RENDER_URL}"
app = Flask(__name__)

# ===== PROVIDERS =====
PROVIDERS = {
    "gemini": {"env":"GEMINI_API_KEY","url":"https://generativelanguage.googleapis.com/v1beta","fmt":"gemini","models":["gemini-2.0-flash","gemini-1.5-flash"],"vision":True},
    "groq": {"env":"GROQ_API_KEY","url":"https://api.groq.com/openai/v1","fmt":"openai","models":["llama-3.3-70b-versatile","llama-3.2-11b-vision-preview"],"vision":True},
    "mistral": {"env":"MISTRAL_API_KEY","url":"https://api.mistral.ai/v1","fmt":"openai","models":["pixtral-12b-2409","mistral-large-latest"],"vision":True},
    "openrouter": {"env":"OPENROUTER_API_KEY","url":"https://openrouter.ai/api/v1","fmt":"openai","models":["meta-llama/llama-3.1-8b-instruct:free"],"vision":True},
    "cerebras": {"env":"CEREBRAS_API_KEY","url":"https://api.cerebras.ai/v1","fmt":"openai","models":["llama-3.3-70b"],"vision":False},
}
BLACK={}
thread_local=threading.local()
def get_sess():
    if not hasattr(thread_local,"s"): thread_local.s=requests.Session()
    return thread_local.s

def tg(m,p, timeout=12):
    try:
        r = get_sess().post(f"{API}/{m}", json=p, timeout=timeout)
        if r.status_code==429: return {"ok":False,"error":"rate_limit","desc":r.text}
        if r.status_code>=500: return {"ok":False,"error":"telegram_internal","desc":r.text}
        j = r.json()
        if not j: return {"ok":False,"error":"empty_json"}
        return j
    except requests.exceptions.Timeout:
        return {"ok":False,"error":"timeout"}
    except Exception as e:
        return {"ok":False,"error":"network","desc":str(e)}

# ===== CONTEXTO COM CACHE TÉCNICO 60s (NÃO É MEMÓRIA PERMANENTE) =====
CONTEXTO_CACHE = {}
CACHE_LOCK = threading.Lock()
def get_contexto(cid):
    with CACHE_LOCK:
        cached = CONTEXTO_CACHE.get(str(cid))
        if cached and time.time()-cached["ts"]<60:
            return cached["dna"], cached["lei"], cached["bio"], cached["titulo"], cached["pin"], True
    res = tg("getChat",{"chat_id":int(cid)})
    if not res.get("ok"):
        # FALHA = NÃO INVENTA BIO
        if cached:
            return cached["dna"], cached["lei"], cached["bio"], cached["titulo"], cached["pin"], True
        return "", "", "", "", "", False
    r = res.get("result",{}) or {}
    titulo = r.get("title","").strip()
    bio = (r.get("description") or "").strip()
    pin_obj = r.get("pinned_message",{}) or {}
    pin = (pin_obj.get("text") or pin_obj.get("caption") or "")[:600].strip()
    dna = f"NOME: {titulo}\nBIO: {bio}\nFIXADO: {pin}"
    lei = f"{bio}\n{pin}".strip()
    with CACHE_LOCK:
        CONTEXTO_CACHE[str(cid)] = {"dna":dna,"lei":lei,"bio":bio,"titulo":titulo,"pin":pin,"ts":time.time()}
    return dna, lei, bio, titulo, pin, True

# ===== PERMISSÕES E ADMIN - FALHA = STATUS DESCONHECIDO =====
def get_perms(cid):
    res = tg("getChatMember",{"chat_id":int(cid),"user_id":BOT_ID})
    if not res.get("ok"):
        return None # PRINCÍPIO 2,10: falha = não executa
    r = res.get("result",{}) or {}
    if r.get("status")=="creator":
        return {"del":True,"ban":True,"ok":True}
    return {"del":bool(r.get("can_delete_messages")), "ban":bool(r.get("can_restrict_members")), "ok":True}

def is_admin(cid,uid):
    if uid==BOT_ID or uid==DONO_ID: return True
    res = tg("getChatAdministrators",{"chat_id":int(cid)})
    if not res.get("ok"):
        return None # PRINCÍPIO 9: desconhecido = não modera
    try:
        return any(a.get("user",{}).get("id")==uid for a in res.get("result",[]) or [])
    except:
        return None

def get_b64(fid):
    try:
        fp=tg("getFile",{"file_id":fid}).get("result",{}).get("file_path")
        if not fp: return None,None
        d=get_sess().get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{fp}", timeout=12).content
        if len(d)>4500000: return None,None
        mime="image/png" if fp.endswith(".png") else "image/webp" if fp.endswith(".webp") else "image/jpeg"
        return base64.b64encode(d).decode(), mime
    except: return None,None

def midia(msg):
    if msg.get("photo"): b,m=get_b64(msg["photo"][-1]["file_id"]); return b,m,"foto", True
    if msg.get("video"):
        thumb=msg["video"].get("thumb") or msg["video"].get("thumbnail")
        b,m=get_b64(thumb["file_id"]) if thumb else (None,None)
        return b,m,"video", bool(b)
    if msg.get("sticker") and not msg["sticker"].get("is_animated") and not msg["sticker"].get("is_video"):
        fid=msg["sticker"].get("thumbnail",{}).get("file_id") or msg["sticker"].get("file_id")
        b,m=get_b64(fid) if fid else (None,None); return b,m,"sticker", bool(b)
    if msg.get("animation"): fid=msg["animation"].get("thumbnail",{}).get("file_id"); b,m=get_b64(fid) if fid else (None,None); return b,m,"gif", bool(b)
    if msg.get("document"): return None,None,"documento", False
    if msg.get("voice"): return None,None,"voz", False
    if msg.get("audio"): return None,None,"audio", False
    return None,None,"texto", True

def call_ia(prompt,b64=None,mime="image/jpeg", temp=0.05):
    for prov,cfg in PROVIDERS.items():
        key=os.getenv(cfg["env"])
        if not key or (b64 and not cfg["vision"]): continue
        for model in cfg["models"]:
            if BLACK.get(f"{prov}:{model}",0)>time.time(): continue
            try:
                if cfg["fmt"]=="openai":
                    c=[{"type":"text","text":prompt}]
                    if b64: c.append({"type":"image_url","image_url":{"url":f"data:{mime};base64,{b64}"}})
                    r=get_sess().post(f"{cfg['url']}/chat/completions", headers={"Authorization":f"Bearer {key}"}, json={"model":model,"messages":[{"role":"user","content":c}],"temperature":temp,"max_tokens":900}, timeout=18)
                else:
                    p=[{"text":prompt}]
                    if b64: p.append({"inline_data":{"mime_type":mime,"data":b64}})
                    r=get_sess().post(f"{cfg['url']}/models/{model}:generateContent?key={key}", json={"contents":[{"parts":p}]}, timeout=18)
                if r.status_code in [400,401,403,429]: BLACK[f"{prov}:{model}"]=time.time()+600; continue
                if r.status_code>=500: BLACK[f"{prov}:{model}"]=time.time()+180; continue
                j=r.json()
                txt=j["choices"][0]["message"]["content"] if cfg["fmt"]=="openai" else j["candidates"][0]["content"]["parts"][0]["text"]
                if txt and len(txt)>5: return txt
            except: BLACK[f"{prov}:{model}"]=time.time()+120; continue
    return None

def existe_literal(trecho, lei):
    if not trecho or not lei: return False
    t=trecho.strip()
    if len(t)<4: return False
    return t in lei

def parse_duracao_strict(t):
    # PRINCÍPIO 8: duração vem só da BIO, ambíguo = não aplica
    if not t: return None
    s=t.lower()
    if "permanente" in s or "para sempre" in s or "definitivo" in s: return 0
    m=re.search(r'(\d+)\s*(segundo|segundos|seg|minuto|minutos|min|hora|horas|h|dia|dias|d)', s)
    if not m:
        m2=re.search(r'(\d+)\s*(s|m|h|d)\b', s)
        if not m2: return None
        n=int(m2.group(1)); u=m2.group(2)
        return n if u=="s" else n*60 if u=="m" else n*3600 if u=="h" else n*86400
    n=int(m.group(1)); u=m.group(2)
    if "seg" in u: return n
    if "min" in u: return n*60
    if "hora" in u or u=="h": return n*3600
    if "dia" in u or u=="d": return n*86400
    return None

def fala_humana(fala):
    return random.choice([fala, f"{fala} - por aqui não", f"opa, {fala.lower()}", f"{fala} ✌️", f"{fala}, combinado?"])

def ia_analisa(dna, lei, texto, b64, mime, tipo, analisavel):
    if not analisavel:
        return None # PRINCÍPIO 15: não analisável = não presume
    prompt=f'''Você é APENAS interpretador da BIO/FIXADO. Nunca invente regras, punições, exceções, durações ou permissões.
CONTEXTO:
{dna}
LEI (ÚNICA FONTE):
"{lei}"
MENSAGEM: "{texto[:1200]}" Tipo={tipo} TemLink={'http' in texto.lower() or 't.me' in texto.lower()}
REGRAS DE INTERPRETAÇÃO:
- Só viola se regra explícita na LEI com trecho LITERAL exato
- Não invente que link é proibido se LEI não falar de link
- Não use substring fora de contexto
- Se dúvida, viola=false
- Punição separada: primeiro diga se viola, depois se BIO autoriza punição
- Copie trecho LITERAL exato da BIO
JSON: {{"viola":bool,"trecho_bio":"","motivo":"","fala":"","confianca":0.0-1.0,"punicao":{{"tipo":"none|ban|mute","trecho_bio_punicao":""}}}}'''
    out=call_ia(prompt,b64,mime, temp=0.05)
    if not out: return None
    try:
        j=json.loads(re.search(r'\{.*\}',out,re.DOTALL).group())
        if "viola" not in j: return None
        if not j.get("viola"): return j
        # VALIDAÇÃO RIGOROSA V32
        if j.get("confianca",0) < 0.85: return None
        if not existe_literal(j.get("trecho_bio",""), lei): return None
        # Evita substring fora de contexto - trecho precisa ter pelo menos 2 palavras da regra completa
        trecho = j.get("trecho_bio","").strip()
        if len(trecho.split())<2: return None
        # Punição separada
        p=j.get("punicao",{})
        if p.get("tipo") in ["ban","mute"]:
            if not existe_literal(p.get("trecho_bio_punicao",""), lei):
                j["punicao"]["tipo"]="none"
            if p.get("tipo")=="mute":
                dur = parse_duracao_strict(p.get("trecho_bio_punicao",""))
                if dur is None:
                    j["punicao"]["tipo"]="none"
        return j
    except: return None

# ===== HANDLERS =====
def handle_message(msg, is_edit=False):
    cid=msg["chat"]["id"]; mid=msg.get("message_id"); uid=msg["from"]["id"]
    if not hasattr(handle_message,"seen"): handle_message.seen={}
    if f"{cid}:{mid}" in handle_message.seen and time.time()-handle_message.seen[f"{cid}:{mid}"]<12: return
    handle_message.seen[f"{cid}:{mid}"]=time.time()
    if len(handle_message.seen)>300: handle_message.seen={k:v for k,v in handle_message.seen.items() if time.time()-v<120}
    if uid==BOT_ID: return
    txt=(msg.get("text") or msg.get("caption") or "").strip()
    dna, lei, bio, titulo, pin, ctx_ok = get_contexto(cid)
    if not ctx_ok and not txt.startswith("/"):
        return # falha ao pegar contexto = não modera

    # COMANDOS - DELETA EM GRUPO
    if txt.startswith("/"):
        cmd=txt.split()[0].lower().split("@")[0]
        if int(cid)<0:
            perms=get_perms(cid)
            if perms and perms.get("del"):
                tg("deleteMessage",{"chat_id":cid,"message_id":mid})
        if cmd in ["/start","/help","/regras","/ping","/orbit"]:
            if int(cid)>0:
                tg("sendMessage",{"chat_id":cid,"text":f"🤖 <b>ORBIT ADM V32 by {SIGNATURE}</b>\nBIO DEFINE → IA INTERPRETA → CÓDIGO VALIDA → PERMISSÃO CONFIRMA → TELEGRAM EXECUTA\nDev: {DONO_NOME}","parse_mode":"HTML"})
            else:
                tg("sendMessage",{"chat_id":cid,"text":f"🤖 <b>ORBIT ADM V32</b>\n<b>LEI:</b>\n{html.escape(lei)[:1200] or 'VAZIA = NÃO MODERA'}\nby {SIGNATURE}","parse_mode":"HTML"})
        return

    # MÍDIA / TEXTO / LINKS / DOCUMENTOS
    if int(cid)>0: return

    # PROTEÇÃO ADMIN - STATUS DESCONHECIDO = NÃO MODERA
    admin_check=is_admin(cid,uid)
    if admin_check is None: return
    if admin_check or uid==DONO_ID:
        if len(txt)>=8 and lei and not is_edit:
            if not hasattr(handle_message,"last_tip"): handle_message.last_tip={}
            if handle_message.last_tip.get(cid,0) < time.time()-1800:
                prompt=f'LEI: "{lei}"\nADM: "{txt[:800]}"\nADM reclamou de algo que NÃO está na LEI? Sugira regra curta. JSON: {{"sugerir":bool,"sugestao_bio":"..."}}'
                out=call_ia(prompt,temp=0.1)
                if out:
                    try:
                        j=json.loads(re.search(r'\{.*\}',out,re.DOTALL).group())
                        if j.get("sugerir") and j.get("sugestao_bio"):
                            handle_message.last_tip[cid]=time.time()
                            tg("sendMessage",{"chat_id":cid,"text":f'💡 Sugestão (não é lei):\n<code>{html.escape(j.get("sugestao_bio",""))}</code>\nby {SIGNATURE}',"parse_mode":"HTML"})
                    except: pass
        return

    if not lei or not lei.strip(): return
    b64,mime,tipo,analisavel = midia(msg)
    # PRINCÍPIO 15,16: foto sem visão, documento sem texto = não analisável
    if tipo in ["documento","audio","voz"] and not txt:
        return # não analisável = não pune
    if tipo=="foto" and not analisavel and not txt:
        return
    # Link só modera se lei falar de link
    if ("http" in txt.lower() or "t.me" in txt.lower()) and "link" not in lei.lower() and "http" not in lei.lower() and "t.me" not in lei.lower():
        # se lei não menciona link, não presume
        pass

    ia=ia_analisa(dna, lei, txt or f"[{tipo}]", b64, mime, tipo, analisavel or bool(txt))
    if not ia or not ia.get("viola"): return

    perms=get_perms(cid)
    if perms is None: return
    if not perms.get("del"): return

    time.sleep(random.uniform(0.6,1.2))
    del_resp=tg("deleteMessage",{"chat_id":cid,"message_id":mid})
    if not del_resp.get("ok"):
        # PRINCÍPIO 11: falha ao apagar = não afirma sucesso
        if del_resp.get("error")=="rate_limit": time.sleep(2)
        return

    try: tg("sendChatAction",{"chat_id":cid,"action":"typing"}); time.sleep(random.uniform(0.5,1.0))
    except: pass

    nome=html.escape(msg["from"].get("first_name",""))
    fala=html.escape(fala_humana(ia.get("fala","Respeite as regras")[:200]))
    trecho=html.escape(ia.get("trecho_bio","")[:180])
    pun=ia.get("punicao",{})

    # FALHA PARCIAL - ações independentes
    if pun.get("tipo")=="ban":
        if not perms.get("ban"):
            tg("sendMessage",{"chat_id":cid,"text":f'<a href="tg://user?id={uid}">{nome}</a> {fala}\n<i>{trecho}</i>\n⚠️ Removido. Sem permissão pra banir.\nby {SIGNATURE}',"parse_mode":"HTML"})
            return
        if existe_literal(pun.get("trecho_bio_punicao",""), lei):
            ban_resp=tg("banChatMember",{"chat_id":cid,"user_id":uid})
            if ban_resp.get("ok"):
                tg("sendMessage",{"chat_id":cid,"text":f'<a href="tg://user?id={uid}">{nome}</a> {fala} - banido\n<i>{trecho}</i>\nby {SIGNATURE}',"parse_mode":"HTML"})
            else:
                tg("sendMessage",{"chat_id":cid,"text":f'<a href="tg://user?id={uid}">{nome}</a> {fala}\n<i>{trecho}</i>\n⚠️ Removido, mas ban falhou: {html.escape(str(ban_resp.get("description","") or ban_resp.get("error","")))}\nby {SIGNATURE}',"parse_mode":"HTML"})
            return
    if pun.get("tipo")=="mute":
        if not perms.get("ban"):
            tg("sendMessage",{"chat_id":cid,"text":f'<a href="tg://user?id={uid}">{nome}</a> {fala}\n<i>{trecho}</i>\n⚠️ Removido. Sem permissão pra silenciar.\nby {SIGNATURE}',"parse_mode":"HTML"})
            return
        if existe_literal(pun.get("trecho_bio_punicao",""), lei):
            dur=parse_duracao_strict(pun.get("trecho_bio_punicao",""))
            if dur is None:
                tg("sendMessage",{"chat_id":cid,"text":f'<a href="tg://user?id={uid}">{nome}</a> {fala}\n<i>{trecho}</i>\nby {SIGNATURE}',"parse_mode":"HTML"})
                return
            mute_resp=tg("restrictChatMember",{"chat_id":cid,"user_id":uid,"permissions":{"can_send_messages":False},"until_date":int(time.time())+dur} if dur>0 else {"chat_id":cid,"user_id":uid,"permissions":{"can_send_messages":False}})
            if mute_resp.get("ok"):
                td=f"{dur}s" if dur>0 else "permanente"
                tg("sendMessage",{"chat_id":cid,"text":f'<a href="tg://user?id={uid}">{nome}</a> {fala} - silenciado {td}\n<i>{trecho}</i>\nby {SIGNATURE}',"parse_mode":"HTML"})
            else:
                tg("sendMessage",{"chat_id":cid,"text":f'<a href="tg://user?id={uid}">{nome}</a> {fala}\n<i>{trecho}</i>\n⚠️ Removido, mas mute falhou.\nby {SIGNATURE}',"parse_mode":"HTML"})
            return

    tg("sendMessage",{"chat_id":cid,"text":f'<a href="tg://user?id={uid}">{nome}</a> {fala}\n<i>{trecho}</i>\nby {SIGNATURE}',"parse_mode":"HTML"})

def handle_chat_member(update):
    # PRINCÍPIO 4: tratamento próprio, não fake message
    try:
        chat=update["chat"]; cid=chat["id"]
        new=update.get("new_chat_member",{}); old=update.get("old_chat_member",{})
        user=new.get("user",{}) or old.get("user",{})
        if not user or user.get("id")==BOT_ID: return
        status_new=new.get("status"); status_old=old.get("status")
        dna, lei, bio, titulo, pin, _ = get_contexto(cid)
        # ENTRADA
        if status_old in ["left","kicked"] and status_new=="member":
            if lei and any(x in lei.lower() for x in ["bem vindo","bem-vindo","bemvindo","boas vindas","seja bem"]):
                nome=user.get("first_name","")
                prompt=f'Crie boas-vindas, NÃO invente regras/links.\nCONTEXTO: {dna}\nNOVO: {nome}\nMáx 2 linhas.'
                welcome=call_ia(prompt,temp=0.9)
                if not welcome or len(welcome)<8: welcome=f"👋 {nome}, bem-vindo ao {titulo}!"
                if "http" in welcome.lower() and "link" not in lei.lower():
                    welcome=f"👋 {nome}, bem-vindo ao {titulo}!"
                tg("sendMessage",{"chat_id":cid,"text":f'<a href="tg://user?id={user["id"]}">{html.escape(nome)}</a> {html.escape(welcome)[:800]}\nby {SIGNATURE}',"parse_mode":"HTML"})
        # SAÍDA / BAN / PROMOÇÃO - apenas log técnico, não modera
    except: pass

def webhook_guardian():
    fail_count=0
    while True:
        time.sleep(300)
        try:
            info=tg("getWebhookInfo",{})
            if not info.get("ok"):
                fail_count+=1
                time.sleep(min(60*fail_count,300))
                continue
            res=info.get("result",{})
            url_atual=res.get("url","")
            last_err=res.get("last_error_message","")
            pending=res.get("pending_update_count",0)
            if not url_atual or url_atual.rstrip("/")!=RENDER_URL.rstrip("/") or (last_err and pending>100):
                # tenta restaurar
                set_res=tg("setWebhook",{"url":f"{RENDER_URL}/","allowed_updates":["message","edited_message","chat_member","my_chat_member"],"secret_token":WEBHOOK_SECRET} if WEBHOOK_SECRET else {"url":f"{RENDER_URL}/","allowed_updates":["message","edited_message","chat_member","my_chat_member"]})
                # PRINCÍPIO 1: não considera recuperado só porque set retornou ok, verifica de novo
                time.sleep(5)
                check=tg("getWebhookInfo",{})
                if check.get("ok") and check.get("result",{}).get("url","").rstrip("/")==RENDER_URL.rstrip("/"):
                    fail_count=0
                else:
                    fail_count+=1
            else:
                fail_count=0
        except:
            fail_count+=1
            time.sleep(min(60*fail_count,300))

def keep_alive():
    while True:
        time.sleep(240)
        try:
            if RENDER_URL: get_sess().get(RENDER_URL, timeout=5)
        except: pass

@app.route("/", methods=["POST"])
def wh():
    if WEBHOOK_SECRET:
        sec=request.headers.get("X-Telegram-Bot-Api-Secret-Token","")
        if sec!=WEBHOOK_SECRET: return "no",403
    u=request.get_json(force=True,silent=True) or {}
    if "message" in u: threading.Thread(target=handle_message, args=(u["message"], False), daemon=True).start()
    if "edited_message" in u: threading.Thread(target=handle_message, args=(u["edited_message"], True), daemon=True).start()
    if "chat_member" in u: threading.Thread(target=handle_chat_member, args=(u["chat_member"],), daemon=True).start()
    if "my_chat_member" in u: threading.Thread(target=handle_chat_member, args=(u["my_chat_member"],), daemon=True).start()
    return "ok",200

@app.route("/", methods=["GET"])
def home(): 
    return f"ORBIT ADM V32 by {SIGNATURE} | V24 PRINCIPIOS | BIO DEFINE → IA INTERPRETA → CODIGO VALIDA → PERMISSAO CONFIRMA → TELEGRAM EXECUTA | ONLINE",200

try:
    tg("setWebhook",{"url":f"{RENDER_URL}/","allowed_updates":["message","edited_message","chat_member","my_chat_member"],"secret_token":WEBHOOK_SECRET} if WEBHOOK_SECRET else {"url":f"{RENDER_URL}/","allowed_updates":["message","edited_message","chat_member","my_chat_member"]})
    print(f"[ORBIT V32 by {SIGNATURE}] ONLINE")
except: pass

threading.Thread(target=keep_alive, daemon=True).start()
threading.Thread(target=webhook_guardian, daemon=True).start()

if __name__=="__main__": 
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","10000")))
