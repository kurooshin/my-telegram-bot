"""
Database abstraction layer — asyncpg connection pool and all table operations.

CONSTRAINT: All state (games, lobbies, chat) is kept in-process. This works
for a single server instance only. See othello_game.py for details.
"""
import json
import logging
import asyncpg
import config

logger = logging.getLogger(__name__)


def normalize_persian(text: str) -> str:
    """Normalize Persian/Arabic characters for consistent matching."""
    text = text.replace('\u064A', '\u06CC')
    text = text.replace('\u0643', '\u06A9')
    text = text.replace('\u200C', '')
    return text


_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Return the shared connection pool, creating it on first call."""
    global _pool
    if _pool is None:
        logger.info("Creating database connection pool...")
        _pool = await asyncpg.create_pool(
            config.DB_URI,
            min_size=2,
            max_size=10,
            statement_cache_size=0,
        )
    return _pool


async def close_pool() -> None:
    """Close the database connection pool (call on shutdown)."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("Database pool closed.")


async def init_db() -> None:
    """Create all required tables if they don't exist, and seed the admin account.
    Migration failures are logged but do NOT crash the bot — the server will
    still start and serve existing functionality."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS bot_keywords (
                id SERIAL PRIMARY KEY,
                keyword TEXT UNIQUE NOT NULL,
                response TEXT NOT NULL,
                match_type TEXT DEFAULT 'flexible'
            );
        ''')
        await conn.execute("ALTER TABLE bot_keywords ADD COLUMN IF NOT EXISTS match_type TEXT DEFAULT 'flexible'")
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS bot_admins (
                user_id BIGINT PRIMARY KEY,
                role TEXT NOT NULL DEFAULT 'co_admin',
                name TEXT DEFAULT 'بدون نام'
            );
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS bot_groups (
                id SERIAL PRIMARY KEY,
                group_id BIGINT UNIQUE NOT NULL,
                title TEXT DEFAULT 'بدون نام',
                is_active BOOLEAN DEFAULT TRUE,
                ai_enabled BOOLEAN DEFAULT FALSE,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        try:
            await _migrate_bot_groups(conn)
        except Exception as e:
            logger.error("bot_groups migration failed (non-fatal): %s", e, exc_info=True)

        await conn.execute('''
            CREATE TABLE IF NOT EXISTS unmatched_messages (
                id SERIAL PRIMARY KEY,
                text TEXT NOT NULL,
                chat_id BIGINT NOT NULL,
                frequency INTEGER DEFAULT 1,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(text, chat_id)
            );
        ''')

        await conn.execute('''
            CREATE TABLE IF NOT EXISTS chat_context (
                chat_id BIGINT PRIMARY KEY,
                last_messages JSONB NOT NULL DEFAULT '[]',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        await conn.execute('''
            INSERT INTO bot_admins (user_id, role, name)
            VALUES ($1, 'admin', 'Owner')
            ON CONFLICT (user_id) DO UPDATE SET role = 'admin'
        ''', config.ADMIN_ID)

        await conn.execute('''
            CREATE TABLE IF NOT EXISTS snake_scores (
                id SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                user_name TEXT DEFAULT 'Player',
                score INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # One row per Telegram user (keyed by the numeric Telegram ID).
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS snake_leaderboard (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                display_name TEXT NOT NULL DEFAULT 'Player',
                best_score INTEGER NOT NULL DEFAULT 0,
                games_played INTEGER NOT NULL DEFAULT 0,
                best_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_snake_lb_rank
            ON snake_leaderboard (best_score DESC, best_at ASC)
        ''')
        # One-time migration of old records (only real numeric Telegram IDs;
        # the random "anon_..." ids from browsers are skipped). The old
        # snake_scores table is kept untouched as a backup.
        if await conn.fetchval('SELECT COUNT(*) FROM snake_leaderboard') == 0:
            await conn.execute('''
                INSERT INTO snake_leaderboard
                    (user_id, display_name, best_score, games_played, best_at)
                SELECT user_id::bigint,
                       COALESCE(MAX(user_name), 'Player'),
                       MAX(score),
                       COUNT(*),
                       (ARRAY_AGG(created_at ORDER BY score DESC, created_at ASC))[1]
                FROM snake_scores
                WHERE user_id ~ '^[0-9]{1,15}$' AND score > 0
                GROUP BY user_id
                ON CONFLICT (user_id) DO NOTHING
            ''')

        await conn.execute('''
            CREATE TABLE IF NOT EXISTS othello_games (
                game_id TEXT PRIMARY KEY,
                board JSONB NOT NULL,
                turn TEXT NOT NULL,
                black_id TEXT NOT NULL,
                black_name TEXT NOT NULL,
                white_id TEXT NOT NULL,
                white_name TEXT NOT NULL,
                game_over BOOLEAN DEFAULT FALSE,
                winner TEXT,
                last_move JSONB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        await conn.execute('''
            CREATE TABLE IF NOT EXISTS othello_lobbies (
                chat_id BIGINT PRIMARY KEY,
                players JSONB NOT NULL DEFAULT '[]',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        await conn.execute('''
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            )
        ''')

        await conn.execute('''
            CREATE TABLE IF NOT EXISTS user_style_samples (
                chat_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                messages JSONB NOT NULL DEFAULT '[]',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (chat_id, user_id)
            )
        ''')

        await conn.execute('''
            CREATE TABLE IF NOT EXISTS user_messages (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                chat_id BIGINT NOT NULL,
                text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_user_messages_user
            ON user_messages (user_id, id DESC)
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS user_styles (
                user_id BIGINT PRIMARY KEY,
                summary TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')


async def _migrate_bot_groups(conn):
    """Idempotent migration: sync bot_groups columns with the current schema.

    Handles:
      - missing group_id column
      - legacy chat_id column (rename to group_id)
      - missing UNIQUE constraint on group_id
      - missing title / is_active / ai_enabled / updated_at columns
    Safe to run multiple times (all ALTERs use IF NOT EXISTS / IF EXISTS).
    """
    # 1. Detect legacy schema — rename chat_id → group_id if present
    legacy = await conn.fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'bot_groups' AND column_name = 'chat_id'"
    )
    if legacy:
        logger.warning("bot_groups: renaming legacy 'chat_id' → 'group_id'")
        await conn.execute("ALTER TABLE bot_groups RENAME COLUMN chat_id TO group_id")

    # 2. Add group_id if completely missing (no legacy column either)
    has_group_id = await conn.fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'bot_groups' AND column_name = 'group_id'"
    )
    if not has_group_id:
        logger.warning("bot_groups: adding missing 'group_id' column")
        await conn.execute("ALTER TABLE bot_groups ADD COLUMN group_id BIGINT")

    # 3. Ensure NOT NULL + UNIQUE on group_id
    #    (first clean up any NULLs left from legacy data)
    await conn.execute("UPDATE bot_groups SET group_id = 0 WHERE group_id IS NULL")
    try:
        await conn.execute("ALTER TABLE bot_groups ALTER COLUMN group_id SET NOT NULL")
    except Exception:
        pass  # already NOT NULL

    # Check if the constraint already exists before trying to create it
    has_constraint = await conn.fetchval(
        "SELECT 1 FROM pg_constraint WHERE conname = 'bot_groups_group_id_key'"
    )
    if not has_constraint:
        try:
            await conn.execute(
                "ALTER TABLE bot_groups ADD CONSTRAINT bot_groups_group_id_key UNIQUE (group_id)"
            )
        except Exception as e:
            logger.warning("Could not add UNIQUE constraint on group_id: %s", e)

    # 4. Add every remaining column the code expects, if missing
    migrations = [
        ("title", "TEXT DEFAULT 'بدون نام'"),
        ("is_active", "BOOLEAN DEFAULT TRUE"),
        ("ai_enabled", "BOOLEAN DEFAULT FALSE"),
        ("updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
    ]
    for col_name, col_type in migrations:
        try:
            await conn.execute(
                f"ALTER TABLE bot_groups ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
            )
        except Exception:
            pass


async def save_othello_game(
    game_id: str, board: list, turn: str,
    black_id: str, black_name: str,
    white_id: str, white_name: str,
    game_over: bool = False, winner: str | None = None,
    last_move: tuple | list | None = None
) -> None:
    """Insert or update an Othello game record."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO othello_games (game_id, board, turn, black_id, black_name, white_id, white_name, game_over, winner, last_move)
            VALUES ($1, $2::jsonb, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)
            ON CONFLICT (game_id) DO UPDATE SET
                board = $2::jsonb, turn = $3, game_over = $8, winner = $9, last_move = $10::jsonb
        """, game_id, json.dumps(board), turn, black_id, black_name, white_id, white_name, game_over, winner, json.dumps(last_move) if last_move else None)


async def load_othello_games() -> list[asyncpg.Record]:
    """Return all unfinished Othello games from the database."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM othello_games WHERE game_over = FALSE")


