"""
AI service — connects to Groq API (llama-3.3-70b-versatile) with
in-memory rate limiting and full compatibility with existing callers.
"""
import asyncio
import logging
import time

from groq import Groq

import config

logger = logging.getLogger(__name__)

_client: Groq | None = None

MODEL = "llama-3.3-70b-versatile"

# Rate-limit guard (conservative for free tier: 28/min, 13000/day)
RATE_LIMIT_PER_MINUTE = 28
RATE_LIMIT_PER_DAY = 13000

_call_timestamps: list[float] = []
_day_count = 0
_day_reset = 0.0

SYSTEM_PROMPT = (
    "تو دستیار یک گروه تلگرامی هستی. کوتاه، دوستانه و طبیعی به فارسی جواب بده. "
    "اگر چیزی رو نمی‌دونی، صادقانه بگو نمی‌دونی به‌جای این‌که حدس بزنی."
)

MAX_KNOWLEDGE_ITEMS = 40


def _build_knowledge_block(known_facts: list[dict] | None) -> str:
    if not known_facts:
        return ""
    lines = ["برخی کلمات کلیدی و پاسخ‌های مرتبط که باید بدانی:"]
    for kw in known_facts[:MAX_KNOWLEDGE_ITEMS]:
        keyword = kw.get("keyword", "")
        response = kw.get("response", "")
        if keyword and response:
            lines.append(f"- {keyword}: {response}")
    return "\n".join(lines)


def _within_quota() -> bool:
    now = time.time()
    global _day_count, _day_reset

    # Reset day counter every 24 h
    if now - _day_reset > 86400:
        _day_count = 0
        _day_reset = now

    # Prune old entries (older than 60 s)
    cutoff = now - 60
    recent = [t for t in _call_timestamps if t > cutoff]
    _call_timestamps[:] = recent

    if len(recent) >= RATE_LIMIT_PER_MINUTE:
        logger.warning("Groq: rate limit per-minute reached (%s/min)", RATE_LIMIT_PER_MINUTE)
        return False

    if _day_count >= RATE_LIMIT_PER_DAY:
        logger.warning("Groq: rate limit per-day reached (%s/day)", RATE_LIMIT_PER_DAY)
        return False

    return True


def check_api_key() -> None:
    if not config.GROQ_API_KEY:
        logger.error(
            "=" * 60
            + "\nGROQ_API_KEY is not set! AI replies will silently return None.\n"
            + "Set this environment variable to enable AI features.\n"
            + "=" * 60
        )
    else:
        logger.info(
            "GROQ_API_KEY is configured (%s chars) — model=%s",
            len(config.GROQ_API_KEY),
            MODEL,
        )


async def get_ai_reply(
    user_text: str,
    history: list[dict] | None = None,
    known_facts: list[dict] | None = None,
) -> str | None:
    if not config.GROQ_API_KEY:
        logger.warning("Groq: GROQ_API_KEY is empty, skipping AI reply")
        return None

    if not _within_quota():
        return None

    global _client
    if _client is None:
        _client = Groq(api_key=config.GROQ_API_KEY)

    # Build system message
    system_msg = SYSTEM_PROMPT
    kb = _build_knowledge_block(known_facts)
    if kb:
        system_msg += "\n\n" + kb

    messages: list[dict] = [{"role": "system", "content": system_msg}]

    # Append conversation history (already in OpenAI-compatible format)
    for msg in (history or []):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            continue
        if role == "assistant":
            role = "assistant"
        else:
            role = "user"
        messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": user_text})

    try:
        loop = asyncio.get_running_loop()
        logger.debug("Groq: sending request (%d messages)", len(messages))

        response = await loop.run_in_executor(
            None,
            lambda: _client.chat.completions.create(
                model=MODEL,
                messages=messages,
                max_tokens=500,
                temperature=0.7,
            ),
        )

        if not response or not response.choices:
            logger.warning("Groq: empty response or no choices")
            return None

        choice = response.choices[0]
        text = choice.message.content if choice.message else None
        if not text:
            finish_reason = getattr(choice, "finish_reason", "UNKNOWN")
            logger.warning("Groq: finish_reason=%s, content is empty", finish_reason)
            return None

        result = text.strip()
        logger.info(
            "Groq: response received — len=%s finish=%s",
            len(result),
            choice.finish_reason,
        )

        # Track rate limit
        _call_timestamps.append(time.time())
        global _day_count
        _day_count += 1

        return result

    except Exception as e:
        logger.error("Groq API error: %s", e, exc_info=True)
        return None
