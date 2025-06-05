# main.py
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from bot import ChovusSmartBot
import logging
from logging.handlers import RotatingFileHandler
import aiosqlite
from config import get_config, set_config
from settings import DB_PATH
from fastapi.staticfiles import StaticFiles

app = FastAPI()

# Montiramo html folder za statičke fajlove
app.mount("/html", StaticFiles(directory="html"), name="html")

# Postavljanje logovanja
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)  # Smanjujemo na INFO
file_handler = RotatingFileHandler("app.log", maxBytes=10*1024*1024, backupCount=5)
file_handler.setLevel(logging.INFO)  # Smanjujemo na INFO
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(file_handler)

bot = ChovusSmartBot()

@app.on_event("startup")
async def startup_event():
    await bot.start_bot()
    logger.info("Application started")

@app.on_event("shutdown")
async def shutdown_event():
    await bot.stop_bot()
    logger.info("Application shutting down")

@app.get("/api/status")
async def get_status():
    return {"status": bot.get_bot_status()}

@app.post("/api/start")
async def start_bot():
    await bot.start_bot()
    return {"status": "Bot started"}

@app.post("/api/stop")
async def stop_bot():
    await bot.stop_bot()
    return {"status": "Bot stopped"}

@app.post("/api/set_strategy")
async def set_strategy(request: dict):
    strategy = request.get("strategy", "Default")
    bot.set_bot_strategy(strategy)
    await set_config("strategy", strategy)
    return {"status": f"Strategy set to {strategy}"}

@app.get("/api/balance")
async def get_balance():
    try:
        balance = await bot.get_available_balance()
        total_balance = await get_config("total_balance", "0")
        total_balance = float(total_balance) if total_balance else 0.0
        logger.info(f"Returning balance: wallet_balance={balance}, total_balance={total_balance}")
        return {
            "wallet_balance": float(balance),
            "total_balance": total_balance,
            "score": await get_config("score", "0")
        }
    except Exception as e:
        logger.error(f"Error fetching balance: {str(e)}")
        balance = await bot.get_available_balance()
        total_balance = float(await get_config("total_balance", "0"))
        logger.info(f"Fallback balance: wallet_balance={balance}, total_balance={total_balance}")
        return {
            "wallet_balance": float(balance),
            "total_balance": total_balance,
            "score": await get_config("score", "0")
        }

@app.get("/api/config")
async def get_config_value(key: str):
    try:
        value = await get_config(key, "")
        return {"key": key, "value": value}
    except Exception as e:
        logger.error(f"Error fetching config value for {key}: {str(e)}")
        return {"key": key, "value": ""}

@app.get("/api/positions")
async def get_positions():
    return await bot.fetch_positions()

@app.get("/api/position_mode")
async def get_position_mode():
    return {"mode": await bot.fetch_position_mode()}

@app.get("/api/market_data")
async def get_market_data():
    try:
        return await bot._scan_pairs()
    except Exception as e:
        logger.error(f"Error fetching market data: {str(e)}")
        return []

@app.get("/api/order_book")
async def get_order_book():
    return bot.get_order_book()

@app.get("/api/trades")
async def get_trades():
    async with aiosqlite.connect(DB_PATH) as conn:
        cursor = await conn.execute("SELECT * FROM trades ORDER BY timestamp DESC")
        trades = await cursor.fetchall()
        return [{"id": t[0], "symbol": t[1], "price": t[2], "timestamp": t[3], "outcome": t[4]} for t in trades]

@app.get("/api/candidates")
async def get_candidates():
    async with aiosqlite.connect(DB_PATH) as conn:
        cursor = await conn.execute("SELECT * FROM candidates ORDER BY score DESC, timestamp DESC")
        candidates = await cursor.fetchall()
        return [{"id": c[0], "symbol": c[1], "price": c[2], "score": c[3], "timestamp": c[4]} for c in candidates]

@app.get("/api/scanning_status")
async def get_scanning_status():
    return bot.scanning_status

@app.get("/api/signals")
async def get_signals():
    async with aiosqlite.connect(DB_PATH) as conn:
        cursor = await conn.execute("SELECT * FROM candidates WHERE score > 0.5 ORDER BY score DESC")
        signals = await cursor.fetchall()
        return [{"symbol": s[1], "price": s[2], "score": s[3], "timestamp": s[4]} for s in signals]

@app.get("/logs")
async def get_logs():
    try:
        with open("app.log", "r") as f:
            logs = f.readlines()
        return logs[-100:]  # Vraćamo poslednjih 100 linija
    except Exception as e:
        logger.error(f"Error reading logs: {str(e)}")
        return ["No logs available"]

@app.get("/", response_class=HTMLResponse)
async def read_root():
    with open("html/index.html", "r") as f:
        return f.read()

@app.get("/logs.html", response_class=HTMLResponse)
async def read_logs():
    with open("html/logs.html", "r") as f:
        return f.read()