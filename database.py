import asyncpg
from config import DATABASE_URL

db_pool: asyncpg.Pool | None = None

async def init_db() -> None:
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                uuid TEXT,
                expiry_date TIMESTAMP,
                custom_id TEXT UNIQUE,
                referrer_id BIGINT,
                referral_count INTEGER DEFAULT 0,
                last_support_time TIMESTAMP
            );
            """
        )
        try:
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS custom_id TEXT UNIQUE;")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referrer_id BIGINT;")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_count INTEGER DEFAULT 0;")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_support_time TIMESTAMP;")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_bonus_claim TIMESTAMP;")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS expired_notification_sent BOOLEAN DEFAULT FALSE;")
        except Exception:
            pass