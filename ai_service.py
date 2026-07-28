"""
AI service — connects to Google Gemini API with built-in free-tier rate limiting.
"""
import asyncio
import logging
import time

import google.generativeai as genai
import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rate limiter (in-memory, no DB needed)
# Limits: 1400 requests/day, 14 requests/minute

DAILY_LIMIT = 1400
MINUTE_LIMIT = 14

_daily_count = 0
_daily_reset = 0.0
_minute_timestamps: list[float] = []


def _rate_limit_ok() -> bool:
    """Check both daily and minute rate limits. Returns True if allowed."""
    global _daily_count, _daily_reset

    now = time.time()

    # Daily reset
    if _daily_reset == 0 or now - _daily_reset > 86400:
        _daily_count = 0
        _daily_reset = now

    if _daily_count >= DAILY_LIMIT:
        logger.warning("Daily AI rate limit reached (%s/%s)", _daily_count, DAILY_LIMIT)
        return False

    # Minute sliding window
    cutoff = now - 60
    _minute_timestamps[:] = [t for t in _minute_timestamps if t > cutoff]
    if len(_minute_timestamps) >= MINUTE_LIMIT:
        logger.warning("Minute AI rate limit reached (%s/%s)", len(_minute_timestamps), MINUTE_LIMIT)
        return False

    _daily_count += 1
    _minute_timestamps.append(now)
    return True


# ---------------------------------------------------------------------------
# Gemini client setup

_genai_configured = False


def _ensure_configured():
    global _genai_configured
    if not _genai_configured and config.GEMINI_API_KEY:
        genai.configure(api_key=config.GEMINI_API_KEY)
        _genai_configured = True


_model = None


def _get_model():
    global _model
    if _model is None and config.GEMINI_API_KEY:
        _ensure_configured()
        _model = genai.GenerativeModel(
            "gemini-2.0-flash",
            system_instruction=(
                "تو یک دستیار گروه تلگرامی هستی. "
                "به فارسی، کوتاه و دوستانه جواب بده. "
                "اگه چیزی رو نمی‌دونی، صادقانه بگو. "
                "از ایموجی‌های مناسب استفاده کن."
            ),
        )
    return _model


# ---------------------------------------------------------------------------
# History conversion helpers

def _build_contents(user_text: str, history: list[dict] | None) -> list[dict]:
    """Convert internal history format to Gemini's content format."""
    contents = []

    if history:
        for msg in history[-10:]:  # keep last 10 turns max
            role = msg.get("role", "user")
            if role not in ("user", "assistant"):
                role = "user"
            contents.append({
                "role": "model" if role == "assistant" else "user",
                "parts": [{"text": msg.get("content", "")}],
            })

    contents.append({
        "role": "user",
        "parts": [{"text": user_text}],
    })
    return contents


# ---------------------------------------------------------------------------
# Public API

async def get_ai_reply(
    user_text: str,
    history: list[dict] | None = None,
    known_facts: list[dict] | None = None,
) -> str | None:
    """
    Send a message to Gemini and return the response text.
    Returns None on rate-limit hit, API error, or if no API key is configured.
    """
    if not config.GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY not set — AI replies disabled")
        return None

    if not _rate_limit_ok():
        logger.warning("Rate limit hit for Gemini — returning None")
        return None

    model = _get_model()
    if model is None:
        logger.error("Gemini model could not be initialized")
        return None

    logger.info(
        "Gemini API call: text_len=%d history_len=%d known_facts=%d",
        len(user_text), len(history) if history else 0, len(known_facts) if known_facts else 0,
    )

    # Build context string from known facts
    context = ""
    if known_facts:
        lines = []
        for kw in known_facts:
            keyword = kw.get("keyword", "")
            response = kw.get("response", "")
            lines.append(f"- {keyword}: {response}")
        context = "دستورالعمل: کلمات کلیدی و پاسخ‌های زیر را بدان:\n" + "\n".join(lines)

    try:
        contents = _build_contents(user_text, history)
        if context:
            contents.insert(0, {
                "role": "user",
                "parts": [{"text": context}],
            })
            contents.insert(1, {
                "role": "model",
                "parts": [{"text": "متوجه شدم. این کلمات کلیدی رو در پاسخ‌هات در نظر می‌گیرم."}],
            })

        # Run Gemini call in a thread pool to avoid blocking the event loop
        loop = asyncio.get_running_loop()
        logger.debug("Gemini: sending request...")
        response = await loop.run_in_executor(
            None,
            lambda: model.generate_content(contents),
        )
        result = response.text.strip() if response and response.text else None
        logger.info("Gemini: response received — has_text=%s len=%s", result is not None, len(result) if result else 0)
        return result

    except Exception as e:
        logger.error("Gemini API error: %s", e, exc_info=True)
        return None