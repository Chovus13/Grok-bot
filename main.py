# main.py
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv
import sqlite3
from bot import ChovusSmartBot, set_config
from config import get_config
import logging

logging.basicConfig(
    level=logging.DEBUG,  # Promenjeno sa INFO na DEBUG
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)



load_dotenv()



def do_something():
    logger.info('nain.py - valjda -Doing something')

key = os.getenv("API_KEY", "")[:4] + "..." + os.getenv("API_KEY", "")[-4:]
print(f"🔑 Using API_KEY: {key}")

app = FastAPI()
templates = Jinja2Templates(directory="html")  # Ostaje "html" jer je index.html u tom folderu

# Inicijalizuj bota
bot = ChovusSmartBot()
bot_task = None

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# --- Middleware ---
@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.info(f"Incoming request: {request.method} {request.url}")
    response = await call_next(request)
    return response


load_dotenv()

key = os.getenv("API_KEY", "")[:4] + "..." + os.getenv("API_KEY", "")[-4:]
print(f"🔑 Using API_KEY: {key}")

app = FastAPI()
templates = Jinja2Templates(directory="html")
DB_PATH = Path(os.getenv("DB_PATH", Path(__file__).resolve().parent / "user_data" / "chovusbot.db"))

# Inicijalizuj bota
bot = ChovusSmartBot()
bot_task = None

# API modeli
class TelegramMessage(BaseModel):
    message: str

class StrategyRequest(BaseModel):
    strategy_name: str

class LeverageRequest(BaseModel):
    leverage: int

class AmountRequest(BaseModel):
    amount: float

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/api/start")
async def start_bot_endpoint():
    global bot_task
    if bot_task is None or bot_task.done():
        try:
            bot_task = asyncio.create_task(bot.start_bot())  # Kreiraj task
            return {"status": "Bot started"}
        except Exception as e:
            logger.error(f"Failed to start bot: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to start bot: {e}")
    return {"status": "Bot is already running"}

@app.post("/api/stop")
async def stop_bot_endpoint():
    global bot_task
    if bot.running:
        try:
            bot.stop_bot()
            if bot._bot_task and not bot._bot_task.done():
                await bot._bot_task
            return {"status": "Bot stopped"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to stop bot: {e}")
    return {"status": "Bot is not running"}

@app.get("/api/status")
async def get_bot_status_endpoint():
    return {"status": bot.get_bot_status(), "strategy": bot.current_strategy}

@app.post("/api/restart")
async def restart_bot_endpoint():
    if bot.running:
        bot.stop_bot()
        if bot._bot_task and not bot._bot_task.done():
            await bot._bot_task
    await bot.start_bot()
    return {"status": "Bot restarted"}

@app.post("/api/set_strategy")
async def set_strategy_endpoint(request: StrategyRequest):
    strategy_status = bot.set_bot_strategy(request.strategy_name)
    return {"status": f"Strategy set to: {strategy_status}"}

# @app.get("/api/config")
# def get_config_api():
#     return get_all_config()

@app.get("/api/balance")
def get_balance():
    return {
        "wallet_balance": get_config("balance", "0"),
        "score": get_config("score", "0")
    }

@app.get("/api/trades")
def get_trades():
    try:
        with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT symbol, price, timestamp, outcome FROM trades ORDER BY id DESC LIMIT 20")
            return [{"symbol": s, "price": p, "time": t, "outcome": o} for s, p, t, o in cursor.fetchall()]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching trades: {e}")

@app.get("/api/pairs")
def get_pairs():
    return get_config("available_pairs", "").split(",")

@app.post("/api/send_telegram")
async def send_telegram_endpoint(msg: TelegramMessage):
    return bot._send_telegram_message(msg.message)

@app.get("/api/market_data")
async def get_market_data(symbol: str = "ETH/BTC"):
    try:
        ticker = await bot.exchange.fetch_ticker(symbol)
        df = await bot.get_candles(symbol)
        high = df['high'].rolling(50).max().iloc[-1]
        low = df['low'].rolling(50).min().iloc[-1]
        fib_range = high - low
        support = high - fib_range * 0.618
        resistance = high - fib_range * 0.382
        smma = bot.calc_smma(df['close'], 5)
        wma = bot.calc_wma(df['close'], 144)
        trend = "Bullish" if smma.iloc[-1] > wma.iloc[-1] else "Bearish"
        return {
            "price": ticker['last'],
            "support": support,
            "resistance": resistance,
            "trend": trend
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching market data: {e}")

@app.get("/api/candidates")
async def get_candidates():
    try:
        with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT symbol, price, score, timestamp FROM candidates ORDER BY score DESC, id DESC LIMIT 10")
            return [{"symbol": s, "price": p, "score": sc, "time": t} for s, p, sc, t in cursor.fetchall()]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching candidates: {e}")

@app.get("/api/signals")
async def get_signals():
    try:
        signals = []
        # Prvo proveri da li ima TP trejdova
        with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT symbol, price, timestamp FROM trades WHERE outcome = 'TP' ORDER BY id DESC LIMIT 5")
            signals.extend([{"symbol": s, "price": p, "time": t, "type": "Trade (TP)"} for s, p, t in cursor.fetchall()])

        # Ako nema TP trejdova, proveri kandidate za potencijalne signale
        if not signals:
            with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT symbol, price, score, timestamp FROM candidates WHERE score > 0.5 ORDER BY score DESC LIMIT 5")
                candidates = cursor.fetchall()
                for symbol, price, score, timestamp in candidates:
                    df = await bot.get_candles(symbol)
                    crossover = bot.confirm_smma_wma_crossover(df)
                    in_fib_zone = bot.fib_zone_check(df)
                    if crossover and in_fib_zone:
                        signals.append({"symbol": symbol, "price": price, "time": timestamp, "type": "Potential (Crossover + Fib)"})
        return signals if signals else [{"symbol": "N/A", "price": 0, "time": "N/A", "type": "N/A"}]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching signals: {e}")

@app.post("/api/set_leverage")
async def set_leverage(request: LeverageRequest):
    try:
        bot.set_leverage(request.leverage)
        set_config("leverage", str(request.leverage))
        return {"status": f"Leverage set to: {request.leverage}x"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error setting leverage: {e}")

@app.post("/api/set_manual_amount")
async def set_manual_amount(request: AmountRequest):
    try:
        bot.set_manual_amount(request.amount)
        set_config("manual_amount", str(request.amount))
        return {"status": f"Manual amount set to: {request.amount} USDT"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error setting manual amount: {e}")

@app.get("/api/logs")
async def get_logs():
    try:
        with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT timestamp, message FROM bot_logs ORDER BY id DESC LIMIT 10")
            return [{"time": t, "message": m} for t, m in cursor.fetchall()]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching logs: {e}")
