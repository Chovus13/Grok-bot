# config.py
import aiosqlite
import os
from settings import DB_PATH
import logging

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "api_key": os.getenv("API_KEY", ""),
    "api_secret": os.getenv("API_SECRET", ""),
    "available_pairs": "BTC/USDT:USDT,ETH/USDT:USDT",
    "leverage_BTC_USDT": "10",
    "leverage_ETH_USDT": "10",
    "leverage_SOL_USDT": "10",
    "balance": "99"
}

async def get_config(key: str, default=None):
    try:
        async with aiosqlite.connect(DB_PATH) as conn:
            cursor = await conn.execute("SELECT value FROM config WHERE key=?", (key,))
            result = await cursor.fetchone()
            return result[0] if result else DEFAULT_CONFIG.get(key, default)
    except Exception as e:
        logger.error(f"Error fetching config for {key}: {str(e)}")
        logger.warning(f"Falling back to default config for {key}")
        return DEFAULT_CONFIG.get(key, default)

async def set_config(key: str, value: str):
    try:
        async with aiosqlite.connect(DB_PATH) as conn:
            await conn.execute("REPLACE INTO config (key, value) VALUES (?, ?)", (key, value))
            await conn.commit()
    except Exception as e:
        logger.error(f"Error setting config for {key}: {str(e)}")