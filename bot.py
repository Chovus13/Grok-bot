# bot.py
import asyncio
import sqlite3
import pandas as pd
from ccxt.async_support import binance
from typing import List, Tuple
import os
from config import get_config, set_config
from settings import DB_PATH
import logging
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
import json
from pprint import pprint

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

file_handler = RotatingFileHandler("bot.log", maxBytes=10*1024*1024, backupCount=5)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.DEBUG)
stream_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

logger.addHandler(file_handler)
logger.addHandler(stream_handler)

load_dotenv()

def table(values):
    """Formatira listu dict-ova u tabelu za prikaz."""
    if not values:
        return "No data to display"
    first = values[0]
    keys = list(first.keys()) if isinstance(first, dict) else range(0, len(first))
    widths = [max([len(str(v[k])) for v in values]) for k in keys]
    string = ' | '.join(['{:<' + str(w) + '}' for w in widths])
    return "\n".join([string.format(*[str(v[k]) for k in keys]) for v in values])

async def init_db():
    try:
        await asyncio.to_thread(_init_db_sync)
        logger.info("Database initialized successfully in async mode")
    except Exception as e:
        logger.error(f"Error initializing database: {str(e)}")
        raise

def _init_db_sync():
    with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=20.0) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        cursor.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", ("available_pairs", "BTC/USDT,ETH/USDT,ETH/BTC,SUNUSDT,CTSIUSDT"))
        cursor.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", ("leverage_BTC_USDT", "3"))
        cursor.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", ("leverage_ETH_USDT", "3"))
        cursor.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", ("balance", "1000"))
        cursor.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", ("api_key", os.getenv("API_KEY", "")))
        cursor.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", ("api_secret", os.getenv("API_SECRET", "")))

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
        logger.info(f"Using API Key: {self.api_key[:4]}...{self.api_key[-4:]}")
        if not self.api_key or not self.api_secret:
            raise ValueError("API key and secret must be provided via config or environment variables")
        self.exchange = binance({
            'apiKey': self.api_key,
            'secret': self.api_secret,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future',  # Ostaje 'future' jer PM podržava futures
            },
            'urls': {
                'api': {
                    'papi': 'https://testnet.binance.com/papi' if testnet else 'https://papi.binance.com'  # Prebaci na PAPI
                }
            }
        })
        self.exchange.set_sandbox_mode(testnet)
        self.running = False
        self._bot_task = None
        self.current_strategy = "default"
        self.scanning_status = []
        self.order_book = {"bids": {}, "asks": {}, "lastUpdateId": 0}
        self.positions = []
        self.position_mode = "One-way"

    # bot.py (dodaj u klasu ChovusSmartBot)
    async def set_margin_type(self, symbol: str, margin_type: str):
        try:
            await self.exchange.set_margin_mode(margin_type, symbol)
            logger.info(f"Set margin type to {margin_type} for {symbol}")
        except Exception as e:
            logger.error(f"Error setting margin type for {symbol}: {str(e)}")

    # bot.py (dodaj u klasu ChovusSmartBot)
    async def set_position_mode(self, dual_side: bool):
        try:
            await self.exchange.fapiprivate_post_positionside_dual(
                {"dualSidePosition": "true" if dual_side else "false"})
            self.position_mode = "Hedge" if dual_side else "One-way"
            logger.info(f"Set position mode to {self.position_mode}")
        except Exception as e:
            logger.error(f"Error setting position mode: {str(e)}")

    async def start_bot(self):
        self.running = True
        # Postavi Hedge mod
        await self.set_position_mode(True)  # Promeni na False ako želiš One-way
        pairs = get_config("available_pairs", "BTC/USDT,ETH/USDT").split(",")
        for symbol in pairs:
            await self.set_margin_type(symbol, "ISOLATED")  # Ili "CROSS"
        await self.fetch_positions()
        await self.fetch_position_mode()
        self._bot_task = asyncio.create_task(self.run())
        asyncio.create_task(self.maintain_order_book("ETHBTC"))
        logger.info("Bot started")

    async def fetch_balance(self):
        try:
            # PAPI endpoint za balans: /papi/v1/balance
            balance = await self.exchange.papi_get_balance()
            available_balance = float(balance[0].get('balance', 0))  # PAPI vraća listu, uzimamo USDT balans
            total_balance = float(balance[0].get('totalBalance', 0))
            set_config("balance", str(available_balance))
            set_config("total_balance", str(total_balance))
            logger.info(f"Fetched available balance: {available_balance} USDT | Total: {total_balance} USDT")
            return available_balance
        except Exception as e:
            logger.error(f"Error fetching balance: {str(e)}")
            fallback_balance = float(get_config("balance", "1000"))
            logger.warning(f"Using fallback balance: {fallback_balance} USDT")
            return fallback_balance

    async def fetch_positions(self):
        try:
            # PAPI endpoint za pozicije: /papi/v1/um/positionRisk
            positions = await self.exchange.papi_get_um_position_risk()
            self.positions = [
                {
                    "symbol": pos['symbol'],
                    "entryPrice": float(pos.get('entryPrice', 0)),
                    "positionAmt": float(pos.get('positionAmt', 0)),
                    "isolated": pos.get('marginType', 'cross') == 'isolated'
                }
                for pos in positions
            ]
            logger.info(f"Fetched positions: {self.positions}")
            return self.positions
        except Exception as e:
            logger.error(f"Error fetching positions: {str(e)}")
            self.positions = []
            return []

    async def fetch_position_mode(self):
        try:
            # PAPI endpoint za režim pozicija: /papi/v1/um/positionSide/dual
            mode = await self.exchange.papi_get_um_position_side_dual()
            self.position_mode = "Hedge" if mode['dualSidePosition'] else "One-way"
            logger.info(f"Position mode: {self.position_mode}")
            return {"mode": self.position_mode}
        except Exception as e:
            logger.error(f"Error fetching position mode: {str(e)}")
            self.position_mode = "Unknown"
            return {"mode": "Unknown"}


    # async def fetch_positions(self):
    #     try:
    #         positions = await self.exchange.fetch_positions()
    #         self.positions = [
    #             {
    #                 "symbol": pos['symbol'],
    #                 "entryPrice": pos.get('entryPrice', 0),
    #                 "positionAmt": pos.get('positionAmt', 0),
    #                 "isolated": pos.get('isolated', False)
    #             }
    #             for pos in positions
    #         ]
    #         logger.info(f"Fetched positions: {self.positions}")
    #         return self.positions
    #     except Exception as e:
    #         logger.error(f"Error fetching positions: {str(e)}")
    #         self.positions = []
    #         return []
    #
    # async def fetch_position_mode(self):
    #     try:
    #         mode = await self.exchange.fapiprivate_get_positionside_dual()
    #         self.position_mode = "Hedge" if mode['dualSidePosition'] else "One-way"
    #         logger.info(f"Position mode: {self.position_mode}")
    #         return {"mode": self.position_mode}
    #     except Exception as e:
    #         logger.error(f"Error fetching position mode: {str(e)}")
    #         self.position_mode = "Unknown"
    #         return {"mode": "Unknown"}
    #
    # async def get_available_balance(self):
    #     try:
    #         balance = await self.exchange.fetch_balance()
    #         available_balance = balance['USDT']['free']
    #         set_config("balance", str(available_balance))
    #         total_balance = balance['USDT']['total']
    #         set_config("total_balance", str(total_balance))
    #         logger.info(f"Fetched available balance: {available_balance} USDT | Total: {total_balance} USDT")
    #         return available_balance
    #     except Exception as e:
    #         logger.error(f"Error fetching balance: {str(e)}")
    #         fallback_balance = float(get_config("balance", "1000"))
    #         logger.warning(f"Using fallback balance: {fallback_balance} USDT")
    #         return fallback_balance

    async def maintain_order_book(self, symbol="ETHBTC"):
        try:
            snapshot = await self.exchange.fetch_order_book(symbol, limit=1000)
            self.order_book["lastUpdateId"] = snapshot["lastUpdateId"]
            self.order_book["bids"] = {float(price): float(amount) for price, amount in snapshot["bids"]}
            self.order_book["asks"] = {float(price): float(amount) for price, amount in snapshot["asks"]}
            logger.info(f"Order book snapshot fetched for {symbol}: lastUpdateId={self.order_book['lastUpdateId']}")

            stream_name = f"{symbol.lower()}@depth"
            async for event in self.exchange.watch_order_book(symbol, params={"streams": stream_name}):
                if "u" not in event or "U" not in event:
                    continue

                update_id = event["u"]
                first_update_id = event["U"]
                previous_update_id = event.get("pu", 0)

                if update_id < self.order_book["lastUpdateId"]:
                    continue

                if first_update_id <= self.order_book["lastUpdateId"] and update_id >= self.order_book["lastUpdateId"]:
                    pass
                elif previous_update_id != self.order_book["lastUpdateId"]:
                    logger.warning(f"Order book out of sync for {symbol}, restarting...")
                    await self.maintain_order_book(symbol)
                    return

                for price, amount in event["bids"]:
                    price = float(price)
                    amount = float(amount)
                    if amount == 0:
                        self.order_book["bids"].pop(price, None)
                    else:
                        self.order_book["bids"][price] = amount

                for price, amount in event["asks"]:
                    price = float(price)
                    amount = float(amount)
                    if amount == 0:
                        self.order_book["asks"].pop(price, None)
                    else:
                        self.order_book["asks"][price] = amount

                self.order_book["lastUpdateId"] = update_id
                logger.debug(f"Order book updated for {symbol}: lastUpdateId={update_id}")
        except Exception as e:
            logger.error(f"Error maintaining order book for {symbol}: {str(e)}")
            await asyncio.sleep(5)
            await self.maintain_order_book(symbol)

    def get_order_book(self):
        return {
            "bids": sorted([[price, amount] for price, amount in self.order_book["bids"].items()], reverse=True)[:10],
            "asks": sorted([[price, amount] for price, amount in self.order_book["asks"].items()])[:10]
        }

    # bot.py (ažuriraj _scan_pairs)
    async def _scan_pairs(self, limit: int = 10) -> List[Tuple[str, float, float, float, float]]:
        log_action = logger.debug
        log_action("Starting pair scanning for USDⓈ-M Futures...")

        try:
            log_action("Fetching exchange info...")
            await self.exchange.load_markets()
            markets = {
                m['symbol']: m for m in self.exchange.markets.values()
                if m['type'] == 'future' and m['quote'] in ['USDT', 'BTC'] and ':' not in m['symbol']
            }
            log_action(f"Available perpetual futures markets: {list(markets.keys())[:10]}... (total: {len(markets)})")

            normalized_markets = markets

            available_pairs = get_config("available_pairs", "BTC/USDT,ETH/USDT,ETH/BTC,SUNUSDT,CTSIUSDT")
            log_action(f"Raw available_pairs from config: {available_pairs}")
            if not available_pairs:
                available_pairs = "BTC/USDT,ETH/USDT,ETH/BTC,SUNUSDT,CTSIUSDT"
                set_config("available_pairs", available_pairs)
                log_action("No available_pairs in config, using default: BTC/USDT,ETH/USDT,ETH/BTC,SUNUSDT,CTSIUSDT")
            all_futures = available_pairs.split(",") if available_pairs else []
            all_futures = [p.strip().upper() for p in all_futures if p.strip()]
            log_action(f"Normalized pairs to scan: {all_futures}")

            all_futures = [p for p in all_futures if p in normalized_markets]
            log_action(f"Valid futures pairs after filtering: {all_futures}")

            if not all_futures:
                log_action(
                    "No valid USDⓈ-M perpetual pairs found in markets. Available markets may not include perpetual futures.")
                self.scanning_status = [{"symbol": "N/A", "status": "No perpetual pairs to scan"}]
                return []

            symbol_mapping = {symbol: symbol for symbol in all_futures}

            log_action("Fetching tickers...")
            try:
                tickers = {}
                for symbol in all_futures:
                    ticker = await self.exchange.fetch_ticker(symbol)
                    tickers[symbol] = ticker
                    log_action(f"Fetched ticker for {symbol}: {ticker}")
                log_action(f"Fetched tickers for {len(tickers)} pairs: {list(tickers.keys())}...")
            except Exception as e:
                log_action(f"Error fetching tickers: {str(e)}")
                self.scanning_status = [{"symbol": "N/A", "status": f"Error fetching tickers: {str(e)}"}]
                return []

            pairs = []
            self.scanning_status = []
            for symbol in all_futures:
                try:
                    ticker = tickers.get(symbol)
                    if not ticker:
                        log_action(f"No ticker data for {symbol}, skipping.")
                        self.scanning_status.append({"symbol": symbol, "status": "No ticker data"})
                        continue

                    price = ticker.get('last', 0)
                    volume = ticker.get('quoteVolume', 0)
                    if not (volume and price and price > 0):
                        log_action(f"Invalid ticker data for {symbol} | Price: {price} | Volume: {volume}")
                        self.scanning_status.append(
                            {"symbol": symbol, "status": f"Invalid ticker data | Price: {price} | Volume: {volume}"})
                        continue

                    market = normalized_markets.get(symbol, {})
                    lot_size = next(
                        (f for f in market.get('info', {}).get('filters', []) if f['filterType'] == 'LOT_SIZE'), {})
                    price_filter = next(
                        (f for f in market.get('info', {}).get('filters', []) if f['filterType'] == 'PRICE_FILTER'), {})

                    min_qty = float(lot_size.get('minQty', 0))
                    max_qty = float(lot_size.get('maxQty', float('inf')))
                    step_size = float(lot_size.get('stepSize', 0))

                    amount = await self.calculate_amount(symbol, price, min_qty, max_qty, step_size)
                    if not amount:
                        log_action(f"Invalid amount for {symbol}, skipping.")
                        self.scanning_status.append({"symbol": symbol, "status": "Invalid amount"})
                        continue

                    leverage = int(get_config(f"leverage_{symbol.replace('/', '_')}", 3))
                    try:
                        await self.exchange.set_leverage(leverage, symbol=symbol_mapping[symbol])
                        log_action(f"Leverage set to {leverage}x for {symbol}")
                    except Exception as e:
                        log_action(f"Failed to set leverage for {symbol}: {str(e)}")
                        self.scanning_status.append({"symbol": symbol, "status": f"Failed to set leverage: {str(e)}"})
                        continue

                    log_action(f"Fetching candles for {symbol}...")
                    df = await self.get_candles(symbol_mapping[symbol], timeframe='1h', limit=150)
                    if df.empty or len(df) < 150:
                        log_action(f"Not enough data for {symbol} (candles: {len(df)}), skipping.")
                        self.scanning_status.append(
                            {"symbol": symbol, "status": f"Not enough data (candles: {len(df)})"})
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
                    self.scanning_status.append({
                        "symbol": symbol,
                        "status": f"Scanned | Price: {price:.4f} | Score: {score:.2f}",
                        "score": score,
                        "price": price
                    })
                    self.log_candidate(symbol, price, score)
                    if score > 0.2:  # Smanjeno na 0.2 za testiranje
                        await self.place_trade(symbol_mapping[symbol], price, amount)
                        log_action(f"Trade placed for {symbol} with score {score:.2f}")  # Popravljena linija
                    if score > 0.2:
                        pairs.append((symbol, price, volume, score, amount))
                        log_action(
                            f"Candidate selected: {symbol} | Price: {price:.4f} | Amount: {amount} | Score: {score:.2f}")
                except Exception as e:
                    log_action(f"Error scanning {symbol}: {str(e)}")
                    self.scanning_status.append({"symbol": symbol, "status": f"Error: {str(e)}"})
                    continue

            pairs.sort(key=lambda x: x[3], reverse=True)
            log_action(f"Scanning complete. Selected {len(pairs)} candidates.")
            return pairs[:limit]

        except Exception as e:
            log_action(f"Error in pair scanning: {str(e)}")
            self.scanning_status = [{"symbol": "N/A", "status": f"Error in pair scanning: {str(e)}"}]
            return []

    async def calculate_amount(self, symbol: str, price: float, min_qty: float, max_qty: float, step_size: float) -> float:
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

    async def place_trade(self, symbol: str, price: float, amount: float):
        try:
            order = await self.exchange.create_limit_buy_order(symbol, amount, price)
            logger.info(f"Placed buy order for {symbol}: {amount} @ {price} USDT | Order ID: {order['id']}")

            tp_price = price * 1.02
            tp_order = await self.exchange.create_limit_sell_order(symbol, amount, tp_price, params={"stopPrice": tp_price, "type": "TAKE_PROFIT"})
            logger.info(f"Placed TP order for {symbol}: {amount} @ {tp_price} USDT | Order ID: {tp_order['id']}")

            sl_price = price * 0.99
            sl_order = await self.exchange.create_stop_limit_order(symbol, 'sell', amount, sl_price, sl_price)
            logger.info(f"Placed SL order for {symbol}: {amount} @ {sl_price} USDT | Order ID: {sl_order['id']}")

            with sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5.0) as conn:
                cursor = conn.cursor()
                cursor.execute("INSERT INTO trades (symbol, price, outcome) VALUES (?, ?, ?)", (symbol, price, "OPEN"))
                conn.commit()

            return order
        except Exception as e:
            logger.error(f"Error placing trade for {symbol}: {str(e)}")
            return None

    async def start_bot(self):
        self.running = True
        # Postavi margin tip na isolated za sve parove pri pokretanju
        pairs = get_config("available_pairs", "BTC/USDT,ETH/USDT").split(",")
        for symbol in pairs:
            await self.set_margin_type(symbol, "ISOLATED")  # Promeni na "CROSS" ako želiš cross margin
        await self.fetch_positions()
        await self.fetch_position_mode()
        self._bot_task = asyncio.create_task(self.run())
        asyncio.create_task(self.maintain_order_book("ETHBTC"))
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
            pairs = get_config("available_pairs", "BTC/USDT,ETH/USDT").split(",")
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

    # bot.py (ažuriraj log_candidate)
    def log_candidate(self, symbol: str, price: float, score: float):
        try:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=20.0)
            cursor = conn.cursor()
            cursor.execute("INSERT INTO candidates (symbol, price, score) VALUES (?, ?, ?)", (symbol, price, score))
            conn.commit()

            cursor.execute("SELECT symbol, price, score, timestamp FROM candidates ORDER BY score DESC, id DESC")
            candidates = [{"symbol": s, "price": p, "score": sc, "time": t} for s, p, sc, t in cursor.fetchall()]
            with open("user_data/candidates.json", "w") as f:
                json.dump(candidates, f, indent=4)
            logger.info(f"Updated candidates.json with {len(candidates)} candidates")

            conn.close()
            logger.info(f"Logged candidate: {symbol} | Price: {price:.4f} | Score: {score:.2f}")
        except Exception as e:
            logger.error(f"Error logging candidate for {symbol}: {str(e)}")
            raise