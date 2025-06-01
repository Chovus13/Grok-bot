# config.py
import sqlite3
import os
from settings import DB_PATH  # Uvozi DB_PATH iz settings.py
import logging

logger = logging.getLogger(__name__)

# Fallback konfiguracija ako SQLite ne radi
DEFAULT_CONFIG = {
    "api_key": os.getenv("API_KEY", ""),
    "api_secret": os.getenv("API_SECRET", ""),
    "available_pairs": "BTC/USDT,ETH/USDT,SOL/USDT",
    "leverage_BTC_USDT": "10",
    "leverage_ETH_USDT": "10",
    "leverage_SOL_USDT": "10",
    "balance": "99"
}

def get_config(key: str, default=None):
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5.0)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM config WHERE key=?", (key,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else DEFAULT_CONFIG.get(key, default)
    except Exception as e:
        logger.error(f"Error fetching config for {key}: {str(e)}")
        logger.warning(f"Falling back to default config for {key}")
        return DEFAULT_CONFIG.get(key, default)

def set_config(key: str, value: str):
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5.0)
        cursor = conn.cursor()
        cursor.execute("REPLACE INTO config (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Error setting config for {key}: {str(e)}")