async def delete_othello_game(game_id: str) -> None:
    """Remove an Othello game record from the database."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM othello_games WHERE game_id = $1", game_id)


async def save_othello_lobby(chat_id: int, players: list[dict]) -> None:
    """Insert or update an Othello lobby record."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO othello_lobbies (chat_id, players)
            VALUES ($1, $2::jsonb)
            ON CONFLICT (chat_id) DO UPDATE SET players = $2::jsonb
        """, chat_id, json.dumps(players))


async def delete_othello_lobby(chat_id: int) -> None:
    """Remove an Othello lobby record from the database."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM othello_lobbies WHERE chat_id = $1", chat_id)


async def load_othello_lobbies() -> list[asyncpg.Record]:
    """Return all Othello lobby records from the database."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM othello_lobbies")


async def get_role(user_id: int) -> str | None:
    """Return the role string for a user, or None if not an admin."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow('SELECT role FROM bot_admins WHERE user_id = $1', user_id)
        return row['role'] if row else None


SNAKE_MAX_SCORE = 399  # 20x20 grid: the snake can never eat more than 399 foods


_RANK_ORDER = "ORDER BY best_score DESC, best_at ASC, user_id ASC"


async def submit_score(user_id: int, username: str | None, display_name: str, score: int) -> dict:
    """Record a finished Snake game for a Telegram user.

    Only the personal best is kept on the leaderboard; the name/@username are
    refreshed on every game so renamed users show up correctly.
    Returns {"is_new_best": bool, "best": int, "rank": int}.
    """
    score = max(0, min(int(score), SNAKE_MAX_SCORE))
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow('''
            WITH old AS (SELECT best_score FROM snake_leaderboard WHERE user_id = $1)
            INSERT INTO snake_leaderboard
                (user_id, username, display_name, best_score, games_played, best_at)
            VALUES ($1, $2, $3, $4, 1, NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                username = EXCLUDED.username,
                display_name = EXCLUDED.display_name,
                games_played = snake_leaderboard.games_played + 1,
                best_score = GREATEST(snake_leaderboard.best_score, EXCLUDED.best_score),
                best_at = CASE WHEN EXCLUDED.best_score > snake_leaderboard.best_score
                               THEN NOW() ELSE snake_leaderboard.best_at END,
                updated_at = NOW()
            RETURNING best_score, (SELECT best_score FROM old) AS prev_best
        ''', user_id, username, display_name, score)
        prev = row['prev_best']
        rank_row = await get_user_rank(user_id, conn)
        return {
            "is_new_best": score > 0 and (prev is None or score > prev),
            "best": row['best_score'],
            "rank": rank_row['rank'] if rank_row else None,
        }


