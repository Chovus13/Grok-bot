# main.py
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from contextlib import asynccontextmanager
import asyncio
import os
from dotenv import load_dotenv
import sqlite3
from bot import ChovusSmartBot, init_db
from config import get_config, set_config
from settings import DB_PATH
import logging
from logging.handlers import RotatingFileHandler

# Podesi logging sa rotacijom
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Kreiraj RotatingFileHandler
file_handler = RotatingFileHandler("bot.log", maxBytes=10*1024*1024, backupCount=5)  # 10 MB po fajlu, čuvaj 5 backup fajlova
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

# Dodaj StreamHandler za konzolu
stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.DEBUG)
stream_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

# Dodaj handlere u logger
logger.addHandler(file_handler)
logger.addHandler(stream_handler)

load_dotenv()

key = os.getenv("API_KEY", "")[:4] + "..." + os.getenv("API_KEY", "")[-4:]
print(f"🔑 Using API_KEY: {key}")

app = FastAPI()
templates = Jinja2Templates(directory="html")

bot = ChovusSmartBot(testnet=True)
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

# Middleware za logovanje
@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.info(f"Incoming request: {request.method} {request.url}")
    response = await call_next(request)
    return response

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await init_db()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database: {str(e)}")
        raise
    yield
    try:
        if bot.running:
            await bot.stop_bot()
            if bot._bot_task and not bot._bot_task.done():
                await bot._bot_task
        await bot.exchange.close()
        logger.info("Exchange instance closed successfully")
    except Exception as e:
        logger.error(f"Error closing exchange instance: {str(e)}")
    logger.info("Shutting down application")

app = FastAPI(lifespan=lifespan)

class TelegramMessage(BaseModel):
    message: str

class StrategyRequest(BaseModel):
    strategy_name: str

class LeverageRequest(BaseModel):
    leverage: int
    symbol: str = None

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
            bot_task = asyncio.create_task(bot.start_bot())
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
            await bot.stop_bot()
            if bot._bot_task and not bot._bot_task.done():
                await bot._bot_task
            bot_task = None
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
        await bot.stop_bot()
        if bot._bot_task and not bot._bot_task.done():
            await bot._bot_task
    await bot.start_bot()
    return {"status": "Bot restarted"}

@app.post("/api/set_strategy")
async def set_strategy_endpoint(request: StrategyRequest):
    strategy_status = bot.set_bot_strategy(request.strategy_name)
    return {"status": f"Strategy set to: {strategy_status}"}

@app.get("/api/balance")
async def get_balance():
    try:
        # Dohvati balans direktno od exchange-a
        balance = await bot.get_available_balance()
        return {
            "wallet_balance": str(balance),
            "score": get_config("score", "0")
        }
    except Exception as e:
        logger.error(f"Error fetching balance: {str(e)}")
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
    pairs = get_config("available_pairs", "")
    return pairs.split(",") if pairs else []

@app.post("/api/send_telegram")
async def send_telegram_endpoint(msg: TelegramMessage):
    return await bot._send_telegram_message(msg.message)

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

        # Dohvati order book podatke
        order_book = await bot.exchange.fetch_order_book(symbol, limit=100)
        bids = order_book['bids']  # Lista [price, amount]
        asks = order_book['asks']  # Lista [price, amount]

        # Pronađi "walls" (najveće količine u bid/ask)
        bid_wall = max(bids, key=lambda x: x[1], default=[0, 0])[0] if bids else 0
        ask_wall = min(asks, key=lambda x: x[1], default=[float('inf'), 0])[0] if asks else 0

        return {
            "price": ticker['last'],
            "support": support,
            "resistance": resistance,
            "bid_wall": bid_wall,
            "ask_wall": ask_wall,
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
            return [{"symbol": s, "price": p, "score": sc, "time": t} for s, p, t in cursor.fetchall()]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching candidates: {e}")

@app.get("/api/signals")
async def get_signals():
    try:
        signals = []
        with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT symbol, price, timestamp FROM trades WHERE outcome = 'TP' ORDER BY id DESC LIMIT 5")
            signals.extend([{"symbol": s, "price": p, "time": t, "type": "Trade (TP)"} for s, p, t in cursor.fetchall()])

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
        bot.set_leverage(request.leverage, symbol=request.symbol)
        if request.symbol:
            set_config(f"leverage_{request.symbol.replace('/', '_')}", str(request.leverage))
        else:
            pairs = get_config("available_pairs", "BTC/USDT,ETH/USDT,SOL/USDT").split(",")
            for sym in pairs:
                set_config(f"leverage_{sym.replace('/', '_')}", str(request.leverage))
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

@app.get("/api/db_dump")
async def db_dump():
    try:
        dump = {}
        with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT key, value FROM config")
            dump["config"] = [{"key": k, "value": v} for k, v in cursor.fetchall()]

            cursor.execute("SELECT symbol, price, timestamp, outcome FROM trades ORDER BY id DESC LIMIT 20")
            dump["trades"] = [{"symbol": s, "price": p, "time": t, "outcome": o} for s, p, t, o in cursor.fetchall()]

            cursor.execute("SELECT symbol, price, score, timestamp FROM candidates ORDER BY score DESC, id DESC LIMIT 20")
            dump["candidates"] = [{"symbol": s, "price": p, "score": sc, "time": t} for s, p, t in cursor.fetchall()]

            cursor.execute("SELECT timestamp, message FROM bot_logs ORDER BY id DESC LIMIT 20")
            dump["bot_logs"] = [{"time": t, "message": m} for t, m in cursor.fetchall()]

        return dump
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error dumping database: {e}")

# Novi endpoint za prikazivanje logova na HTML stranici
@app.get("/logs", response_class=HTMLResponse)
async def show_logs(request: Request):
    try:
        logs = []
        with open("bot.log", "r") as f:
            lines = f.readlines()
            for line in lines[-100:]:  # Prikazujemo poslednjih 100 logova
                # Parsiraj log liniju: format je "2025-06-01 04:06:13,498 - LEVEL - Message"
                parts = line.strip().split(" - ", 2)
                if len(parts) != 3:
                    continue
                timestamp, level, message = parts
                # Filtriraj DEBUG poruke osim ako nisu bitne za skeniranje
                if level == "DEBUG" and not ("Scanned" in message or "Fetching" in message or "Starting pair scanning" in message):
                    continue
                logs.append({"timestamp": timestamp, "level": level, "message": message})
        return templates.TemplateResponse("logs.html", {"request": request, "logs": logs})
    except Exception as e:
        logger.error(f"Error reading logs: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error reading logs: {e}")