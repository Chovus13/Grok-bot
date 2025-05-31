# ChovusSmartBot_v9.py
import ccxt.async_support as ccxt
import time
import os
import json
from datetime import datetime, timedelta
import pandas as pd
import threading
import schedule
import requests
from dotenv import load_dotenv
import asyncio
import sqlite3
from pathlib import Path
import logging
import logging.handlers
from .scan_pairs_safe_amount import _scan_pairs, calculate_amount, get_available_balance
from typing import Any, Dict


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

def init_db():
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, price REAL, timestamp TEXT, outcome TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS score_log (timestamp TEXT, score INTEGER)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS bot_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, message TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS candidates (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, symbol TEXT, price REAL, score REAL)''')
        conn.commit()

# _config_data = {} # Primer skladišta za konfiguraciju
# def get_config(key: str, default: Any = None) -> Any:
#     """Jednostavan primer get_config funkcije."""
#     global _config_data
#     # U praksi, ovde bi čitao iz fajla, env varijabli, itd.
#     if not _config_data: # Učitaj samo jednom
#         logging.info("Učitavanje dummy konfiguracije...")
#         _config_data = {
#             "available_pairs": "BTC/USDT,ETH/USDT,SOL/USDT",
#             "leverage_BTC_USDT": 10,
#             "balance": "1000.0",
#             "api_key": "TVOJ_API_KEY",
#             "secret_key": "TVOJ_SECRET_KEY"
#         }
#    return _config_data.get(key, default)

def get_config(key: str, default=None):
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM config WHERE key=?", (key,))
        result = cursor.fetchone()
        return result[0] if result else default

def set_config(key: str, value: str):
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        cursor = conn.cursor()
        cursor.execute("REPLACE INTO config (key, value) VALUES (?, ?)", (key, value))
        conn.commit()

def get_all_config():
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM config")
        return {k: v for k, v in cursor.fetchall()}

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


def log_action(message):
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        cursor = conn.cursor()
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("INSERT INTO bot_logs (timestamp, message) VALUES (?, ?)", (now, message))
        conn.commit()

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
        self._scan_pairs = _scan_pairs.__get__(self, ChovusSmartBot)  # Bind metoda na self
        self.calculate_amount = calculate_amount.__get__(self, ChovusSmartBot)
        self.get_available_balance = get_available_balance.__get__(self, ChovusSmartBot)
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

    def get_bot_status(self):
        return "Running" if self.running else "Stopped"

    def set_bot_strategy(self, strategy_name: str):
        self.current_strategy = strategy_name
        log_action(f"Strategy set to: {strategy_name}")
        return strategy_name

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
            with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT symbol, price, outcome FROM trades")
                data = cursor.fetchall()
                df = pd.DataFrame(data, columns=['symbol', 'price', 'outcome'])
                if df.empty:
                    log_action("No trade data to learn from.")
                    return
                summary = df.groupby("symbol")["outcome"].value_counts().unstack().fillna(0)
                log_action(f"Performance summary: {summary.to_dict()}")
        except Exception as e:
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

    async def _execute_buy_order(self, symbol, quantity):
        try:
            order = await self.exchange.create_market_buy_order(symbol, quantity)
            log_action(f"Executed BUY order for {symbol}: {order}")
            return order
        except Exception as e:
            log_action(f"Error executing buy order for {symbol}: {e}")
            return None

    async def _execute_sell_order(self, symbol, quantity):
        try:
            order = await self.exchange.create_market_sell_order(symbol, quantity)
            log_action(f"Executed SELL order for {symbol}: {order}")
            return order
        except Exception as e:
            log_action(f"Error executing sell order for {symbol}: {e}")
            return None

    async def _open_long(self, symbol, score):
        try:
            market = self.exchange.market(symbol)
            ticker = await self.exchange.fetch_ticker(symbol)
            price = ticker['ask']
            balance = await self.exchange.fetch_balance({"type": "future"})
            usdt_balance = balance['total']['USDT'] * 0.99
            alloc = self.smart_allocation(score)
            min_qty = market['limits']['amount']['min']
            max_qty = market['limits']['amount']['max']
            quantity = (usdt_balance * alloc * self.leverage) / price
            quantity = self.exchange.amount_to_precision(symbol, quantity)
            if quantity < min_qty:
                log_action(f"Calculated quantity {quantity} is less than min_qty {min_qty}. Setting to min_qty.")
                quantity = min_qty
            if quantity > max_qty:
                log_action(f"Calculated quantity {quantity} is more than max_qty {max_qty}. Setting to max_qty.")
                quantity = max_qty
            order = await self._execute_buy_order(symbol, float(quantity))
            return order, price
        except Exception as e:
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
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = {"chat_id": chat_id, "text": message}
        try:
            r = requests.post(url, data=data)
            log_action(f"Telegram status: {r.status_code}, Response: {r.text}")
            return {"status": "✅ Sent!" if r.status_code == 200 else f"❌ Error: {r.text}"}
        except Exception as e:
            log_action(f"Telegram send error: {e}")
            return {"status": f"❌ Exception: {e}"}

    def _send_report_loop(self):
        schedule.every().day.at(get_config("report_time", "09:00")).do(self._send_daily_report)
        while self.running:
            schedule.run_pending()
            time.sleep(1)

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