async def get_user_rank(user_id: int, conn=None) -> dict | None:
    """Return {user_id, user_name, username, score, rank} for one player, or None if unranked."""
    async def _run(c):
        r = await c.fetchrow(f'''
            SELECT * FROM (
                SELECT user_id, username, display_name AS user_name, best_score AS score,
                       ROW_NUMBER() OVER ({_RANK_ORDER}) AS rank
                FROM snake_leaderboard WHERE best_score > 0
            ) t WHERE user_id = $1
        ''', user_id)
        if not r:
            return None
        d = dict(r)
        d['user_id'] = str(d['user_id'])
        return d

    if conn is not None:
        return await _run(conn)
    pool = await get_pool()
    async with pool.acquire() as c:
        return await _run(c)


async def get_leaderboard(limit: int = 10) -> list[dict]:
    """Return the top Snake players (one row per user, best score), with ranks."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(f'''
            SELECT user_id, username, display_name AS user_name, best_score AS score,
                   ROW_NUMBER() OVER ({_RANK_ORDER}) AS rank
            FROM snake_leaderboard
            WHERE best_score > 0
            {_RANK_ORDER}
            LIMIT $1
        ''', limit)
        out = []
        for r in rows:
            d = dict(r)
            d['user_id'] = str(d['user_id'])
            out.append(d)
        return out


async def is_ai_enabled(chat_id: int) -> bool:
    """Check whether AI replies are enabled for this chat."""
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            val = await conn.fetchval(
                'SELECT ai_enabled FROM bot_groups WHERE group_id = $1', chat_id
            )
            return bool(val) if val is not None else False
    except Exception as e:
        logger.error("is_ai_enabled error for %s: %s", chat_id, e, exc_info=True)
        return False


async def set_ai_enabled(chat_id: int, enabled: bool, title: str | None = None) -> bool:
    """Turn AI on/off for a chat. Upserts the group if missing. Returns True on success."""
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            result = await conn.fetchval("""
                INSERT INTO bot_groups (group_id, title, ai_enabled, updated_at)
                VALUES ($1, COALESCE($2, 'بدون نام'), $3, CURRENT_TIMESTAMP)
                ON CONFLICT (group_id) DO UPDATE SET
                    ai_enabled = $3,
                    title = COALESCE($2, bot_groups.title),
                    updated_at = CURRENT_TIMESTAMP
                RETURNING ai_enabled
            """, chat_id, title, enabled)
            return bool(result) == enabled
    except Exception as e:
        logger.error("set_ai_enabled error for %s: %s", chat_id, e, exc_info=True)
        return False


async def get_all_keywords() -> list[dict]:
    """Return all keyword-response pairs."""
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                'SELECT keyword, response, match_type FROM bot_keywords ORDER BY id'
            )
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error("get_all_keywords error: %s", e)
        return []


async def log_unmatched(text: str, chat_id: int) -> None:
    """Log or increment frequency of an unmatched message."""
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO unmatched_messages (text, chat_id, frequency, last_seen)
                VALUES ($1, $2, 1, CURRENT_TIMESTAMP)
                ON CONFLICT (text, chat_id) DO UPDATE SET
                    frequency = unmatched_messages.frequency + 1,
                    last_seen = CURRENT_TIMESTAMP
            """, text, chat_id)
    except Exception as e:
        logger.error("log_unmatched error: %s", e)


