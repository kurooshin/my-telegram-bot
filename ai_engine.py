"""
موتور هوش مصنوعی — یادگیری سبک نوشتاری هر کاربر و تولید پیشنهادهای پاسخ شخصی.

همه‌ی تماس‌ها با Groq در try/except هستن تا خطای API هرگز هندلر پیام یا
پاسخ وب‌هوک رو کرش نکنه.
"""
import asyncio
import json
import logging

from groq import Groq

import config
import database

logger = logging.getLogger(__name__)

_client: Groq | None = None

MODEL = "llama-3.3-70b-versatile"

MIN_MESSAGES_FOR_STYLE = 10

STYLE_PROMPT = (
    "تو یک تحلیل‌گر سبک نوشتاری هستی. چند پیام از یک کاربر ایرانی توی تلگرام "
    "بهت داده شده. خلاصه‌ای کوتاه (۲ تا ۴ خط) از سبک نوشتاری اون کاربر بنویس که "
    "بعداً بتونه به‌عنوان راهنمای شخصی‌سازی پاسخ‌ها استفاده بشه. این‌ها رو پوشش بده:\n"
    "- رسمی یا غیررسمی بودن لحن\n"
    "- ایموجی‌ها و نشانه‌های پرکاربرد\n"
    "- عبارت‌ها و کلمه‌های تکراری موردعلاقه‌ش\n"
    "- طول جمله‌ها و نحوه‌ی جمله‌بندی\n"
    "- موضوعاتی که معمولاً درباره‌شون حرف می‌زنه\n"
    "خروجی باید یک توصیف قابل‌استفاده از سبک باشه، نه کپی پیام‌ها. فقط خلاصه رو بنویس."
)

SUGGESTION_PROMPT = (
    "تو یک دستیار پیشنهاد پاسخ هوشمند هستی. بر اساس سبک نوشتاری کاربر و "
    "بافت گفتگوی اخیر، دقیقاً ۳ پیشنهاد پاسخ کوتاه به فارسی بنویس که کاربر "
    "بتونه با یک ضربه بفرستشون.\n"
    "قوانین:\n"
    "- هر پیشنهاد کوتاه باشه (حداکثر ۳ جمله)\n"
    "- دقیقاً به سبک نوشتاری خود کاربر نوشته بشه (لحن، ایموجی، عبارت‌ها)\n"
    "- به بافت گفتگوی اخیر مربوط باشه\n"
    "- فقط خروجی JSON با این ساختار دقیق برگردون: "
    '{"suggestions": ["...", "...", "..."]}'
)


def _get_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=config.GROQ_API_KEY)
    return _client


def _sync_style_summary(messages: list[dict]) -> str | None:
    """نسخه‌ی همگام — داخل thread pool اجرا می‌شه."""
    if not config.GROQ_API_KEY:
        logger.warning("⚠️ GROQ_API_KEY خالی است — تحلیل سبک انجام نشد")
        return None
    sample = "\n".join(f"- {m['text'][:200]}" for m in messages)
    try:
        response = _get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": STYLE_PROMPT},
                {"role": "user", "content": f"پیام‌های کاربر:\n{sample}"},
            ],
            max_tokens=200,
            temperature=0.5,
        )
        text = response.choices[0].message.content if response.choices else None
        return text.strip() if text else None
    except Exception as e:
        logger.error("❌ خطا در تحلیل سبک: %s", e, exc_info=True)
        return None


def _sync_suggestions(style_summary: str, recent_context: str) -> list[str]:
    """نسخه‌ی همگام — داخل thread pool اجرا می‌شه."""
    if not config.GROQ_API_KEY:
        logger.warning("⚠️ GROQ_API_KEY خالی است — پیشنهاد ساخته نشد")
        return []
    try:
        response = _get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SUGGESTION_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"سبک نوشتاری کاربر:\n{style_summary}\n\n"
                        f"بافت گفتگوی اخیر:\n{recent_context}\n\n"
                        "JSON تولید کن."
                    ),
                },
            ],
            max_tokens=200,
            temperature=0.7,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content if response.choices else None
        if not content:
            logger.warning("⚠️ پیشنهاد خالی برگشت")
            return []
        data = json.loads(content)
        suggestions = data.get("suggestions", [])
        if not isinstance(suggestions, list):
            return []
        return [str(s).strip() for s in suggestions[:3]]
    except Exception as e:
        logger.error("❌ خطا در تولید پیشنهاد: %s", e, exc_info=True)
        return []


async def update_style_profile(user_id: int) -> str | None:
    """سبک نوشتاری کاربر رو به‌روزرسانی و ذخیره می‌کنه. برمی‌گردونه خلاصه‌ی سبک یا None."""
    messages = await database.get_recent_messages(user_id, limit=30)
    if len(messages) < MIN_MESSAGES_FOR_STYLE:
        logger.info("⏳ برای %s پیام کافی برای تحلیل سبک نیست (%s پیام)", user_id, len(messages))
        return None

    loop = asyncio.get_running_loop()
    logger.info("⏳ در حال یادگیری سبک کاربر %s ...", user_id)
    summary = await loop.run_in_executor(None, lambda: _sync_style_summary(messages))
    if not summary:
        logger.warning("❌ سبک برای کاربر %s ساخته نشد", user_id)
        return None

    await database.save_style(user_id, summary)
    await database.trim_user_messages(user_id, keep=40)
    logger.info("✅ سبک کاربر %s ذخیره شد", user_id)
    return summary


async def generate_suggestions(
    style_summary: str,
    recent_context: list[str] | None = None,
) -> list[str]:
    """۳ پیشنهاد پاسخ کوتاه به سبک کاربر برمی‌گردونه (اگه خطا بشه لیست خالی)."""
    context_text = "\n".join(f"- {c[:200]}" for c in (recent_context or []))
    if not context_text:
        context_text = "بافتی در دسترس نیست."
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, lambda: _sync_suggestions(style_summary, context_text)
    )
