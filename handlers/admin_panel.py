# handlers/admin_panel.py
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, CallbackQueryHandler, MessageHandler, filters
import database

ADD_KEYWORD, ADD_RESPONSE, ADD_CO_ID, ADD_CO_NAME = range(4)

async def panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    role = await database.get_role(user_id)
    
    if not role:
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton("➕ افزودن کلمه کلیدی", callback_data="btn_add_kw")],
        [InlineKeyboardButton("📋 لیست کلمات آموزش‌دیده", callback_data="btn_list_kw")]
    ]
    
    if role == 'admin':
        keyboard.append([InlineKeyboardButton("👤 افزودن کو-ادمین جدید", callback_data="btn_add_co")])
        keyboard.append([InlineKeyboardButton("👑 لیست ادمین‌ها", callback_data="btn_list_ad")])
        
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🛠 به پنل مدیریت ربات خوش آمدید.\nیک گزینه را انتخاب کنید:", reply_markup=reply_markup)
    return ConversationHandler.END

async def inline_button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    role = await database.get_role(user_id)
    
    await query.answer()
    if not role:
        return

    if query.data == "btn_add_kw":
        await query.edit_message_text("📝 کلمه کلیدی جدید را ارسال کنید:")
        return ADD_KEYWORD
    elif query.data == "btn_add_co" and role == 'admin':
        await query.edit_message_text("🆔 آیدی عددی کو-ادمین جدید را بفرستید:")
        return ADD_CO_ID

    elif query.data == "btn_list_kw":
        await display_beautiful_keywords(query)
    elif query.data == "btn_list_ad" and role == 'admin':
        await display_beautiful_admins(query)
    elif query.data.startswith("del_"):
        kw_id = int(query.data.split("_")[1])
        conn = await database.get_db_connection()
        try:
            await conn.execute('DELETE FROM bot_keywords WHERE id = $1', kw_id)
            await display_beautiful_keywords(query, "✅ کلمه با موفقیت حذف شد.\n\n")
        finally:
            await conn.close()

    elif query.data.startswith("remad_") and role == 'admin':
        co_id = int(query.data.split("_")[1])
        conn = await database.get_db_connection()
        try:
            await conn.execute("DELETE FROM bot_admins WHERE user_id = $1 AND role = 'co_admin'", co_id)
            await display_beautiful_admins(query, "✅ کو-ادمین با موفقیت حذف شد.\n\n")
        finally:
            await conn.close()

    return ConversationHandler.END

async def display_beautiful_keywords(query, prefix=""):
    conn = await database.get_db_connection()
    try:
        rows = await conn.fetch('SELECT id, keyword, response FROM bot_keywords ORDER BY id DESC')
        if not rows:
            await query.edit_message_text(f"{prefix}ℹ️ هنوز هیچ کلمه‌ای آموزش داده نشده است.")
            return
        text = f"{prefix}📋 **لیست کلمات فعال ربات:**\n\n"
        keyboard = []
        for i, r in enumerate(rows, 1):
            text += f"{i}. `{r['keyword']}` = {r['response']}\n"
            keyboard.append([InlineKeyboardButton(f"🗑 حذف کلمه {i} ({r['keyword']})", callback_data=f"del_{r['id']}")])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    finally:
        await conn.close()

# --- بخش مکالمات افزودن کلمه و ادمین ---
async def process_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['temp_kw'] = update.message.text.strip()
    await update.message.reply_text("💬 پاسخ کلمه را ارسال کنید:")
    return ADD_RESPONSE

async def process_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyword = context.user_data.get('temp_kw')
    response = update.message.text.strip()
    conn = await database.get_db_connection()
    try:
        await conn.execute('INSERT INTO bot_keywords (keyword, response) VALUES ($1, $2) ON CONFLICT (keyword) DO UPDATE SET response = $2', keyword, response)
        await update.message.reply_text(f"✅ کلمه کلیدی ذخیره شد!")
    finally:
        await conn.close()
        context.user_data.clear()
    return ConversationHandler.END

async def process_co_admin_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['temp_co_id'] = int(update.message.text.strip())
        await update.message.reply_text("✍️ نام یا لقب کو-ادمین:")
        return ADD_CO_NAME
    except ValueError:
        await update.message.reply_text("❌ خطا در فرمت آیدی عددی.")
        return ADD_CO_ID

async def process_co_admin_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    co_id = context.user_data.get('temp_co_id')
    co_name = update.message.text.strip()
    conn = await database.get_db_connection()
    try:
        await conn.execute('INSERT INTO bot_admins (user_id, role, name) VALUES ($1, \'co_admin\', $2) ON CONFLICT (user_id) DO UPDATE SET name = $2', co_id, co_name)
        await update.message.reply_text(f"✅ کاربر {co_name} ثبت شد.")
    finally:
        await conn.close()
        context.user_data.clear()
    return ConversationHandler.END

async def cancel_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ عملیات لغو شد.")
    return ConversationHandler.END

panel_conversation = ConversationHandler(
    entry_points=[
        CommandHandler("panel", panel_command),
        CallbackQueryHandler(inline_button_router, pattern="^(btn_|del_|remad_)")
    ],
    states={
        ADD_KEYWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_keyword)],
        ADD_RESPONSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_response)],
        ADD_CO_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_co_admin_id)],
        ADD_CO_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_co_admin_name)]
    },
    fallbacks=[CommandHandler("cancel", cancel_action)],
    per_message=False,
    allow_reentry=True
)