async def get_top_unmatched(limit: int = 15) -> list[dict]:
    """Return most frequent unmatched messages grouped by text."""
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT text, SUM(frequency)::int AS total
                FROM unmatched_messages
                GROUP BY text
                ORDER BY total DESC
                LIMIT $1
            """, limit)
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error("get_top_unmatched error: %s", e)
        return []


async def get_chat_history(chat_id: int, limit: int = 6) -> list[dict]:
    """Read last N conversation turns for a chat."""
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchval(
                'SELECT last_messages FROM chat_context WHERE chat_id = $1', chat_id
            )
            if row:
                messages = json.loads(row) if isinstance(row, str) else row
                return list(messages)[-limit:]
            return []
    except Exception as e:
        logger.error("get_chat_history error for %s: %s", chat_id, e)
        return []


async def save_chat_turn(chat_id: int, user_text: str, bot_text: str, keep_last: int = 6) -> None:
    """Save one conversation turn, keeping only the last N turns."""
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            existing = await conn.fetchval(
                'SELECT last_messages FROM chat_context WHERE chat_id = $1', chat_id
            )
            messages = json.loads(existing) if isinstance(existing, str) else (list(existing) if existing else [])
            messages.append({"role": "user", "content": user_text})
            messages.append({"role": "assistant", "content": bot_text})
            messages = messages[-keep_last * 2:]

            await conn.execute("""
                INSERT INTO chat_context (chat_id, last_messages, updated_at)
                VALUES ($1, $2::jsonb, CURRENT_TIMESTAMP)
                ON CONFLICT (chat_id) DO UPDATE SET
                    last_messages = $2::jsonb,
                    updated_at = CURRENT_TIMESTAMP
            """, chat_id, json.dumps(messages))
    except Exception as e:
        logger.error("save_chat_turn error for %s: %s", chat_id, e)


async def get_bot_persona() -> str:
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT value FROM bot_settings WHERE key = 'persona'"
            )
            return row['value'] if row else ''
    except Exception as e:
        logger.error("get_bot_persona error: %s", e, exc_info=True)
        return ''


async def set_bot_persona(text: str) -> None:
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO bot_settings (key, value)
                VALUES ('persona', $1)
                ON CONFLICT (key) DO UPDATE SET value = $1
            """, text)
    except Exception as e:
        logger.error("set_bot_persona error: %s", e, exc_info=True)


MAX_STYLE_SAMPLES = 15


