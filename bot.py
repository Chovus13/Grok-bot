
# bot.py
import asyncio
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
import websockets
from pprint import pprint
import aiosqlite
import aiofiles



logger = logging.getLogger(__name__)
# Podešavanje logger-a
os.makedirs('logs', exist_ok=True)
logger.setLevel(logging.INFO)

file_handler = logging.FileHandler('logs/bot.log')
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(file_handler)

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(stream_handler)

load_dotenv()


# DB setup
DB_PATH = Path(os.getenv("DB_PATH", Path(__file__).resolve().parent / "user_data" / "chovusbot.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)




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
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('''CREATE TABLE IF NOT EXISTS candidates
                              (symbol TEXT, price REAL, score REAL, timestamp INTEGER)''')
            await db.execute('''CREATE TABLE IF NOT EXISTS trades
                              (symbol TEXT, price REAL, amount REAL, timestamp INTEGER)''')
            await db.commit()
        logger.info("Database initialized successfully in async mode")
    except Exception as e:
        logger.error(f"Error initializing database: {str(e)}")
        raise

def log_trade(symbol, price, outcome):
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        cursor = conn.cursor()
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("INSERT INTO trades (symbol, price, timestamp, outcome) VALUES (?, ?, ?, ?)", (symbol, price, now, outcome))
        conn.commit()

def log_score(score):
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        cursor = conn.cursor()
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("INSERT INTO score_log (timestamp, score) VALUES (?, ?)", (now, score))
        conn.commit()



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
                'defaultType': 'future',
            },
            'urls': {
                'api': {
                    'papi': 'https://testnet.binance.com/papi' if testnet else 'https://papi.binance.com'
                }
            }
        })
        self.exchange.set_sandbox_mode(testnet)

# Constants
SYMBOLS = []
ROUND_LEVELS = [0.01, 0.1, 0.5, 1, 5, 10, 50, 100, 500, 1000]
VOLUME_SPIKE_THRESHOLD = 1.5  # Pretpostavka za ai_score
TRADE_DURATION_LIMIT = 60 * 10
STOP_LOSS_PERCENT = 0.01
TRAILING_TP_STEP = 0.005
TRAILING_TP_OFFSET = 0.02
#leverage = []
#LEVERAGE = 1

