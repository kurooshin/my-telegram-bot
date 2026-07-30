"""
AI service — connects to Google Gemini API (gemini-2.0-flash) via the
new google-genai library, with in-memory rate limiting.
"""
import asyncio
import logging
import time

from google import genai
from google.genai import types

import config

logger = logging.getLogger(__name__)

_client: genai.Client | None = None

MODEL = "gemini-2.0-flash"

# Rate-limit guard (free-tier Gemini: 14/min, 1400/day)
RATE_LIMIT_PER_MINUTE = 14
RATE_LIMIT_PER_DAY = 1400

_call_timestamps: list[float] = []
_day_count = 0
_day_reset = 0.0

SYSTEM_PROMPT = (
    "تو یک دستیار ایرانی توی یک گروه تلگرامی هستی. "
    "قوانین سختگیرانه‌ای که باید همیشه رعایت کنی:\n"
    "۱. فقط و فقط به زبان فارسیِ محاوره‌ای و روزمره جواب بده — "
    "دقیقاً مثل یک کاربر ایرانی معمولی توی تلگرام می‌نویسه، نه فارسیِ رسمی/کتابی "
    "و نه ترجمه‌ی لغت‌به‌لغت از انگلیسی.\n"
    "۲. هرگز کلمه یا جمله‌ی انگلیسی وسط جواب نیار مگر اسم خاص یا اصطلاح تخصصی "
    "که معادل فارسی رایج نداره.\n"
    "۳. جواب‌ها کوتاه باشن (۱ تا ۳ جمله)، مگر این‌که واقعاً نیاز به توضیح بیشتر باشه.\n"
    "۴. اگه چیزی رو نمی‌دونی، صادقانه و کوتاه بگو نمی‌دونی، حدس نزن.\n"
    "۵. لحنت دوستانه و صمیمیه، نه رسمی و اداری."
)

MAX_KNOWLEDGE_ITEMS = 40


def _build_knowledge_block(known_facts: list[dict] | None) -> str:
    if not known_facts:
        return ""
    lines = ["این اطلاعاتی هست که از قبل می‌دونی:"]
    for kw in known_facts[:MAX_KNOWLEDGE_ITEMS]:
        keyword = kw.get("keyword", "")
        response = kw.get("response", "")
        if keyword and response:
            lines.append(f"- {keyword}: {response}")
    return "\n".join(lines)


def _within_quota() -> bool:
    now = time.time()
    global _day_count, _day_reset

    if now - _day_reset > 86400:
        _day_count = 0
        _day_reset = now

    cutoff = now - 60
    recent = [t for t in _call_timestamps if t > cutoff]
    _call_timestamps[:] = recent

    if len(recent) >= RATE_LIMIT_PER_MINUTE:
        logger.warning("Gemini: rate limit per-minute reached (%s/min)", RATE_LIMIT_PER_MINUTE)
        return False

    if _day_count >= RATE_LIMIT_PER_DAY:
        logger.warning("Gemini: rate limit per-day reached (%s/day)", RATE_LIMIT_PER_DAY)
        return False

    return True


def check_api_key() -> None:
    if not config.GEMINI_API_KEY:
        logger.error(
            "=" * 60
            + "\nGEMINI_API_KEY is not set! AI replies will silently return None.\n"
            + "Set this environment variable to enable AI features.\n"
            + "=" * 60
        )
    else:
        logger.info(
            "GEMINI_API_KEY is configured (%s chars) — model=%s",
            len(config.GEMINI_API_KEY),
            MODEL,
        )


async def get_ai_reply(
    user_text: str,
    history: list[dict] | None = None,
    known_facts: list[dict] | None = None,
    persona: str | None = None,
) -> str | None:
    if not config.GEMINI_API_KEY:
        logger.warning("Gemini: GEMINI_API_KEY is empty, skipping AI reply")
        return None

    if not _within_quota():
        return None

    global _client
    if _client is None:
        _client = genai.Client(api_key=config.GEMINI_API_KEY)

    # Build system instruction
    system_instruction = SYSTEM_PROMPT
    if persona:
        system_instruction += (
            "\n\nدرباره‌ی خودت این‌طور معرفی کن و طبق این قوانین رفتار کن:\n"
            + persona
        )
    kb = _build_knowledge_block(known_facts)
    if kb:
        system_instruction += "\n\n" + kb

    # Build contents (history + current message)
    contents: list[types.Content] = []
    for msg in (history or []):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        gemini_role = "model" if role == "assistant" else "user"
        contents.append(types.Content(role=gemini_role, parts=[types.Part(text=content)]))
    contents.append(types.Content(role="user", parts=[types.Part(text=user_text)]))

    try:
        loop = asyncio.get_running_loop()
        logger.debug("Gemini: sending request (%d contents)", len(contents))

        response = await loop.run_in_executor(
            None,
            lambda: _client.models.generate_content(
                model=MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    max_output_tokens=500,
                    temperature=0.7,
                ),
            ),
        )

        # --- Inspect response ---
        if response is None:
            logger.error("Gemini: response object is None")
            return None

        try:
            fb = response.prompt_feedback
            if fb and fb.block_reason:
                logger.warning("Gemini: prompt BLOCKED — reason=%s", fb.block_reason)
        except Exception:
            pass

        candidates = getattr(response, "candidates", None)
        if not candidates:
            logger.warning("Gemini: no candidates in response")
            return None

        candidate = candidates[0]
        finish_reason = getattr(candidate, "finish_reason", None)
        finish_name = finish_reason.name if finish_reason else "UNKNOWN"

        if finish_name != "STOP":
            logger.warning("Gemini: candidate finish_reason=%s (not STOP)", finish_name)
            try:
                for rating in candidate.safety_ratings or []:
                    if rating.probability.name != "NEGLIGIBLE":
                        logger.warning("  safety: %s = %s", rating.category.name, rating.probability.name)
            except Exception:
                pass

        text = response.text
        result = text.strip() if text else None
        logger.info(
            "Gemini: response received — has_text=%s len=%s finish=%s",
            result is not None,
            len(result) if result else 0,
            finish_name,
        )

        if result:
            _call_timestamps.append(time.time())
            global _day_count
            _day_count += 1

        return result

    except Exception as e:
        logger.error("Gemini API error: %s", e, exc_info=True)
        return None
