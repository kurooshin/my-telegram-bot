from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

async def say(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    command_prefix = "/say "
    if not text.startswith(command_prefix):
        return
    reply_text = text[len(command_prefix):].strip()
    if not reply_text:
        await update.message.reply_text("متن را بعد از /say بنویسید.")
        return
    reply_to = update.message.reply_to_message
    chat_id = update.effective_chat.id
    await update.message.delete()
    if reply_to:
        await context.bot.send_message(chat_id=chat_id, text=reply_text, reply_to_message_id=reply_to.message_id)
    else:
        await context.bot.send_message(chat_id=chat_id, text=reply_text)

say_handler = CommandHandler("say", say)