class ChovusSmartBot:
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.info("Inicijalizacija ChovusSmartBot-a...")
        self.running = False
        self.current_strategy = "Default"
        self.leverage = int(get_config("leverage", "10"))
        self.manual_amount = float(get_config("manual_amount", "0"))
        self._bot_task = None
        self._telegram_report_thread = None
        self.exchange = ccxt.binance({
            'apiKey': os.getenv('API_KEY'),
            'secret': os.getenv('API_SECRET'),
            'enableRateLimit': True,
            'urls': {'api': {'fapi': 'https://testnet.binancefuture.com'}},
        })
        self.exchange.load_markets()
        self.exchange.fetch_balance
        if get_config("balance") is None:
            set_config("balance", "99.0")
        if get_config("score") is None:
            set_config("score", "0")
        if get_config("report_time") is None:
            set_config("report_time", "09:00")


    scan_pairs = _scan_pairs
    calculate_amount = calculate_amount
    get_available_balance = get_available_balance

    # FIXME async def connect_websocket(self):
    #     ws = await exchange.watch_ticker('ETHBTC')
    #     async for msg in ws:
    #         logger.debug(f"Received ticker: {msg}")

    # TODO Websocket API je odvojen od market data stream-a, pa za cene koristi wss://dapi.binance.com/dapi/v1 (za COIN-M) ili wss://fstream.binance.com (za USDⓈ-M).

    async def run(self):
        candidates = await self._scan_pairs(limit=10)
        for symbol, price, volume, score, amount in candidates:
            logging.info(f"Top candidate: {symbol} | Price: {price} | Amount: {amount} | Score: {score}")

    async def start_bot(self):
        if self.running:
            log_action("Bot is already running.")
            return
        log_action("Bot starting...")
        self.running = True
        try:
            await self.set_leverage(self.symbol, self.leverage)  # Osiguraj da je set_leverage await-ovan
        except Exception as e:
            log_action(f"Error setting leverage in start_bot: {str(e)}")
            self.running = False
            raise
        self._bot_task = asyncio.create_task(self._main_bot_loop())
        if self._telegram_report_thread is None or not self._telegram_report_thread.is_alive():
            self._telegram_report_thread = threading.Thread(target=self._send_report_loop, daemon=True)
            self._telegram_report_thread.start()
        log_action("Bot started.")

    def stop_bot(self):
        if not self.running:
            log_action("Bot is not running.")
            return
        log_action("Bot stopping...")

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

    async def stream_order_book(self, symbol: str):
        try:
            symbol = symbol.replace('/', '').lower()  # Pretvori ETH/BTC u ethbtc
            uri = f"wss://papi.binance.com/ws/{symbol}@depth20@100ms"  # PAPI WebSocket za order book
            async with websockets.connect(uri) as websocket:
                while self.running:
                    message = await websocket.recv()
                    data = json.loads(message)
                    self.order_book = {
                        "bids": {float(price): float(amount) for price, amount in data['bids'][:5]},
                        "asks": {float(price): float(amount) for price, amount in data['asks'][:5]},
                        "lastUpdateId": data['lastUpdateId']
                    }
                    # Ažuriraj UI podatke
                    price = (list(self.order_book['bids'].keys())[0] + list(self.order_book['asks'].keys())[0]) / 2
                    set_config("price", str(price))
                    set_config("bid_wall", str(min(self.order_book['bids'].keys())))
                    set_config("ask_wall", str(max(self.order_book['asks'].keys())))
                    logger.debug(f"Updated order book for {symbol}: {self.order_book}")
                    await asyncio.sleep(0.1)  # Spreči preopterećenje
        except Exception as e:
            logger.error(f"Error in WebSocket stream for {symbol}: {str(e)}")
            self.order_book = {"bids": {}, "asks": {}, "lastUpdateId": 0}

    async def start_bot(self):
        self.running = True
        await self.set_position_mode(False)  # One-way mod
        pairs = get_config("available_pairs", "BTC/USDT,ETH/USDT,ETH/BTC,SUNUSDT,CTSIUSDT").split(",")
        for symbol in pairs:
            await self.set_margin_type(symbol, "ISOLATED")
        await self.fetch_positions()
        await self.fetch_position_mode()
        self._bot_task = asyncio.create_task(self.run())
        # Pokreni WebSocket stream za ETH/BTC
        asyncio.create_task(self.stream_order_book("ETH/BTC"))
        logger.info("Bot started")


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
            # Koristi CCXT fetch_balance za PAPI
            balance = await self.exchange.fetch_balance(params={'recvWindow': 5000})
            usdt_balance = next((asset for asset, info in balance['info'].items() if asset == 'USDT'), None)
            if not usdt_balance:
                raise ValueError("USDT balance not found in response")
            available_balance = float(balance['USDT'].get('free', 0))
            total_balance = float(balance['USDT'].get('total', 0))
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
            # Koristi CCXT request za PAPI endpoint /papi/v1/um/positionRisk
            positions = await self.exchange.request('papi/v1/um/positionRisk', 'GET', params={'recvWindow': 5000})
            if not isinstance(positions, list):
                raise ValueError("Positions response is not a list")
            self.positions = [
                {
                    "symbol": pos.get('symbol', ''),
                    "entryPrice": float(pos.get('entryPrice', 0)),
                    "positionAmt": float(pos.get('positionAmt', 0)),
                    "isolated": pos.get('marginType', 'cross') == 'isolated'
                }
                for pos in positions if float(pos.get('positionAmt', 0)) != 0
            ]
            logger.info(f"Fetched positions: {self.positions}")
            return self.positions
        except Exception as e:
            logger.error(f"Error fetching positions: {str(e)}")
            self.positions = []
            return []

    async def fetch_position_mode(self):
        try:
            # Koristi CCXT request za PAPI endpoint /papi/v1/um/positionSide/dual
            mode = await self.exchange.request('papi/v1/um/positionSide/dual', 'GET', params={'recvWindow': 5000})
            self.position_mode = "Hedge" if mode.get('dualSidePosition', False) else "One-way"
            logger.info(f"Position mode: {self.position_mode}")
            return {"mode": self.position_mode}
        except Exception as e:
            logger.error(f"Error fetching position mode: {str(e)}")
            self.position_mode = "One-way"
            logger.warning(f"Using fallback position mode: {self.position_mode}")
            return {"mode": self.position_mode}

    # return {"mode": self.position_mode}

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

    # FIXME Ograničenja: Leverage zavisi od para i pravila Binance-a. Proveri leverage polje u /fapi/v1/exchangeInfo
    #  ili /dapi/v1/exchangeInfo za maksimalni dozvoljeni leverage (npr. za BTCUSD_PERP, proveri maintMarginPercent i requiredMarginPercent).

    async def set_leverage(self, symbol, leverage):
        try:
            response = await self.exchange.set_leverage(leverage, symbol)
            logger.info(f"Leverage set to {leverage}x for {symbol}")
        except Exception as e:
            logger.error(f"Failed to set leverage for {symbol}: {e}")



            # FIXME symbol_info = exchange.fetch_markets()  # Molim te dodaj ovo gde je potrebno
            # for symbol in symbol_info:
            #     lot_size = next(filter(lambda x: x['type'] == 'LOT_SIZE', symbol['filters']))
            #     min_qty, max_qty, step_size = lot_size['minQty'], lot_size['maxQty'], lot_size['stepSize']
            #     # Postavi amount u skladu sa stepSize

            # TODO Ispravno rukovanje asinhronim pozivima (await za set_leverage i slične). Validaciju amount-a prema LOT_SIZE i contractSize. ZA LEVERAGE, koliki je tvoj max amount u odnou na leverage




    def set_manual_amount(self, amount: float):
        self.manual_amount = amount
        log_action(f"Manual amount set to: {amount} USDT")

    def smart_allocation(self, score):
        if self.manual_amount > 0:
            return self.manual_amount / float(get_config("balance", "99"))
        if score > 0.9:
            return 0.5
        elif score > 0.8:
            return 0.3
        elif score > 0.7:
            return 0.2
        else:
            return 0.1

    def log_candidate(self, symbol, price, score):
        try:
            with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
                cursor = conn.cursor()
                now = time.strftime("%Y-%m-%d %H:%M:%S")
                cursor.execute("INSERT INTO candidates (timestamp, symbol, price, score) VALUES (?, ?, ?, ?)",
                              (now, symbol, price, score))
                conn.commit()
                log_action(f"Logged candidate: {symbol} | Price: {price:.4f} | Score: {score:.2f}")
            self.export_candidates_to_json()
        except Exception as e:
            log_action(f"Error in log_candidate: {str(e)}")

    def export_candidates_to_json(self):
        try:
            log_action("Exporting candidates to JSON...")
            with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT timestamp, symbol, price, score FROM candidates ORDER BY id DESC LIMIT 10")
                candidates = [{"time": t, "symbol": s, "price": p, "score": sc} for t, s, p, sc in cursor.fetchall()]
                json_path = Path(DB_PATH).parent / "candidates.json"
                log_action(f"Writing candidates to {json_path}")
                with open(json_path, "w") as f:
                    json.dump(candidates, f, indent=2)
                log_action("Candidates exported to JSON successfully.")
        except Exception as e:
            log_action(f"Error exporting candidates to JSON: {e}")


    async def learn_from_history(self):

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

            log_action(f"Error analyzing history: {e}")

    async def get_candles(self, symbol, timeframe='15m', limit=100):
        ohlcv = await self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return df

    def calc_smma(self, series, length):
        smma = [series.iloc[0]]
        for i in range(1, len(series)):
            smma.append((smma[-1] * (length - 1) + series.iloc[i]) / length)
        return pd.Series(smma, index=series.index)

    def calc_wma(self, series, length):
        weights = range(1, length + 1)
        return series.rolling(length).apply(
            lambda prices: sum(weights[i] * prices[i] for i in range(length)) / sum(weights), raw=True)

    def confirm_smma_wma_crossover(self, df):
        if len(df) < 144: return False
        smma = self.calc_smma(df['close'], 5)
        wma = self.calc_wma(df['close'], 144)
        return smma.iloc[-2] < wma.iloc[-2] and smma.iloc[-1] > wma.iloc[-1]

    def fib_zone_check(self, df):
        if len(df) < 50: return False
        high = df['high'].rolling(50).max()
        low = df['low'].rolling(50).min()
        fib_range = high - low
        fib_382 = high - fib_range * 0.382
        fib_618 = high - fib_range * 0.618
        latest_price = df['close'].iloc[-1]
        return fib_618.iloc[-1] <= latest_price <= fib_382.iloc[-1]

    def is_near_round(self, price):
        for level in ROUND_LEVELS:
            if abs(price % level - level) < 0.01 * level or price % level < 0.01 * level:
                return True
        return False

    def ai_score(self, price, volume, avg_volume, crossover, in_fib_zone):
        score = 0
        if self.is_near_round(price): score += 1
        if volume > avg_volume * VOLUME_SPIKE_THRESHOLD: score += 1
        if crossover: score += 1.5
        if in_fib_zone: score += 0.5
        return min(score / 4.0, 1.0)

    async def _monitor_trade(self, symbol, entry_price):
        log_action(f"Monitoring trade for {symbol} at entry {entry_price:.4f}")
        tp = entry_price * (1 + TRAILING_TP_OFFSET)
        sl = entry_price * (1 - STOP_LOSS_PERCENT)
        highest_price = entry_price
        end_time = datetime.now() + timedelta(seconds=TRADE_DURATION_LIMIT)
        while self.running and datetime.now() < end_time:
            try:
                ticker = await self.exchange.fetch_ticker(symbol)
                price = ticker['last']
                if price > highest_price:
                    highest_price = price
                    tp = highest_price * (1 - TRAILING_TP_STEP)
                if price >= tp:
                    log_action(f"TP hit for {symbol} at {price:.4f}")
                    current_balance = float(get_config("balance", "0"))
                    current_score = int(get_config("score", "0"))
                    profit = (price - entry_price) * self.leverage
                    set_config("balance", str(current_balance + profit))
                    set_config("score", str(current_score + 1))
                    log_trade(symbol, price, "TP")
                    await self._execute_sell_order(symbol, 'ALL')
                    return "TP"
                if price <= sl:
                    log_action(f"SL hit for {symbol} at {price:.4f}")
                    current_balance = float(get_config("balance", "0"))
                    current_score = int(get_config("score", "0"))
                    loss = (entry_price - price) * self.leverage
                    set_config("balance", str(current_balance - loss))
                    set_config("score", str(current_score - 1))
                    log_trade(symbol, price, "SL")
                    await self._execute_sell_order(symbol, 'ALL')
                    return "SL"
                await asyncio.sleep(2)
            except Exception as e:
                log_action(f"Error monitoring trade for {symbol}: {e}")
                await asyncio.sleep(5)
        if self.running:
            log_action(f"Trade for {symbol} timed out.")
            current_balance = float(get_config("balance", "0"))
            current_score = int(get_config("score", "0"))
            try:
                ticker = await self.exchange.fetch_ticker(symbol)
                current_price = ticker['last']
                if current_price > entry_price:
                    profit = (current_price - entry_price) * self.leverage
                    set_config("balance", str(current_balance + profit))
                    set_config("score", str(current_score + 0.5))
                    log_trade(symbol, current_price, "TIMEOUT_PROFIT")
                else:
                    loss = (entry_price - current_price) * self.leverage
                    set_config("balance", str(current_balance - loss))
                    set_config("score", str(current_score - 0.5))
                    log_trade(symbol, current_price, "TIMEOUT_LOSS")
                await self._execute_sell_order(symbol, 'ALL')
                return "TIMEOUT"
            except Exception as e:
                log_action(f"Error closing timed out trade for {symbol}: {e}")
                return "ERROR_TIMEOUT"


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
            tp_order = await self.exchange.create_limit_sell_order(symbol, amount, tp_price,
                                                                   params={"stopPrice": tp_price, "type": "TAKE_PROFIT"})
            logger.info(f"Placed TP order for {symbol}: {amount} @ {tp_price} USDT | Order ID: {tp_order['id']}")

            sl_price = price * 0.99
            sl_order = await self.exchange.create_stop_limit_order(symbol, 'sell', amount, sl_price, sl_price)
            logger.info(f"Placed SL order for {symbol}: {amount} @ {sl_price} USDT | Order ID: {sl_order['id']}")

            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("INSERT INTO trades (symbol, price, outcome) VALUES (?, ?, ?)", (symbol, price, "OPEN"))
                await db.commit()

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

            log_action(f"Error opening long position for {symbol}: {e}")
            return None, None

    async def _main_bot_loop(self):
        log_action("[BOT] Starting main bot loop...")
        while self.running:
            log_action("Bot loop iteration running...")
            try:
                log_action("Initiating pair scan...")
                targets = await self._scan_pairs()
                log_action(f"Found {len(targets)} high-score targets: {[t[0] for t in targets]}")
                if targets:
                    symbol, price, volume, score = targets[0]
                    log_action(f"[BOT] Opening position on {symbol} with score {score:.2f}")
                    order, entry_price = await self._open_long(symbol, score)
                    if order:
                        log_action(f"Position opened for {symbol} at {entry_price}")
                        trade_outcome = await self._monitor_trade(symbol, entry_price)
                        log_action(f"Trade for {symbol} finished with outcome: {trade_outcome}")
                        with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
                            cursor = conn.cursor()
                            now = time.strftime("%Y-%m-%d %H:%M:%S")
                            cursor.execute("INSERT INTO trades (symbol, price, timestamp, outcome) VALUES (?, ?, ?, ?)",
                                           (symbol, entry_price, now, trade_outcome))
                            conn.commit()
                    else:
                        log_action(f"Could not open position for {symbol}.")
                else:
                    log_action("No high-score targets found in this scan.")
                await self.learn_from_history()
            except Exception as ex:
                log_action(f"Main bot loop error: {str(ex)}")
            await asyncio.sleep(15)

    def _send_telegram_message(self, message):
        token = os.getenv('TELEGRAM_BOT_TOKEN')
        chat_id = os.getenv('TELEGRAM_CHAT_ID')
        if not token or not chat_id:
            log_action("Missing Telegram token or chat_id in .env")
            return {"status": "❌ Missing token or chat_id in .env"}
        url = f"https://api.telegram.org/bot{token}/sendMessage
        data = {"chat_id": chat_id, "text": message}

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
async def log_candidate(self, symbol: str, price: float, score: float):
    try:
        timestamp = int(time.time())
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO candidates (symbol, price, score, timestamp) VALUES (?, ?, ?, ?)",
                             (symbol, price, score, timestamp))
            await db.commit()

            cursor = await db.execute(
                "SELECT symbol, price, score, timestamp FROM candidates ORDER BY score DESC, id DESC")
            candidates = [{"symbol": s, "price": p, "score": sc, "time": t} for s, p, sc, t in await cursor.fetchall()]

        # Asinhrono upisivanje u candidates.json
        async with aiofiles.open("user_data/candidates.json", "w") as f:
            await f.write(json.dumps(candidates, indent=4))
        logger.info(f"Updated candidates.json with {len(candidates)} candidates")

        logger.info(f"Logged candidate: {symbol} | Price: {price:.4f} | Score: {score:.2f}")
    except Exception as e:
        logger.error(f"Error logging candidate for {symbol}: {str(e)}")

    def _send_daily_report(self):
        msg = f"📊 ChovusBot Report:\nWallet = {float(get_config('balance', '0')):.2f} USDT, Score = {int(get_config('score', '0'))}"
        self._send_telegram_message(msg)
        log_action(f"Daily report sent at {datetime.now().strftime('%H:%M')}")

        # FIXME ovo je samo uputsvo, NIJE NEOPHODNO!!! COIN-M Futures
        # API: GET /dapi/v1/exchangeInfo (primjer iz logova za BTCUSD_PERP).
        #
        # Primjer podataka:
        # Simbol: BTCUSD_PERP
        #
        # contractSize: 100
        #
        # minQty: 1, maxQty: 1000000, stepSize: 1 (iz LOT_SIZE filtera)
        #
        # pricePrecision: 1, tickSize: 0.1
        #
        # Filteri: Slično kao USDⓈ-M, koristi LOT_SIZE za amount i PRICE_FILTER za cenu.
        #
        # Akcija: Skeniraj sve simbole iz /dapi/v1/exchangeInfo. Postavi amount prema contractSize i LOT_SIZE (npr. za BTCUSD_PERP, amount mora biti celobrojni umnožak od 1).

