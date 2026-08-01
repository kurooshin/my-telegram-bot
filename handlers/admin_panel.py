"""Admin panel — keyword management, co-admin management, group toggling."""

import logging
import warnings
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, CallbackQueryHandler, MessageHandler, filters
import database

logger = logging.getLogger(__name__)
warnings.filterwarnings("ignore", message=".*per_message.*CallbackQueryHandler.*")

ADD_KEYWORD, ADD_RESPONSE, ADD_MATCH_TYPE, ADD_CO_ID, ADD_CO_NAME, EDIT_PERSONA, SET_TRIGGER = range(7)


async def _get_target_chat(context, chat, role):
    """Determine the target chat_id for panel operations.

    In a group → the group itself.
    In private → context.user_data['target_group_id'] (set by group selector).
    Returns (chat_id, title, source_description).
    """
    if chat.type in ("group", "supergroup"):
        return chat.id, chat.title, "group"

    target = context.user_data.get("target_group_id")
    if target:
        pool = await database.get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT title FROM bot_groups WHERE group_id = $1", target
            )
            title = row["title"] if row else "Unknown"
        return target, title, "target"

    return None, None, None


async def panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    role = await database.get_role(user_id)
    if not role:
        return ConversationHandler.END

    chat = update.effective_chat

    # In a group → per-group panel (unchanged)
    if chat.type in ("group", "supergroup"):
        target_id = chat.id
        target_title = chat.title
        try:
            ai_on = await database.is_ai_enabled(target_id)
        except Exception as e:
            logger.error("panel_command: is_ai_enabled failed for %s: %s", target_id, e)
            ai_on = False
        header = f"🛠 **پنل مدیریت** — {_esc_md(str(target_title))}\n\n"
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
            keyboard.append([InlineKeyboardButton("🎭 تنظیم شخصیت/معرفی بات", callback_data="btn_edit_persona")])
            keyboard.append([InlineKeyboardButton("📢 تنظیم کلمه صدا زدن بات", callback_data="btn_set_trigger")])
        await update.message.reply_text(
            header, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
        return ConversationHandler.END

    # Private chat → global management panel
    return await _show_global_panel(update, role)


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
        chat = update.effective_chat

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
            if chat.type in ("group", "supergroup"):
                target_chat_id = chat.id
                target_title = chat.title
            else:
                target_chat_id = context.user_data.get("selected_group_id")
                target_title = context.user_data.get("selected_group_title", "Unknown")
                if not target_chat_id:
                    await query.edit_message_text("❌ لطفاً اول یک گروه را از «مدیریت گروه‌ها» انتخاب کن.")
                    return ConversationHandler.END
            current = await database.is_ai_enabled(target_chat_id)
            new_state = not current
            logger.info("AI toggle: target=%s current=%s new=%s", target_chat_id, current, new_state)
            ok = await database.set_ai_enabled(target_chat_id, new_state, title=target_title)
            if not ok:
                logger.error("AI toggle FAILED: target=%s write returned False", target_chat_id)
                await query.edit_message_text("❌ خطا در ذخیره‌سازی وضعیت AI در دیتابیس. لاگ را بررسی کن.")
                return ConversationHandler.END
            verify = await database.is_ai_enabled(target_chat_id)
            logger.info("AI toggle: target=%s verify=%s expected=%s", target_chat_id, verify, new_state)
            if verify != new_state:
                logger.error("AI toggle MISMATCH: target=%s wrote=%s read-back=%s", target_chat_id, new_state, verify)
                await query.edit_message_text("❌ خطا: وضعیت AI ذخیره شد اما تأیید نشد. دوباره تلاش کن.")
                return ConversationHandler.END
            status = "✅ AI روشن شد" if new_state else "❌ AI خاموش شد"
            await query.edit_message_text(f"{status}\n\nاز /panel برای بازگشت به پنل استفاده کن.")
        elif query.data == "btn_pick_group":
            await _show_group_picker(query)
        elif query.data == "btn_close":
            await query.edit_message_text("✅ بسته شد.")
        elif query.data.startswith("btn_setgroup_"):
            gid = int(query.data.split("_", 2)[2])
            pool = await database.get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT title FROM bot_groups WHERE group_id = $1", gid
                )
            if not row:
                await query.edit_message_text("❌ گروه یافت نشد.")
                return ConversationHandler.END
            context.user_data["target_group_id"] = gid
            context.user_data["target_group_title"] = row["title"]
            # Re-show panel for this group
            target_title = row["title"]
            try:
                ai_on = await database.is_ai_enabled(gid)
            except Exception as e:
                logger.error("panel: is_ai_enabled failed for %s: %s", gid, e)
                ai_on = False
            ai_label = "🟢 AI روشن است (خاموش کن)" if ai_on else "🔴 AI خاموش است (روشن کن)"
            header = f"🛠 **پنل مدیریت** — {_esc_md(str(target_title))}\n\n"
            kb = [
                [InlineKeyboardButton("➕ Add Keyword", callback_data="btn_add_kw")],
                [InlineKeyboardButton("📋 List Keywords", callback_data="btn_list_kw")],
                [InlineKeyboardButton(ai_label, callback_data="btn_toggle_ai")],
                [InlineKeyboardButton("💡 پیام‌های بی‌جواب پرتکرار", callback_data="btn_unmatched")],
            ]
            if role == 'admin':
                kb.append([InlineKeyboardButton("👤 Add Co-Admin", callback_data="btn_add_co")])
                kb.append([InlineKeyboardButton("👑 List Admins", callback_data="btn_list_ad")])
                kb.append([InlineKeyboardButton("🎭 تنظیم شخصیت/معرفی بات", callback_data="btn_edit_persona")])
                kb.append([InlineKeyboardButton("📢 تنظیم کلمه صدا زدن بات", callback_data="btn_set_trigger")])
            kb.append([InlineKeyboardButton("🔙 تغییر گروه", callback_data="btn_pick_group")])
            await query.edit_message_text(
                header, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
            )
        elif query.data == "btn_edit_persona" and role == 'admin':
            current = await database.get_bot_persona()
            if current:
                msg = (
                    f"🎭 **شخصیت/معرفی فعلی بات:**\n\n{current}\n\n"
                    "✍️ متن جدید را بفرست (یا /cancel برای انصراف):"
                )
            else:
                msg = (
                    "🎭 هنوز شخصیتی تنظیم نشده.\n\n"
                    "✍️ متن معرفی و قوانین بات را بفرست (یا /cancel برای انصراف):"
                )
            await query.edit_message_text(msg, parse_mode="Markdown")
            return EDIT_PERSONA
        elif query.data == "btn_set_trigger" and role == 'admin':
            current = await database.get_trigger_word()
            await query.edit_message_text(
                f"📢 کلمه صدا زدن فعلی: `{current}`\n\n"
                "✍️ کلمه جدید را بفرست (یا /cancel برای انصراف):",
                parse_mode="Markdown"
            )
            return SET_TRIGGER
        elif query.data == "btn_list_groups" and role == 'admin':
            await _show_group_management_list(query, role)
        elif query.data.startswith("pmgrp_") and role == 'admin':
            gid = int(query.data.split("_", 1)[1])
            context.user_data["selected_group_id"] = gid
            await _show_submenu_for_group(query, gid, role)
        elif query.data == "btn_toggle_ai_pm" and role == 'admin':
            gid = context.user_data.get("selected_group_id")
            if not gid:
                await query.edit_message_text("❌ لطفاً دوباره از لیست گروه‌ها انتخاب کن.")
                await _show_group_management_list(query, role)
                return ConversationHandler.END
            current = await database.is_ai_enabled(gid)
            new_state = not current
            ok = await database.set_ai_enabled(gid, new_state)
            if not ok:
                await query.edit_message_text("❌ خطا در ذخیره‌سازی وضعیت AI.")
                return ConversationHandler.END
            await _show_submenu_for_group(query, gid, role)
            return ConversationHandler.END
        elif query.data == "btn_toggle_active_pm" and role == 'admin':
            gid = context.user_data.get("selected_group_id")
            if not gid:
                await query.edit_message_text("❌ لطفاً دوباره از لیست گروه‌ها انتخاب کن.")
                await _show_group_management_list(query, role)
                return ConversationHandler.END
            await _toggle_group_active(gid, query)
            await _show_submenu_for_group(query, gid, role)
            return ConversationHandler.END
        elif query.data == "btn_back_global" and role == 'admin':
            keyboard = [
                [InlineKeyboardButton("➕ Add Keyword", callback_data="btn_add_kw")],
                [InlineKeyboardButton("📋 List Keywords", callback_data="btn_list_kw")],
                [InlineKeyboardButton("💡 پیام‌های بی‌جواب پرتکرار", callback_data="btn_unmatched")],
            ]
            if role == 'admin':
                keyboard.append([InlineKeyboardButton("👤 Add Co-Admin", callback_data="btn_add_co")])
                keyboard.append([InlineKeyboardButton("👑 List Admins", callback_data="btn_list_ad")])
                keyboard.append([InlineKeyboardButton("🎭 تنظیم شخصیت/معرفی بات", callback_data="btn_edit_persona")])
                keyboard.append([InlineKeyboardButton("📢 تنظیم کلمه صدا زدن بات", callback_data="btn_set_trigger")])
                keyboard.append([InlineKeyboardButton("🏘 مدیریت گروه‌ها", callback_data="btn_list_groups")])
            await query.edit_message_text(
                "🛠 **پنل مدیریت (تنظیمات سراسری)**",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )
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


