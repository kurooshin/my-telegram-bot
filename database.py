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


async def submit_score(user_id: str, user_name: str, score: int) -> bool:
    """Submit a Snake score. Returns True if it's a new personal best."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute('INSERT INTO snake_scores (user_id, user_name, score) VALUES ($1, $2, $3)', user_id, user_name, score)
        old_best = await conn.fetchval(
            'SELECT score FROM snake_scores WHERE user_id = $1 ORDER BY score DESC LIMIT 1 OFFSET 1',
            user_id
        )
        return old_best is None or score > old_best


async def get_leaderboard(limit: int = 10) -> list[dict]:
    """Return the top Snake scores, each player's best score only, ordered by score descending."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch('''
            SELECT s.user_id, MAX(s.user_name) AS user_name, MAX(s.score) AS score
            FROM snake_scores s
            GROUP BY s.user_id
            ORDER BY score DESC
            LIMIT $1
        ''', limit)
        return [dict(r) for r in rows]


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
