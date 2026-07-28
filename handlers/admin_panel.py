"""Admin panel — keyword management, co-admin management, group toggling."""

import warnings
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, CallbackQueryHandler, MessageHandler, filters
import database

warnings.filterwarnings("ignore", message=".*per_message.*CallbackQueryHandler.*")

ADD_KEYWORD, ADD_RESPONSE, ADD_MATCH_TYPE, ADD_CO_ID, ADD_CO_NAME = range(5)


async def panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    role = await database.get_role(user_id)
    if not role:
        return ConversationHandler.END

    chat = update.effective_chat

    # Always read fresh from DB — never use cached variables
    try:
        ai_on = await database.is_ai_enabled(chat.id) if chat else False
    except Exception as e:
        logger.error("panel_command: is_ai_enabled failed for %s: %s", chat.id, e)
        ai_on = False

    ai_label = "🟢 AI روشن است (خاموش کن)" if ai_on else "🔴 AI خاموش است (روشن کن)"

    keyboard = [
        [InlineKeyboardButton("➕ Add Keyword", callback_data="btn_add_kw")],
        [InlineKeyboardButton("📋 List Keywords", callback_data="btn_list_kw")],
        [InlineKeyboardButton(ai_label, callback_data="btn_toggle_ai")],
        [InlineKeyboardButton("💡 پیام‌های بی‌جواب پرتکرار", callback_data="btn_unmatched")],
    ]
    if role == 'admin':
        keyboard.append([InlineKeyboardButton("👤 Add Co-Admin", callback_data="btn_add_co")])
        keyboard.append([InlineKeyboardButton("👑 List Admins", callback_data="btn_list_ad")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🛠 Admin Panel\nChoose an option:", reply_markup=reply_markup)
    return ConversationHandler.END


async def inline_button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    # Always acknowledge the callback FIRST — before any DB calls,
    # so the Telegram loading spinner stops even if something crashes later.
    await query.answer()

    try:
        user_id = update.effective_user.id
        role = await database.get_role(user_id)
        if not role:
            await query.edit_message_text("⛔ دسترسی غیرمجاز. شما ادمین نیستید.")
            return ConversationHandler.END
    except Exception as e:
        logger.error("Role check error in inline_button_router: %s", e, exc_info=True)
        await query.edit_message_text("❌ خطا در بررسی دسترسی. لاگ را بررسی کن.")
        return ConversationHandler.END

    try:
        if query.data == "btn_add_kw":
            await query.edit_message_text("📝 Send the new keyword:")
            return ADD_KEYWORD
        elif query.data == "btn_add_co" and role == 'admin':
            await query.edit_message_text("🆔 Send the co-admin's numeric user ID:")
            return ADD_CO_ID
        elif query.data == "btn_list_kw":
            await display_beautiful_keywords(query)
        elif query.data == "btn_list_ad" and role == 'admin':
            await display_beautiful_admins(query)
        elif query.data == "btn_toggle_ai":
            chat = update.effective_chat
            current = await database.is_ai_enabled(chat.id)
            new_state = not current
            ok = await database.set_ai_enabled(chat.id, new_state, title=chat.title)
            if not ok:
                logger.error("AI toggle DB write returned False for chat %s", chat.id)
                await query.edit_message_text("❌ خطا در ذخیره‌سازی وضعیت AI در دیتابیس. لاگ را بررسی کن.")
                return ConversationHandler.END
            status = "✅ AI روشن شد" if new_state else "❌ AI خاموش شد"
            await query.edit_message_text(f"{status}\n\nاز /panel برای بازگشت به پنل استفاده کن.")
        elif query.data == "btn_unmatched":
            top = await database.get_top_unmatched(limit=15)
            if not top:
                await query.edit_message_text("ℹ️ هنوز پیام بی‌جوابی ثبت نشده.")
                return ConversationHandler.END
            lines = ["💡 **پیام‌های بی‌جواب پرتکرار:**\n"]
            for i, item in enumerate(top, 1):
                txt = _esc_md(item['text'][:40])
                lines.append(f"{i}. `{txt}` — {item['total']} بار")
            await query.edit_message_text("\n".join(lines), parse_mode="Markdown")
        elif query.data.startswith("del_"):
            kw_id = int(query.data.split("_")[1])
            pool = await database.get_pool()
            async with pool.acquire() as conn:
                await conn.execute('DELETE FROM bot_keywords WHERE id = $1', kw_id)
                await display_beautiful_keywords(query, "✅ Keyword deleted.\n\n")
        elif query.data.startswith("remad_") and role == 'admin':
            co_id = int(query.data.split("_")[1])
            pool = await database.get_pool()
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM bot_admins WHERE user_id = $1 AND role = 'co_admin'", co_id)
                await display_beautiful_admins(query, "✅ Co-admin removed.\n\n")
    except Exception as e:
        logger.error("Callback handler error for %s: %s", query.data, e, exc_info=True)
        await query.edit_message_text("❌ خطا در پردازش درخواست. لاگ را بررسی کن.")

    return ConversationHandler.END


def _esc_md(text: str) -> str:
    """Escape Telegram Markdown v1 special characters."""
    if not text:
        return ""
    for char in ('_', '*', '`', '['):
        text = text.replace(char, f'\\{char}')
    return text


async def display_beautiful_keywords(query, prefix=""):
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch('SELECT id, keyword, response, match_type FROM bot_keywords ORDER BY id DESC')
        if not rows:
            await query.edit_message_text(f"{prefix}ℹ️ No keywords saved yet.")
            return
        text = f"{prefix}📋 **Keywords:**\n\n"
        keyboard = []
        for i, r in enumerate(rows, 1):
            mt = "🔴" if r['match_type'] == 'exact' else "🟢"
            kw_safe = _esc_md(r['keyword'])
            res_safe = _esc_md(r['response'])
            text += f"{i}. `{kw_safe}` = {res_safe} {mt}\n"
            keyboard.append([InlineKeyboardButton(f"🗑 Delete {i} ({r['keyword'][:15]})", callback_data=f"del_{r['id']}")])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def display_beautiful_admins(query, prefix=""):
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch('SELECT user_id, role, name FROM bot_admins ORDER BY role DESC, name ASC')
        if not rows:
            await query.edit_message_text(f"{prefix}ℹ️ No admins found.")
            return
        text = f"{prefix}👑 **Admins:**\n\n"
        keyboard = []
        for i, r in enumerate(rows, 1):
            role_emoji = "👑" if r['role'] == 'admin' else "👤"
            name_safe = _esc_md(r['name'] or 'Unknown')
            text += f"{i}. {role_emoji} `{r['user_id']}` — {name_safe} ({r['role']})\n"
            if r['role'] == 'co_admin':
                keyboard.append([InlineKeyboardButton(f"🗑 Remove {r['name']}", callback_data=f"remad_{r['user_id']}")])
        markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)


async def process_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['temp_kw'] = database.normalize_persian(update.message.text.strip())
    await update.message.reply_text("💬 Send the response for this keyword:")
    return ADD_RESPONSE


async def process_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['temp_res'] = update.message.text.strip()
    keyboard = [
        [InlineKeyboardButton("🟢 Flexible (default)", callback_data="mtype_flexible")],
        [InlineKeyboardButton("🔴 Exact match", callback_data="mtype_exact")]
    ]
    await update.message.reply_text(
        "Choose match type:\n\n"
        "🟢 Flexible — matches variations (e.g. 'hello' matches 'helloo')\n"
        "🔴 Exact — only the exact word matches",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ADD_MATCH_TYPE


async def process_match_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    match_type = query.data.replace("mtype_", "")
    keyword = context.user_data.get('temp_kw')
    response = context.user_data.get('temp_res')
    if not keyword or not response:
        await query.edit_message_text("❌ Session expired. Use /panel to start over.")
        context.user_data.clear()
        return ConversationHandler.END
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            'INSERT INTO bot_keywords (keyword, response, match_type) VALUES ($1, $2, $3) '
            'ON CONFLICT (keyword) DO UPDATE SET response = $2, match_type = $3',
            keyword, response, match_type
        )
        type_label = "exact" if match_type == "exact" else "flexible"
        await query.edit_message_text(f"✅ Keyword saved! (type: {type_label})")
    context.user_data.clear()
    return ConversationHandler.END


async def process_co_admin_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['temp_co_id'] = int(update.message.text.strip())
        await update.message.reply_text("✍️ Send the co-admin's name:")
        return ADD_CO_NAME
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID. Must be a number.")
        return ADD_CO_ID


async def process_co_admin_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    co_id = context.user_data.get('temp_co_id')
    co_name = update.message.text.strip()
    if not co_id:
        await update.message.reply_text("❌ Session expired. Use /panel to start over.")
        context.user_data.clear()
        return ConversationHandler.END
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            'INSERT INTO bot_admins (user_id, role, name) VALUES ($1, \'co_admin\', $2) '
            'ON CONFLICT (user_id) DO UPDATE SET name = $2',
            co_id, co_name
        )
        await update.message.reply_text(f"✅ Co-admin {co_name} registered.")
    context.user_data.clear()
    return ConversationHandler.END