async def _show_group_picker(query):
    """Edit the current message into a group picker list."""
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT group_id, title, ai_enabled FROM bot_groups ORDER BY updated_at DESC NULLS LAST"
        )
    if not rows:
        await query.edit_message_text("ℹ️ هیچ گروهی در دیتابیس ثبت نشده.")
        return
    keyboard = []
    for r in rows:
        gid = r["group_id"]
        gtitle = r["title"] or str(gid)
        status = "🟢" if r["ai_enabled"] else "🔴"
        label = f"{status} {gtitle[:35]}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"btn_setgroup_{gid}")])
    keyboard.append([InlineKeyboardButton("❌ بستن", callback_data="btn_close")])
    await query.edit_message_text(
        "📋 **انتخاب گروه**\nگروه مورد نظر را انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def _show_global_panel(update, role: str):
    """Show the global management panel in private chat."""
    keyboard = [
        [InlineKeyboardButton("➕ Add Keyword", callback_data="btn_add_kw")],
        [InlineKeyboardButton("📋 List Keywords", callback_data="btn_list_kw")],
        [InlineKeyboardButton("💡 پیام‌های بی‌جواب پرتکرار", callback_data="btn_unmatched")],
    ]
    if role == 'admin':
        keyboard.append([InlineKeyboardButton("👤 Add Co-Admin", callback_data="btn_add_co")])
        keyboard.append([InlineKeyboardButton("👑 List Admins", callback_data="btn_list_ad")])
        keyboard.append([InlineKeyboardButton("🎭 تنظیم شخصیت/معرفی بات", callback_data="btn_edit_persona")])
        keyboard.append([InlineKeyboardButton("📢 تنظیم کلمه صدا زدن بات", callback_data="btn_set_trigger")])
        keyboard.append([InlineKeyboardButton("🏘 مدیریت گروه‌ها", callback_data="btn_list_groups")])
    await update.message.reply_text(
        "🛠 **پنل مدیریت (تنظیمات سراسری)**",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def _show_group_management_list(query, role: str):
    """Show all groups for management (private chat)."""
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT group_id, title, is_active, ai_enabled FROM bot_groups ORDER BY updated_at DESC NULLS LAST"
        )
    if not rows:
        await query.edit_message_text("ℹ️ هنوز بات به هیچ گروهی اضافه نشده.")
        return
    keyboard = []
    for r in rows:
        gid = r["group_id"]
        gtitle = r["title"] or str(gid)
        active = "✅" if r["is_active"] else "⛔"
        ai = "🟢" if r["ai_enabled"] else "🔴"
        label = f"{active} {ai} {gtitle[:30]}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"pmgrp_{gid}")])
    keyboard.append([InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="btn_back_global")])
    keyboard.append([InlineKeyboardButton("❌ بستن", callback_data="btn_close")])
    await query.edit_message_text(
        "🏘 **مدیریت گروه‌ها**\nبرای مشاهده تنظیمات روی هر گروه کلیک کن:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def _show_submenu_for_group(query, gid: int, role: str):
    """Show the submenu for a specific group."""
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT group_id, title, is_active, ai_enabled FROM bot_groups WHERE group_id = $1", gid
        )
    if not row:
        await query.edit_message_text("❌ گروه یافت نشد.")
        return
    gtitle = row["title"] or str(gid)
    is_active = row["is_active"]
    ai_on = row["ai_enabled"]
    active_label = "✅ فعال" if is_active else "⛔ غیرفعال"
    ai_label = "🟢 AI روشن است (خاموش کن)" if ai_on else "🔴 AI خاموش است (روشن کن)"
    header = f"🏘 **{_esc_md(str(gtitle))}**\nآیدی: `{gid}`\n\n{active_label} | {ai_label}\n\n"
    keyboard = [
        [InlineKeyboardButton(ai_label, callback_data="btn_toggle_ai_pm")],
        [InlineKeyboardButton("⛔️ فعال/غیرفعال کردن گروه", callback_data="btn_toggle_active_pm")],
        [InlineKeyboardButton("🔙 بازگشت به لیست گروه‌ها", callback_data="btn_list_groups")],
    ]
    await query.edit_message_text(header, reply_markup=InlineKeyboardMarkup(keyboard))


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


