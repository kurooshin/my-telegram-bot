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
    """Create all required tables if they don't exist, and seed the admin account."""
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
