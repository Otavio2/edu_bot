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
def tg(m,p):
    try: return get_sess().post(f"{API}/{m}", json=p, timeout=12).json()
    except: return {"ok":False}
def get_contexto(cid):
    try:
        r=tg("getChat",{"chat_id":int(cid)}).get("result",{}) or {}
        titulo=r.get("title","").strip()
        bio=(r.get("description") or "").strip()
        pin_obj=r.get("pinned_message",{}) or {}
        pin=(pin_obj.get("text") or pin_obj.get("caption") or "")[:600].strip()
        dna=f"NOME: {titulo}\nBIO: {bio}\nFIXADO: {pin}"
        lei=f"{bio}\n{pin}".strip()
        return dna, lei, bio, titulo, pin
    except: return "", "", "", "", ""
def get_perms(cid):
    try:
        r=tg("getChatMember",{"chat_id":int(cid),"user_id":BOT_ID}).get("result",{}) or {}
        if r.get("status")=="creator": return {"del":True,"ban":True}
        return {"del":bool(r.get("can_delete_messages")), "ban":bool(r.get("can_restrict_members"))}
    except: return {"del":False,"ban":False}
def is_admin(cid,uid):
    if uid==BOT_ID or uid==DONO_ID: return True
    try: return any(a.get("user",{}).get("id")==uid for a in tg("getChatAdministrators",{"chat_id":int(cid)}).get("result",[]) or [])
    except: return False
def get_b64(fid):
    try:
        fp=tg("getFile",{"file_id":fid}).get("result",{}).get("file_path")
        if not fp: return None,None
        d=get_sess().get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{fp}", timeout=12).content
        if len(d)>4000000: return None,None
        mime="image/png" if fp.endswith(".png") else "image/webp" if fp.endswith(".webp") else "image/jpeg"
        return base64.b64encode(d).decode(), mime
    except: return None,None
def midia(msg):
    if msg.get("photo"): b,m=get_b64(msg["photo"][-1]["file_id"]); return b,m,"foto"
    if msg.get("sticker") and not msg["sticker"].get("is_animated") and not msg["sticker"].get("is_video"):
        fid=msg["sticker"].get("thumbnail",{}).get("file_id") or msg["sticker"].get("file_id")
        b,m=get_b64(fid) if fid else (None,None); return b,m,"sticker"
    if msg.get("animation"): fid=msg["animation"].get("thumbnail",{}).get("file_id"); b,m=get_b64(fid) if fid else (None,None); return b,m,"gif"
    return None,None,None
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
                    r=get_sess().post(f"{cfg['url']}/chat/completions", headers={"Authorization":f"Bearer {key}"}, json={"model":model,"messages":[{"role":"user","content":c}],"temperature":temp,"max_tokens":800}, timeout=18)
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
    return trecho.strip() and len(trecho.strip())>=4 and trecho.strip() in lei
def parse_duracao(t):
    if not t: return 0
    s=t.lower()
    m=re.search(r'(\d+)\s*(segundo|segundos|seg|minuto|minutos|min|hora|horas|h|dia|dias|d)', s)
    if not m:
        m2=re.search(r'(\d+)\s*(s|m|h|d)\b', s)
        if not m2: return 0
        n=int(m2.group(1)); u=m2.group(2)
        return n if u=="s" else n*60 if u=="m" else n*3600 if u=="h" else n*86400
    n=int(m.group(1)); u=m.group(2)
    if "seg" in u: return n
    if "min" in u: return n*60
    if "hora" in u or u=="h": return n*3600
    if "dia" in u or u=="d": return n*86400
    return 0
def fala_humana(fala):
    vars = [fala, f"{fala} - por aqui não", f"opa, {fala.lower()}", f"{fala} ✌️", f"{fala}, combinado?"]
    return random.choice(vars)
def ia_analisa(dna, lei, texto, b64, mime, tipo):
    prompt=f'''Você é interpretador, não autoridade.
CONTEXTO (NOME+BIO+FIXADO):
{dna}
LEI (BIO+FIXADO):
"{lei}"
MENSAGEM: "{texto[:1200]}" Midia={tipo}
REGRAS: Só viole se regra explícita na LEI. Não invente. Link só se LEI falar link. Conflito=viola false. Copie trecho LITERAL.
JSON: {{"viola":bool,"trecho_bio":"","fala":"","confianca":0.0-1.0,"punicao":{{"tipo":"none|ban|mute","trecho_bio_punicao":""}}}}'''
    out=call_ia(prompt,b64,mime, temp=0.05)
    if not out: return None
    try:
        j=json.loads(re.search(r'\{.*\}',out,re.DOTALL).group())
        if "viola" not in j: return None
        if not j.get("viola"): return j
        if not existe_literal(j.get("trecho_bio",""), lei): return None
        if j.get("confianca",0) < 0.85: return None
        p=j.get("punicao",{})
        if p.get("tipo") in ["ban","mute"]:
            if not existe_literal(p.get("trecho_bio_punicao",""), lei):
                j["punicao"]["tipo"]="none"
        return j
    except: return None
