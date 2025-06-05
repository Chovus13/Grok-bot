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
import logging
from logging.handlers import RotatingFileHandler
import aiosqlite

from bot import ChovusSmartBot, init_db
from config import get_config, set_config
from settings import DB_PATH

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
logger.info(f"🔑 Using API_KEY: {key}")

app = FastAPI()
templates = Jinja2Templates(directory="html")

bot = ChovusSmartBot(testnet=False)
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
    # Startup
    await init_db()  # Inicijalizuj bazu asinhrono
    global bot
    bot = ChovusSmartBot()
    await bot.start_bot()
    yield
    # Shutdown
    await bot.stop_bot()
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
        balance = await bot.fetch_balance()  # Zameni sa fetch_balance
        total_balance = get_config("total_balance", "0")
        total_balance = float(total_balance) if total_balance else 0.0
        return {
            "wallet_balance": float(balance),
            "total_balance": total_balance,
            "score": get_config("score", "0")
        }
    except Exception as e:
        logger.error(f"Error fetching balance: {str(e)}")
        return {
            "wallet_balance": float(get_config("balance", "1000")),
            "total_balance": 0.0,
            "score": get_config("score", "0")
        }

@app.get("/api/trades")
async def get_trades():
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT symbol, price, timestamp, outcome FROM trades ORDER BY id DESC LIMIT 20")
            return [{"symbol": s, "price": p, "time": t, "outcome": o} for s, p, t, o in await cursor.fetchall()]
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
async def get_market_data():
    try:
        price = float(get_config("price", "0.0239"))
        bid_wall = float(get_config("bid_wall", "0.0238"))
        ask_wall = float(get_config("ask_wall", "0.0239"))
        support = bid_wall * 1.005  # 0.5% iznad bid wall-a
        resistance = ask_wall * 1.015  # 1.5% iznad ask wall-a
        trend = "Bullish" if price > (support + resistance) / 2 else "Bearish"
        return {
            "price": price,
            "support": support,
            "resistance": resistance,
            "bid_wall": bid_wall,
            "ask_wall": ask_wall,
            "trend": trend
        }
    except Exception as e:
        logger.error(f"Error fetching market data: {str(e)}")
        return {
            "price": 0.0239,
            "support": 0.0243,
            "resistance": 0.0245,
            "bid_wall": 0.0238,
            "ask_wall": 0.0239,
            "trend": "Bearish"
        }

@app.get("/api/candidates")
async def get_candidates():
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT symbol, price, score, timestamp FROM candidates ORDER BY score DESC, id DESC LIMIT 10")
            return [{"symbol": s, "price": p, "score": sc, "time": t} for s, p, sc, t in await cursor.fetchall()]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching candidates: {e}")

@app.get("/api/signals")
async def get_signals():
    try:
        signals = []
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT symbol, price, timestamp FROM trades WHERE outcome = 'TP' ORDER BY id DESC LIMIT 5")
            signals.extend([{"symbol": s, "price": p, "time": t, "type": "Trade (TP)"} for s, p, t in await cursor.fetchall()])

        if not signals:
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute("SELECT symbol, price, score, timestamp FROM candidates WHERE score > 0.5 ORDER BY score DESC LIMIT 5")
                candidates = await cursor.fetchall()
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

@app.get("/logs", response_class=HTMLResponse)
async def show_logs(request: Request):
    try:
        logs = []
        with open("bot.log", "r") as f:
            lines = f.readlines()
            for line in lines[-100:]:  # Prikazujemo poslednjih 100 logova
                parts = line.strip().split(" - ", 2)
                if len(parts) != 3:
                    continue
                timestamp, level, message = parts
                if level in ["INFO", "WARNING", "ERROR"] or (level == "DEBUG" and any(keyword in message for keyword in ["Available futures markets", "Raw available_pairs", "Scanning", "Scanned", "Fetching", "Starting pair scanning"])):
                    logs.append({"timestamp": timestamp, "level": level, "message": message})
        return templates.TemplateResponse("logs.html", {"request": request, "logs": logs})
    except Exception as e:
        logger.error(f"Error reading logs: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error reading logs: {e}")

@app.get("/api/scanning_status")
async def get_scanning_status():
    try:
        return bot.scanning_status
    except Exception as e:
        logger.error(f"Error fetching scanning status: {str(e)}")
        return [{"symbol": "N/A", "status": "Error fetching status"}]

@app.get("/api/order_book")
async def get_order_book():
    try:
        return bot.get_order_book()
    except Exception as e:
        logger.error(f"Error fetching order book: {str(e)}")
        return {"bids": [], "asks": []}

@app.get("/api/positions")
async def get_positions():
    try:
        positions = await bot.fetch_positions()
        return positions
    except Exception as e:
        logger.error(f"Error fetching positions: {str(e)}")
        return []

@app.get("/api/position_mode")
async def get_position_mode():
    try:
        mode = await bot.fetch_position_mode()
        return {"mode": mode}
    except Exception as e:
        logger.error(f"Error fetching position mode: {str(e)}")
        return {"mode": "Unknown"}