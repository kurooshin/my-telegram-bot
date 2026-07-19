# database.py
import asyncpg
import config

def normalize_persian(text: str) -> str:
    text = text.replace('\u064A', '\u06CC')  # ي -> ی
    text = text.replace('\u0643', '\u06A9')  # ك -> ک
    text = text.replace('\u200C', '')        # ZWNJ
    return text

_pool = None

async def get_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(config.DB_URI, min_size=2, max_size=10, statement_cache_size=0)
    return _pool

async def get_db_connection():
    pool = await get_pool()
    return await pool.acquire()

async def init_db():
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

async def save_othello_game(game_id, board, turn, black_id, black_name, white_id, white_name, game_over=False, winner=None, last_move=None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO othello_games (game_id, board, turn, black_id, black_name, white_id, white_name, game_over, winner, last_move)
            VALUES ($1, $2::jsonb, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)
            ON CONFLICT (game_id) DO UPDATE SET
                board = $2::jsonb, turn = $3, game_over = $8, winner = $9, last_move = $10::jsonb
        """, game_id, json.dumps(board), turn, black_id, black_name, white_id, white_name, game_over, winner, json.dumps(last_move) if last_move else None)

async def load_othello_games():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM othello_games WHERE game_over = FALSE")
        return rows

async def delete_othello_game(game_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM othello_games WHERE game_id = $1", game_id)

async def save_othello_lobby(chat_id, players):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO othello_lobbies (chat_id, players)
            VALUES ($1, $2::jsonb)
            ON CONFLICT (chat_id) DO UPDATE SET players = $2::jsonb
        """, chat_id, json.dumps(players))

async def delete_othello_lobby(chat_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM othello_lobbies WHERE chat_id = $1", chat_id)

async def load_othello_lobbies():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM othello_lobbies")
        return rows

async def get_role(user_id: int) -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow('SELECT role FROM bot_admins WHERE user_id = $1', user_id)
        return row['role'] if row else None

async def submit_score(user_id: str, user_name: str, score: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute('INSERT INTO snake_scores (user_id, user_name, score) VALUES ($1, $2, $3)', user_id, user_name, score)
        old_best = await conn.fetchval(
            'SELECT score FROM snake_scores WHERE user_id = $1 ORDER BY score DESC LIMIT 1 OFFSET 1',
            user_id
        )
        return old_best is None or score > old_best

async def get_leaderboard(limit: int = 10):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            'SELECT DISTINCT ON (s.user_id) s.user_id, s.user_name, s.score FROM snake_scores s ORDER BY s.user_id, s.score DESC LIMIT $1',
            limit
        )
        return [dict(r) for r in rows]
