import json
import logging
import random
from flask import Flask, request
from telegram import (
    Update, InlineKeyboardButton,
    InlineKeyboardMarkup, InputSticker
)
from telegram.ext import (
    ApplicationBuilder, ContextTypes,
    MessageHandler, CommandHandler,
    CallbackQueryHandler, filters
)
from apscheduler.schedulers.background import BackgroundScheduler
from config import TOKEN, URL, OWNER_ID, BOT_NAME

logging.basicConfig(level=logging.INFO)

# ===== LOAD =====
def load(file, default):
    try:
        with open(file) as f:
            return json.load(f)
    except:
        return default

cache = load("cache.json", {"processed": []})
users = load("users.json", {"chats": {}, "config": {}})

def save():
    json.dump(cache, open("cache.json","w"))
    json.dump(users, open("users.json","w"))

# ===== DETECTAR TIPO =====
def detectar_tipo(msg):
    if msg.photo:
        return "imagem"
    elif msg.video:
        return "video"
    elif msg.text:
        return "texto"
    return "outro"

# ===== LEGENDAS =====
def gerar_legenda(tipo):
    frases = {
        "imagem": ["📸 Olha isso!", "🔥 Top demais!", "👀 Veja isso!"],
        "video": ["🎥 Assiste isso!", "🔥 Vídeo brabo!", "🚀 Imperdível!"],
        "texto": ["💬 Reflexão:", "🧠 Pense nisso:", "📢 Mensagem:"]
    }
    base = random.choice(frases.get(tipo, ["✨ Confira"]))
    return f"{base}\n\n{BOT_NAME}"

# ===== PAINEL =====
async def painel(update, context):
    kb = [
        [InlineKeyboardButton("⚙️ Ativar", callback_data="ativar")],
        [InlineKeyboardButton("📊 Status", callback_data="status")]
    ]
    await update.message.reply_text(
        f"🧠 Painel {BOT_NAME}",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# ===== BOTÕES =====
async def buttons(update, context):
    q = update.callback_query
    await q.answer()
    chat = str(q.message.chat_id)

    if q.data == "ativar":
        users["chats"][chat] = True
        save()
        await q.edit_message_text("✅ Ativado!")

    elif q.data == "status":
        ativo = users["chats"].get(chat, False)
        await q.edit_message_text(f"Ativo: {'✅' if ativo else '❌'}")

# ===== ADMIN =====
async def is_admin(chat_id, bot):
    try:
        m = await bot.get_chat_member(chat_id, bot.id)
        return m.status in ["administrator", "creator"]
    except:
        return False

# ===== PROCESSAMENTO =====
async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post or update.message
    if not msg:
        return

    key = f"{msg.chat_id}_{msg.message_id}"
    if key in cache["processed"]:
        return

    cache["processed"].append(key)

    tipo = detectar_tipo(msg)
    legenda = gerar_legenda(tipo)

    # ===== REPOST =====
    for chat in users["chats"]:
        try:
            if not await is_admin(int(chat), context.bot):
                continue

            await context.bot.copy_message(
                chat_id=int(chat),
                from_chat_id=msg.chat_id,
                message_id=msg.message_id,
                caption=legenda
            )
        except:
            pass

    # ===== STICKERS POR TIPO =====
    if msg.photo:
        try:
            file = await msg.photo[-1].get_file()
            path = f"{tipo}.webp"
            await file.download_to_drive(path)

            pack_name = f"{tipo}_pack_by_bot"

            await context.bot.add_sticker_to_set(
                user_id=OWNER_ID,
                name=pack_name,
                sticker=InputSticker(path, emoji_list=["🔥"])
            )
        except:
            pass

    save()

# ===== SCHEDULER =====
scheduler = BackgroundScheduler()
scheduler.add_job(save, "interval", minutes=10)
scheduler.start()

# ===== FLASK =====
app = Flask(__name__)

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), app_bot.bot)
    app_bot.update_queue.put_nowait(update)
    return "ok"

@app.route("/")
def home():
    return "ONLINE"

# ===== BOT =====
app_bot = ApplicationBuilder().token(TOKEN).build()

app_bot.add_handler(CommandHandler("painel", painel))
app_bot.add_handler(CallbackQueryHandler(buttons))
app_bot.add_handler(MessageHandler(filters.ALL, handle))

# ===== START =====
if __name__ == "__main__":
    app_bot.run_webhook(
        listen="0.0.0.0",
        port=10000,
        url_path=TOKEN,
        webhook_url=f"{URL}/{TOKEN}"
)
