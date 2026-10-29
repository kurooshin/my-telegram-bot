"""Admin panel — keyword management, co-admin management, group toggling."""

import html
import logging
import math
import warnings
import asyncpg
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, CallbackQueryHandler, MessageHandler, filters
import config
import database

logger = logging.getLogger(__name__)
warnings.filterwarnings("ignore", message=".*per_message.*CallbackQueryHandler.*")

ADD_KEYWORD, ADD_RESPONSE, ADD_MATCH_TYPE, ADD_CO_ID, ADD_CO_NAME, EDIT_PERSONA, SET_TRIGGER, \
    EDIT_KW_RESPONSE, EDIT_KW_TEXT = range(9)

# عبارتی که توی چت نوشته بشه منوی مدیریت باز میشه (علاوه بر /menu و /panel)
MENU_PHRASE_REGEX = r"^\s*منو[یي][\s\u200c]*عباس\s*[!.؟?]*\s*$"

KW_PAGE_SIZE = 8
AI_OFF_ALERT = "🔴 بخش هوش مصنوعی (AI و یادگیری سبک) فعلاً خاموشه. فقط کلمات کلیدی کار می‌کنن."


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
    title = chat.title if chat.type in ("group", "supergroup") else None
    await update.message.reply_text(
        _main_menu_header(title),
        reply_markup=_main_menu_markup(role),
        parse_mode="HTML",
    )
    return ConversationHandler.END