async def cancel_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END


async def toggle_group_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    role = await database.get_role(user_id)
    if not role:
        return

    chat = update.effective_chat
    parts = (update.message.text or '').split()

    if len(parts) >= 2:
        try:
            target_id = int(parts[1])
        except ValueError:
            await update.message.reply_text("❌ Invalid group ID. Usage: /toggle_group <group_id>")
            return
    elif chat.type in ("group", "supergroup"):
        target_id = chat.id
    else:
        await update.message.reply_text("❌ Usage: /toggle_group <group_id> (or run in a group)")
        return

    pool = await database.get_pool()
    async with pool.acquire() as conn:
        current = await conn.fetchval('SELECT is_active FROM bot_groups WHERE group_id = $1', target_id)
        if current is None:
            await update.message.reply_text("❌ Group not found in database.")
            return
        new = not current
        await conn.execute(
            'UPDATE bot_groups SET is_active = $1, updated_at = CURRENT_TIMESTAMP WHERE group_id = $2',
            new, target_id
        )
        status = "✅ enabled" if new else "❌ disabled"
        await update.message.reply_text(f"Group `{target_id}` {status}.")


async def debug_ai_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """موقت — ردیف خام دیتابیس این چت رو نشون میده برای عیب‌یابی"""
    user_id = update.effective_user.id
    role = await database.get_role(user_id)
    if not role:
        return
    chat = update.effective_chat
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow('SELECT * FROM bot_groups WHERE group_id = $1', chat.id)
    if not row:
        await update.message.reply_text(f"❌ No row found for chat_id={chat.id}")
        return
    await update.message.reply_text(
        f"chat_id: {chat.id}\nis_active: {row['is_active']}\n"
        f"ai_enabled: {row['ai_enabled']}\nupdated_at: {row['updated_at']}"
    )


toggle_group_handler = CommandHandler("toggle_group", toggle_group_command)
debug_ai_handler = CommandHandler("debug_ai", debug_ai_command)

panel_conversation = ConversationHandler(
    entry_points=[
        CommandHandler("panel", panel_command),
        CallbackQueryHandler(inline_button_router, pattern="^(btn_|del_|remad_)")
    ],
    states={
        ADD_KEYWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_keyword)],
        ADD_RESPONSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_response)],
        ADD_MATCH_TYPE: [CallbackQueryHandler(process_match_type, pattern="^mtype_")],
        ADD_CO_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_co_admin_id)],
        ADD_CO_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_co_admin_name)]
    },
    fallbacks=[CommandHandler("cancel", cancel_action)],
    per_message=False,
    allow_reentry=True
)

