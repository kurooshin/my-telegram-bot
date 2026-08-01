"""پاسخ هوشمند شخصی‌سازی‌شده — یادگیری سبک نوشتاری و پیشنهاد پاسخ با یک ضربه."""

import logging
import secrets

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
import database
import ai_engine

logger = logging.getLogger(__name__)

# تعداد پیام لازم بین دو به‌روزرسانی سبک
STYLE_UPDATE_THRESHOLD = 15

# شمارنده‌ی پیام در حافظه (in-memory) — کاربر → تعداد پیام از آخرین به‌روزرسانی سبک
_msg_counts: dict[int, int] = {}

# نگاشت id کوتاه → متن پیشنهاد (چون callback_data سقف ۶۴ بایتی داره)
_suggestion_map: dict[str, str] = {}

MAX_BUTTON_TEXT = 64


async def track_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """هر پیام متنی گروه رو ذخیره می‌کنه و گاهی اوقات سبک کاربر رو به‌روزرسانی می‌کنه."""
    if update.effective_chat.type not in ("group", "supergroup"):
        return
    msg = update.message
    if not msg or not msg.text:
        return

    user = update.effective_user
    if not user:
        return

    user_id = user.id
    chat_id = update.effective_chat.id

    try:
        await database.save_message(user_id, chat_id, msg.text[:500])
    except Exception as e:
        logger.error("❌ ذخیره پیام ناموفق: %s", e, exc_info=True)

    count = _msg_counts.get(user_id, 0) + 1
    if count >= STYLE_UPDATE_THRESHOLD:
        _msg_counts[user_id] = 0
        context.application.create_task(_update_style_async(user_id))
    else:
        _msg_counts[user_id] = count


async def _update_style_async(user_id: int):
    try:
        await ai_engine.update_style_profile(user_id)
        _msg_counts[user_id] = 0
    except Exception as e:
        logger.error("❌ به‌روزرسانی سبک کاربر %s ناموفق: %s", user_id, e, exc_info=True)


async def suggest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    style = await database.get_style(user_id)
    if not style:
        await update.message.reply_text(
            "😅 هنوز به اندازه‌ی کافی از پیام‌هات ندیدم تا سبکت رو یاد بگیرم.\n"
            "یه چند پیام دیگه توی گروه بفرست تا بتونم پیشنهادهای شخصی بهت بدم."
        )
        return

    await update.message.reply_text("⏳ دارم پیشنهادها رو آماده می‌کنم...")

    try:
        recent = await database.get_recent_messages(user_id, limit=10)
        context_messages = [m['text'] for m in recent if m.get('chat_id') == chat_id][:5]
        suggestions = await ai_engine.generate_suggestions(style, context_messages)
    except Exception as e:
        logger.error("❌ خطا در تولید پیشنهاد برای %s: %s", user_id, e, exc_info=True)
        await update.message.reply_text("❌ خطایی پیش اومد. یه بار دیگه تلاش کن.")
        return

    if not suggestions:
        await update.message.reply_text("❌ پیشنهادی ساخته نشد. یه بار دیگه تلاش کن.")
        return

    keyboard = []
    for text in suggestions:
        token = secrets.token_hex(8)
        _suggestion_map[token] = text
        label = text if len(text) <= MAX_BUTTON_TEXT else text[:MAX_BUTTON_TEXT - 1] + "…"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"sug_{token}")])

    await update.message.reply_text(
        "💬 پیشنهادهای من (یکی رو بزن تا همون‌موقع فرستاده بشه):",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def suggestion_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    token = query.data.removeprefix("sug_")
    text = _suggestion_map.pop(token, None)
    if not text:
        await query.edit_message_text("⌛ این پیشنهاد منقضی شده. دوباره /suggest بزن.")
        return

    chat_id = query.message.chat_id
    await context.bot.send_message(chat_id=chat_id, text=text)
    try:
        await query.message.delete()
    except Exception:
        pass


async def forget_me_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await database.delete_user_data(user_id)
    _msg_counts.pop(user_id, None)
    await update.message.reply_text(
        "🗑 همه‌ی پیام‌ها و اطلاعات سبک نوشتاری تو حذف شد.\n"
        "از این به بعد هیچی از تو ذخیره نمی‌کنم تا دوباره پیام بفرستی."
    )


async def privacy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔒 حریم خصوصی:\n"
        "این بات برای شخصی‌سازی پیشنهادهای پاسخ، تعداد محدودی از آخرین پیام‌هات "
        "در گروه رو (یک پنجره‌ی چرخان از ۳۰ تا ۴۰ پیام) نگه می‌داره و ازش یه "
        "خلاصه‌ی سبک نوشتاری می‌سازه. پیام‌های خام به‌صورت دائمی نگه‌داری نمی‌شن.\n"
        "برای حذف کامل داده‌هات، دستور /forget_me رو بزن."
    )


smart_reply_learning = MessageHandler(
    filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS, track_message
)
suggest_handler = CommandHandler("suggest", suggest_command)
suggestion_callback_handler = CallbackQueryHandler(suggestion_callback, pattern="^sug_")
forget_me_handler = CommandHandler("forget_me", forget_me_command)
privacy_handler = CommandHandler("privacy", privacy_command)