async def inline_button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    # دکمه‌ی AI وقتی خاموشه فقط یه هشدار نشون میده (باید قبل از answer() عمومی بیاد)
    if query.data == "menu_ai_off":
        await query.answer(AI_OFF_ALERT, show_alert=True)
        return ConversationHandler.END

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

        # دکمه‌های قدیمی AI/شخصیت روی پیام‌های قدیمی هنوز ممکنه کلیک بشن
        if not config.AI_ENABLED and query.data in ("btn_toggle_ai", "btn_toggle_ai_pm", "btn_edit_persona"):
            await query.edit_message_text(AI_OFF_ALERT)
            return ConversationHandler.END

        if query.data == "menu_main" or query.data == "btn_back_global":
            title = chat.title if chat.type in ("group", "supergroup") else None
            await query.edit_message_text(
                _main_menu_header(title), reply_markup=_main_menu_markup(role), parse_mode="HTML"
            )
        elif query.data == "menu_other":
            await _show_other_settings(query, role, chat)
        elif query.data == "kw_menu":
            await _show_kw_menu(query)
        elif query.data == "kw_noop":
            pass
        elif query.data.startswith("kw_list_"):
            await _show_kw_list(query, int(query.data.split("_")[2]))
        elif query.data.startswith("kw_view_"):
            await _show_kw_card(query, int(query.data.split("_")[2]))
        elif query.data.startswith("kw_type_"):
            kw_id = int(query.data.split("_")[2])
            pool = await database.get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE bot_keywords SET match_type = CASE WHEN match_type = 'exact' "
                    "THEN 'flexible' ELSE 'exact' END WHERE id = $1", kw_id
                )
            await _show_kw_card(query, kw_id)
        elif query.data.startswith("kw_delask_"):
            kw_id = int(query.data.split("_")[2])
            row = await _get_kw(kw_id)
            if not row:
                await _show_kw_list(query, 0, prefix="❌ این کلمه دیگه وجود نداره.\n\n")
            else:
                kb = [[
                    InlineKeyboardButton("✅ آره، حذف کن", callback_data=f"kw_delyes_{kw_id}"),
                    InlineKeyboardButton("❌ نه", callback_data=f"kw_view_{kw_id}"),
                ]]
                await query.edit_message_text(
                    f"🗑 کلمه‌ی <code>{_h(row['keyword'])}</code> حذف بشه؟",
                    reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML",
                )
        elif query.data.startswith("kw_delyes_"):
            kw_id = int(query.data.split("_")[2])
            pool = await database.get_pool()
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM bot_keywords WHERE id = $1", kw_id)
            await _show_kw_list(query, 0, prefix="✅ کلمه حذف شد.\n\n")
        elif query.data.startswith("kw_editres_"):
            kw_id = int(query.data.split("_")[2])
            row = await _get_kw(kw_id)
            if not row:
                await _show_kw_list(query, 0, prefix="❌ این کلمه دیگه وجود نداره.\n\n")
                return ConversationHandler.END
            context.user_data["edit_kw_id"] = kw_id
            await query.edit_message_text(
                f"✏️ پاسخ جدید برای <code>{_h(row['keyword'])}</code> رو بفرست\n"
                "(یا /cancel برای انصراف):",
                parse_mode="HTML",
            )
            return EDIT_KW_RESPONSE
        elif query.data.startswith("kw_edittxt_"):
            kw_id = int(query.data.split("_")[2])
            row = await _get_kw(kw_id)
            if not row:
                await _show_kw_list(query, 0, prefix="❌ این کلمه دیگه وجود نداره.\n\n")
                return ConversationHandler.END
            context.user_data["edit_kw_id"] = kw_id
            await query.edit_message_text(
                f"🔤 کلمه‌ی فعلی: <code>{_h(row['keyword'])}</code>\n\n"
                "کلمه‌ی جدید رو بفرست (یا /cancel برای انصراف):",
                parse_mode="HTML",
            )
            return EDIT_KW_TEXT
        elif query.data == "btn_add_kw":
            await query.edit_message_text("📝 کلمه‌ی جدید رو بفرست (یا /cancel برای انصراف):")
            return ADD_KEYWORD
        elif query.data == "btn_add_co" and role == 'admin':
            await query.edit_message_text("🆔 Send the co-admin's numeric user ID:")
            return ADD_CO_ID
        elif query.data == "btn_list_kw":
            await _show_kw_list(query, 0)
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
            await query.edit_message_text(
                _main_menu_header(row["title"]), reply_markup=_main_menu_markup(role), parse_mode="HTML"
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
        elif query.data == "btn_unmatched":
            top = await database.get_top_unmatched(limit=15)
            if not top:
                await query.edit_message_text(
                    "ℹ️ هنوز پیام بی‌جوابی ثبت نشده.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 کلمات کلیدی", callback_data="kw_menu")]]),
                )
                return ConversationHandler.END
            lines = ["💡 **پیام‌های بی‌جواب پرتکرار:**\n"]
            for i, item in enumerate(top, 1):
                txt = _esc_md(item['text'][:40])
                lines.append(f"{i}. `{txt}` — {item['total']} بار")
            await query.edit_message_text(
                "\n".join(lines), parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 کلمات کلیدی", callback_data="kw_menu")]]),
            )
        elif query.data.startswith("del_"):
            kw_id = int(query.data.split("_")[1])
            pool = await database.get_pool()
            async with pool.acquire() as conn:
                await conn.execute('DELETE FROM bot_keywords WHERE id = $1', kw_id)
            await _show_kw_list(query, 0, prefix="✅ کلمه حذف شد.\n\n")
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
    """Show the main management menu in private chat."""
    await update.message.reply_text(
        _main_menu_header(None), reply_markup=_main_menu_markup(role), parse_mode="HTML"
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
    if config.AI_ENABLED:
        ai_label = "🟢 AI روشن است (خاموش کن)" if ai_on else "🔴 AI خاموش است (روشن کن)"
    else:
        ai_label = "🔴 AI فعلاً خاموشه"
    header = f"🏘 **{_esc_md(str(gtitle))}**\nآیدی: `{gid}`\n\n{active_label} | {ai_label}\n\n"
    keyboard = [
        [InlineKeyboardButton(ai_label, callback_data="btn_toggle_ai_pm" if config.AI_ENABLED else "menu_ai_off")],
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


def _h(text) -> str:
    return html.escape(str(text or ""))


def _type_label(match_type: str) -> str:
    return "🔴 دقیق (Exact)" if match_type == "exact" else "🟢 منعطف (Flexible)"


async def _get_kw(kw_id: int):
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT id, keyword, response, match_type FROM bot_keywords WHERE id = $1", kw_id
        )


async def _send(target, text: str, markup=None):
    """Edit the message if target is a CallbackQuery, otherwise reply to the Message."""
    if hasattr(target, "edit_message_text"):
        await target.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    else:
        await target.reply_text(text, reply_markup=markup, parse_mode="HTML")


def _main_menu_header(title) -> str:
    if title:
        return f"🛠 <b>منوی مدیریت</b> — {_h(title)}"
    return "🛠 <b>منوی مدیریت</b>"


def _main_menu_markup(role: str) -> InlineKeyboardMarkup:
    kb = [[InlineKeyboardButton("🔑 کلمات کلیدی", callback_data="kw_menu")]]
    if not config.AI_ENABLED:
        kb.append([InlineKeyboardButton("🤖 هوش مصنوعی (فعلاً خاموش)", callback_data="menu_ai_off")])
    if role == "admin":
        kb.append([InlineKeyboardButton("⚙️ سایر تنظیمات", callback_data="menu_other")])
    kb.append([InlineKeyboardButton("❌ بستن", callback_data="btn_close")])
    return InlineKeyboardMarkup(kb)


async def _show_other_settings(query, role: str, chat):
    """سایر تنظیمات (ادمین‌ها، کلمه‌ی صدا زدن، گروه‌ها و ...) — فقط برای ادمین اصلی."""
    if role != "admin":
        await query.edit_message_text("⛔ این بخش فقط برای ادمین اصلیه.")
        return
    kb = [
        [InlineKeyboardButton("📢 کلمه صدا زدن بات", callback_data="btn_set_trigger")],
        [InlineKeyboardButton("👤 افزودن ادمین", callback_data="btn_add_co"),
         InlineKeyboardButton("👑 لیست ادمین‌ها", callback_data="btn_list_ad")],
        [InlineKeyboardButton("🏘 مدیریت گروه‌ها", callback_data="btn_list_groups")],
    ]
    if config.AI_ENABLED:
        kb.insert(0, [InlineKeyboardButton("🎭 تنظیم شخصیت/معرفی بات", callback_data="btn_edit_persona")])
        if chat.type in ("group", "supergroup"):
            try:
                ai_on = await database.is_ai_enabled(chat.id)
            except Exception:
                ai_on = False
            label = "🟢 AI روشن است (خاموش کن)" if ai_on else "🔴 AI خاموش است (روشن کن)"
            kb.insert(0, [InlineKeyboardButton(label, callback_data="btn_toggle_ai")])
    else:
        kb.insert(0, [InlineKeyboardButton("🎭 تنظیم شخصیت (غیرفعال)", callback_data="menu_ai_off")])
    kb.append([InlineKeyboardButton("🔙 منوی اصلی", callback_data="menu_main")])
    await query.edit_message_text(
        "⚙️ <b>سایر تنظیمات</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML"
    )


async def _show_kw_menu(target, prefix: str = ""):
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM bot_keywords")
        exact = await conn.fetchval("SELECT COUNT(*) FROM bot_keywords WHERE match_type = 'exact'")
    text = (
        f"{prefix}🔑 <b>کلمات کلیدی</b>\n\n"
        f"تعداد: {total}  (🟢 منعطف: {total - exact} | 🔴 دقیق: {exact})\n\n"
        "🟢 منعطف: اگه پیام شامل کلمه باشه جواب میده\n"
        "🔴 دقیق: فقط وقتی کل پیام همون کلمه باشه جواب میده"
    )
    kb = [
        [InlineKeyboardButton("➕ افزودن کلمه", callback_data="btn_add_kw"),
         InlineKeyboardButton("📋 لیست و ویرایش", callback_data="kw_list_0")],
        [InlineKeyboardButton("💡 پیام‌های بی‌جواب پرتکرار", callback_data="btn_unmatched")],
        [InlineKeyboardButton("🔙 منوی اصلی", callback_data="menu_main")],
    ]
    await _send(target, text, InlineKeyboardMarkup(kb))


async def _show_kw_list(target, page: int = 0, prefix: str = ""):
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, keyword, response, match_type FROM bot_keywords ORDER BY id DESC"
        )
    back = [InlineKeyboardButton("🔙 کلمات کلیدی", callback_data="kw_menu")]
    if not rows:
        kb = [[InlineKeyboardButton("➕ افزودن کلمه", callback_data="btn_add_kw")], back]
        await _send(target, f"{prefix}ℹ️ هنوز کلمه‌ای ثبت نشده.", InlineKeyboardMarkup(kb))
        return
    pages = max(1, math.ceil(len(rows) / KW_PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    chunk = rows[page * KW_PAGE_SIZE:(page + 1) * KW_PAGE_SIZE]
    kb = []
    for r in chunk:
        dot = "🔴" if r["match_type"] == "exact" else "🟢"
        label = f"{dot} {r['keyword'][:20]} ← {r['response'][:16].replace(chr(10), ' ')}"
        kb.append([InlineKeyboardButton(label, callback_data=f"kw_view_{r['id']}")])
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"kw_list_{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="kw_noop"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"kw_list_{page + 1}"))
        kb.append(nav)
    kb.append([InlineKeyboardButton("➕ افزودن کلمه", callback_data="btn_add_kw")])
    kb.append(back)
    await _send(
        target,
        f"{prefix}📋 <b>لیست کلمات</b> ({len(rows)} مورد)\nروی هر کلمه بزن تا ویرایشش کنی:",
        InlineKeyboardMarkup(kb),
    )


async def _show_kw_card(target, kw_id: int, prefix: str = ""):
    row = await _get_kw(kw_id)
    if not row:
        await _show_kw_list(target, 0, prefix="❌ این کلمه دیگه وجود نداره.\n\n")
        return
    response = row["response"]
    if len(response) > 1500:
        response = response[:1500] + "…"
    text = (
        f"{prefix}🔑 <b>کلمه:</b> <code>{_h(row['keyword'])}</code>\n\n"
        f"💬 <b>پاسخ:</b>\n{_h(response)}\n\n"
        f"🔁 <b>نوع:</b> {_type_label(row['match_type'])}"
    )
    kb = [
        [InlineKeyboardButton("✏️ ویرایش پاسخ", callback_data=f"kw_editres_{kw_id}"),
         InlineKeyboardButton("🔤 ویرایش کلمه", callback_data=f"kw_edittxt_{kw_id}")],
        [InlineKeyboardButton("🔁 تغییر نوع (منعطف/دقیق)", callback_data=f"kw_type_{kw_id}")],
        [InlineKeyboardButton("🗑 حذف", callback_data=f"kw_delask_{kw_id}")],
        [InlineKeyboardButton("🔙 لیست کلمات", callback_data="kw_list_0")],
    ]
    await _send(target, text, InlineKeyboardMarkup(kb))


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
    await update.message.reply_text("💬 حالا پاسخ این کلمه رو بفرست:")
    return ADD_RESPONSE


async def process_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['temp_res'] = update.message.text.strip()
    keyboard = [
        [InlineKeyboardButton("🟢 منعطف (پیش‌فرض)", callback_data="mtype_flexible")],
        [InlineKeyboardButton("🔴 دقیق", callback_data="mtype_exact")]
    ]
    await update.message.reply_text(
        "نوع تطبیق رو انتخاب کن:\n\n"
        "🟢 منعطف — اگه پیام شامل کلمه باشه جواب میده\n"
        "🔴 دقیق — فقط وقتی کل پیام همون کلمه باشه",
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
        await query.edit_message_text("❌ زمان جلسه تموم شد. دوباره /menu بزن.")
        context.user_data.clear()
        return ConversationHandler.END
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            'INSERT INTO bot_keywords (keyword, response, match_type) VALUES ($1, $2, $3) '
            'ON CONFLICT (keyword) DO UPDATE SET response = $2, match_type = $3',
            keyword, response, match_type
        )
    context.user_data.clear()
    await _show_kw_menu(query, prefix="✅ کلمه ذخیره شد.\n\n")
    return ConversationHandler.END


async def process_edit_kw_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kw_id = context.user_data.get("edit_kw_id")
    new_text = (update.message.text or "").strip()
    if not kw_id:
        await update.message.reply_text("❌ زمان جلسه تموم شد. دوباره /menu بزن.")
        return ConversationHandler.END
    if not new_text:
        await update.message.reply_text("❌ پاسخ نمی‌تونه خالی باشه. دوباره بفرست:")
        return EDIT_KW_RESPONSE
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE bot_keywords SET response = $1 WHERE id = $2", new_text, kw_id)
    context.user_data.pop("edit_kw_id", None)
    await _show_kw_card(update.message, kw_id, prefix="✅ پاسخ ویرایش شد.\n\n")
    return ConversationHandler.END


async def process_edit_kw_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kw_id = context.user_data.get("edit_kw_id")
    new_kw = database.normalize_persian((update.message.text or "").strip())
    if not kw_id:
        await update.message.reply_text("❌ زمان جلسه تموم شد. دوباره /menu بزن.")
        return ConversationHandler.END
    if not new_kw:
        await update.message.reply_text("❌ کلمه نمی‌تونه خالی باشه. دوباره بفرست:")
        return EDIT_KW_TEXT
    pool = await database.get_pool()
    try:
        async with pool.acquire() as conn:
            await conn.execute("UPDATE bot_keywords SET keyword = $1 WHERE id = $2", new_kw, kw_id)
    except asyncpg.UniqueViolationError:
        await update.message.reply_text("⚠️ این کلمه از قبل ثبت شده. یه کلمه‌ی دیگه بفرست (یا /cancel):")
        return EDIT_KW_TEXT
    context.user_data.pop("edit_kw_id", None)
    await _show_kw_card(update.message, kw_id, prefix="✅ کلمه ویرایش شد.\n\n")
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
        CommandHandler("menu", panel_command),
        MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(MENU_PHRASE_REGEX), panel_command),
        CallbackQueryHandler(inline_button_router, pattern="^(btn_|del_|remad_|pmgrp_|kw_|menu_)")
    ],
    states={
        ADD_KEYWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_keyword)],
        ADD_RESPONSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_response)],
        ADD_MATCH_TYPE: [CallbackQueryHandler(process_match_type, pattern="^mtype_")],
        ADD_CO_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_co_admin_id)],
        ADD_CO_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_co_admin_name)],
        EDIT_PERSONA: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_persona)],
        SET_TRIGGER: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_trigger)],
        EDIT_KW_RESPONSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_edit_kw_response)],
        EDIT_KW_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_edit_kw_text)],
        ConversationHandler.TIMEOUT: [MessageHandler(filters.ALL, conversation_timeout_handler)],
    },
    fallbacks=[CommandHandler("cancel", cancel_action)],
    per_message=False,
    allow_reentry=True,
    conversation_timeout=300,
)