async def process_persona(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    await database.set_bot_persona(text)
    await update.message.reply_text("✅ شخصیت/معرفی بات با موفقیت ذخیره شد.")
    context.user_data.clear()
    return ConversationHandler.END


async def process_trigger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    word = update.message.text.strip()
    if not word:
        await update.message.reply_text("❌ کلمه نمی‌تواند خالی باشد.")
        return SET_TRIGGER
    await database.set_trigger_word(word)
    await update.message.reply_text(f"✅ کلمه صدا زدن بات به `{word}` تغییر یافت.", parse_mode="Markdown")
    context.user_data.clear()
    return ConversationHandler.END


async def cancel_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END


async def conversation_timeout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fires when a panel flow (Add Keyword, Edit Persona, etc.) is abandoned for
    too long. Without this, a half-finished flow would silently swallow every
    future message from that admin in that chat forever — this is what makes
    keyword/AI replies 'stop working' after clicking a panel button and not
    finishing it."""
    context.user_data.clear()
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⏰ زمان انتظار پنل تموم شد و به حالت عادی برگشتی. برای شروع دوباره /panel رو بزن."
            )
        except Exception:
            pass
    return ConversationHandler.END


async def _toggle_group_active(gid: int, query=None, update=None):
    """Toggle is_active for a group. Works with either callback query or update."""
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        current = await conn.fetchval('SELECT is_active FROM bot_groups WHERE group_id = $1', gid)
        if current is None:
            msg = "❌ گروه در دیتابیس یافت نشد."
            if query:
                await query.edit_message_text(msg)
            elif update:
                await update.message.reply_text(msg)
            return
        new = not current
        await conn.execute(
            'UPDATE bot_groups SET is_active = $1, updated_at = CURRENT_TIMESTAMP WHERE group_id = $2',
            new, gid
        )


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

    await _toggle_group_active(target_id, update=update)
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        current = await conn.fetchval('SELECT is_active FROM bot_groups WHERE group_id = $1', target_id)
        status = "✅ enabled" if current else "❌ disabled"
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


async def conversation_timeout_handler(update, context):
    """اگه یه فلوی پنل (مثل Add Keyword) رها بشه، بعد از timeout به حالت عادی برمی‌گرده."""
    context.user_data.clear()
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⏰ زمان انتظار پنل تموم شد و به حالت عادی برگشتی. برای شروع دوباره /panel رو بزن."
            )
        except Exception:
            pass
    return ConversationHandler.END


toggle_group_handler = CommandHandler("toggle_group", toggle_group_command)
debug_ai_handler = CommandHandler("debug_ai", debug_ai_command)

panel_conversation = ConversationHandler(
    entry_points=[
        CommandHandler("panel", panel_command),
        CallbackQueryHandler(inline_button_router, pattern="^(btn_|del_|remad_|pmgrp_)")
    ],
    states={
        ADD_KEYWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_keyword)],
        ADD_RESPONSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_response)],
        ADD_MATCH_TYPE: [CallbackQueryHandler(process_match_type, pattern="^mtype_")],
        ADD_CO_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_co_admin_id)],
        ADD_CO_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_co_admin_name)],
        EDIT_PERSONA: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_persona)],
        SET_TRIGGER: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_trigger)],
        ConversationHandler.TIMEOUT: [MessageHandler(filters.ALL, conversation_timeout_handler)],
    },
    fallbacks=[CommandHandler("cancel", cancel_action)],
    per_message=False,
    allow_reentry=True,
    conversation_timeout=300,
)
