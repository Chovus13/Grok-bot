# bot.py
import asyncio
import sqlite3
import pandas as pd
from ccxt.async_support import binance
from typing import List, Tuple
import os
from config import get_config
from settings import DB_PATH
import logging
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv

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


async def init_db():
    try:
        await asyncio.to_thread(_init_db_sync)
        logger.info("Database initialized successfully in async mode")
    except Exception as e:
        logger.error(f"Error initializing database: {str(e)}")
        raise


def _init_db_sync():
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        cursor.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)",
                       ("available_pairs", "BTC/USDT,ETH/USDT,SOL/USDT"))
        cursor.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", ("leverage_BTC_USDT", "10"))
        cursor.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", ("leverage_ETH_USDT", "10"))
        cursor.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", ("leverage_SOL_USDT", "10"))
        cursor.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", ("balance", "1000"))
        cursor.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", ("api_key", os.getenv("API_KEY", "")))
        cursor.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)",
                       ("api_secret", os.getenv("API_SECRET", "")))

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                price REAL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                outcome TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                price REAL,
                score REAL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bot_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                message TEXT
            )
        """)
        conn.commit()


class ChovusSmartBot:
    def __init__(self, api_key: str = None, api_secret: str = None, testnet: bool = False):
        self.api_key = api_key or get_config("api_key", os.getenv("API_KEY", ""))
        self.api_secret = api_secret or get_config("api_secret", os.getenv("API_SECRET", ""))
        if not self.api_key or not self.api_secret:
            raise ValueError("API key and secret must be provided via config or environment variables")
        self.exchange = binance({
            'apiKey': self.api_key,
            'secret': self.api_secret,
            'enableRateLimit': True,
            'urls': {
                'api': {
                    'fapi': 'https://testnet.binance.vision'
                }
            } if testnet else {
                'api': {
                    'fapi': 'https://testnet.binancefuture.com'
                }
            }
        })
        self.exchange.set_sandbox_mode(True)  # Eksplicitno uključi sandbox mod
        self.running = False
        self._bot_task = None
        self.current_strategy = "default"

    async def _scan_pairs(self, limit: int = 10) -> List[Tuple[str, float, float, float, float]]:
        log_action = logger.info
        log_action("Starting pair scanning for USDⓈ-M Futures...")

        try:
            log_action("Fetching exchange info...")
            exchange_info = await self.exchange.fetch_markets()
            markets = {m['symbol']: m for m in exchange_info if m['type'] == 'future' and m['quote'] == 'USDT'}

            # Proveri available_pairs iz config-a, ako nije postavljen koristi default
            available_pairs = get_config("available_pairs", "BTC/USDT,ETH/USDT,SOL/USDT")
            if not available_pairs:
                available_pairs = "BTC/USDT,ETH/USDT,SOL/USDT"  # Dodatni fallback
                log_action("No available_pairs in config, using default: BTC/USDT,ETH/USDT,SOL/USDT")
            all_futures = available_pairs.split(",") if available_pairs else []
            all_futures = [p for p in all_futures if p in markets]
            log_action(f"Scanning {len(all_futures)} predefined pairs: {all_futures}...")

            if not all_futures:
                log_action("No valid USDⓈ-M pairs defined in config or markets. Add pairs to scan.")
                return []

            log_action("Fetching tickers...")
            try:
                tickers = await self.exchange.fetch_tickers(all_futures)
                log_action(f"Fetched tickers for {len(tickers)} pairs: {list(tickers.keys())[:5]}...")
                await asyncio.sleep(0.5)
            except Exception as e:
                log_action(f"Error fetching tickers: {str(e)}")
                return []

            pairs = []
            for symbol in all_futures:
                try:
                    ticker = tickers.get(symbol)
                    if not ticker:
                        log_action(f"No ticker data for {symbol}, skipping.")
                        continue

                    price = ticker.get('last', 0)
                    volume = ticker.get('quoteVolume', 0)
                    if not (volume and price and price > 0):
                        log_action(f"Invalid ticker data for {symbol} | Price: {price} | Volume: {volume}")
                        continue

                    market = markets.get(symbol, {})
                    lot_size = next((f for f in market.get('filters', []) if f['filterType'] == 'LOT_SIZE'), {})
                    price_filter = next((f for f in market.get('filters', []) if f['filterType'] == 'PRICE_FILTER'), {})

                    min_qty = float(lot_size.get('minQty', 0))
                    max_qty = float(lot_size.get('maxQty', float('inf')))
                    step_size = float(lot_size.get('stepSize', 0))

                    amount = await self.calculate_amount(symbol, price, min_qty, max_qty, step_size)
                    if not amount:
                        log_action(f"Invalid amount for {symbol}, skipping.")
                        continue

                    leverage = int(get_config(f"leverage_{symbol.replace('/', '_')}", 10))
                    try:
                        await self.exchange.set_leverage(leverage, symbol=symbol)
                        log_action(f"Leverage set to {leverage}x for {symbol}")
                    except Exception as e:
                        log_action(f"Failed to set leverage for {symbol}: {str(e)}")
                        continue

                    log_action(f"Fetching candles for {symbol}...")
                    df = await self.get_candles(symbol, timeframe='1h', limit=150)
                    if df.empty or len(df) < 150:
                        log_action(f"Not enough data for {symbol} (candles: {len(df)}), skipping.")
                        continue

                    log_action(f"Calculating indicators for {symbol}...")
                    crossover = self.confirm_smma_wma_crossover(df)
                    in_fib_zone = self.fib_zone_check(df)
                    avg_volume = df['volume'].iloc[-50:].mean() if len(df) >= 50 else volume
                    score = self.ai_score(price, volume, avg_volume, crossover, in_fib_zone)

                    log_action(
                        f"Scanned {symbol} | Price: {price:.4f} | Volume: {volume:.2f} | Amount: {amount} | "
                        f"Score: {score:.2f} | Crossover: {crossover} | Fib Zone: {in_fib_zone}"
                    )
                    self.log_candidate(symbol, price, score)
                    if score > 0.2:
                        pairs.append((symbol, price, volume, score, amount))
                        log_action(
                            f"Candidate selected: {symbol} | Price: {price:.4f} | Amount: {amount} | Score: {score:.2f}")
                except Exception as e:
                    log_action(f"Error scanning {symbol}: {str(e)}")
                    continue

            pairs.sort(key=lambda x: x[3], reverse=True)
            log_action(f"Scanning complete. Selected {len(pairs)} candidates.")
            return pairs[:limit]

        except Exception as e:
            log_action(f"Error in pair scanning: {str(e)}")
            return []

    async def calculate_amount(self, symbol: str, price: float, min_qty: float, max_qty: float,
                               step_size: float) -> float:
        try:
            balance = await self.get_available_balance()
            target_risk = balance * 0.1 / price
            amount = max(min_qty, min(max_qty, round(target_risk / step_size) * step_size))

            market = self.exchange.markets.get(symbol, {})
            precision = market.get('precision', {}).get('amount', 8)
            amount = round(amount, precision)

            if amount < min_qty or amount > max_qty:
                logger.error(f"Calculated amount {amount} for {symbol} is out of bounds [{min_qty}, {max_qty}]")
                return 0
            return amount
        except Exception as e:
            logger.error(f"Error calculating amount for {symbol}: {str(e)}")
            return 0

    async def get_available_balance(self) -> float:
        try:
            balance = await self.exchange.fetch_balance(params={"type": "future"})
            available = float(balance['USDT'].get('free', 0))
            logger.info(f"Fetched available balance: {available} USDT")
            return available
        except Exception as e:
            logger.error(f"Error fetching balance: {str(e)}")
            fallback = float(get_config("balance", "0"))
            logger.warning(f"Using fallback balance: {fallback} USDT")
            return fallback

    async def start_bot(self):
        self.running = True
        self._bot_task = asyncio.create_task(self.run())
        logger.info("Bot started")

    async def stop_bot(self):
        self.running = False
        if self._bot_task:
            self._bot_task.cancel()
        try:
            await self.exchange.close()
            logger.info("Exchange instance closed in stop_bot")
        except Exception as e:
            logger.error(f"Error closing exchange in stop_bot: {str(e)}")
        logger.info("Bot stopped")

    def get_bot_status(self):
        return "running" if self.running else "stopped"

    def set_bot_strategy(self, strategy_name: str):
        self.current_strategy = strategy_name
        logger.info(f"Strategy set to: {strategy_name}")
        return self.current_strategy

    def set_leverage(self, leverage: int, symbol: str = None):
        if symbol is None:
            pairs = get_config("available_pairs", "BTC/USDT,ETH/USDT,SOL/USDT").split(",")
            for sym in pairs:
                try:
                    asyncio.create_task(self.exchange.set_leverage(leverage, symbol=sym))
                    logger.info(f"Leverage set to {leverage}x for {sym}")
                except Exception as e:
                    logger.error(f"Failed to set leverage for {sym}: {str(e)}")
        else:
            try:
                asyncio.create_task(self.exchange.set_leverage(leverage, symbol=symbol))
                logger.info(f"Leverage set to {leverage}x for {symbol}")
            except Exception as e:
                logger.error(f"Failed to set leverage for {symbol}: {str(e)}")

    def set_manual_amount(self, amount: float):
        logger.info(f"Manual amount set to {amount} USDT")

    async def _send_telegram_message(self, message: str):
        logger.info(f"Telegram message sent: {message}")
        return {"status": "Message sent"}

    async def run(self):
        while self.running:
            candidates = await self._scan_pairs(limit=10)
            for symbol, price, volume, score, amount in candidates:
                logger.info(f"Top candidate: {symbol} | Price: {price:.4f} | Amount: {amount} | Score: {score:.2f}")
            await asyncio.sleep(60)

    async def get_candles(self, symbol: str, timeframe: str = '1h', limit: int = 150) -> pd.DataFrame:
        try:
            if not symbol:
                raise ValueError("Symbol cannot be empty")
            ohlcv = await self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            return df
        except Exception as e:
            logger.error(f"Error fetching candles for {symbol}: {str(e)}")
            return pd.DataFrame()

    def confirm_smma_wma_crossover(self, df: pd.DataFrame) -> bool:
        try:
            smma = self.calc_smma(df['close'], 5)
            wma = self.calc_wma(df['close'], 144)
            return smma.iloc[-1] > wma.iloc[-1] and smma.iloc[-2] <= wma.iloc[-2]
        except Exception as e:
            logger.error(f"Error calculating SMMA/WMA crossover: {str(e)}")
            return False

    def fib_zone_check(self, df: pd.DataFrame) -> bool:
        try:
            high = df['high'].rolling(50).max().iloc[-1]
            low = df['low'].rolling(50).min().iloc[-1]
            fib_range = high - low
            support = high - fib_range * 0.618
            resistance = high - fib_range * 0.382
            current_price = df['close'].iloc[-1]
            return support <= current_price <= resistance
        except Exception as e:
            logger.error(f"Error checking Fibonacci zone: {str(e)}")
            return False

    def ai_score(self, price: float, volume: float, avg_volume: float, crossover: bool, in_fib_zone: bool) -> float:
        score = 0.0
        if crossover:
            score += 0.4
        if in_fib_zone:
            score += 0.3
        if volume > avg_volume:
            score += 0.3
        return score

    def calc_smma(self, series: pd.Series, period: int) -> pd.Series:
        smma = series.copy()
        for i in range(period, len(series)):
            smma.iloc[i] = (smma.iloc[i - 1] * (period - 1) + series.iloc[i]) / period
        return smma

    def calc_wma(self, series: pd.Series, period: int) -> pd.Series:
        weights = pd.Series(range(1, period + 1))
        return series.rolling(period).apply(lambda x: (x * weights).sum() / weights.sum(), raw=True)

    def log_candidate(self, symbol: str, price: float, score: float):
        try:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5.0)
            cursor = conn.cursor()
            cursor.execute("INSERT INTO candidates (symbol, price, score) VALUES (?, ?, ?)", (symbol, price, score))
            conn.commit()

            # Učitaj trenutne kandidate i eksportuj u JSON
            cursor.execute("SELECT symbol, price, score, timestamp FROM candidates ORDER BY score DESC, id DESC")
            candidates = [{"symbol": s, "price": p, "score": sc, "time": t} for s, p, t in cursor.fetchall()]
            with open("user_data/candidates.json", "w") as f:
                json.dump(candidates, f, indent=4)

            conn.close()
            logger.info(f"Logged candidate: {symbol} | Price: {price:.4f} | Score: {score:.2f}")
        except Exception as e:
            logger.error(f"Error logging candidate for {symbol}: {str(e)}")