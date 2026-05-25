import asyncpg
from typing import Optional

_pool: Optional[asyncpg.Pool] = None


async def init_pool(host: str, port: int, database: str, user: str, password: str):
    global _pool
    _pool = await asyncpg.create_pool(
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
        min_size=1,
        max_size=5,
        command_timeout=10,
    )


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def get_pool() -> Optional[asyncpg.Pool]:
    return _pool