async def save_user_style_sample(chat_id: int, user_id: int, text: str) -> None:
    """Append one message to a user's rolling style buffer (per chat), capped
    at MAX_STYLE_SAMPLES. Used to learn how each person writes so replies can
    be adapted to their tone/vocabulary. Silently no-ops on very short/blank text."""
    text = (text or "").strip()
    if not text or len(text) < 2:
        return
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            existing = await conn.fetchval(
                'SELECT messages FROM user_style_samples WHERE chat_id = $1 AND user_id = $2',
                chat_id, user_id
            )
            samples = json.loads(existing) if isinstance(existing, str) else (list(existing) if existing else [])
            samples.append(text)
            samples = samples[-MAX_STYLE_SAMPLES:]
            await conn.execute("""
                INSERT INTO user_style_samples (chat_id, user_id, messages, updated_at)
                VALUES ($1, $2, $3::jsonb, CURRENT_TIMESTAMP)
                ON CONFLICT (chat_id, user_id) DO UPDATE SET
                    messages = $3::jsonb,
                    updated_at = CURRENT_TIMESTAMP
            """, chat_id, user_id, json.dumps(samples))
    except Exception as e:
        logger.error("save_user_style_sample error for chat=%s user=%s: %s", chat_id, user_id, e)


async def get_user_style_samples(chat_id: int, user_id: int, limit: int = 8) -> list[str]:
    """Return the most recent messages a user sent in this chat, for style reference."""
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchval(
                'SELECT messages FROM user_style_samples WHERE chat_id = $1 AND user_id = $2',
                chat_id, user_id
            )
            if not row:
                return []
            samples = json.loads(row) if isinstance(row, str) else row
            return list(samples)[-limit:]
    except Exception as e:
        logger.error("get_user_style_samples error for chat=%s user=%s: %s", chat_id, user_id, e)
        return []


async def get_trigger_word() -> str:
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT value FROM bot_settings WHERE key = 'trigger_word'"
            )
            return row['value'] if row else 'بات'
    except Exception as e:
        logger.error("get_trigger_word error: %s", e, exc_info=True)
        return 'بات'


async def set_trigger_word(word: str) -> None:
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO bot_settings (key, value)
                VALUES ('trigger_word', $1)
                ON CONFLICT (key) DO UPDATE SET value = $1
            """, word)
    except Exception as e:
        logger.error("set_trigger_word error: %s", e, exc_info=True)


# ---------------------------------------------------------------------------
# Smart-reply (/suggest) storage: raw messages + learned style summary
# ---------------------------------------------------------------------------

async def save_message(user_id: int, chat_id: int, text: str) -> None:
    """Store one group message of a user (used to learn their writing style)."""
    text = (text or "").strip()
    if not text:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            'INSERT INTO user_messages (user_id, chat_id, text) VALUES ($1, $2, $3)',
            user_id, chat_id, text
        )


async def get_recent_messages(user_id: int, limit: int = 30) -> list[dict]:
    """Return the user's latest messages, newest first, as dicts with
    'text' and 'chat_id' keys."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            'SELECT text, chat_id FROM user_messages '
            'WHERE user_id = $1 ORDER BY id DESC LIMIT $2',
            user_id, limit
        )
    return [{'text': r['text'], 'chat_id': r['chat_id']} for r in rows]


async def trim_user_messages(user_id: int, keep: int = 40) -> None:
    """Keep only the newest `keep` messages of a user."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            'DELETE FROM user_messages WHERE user_id = $1 AND id NOT IN ('
            '  SELECT id FROM user_messages WHERE user_id = $1 ORDER BY id DESC LIMIT $2)',
            user_id, keep
        )


async def get_style(user_id: int) -> str | None:
    """Return the learned style summary of a user, or None."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            'SELECT summary FROM user_styles WHERE user_id = $1', user_id
        )


async def save_style(user_id: int, summary: str) -> None:
    """Insert or update the learned style summary of a user."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO user_styles (user_id, summary, updated_at)
            VALUES ($1, $2, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id) DO UPDATE SET
                summary = EXCLUDED.summary,
                updated_at = CURRENT_TIMESTAMP
            """,
            user_id, summary
        )


async def delete_user_data(user_id: int) -> None:
    """Delete everything stored about a user for smart-reply (/forget_me)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute('DELETE FROM user_messages WHERE user_id = $1', user_id)
        await conn.execute('DELETE FROM user_styles WHERE user_id = $1', user_id)
        await conn.execute('DELETE FROM user_style_samples WHERE user_id = $1', user_id)
