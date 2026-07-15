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
                response TEXT NOT NULL
            );
        ''')
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

async def get_role(user_id: int) -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow('SELECT role FROM bot_admins WHERE user_id = $1', user_id)
        return row['role'] if row else None