def handle(msg):
    cid=msg["chat"]["id"]; mid=msg.get("message_id"); uid=msg["from"]["id"]
    if not hasattr(handle,"seen"): handle.seen={}
    if f"{cid}:{mid}" in handle.seen and time.time()-handle.seen[f"{cid}:{mid}"]<10: return
    handle.seen[f"{cid}:{mid}"]=time.time()
    if len(handle.seen)>200: handle.seen={k:v for k,v in handle.seen.items() if time.time()-v<60}
    if uid==BOT_ID: return
    txt=(msg.get("text") or msg.get("caption") or "").strip()
    dna, lei, bio, titulo, pin = get_contexto(cid)
    if txt.startswith("/"):
        cmd=txt.split()[0].lower().split("@")[0]
        if int(cid) < 0:
            perms=get_perms(cid)
            if perms["del"]:
                tg("deleteMessage",{"chat_id":cid,"message_id":mid})
        if cmd in ["/start","/help","/regras","/ping"]:
            if int(cid)>0:
                tg("sendMessage",{"chat_id":cid,"text":f"🤖 <b>ORBIT ADM by {SIGNATURE}</b>\n100% GRUPO - ADM\nLEI = BIO+FIXADO\nDev: {DONO_NOME}","parse_mode":"HTML"})
            else:
                tg("sendMessage",{"chat_id":cid,"text":f"🤖 <b>ORBIT ADM by {SIGNATURE}</b>\n<b>LEI ATUAL:</b>\n{html.escape(lei)[:1200] or 'VAZIA = NÃO MODERA'}","parse_mode":"HTML"})
        return
    if msg.get("new_chat_members"):
        if lei and any(x in lei.lower() for x in ["bem vindo","bem-vindo","bemvindo","boas vindas","seja bem"]):
            for m in msg["new_chat_members"]:
                if m["id"]==BOT_ID: continue
                try: tg("sendChatAction",{"chat_id":cid,"action":"typing"}); time.sleep(random.uniform(0.6,1.0))
                except: pass
                nome=m.get("first_name","")
                vibe=random.choice(["curta e hype","acolhedora","zoeira leve","elegante","descolada"])
                prompt=f'Você cria boas-vindas.\nCONTEXTO: {dna}\nNOVO: {nome}\nEstilo: {vibe}\nUse SÓ contexto atual. NÃO invente regras. Máx 3 linhas.'
                welcome=call_ia(prompt, temp=0.95)
                if not welcome or len(welcome)<10: welcome=f"👋 {nome}, bem-vindo ao {titulo}!"
                tg("sendMessage",{"chat_id":cid,"text":f'<a href="tg://user?id={m["id"]}">{html.escape(nome)}</a> {html.escape(welcome)[:900]}\nADM by {SIGNATURE}',"parse_mode":"HTML"})
        return
    if int(cid)>0: return
    if is_admin(cid,uid) or uid==DONO_ID:
        if len(txt)>=8 and lei:
            prompt=f'LEI: "{lei}"\nNOME: "{titulo}"\nADM: "{txt[:800]}"\nADM reclamou de algo que NÃO está na LEI? Se sim, sugira regra curta.\nJSON: {{"sugerir":bool,"sugestao_bio":"..."}}'
            out=call_ia(prompt, temp=0.1)
            if out:
                try:
                    j=json.loads(re.search(r'\{.*\}',out,re.DOTALL).group())
                    if j.get("sugerir") and j.get("sugestao_bio"):
                        if not hasattr(handle,"last_tip"): handle.last_tip={}
                        if handle.last_tip.get(cid,0) < time.time()-600:
                            handle.last_tip[cid]=time.time()
                            sug=html.escape(j.get("sugestao_bio",""))
                            tg("sendMessage",{"chat_id":cid,"text":f'💡 Sugestão (não é lei ainda):\n<code>{sug}</code>\nAdicione na BIO/FIXADO.\nADM by {SIGNATURE}',"parse_mode":"HTML"})
                except: pass
        return
    if not lei or not lei.strip(): return
    b64,mime,tipo=midia(msg)
    if not txt and not b64: return
    ia=ia_analisa(dna, lei, txt or "[midia]", b64, mime or "image/jpeg", tipo or "texto")
    if not ia or not ia.get("viola"): return
    perms=get_perms(cid)
    if not perms["del"]: return
    time.sleep(random.uniform(0.8, 1.5))
    del_resp=tg("deleteMessage",{"chat_id":cid,"message_id":mid})
    if not del_resp.get("ok"): return
    try:
        tg("sendChatAction",{"chat_id":cid,"action":"typing"})
        time.sleep(random.uniform(0.7, 1.2))
    except: pass
    nome=html.escape(msg["from"].get("first_name",""))
    fala=html.escape(fala_humana(ia.get("fala","Respeite as regras")[:200]))
    trecho=html.escape(ia.get("trecho_bio","")[:180])
    pun=ia.get("punicao",{})
    if pun.get("tipo")=="ban":
        if not perms["ban"]:
            tg("sendMessage",{"chat_id":cid,"text":f'<a href="tg://user?id={uid}">{nome}</a> {fala}\n<i>{trecho}</i>\n⚠️ Removido, sem permissão pra banir.\nADM by {SIGNATURE}',"parse_mode":"HTML"})
            return
        if existe_literal(pun.get("trecho_bio_punicao",""), lei):
            ban_resp=tg("banChatMember",{"chat_id":cid,"user_id":uid})
            if ban_resp.get("ok"):
                tg("sendMessage",{"chat_id":cid,"text":f'<a href="tg://user?id={uid}">{nome}</a> {fala} - banido\n<i>{trecho}</i>\nADM by {SIGNATURE}',"parse_mode":"HTML"})
            else:
                tg("sendMessage",{"chat_id":cid,"text":f'<a href="tg://user?id={uid}">{nome}</a> {fala}\n<i>{trecho}</i>\n⚠️ Removido, mas ban falhou.\nADM by {SIGNATURE}',"parse_mode":"HTML"})
            return
    if pun.get("tipo")=="mute":
        if not perms["ban"]:
            tg("sendMessage",{"chat_id":cid,"text":f'<a href="tg://user?id={uid}">{nome}</a> {fala}\n<i>{trecho}</i>\n⚠️ Removido, sem permissão pra silenciar.\nADM by {SIGNATURE}',"parse_mode":"HTML"})
            return
        if existe_literal(pun.get("trecho_bio_punicao",""), lei):
            dur=parse_duracao(pun.get("trecho_bio_punicao",""))
            if dur<=0:
                tg("sendMessage",{"chat_id":cid,"text":f'<a href="tg://user?id={uid}">{nome}</a> {fala}\n<i>{trecho}</i>\nADM by {SIGNATURE}',"parse_mode":"HTML"})
                return
            mute_resp=tg("restrictChatMember",{"chat_id":cid,"user_id":uid,"permissions":{"can_send_messages":False},"until_date":int(time.time())+dur})
            if mute_resp.get("ok"):
                tg("sendMessage",{"chat_id":cid,"text":f'<a href="tg://user?id={uid}">{nome}</a> {fala} - silenciado {dur}s\n<i>{trecho}</i>\nADM by {SIGNATURE}',"parse_mode":"HTML"})
            else:
                tg("sendMessage",{"chat_id":cid,"text":f'<a href="tg://user?id={uid}">{nome}</a> {fala}\n<i>{trecho}</i>\n⚠️ Removido, mas mute falhou.\nADM by {SIGNATURE}',"parse_mode":"HTML"})
            return
    tg("sendMessage",{"chat_id":cid,"text":f'<a href="tg://user?id={uid}">{nome}</a> {fala}\n<i>{trecho}</i>\nADM by {SIGNATURE}',"parse_mode":"HTML"})
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
    if "message" in u: threading.Thread(target=handle, args=(u["message"],), daemon=True).start()
    if "edited_message" in u: threading.Thread(target=handle, args=(u["edited_message"],), daemon=True).start()
    return "ok",200
@app.route("/", methods=["GET"])
def home(): return f"ORBIT ADM V30 by {SIGNATURE} | HUMANO SEM MEMÓRIA | ONLINE",200
try:
    tg("setWebhook",{"url":f"{RENDER_URL}/","allowed_updates":["message","edited_message"],"secret_token":WEBHOOK_SECRET} if WEBHOOK_SECRET else {"url":f"{RENDER_URL}/","allowed_updates":["message","edited_message"]})
    print(f"[ORBIT V30 HUMANO by {SIGNATURE}] ONLINE")
except: pass
threading.Thread(target=keep_alive, daemon=True).start()
if __name__=="__main__": app.run(host="0.0.0.0", port=int(os.getenv("PORT","10000")))
