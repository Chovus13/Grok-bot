# scan_pairs_safe_amount.py
import asyncio
import logging
import ccxt.async_support as ccxt
from typing import List, Tuple, TYPE_CHECKING
from bot import get_config
if TYPE_CHECKING:
    from bot import ChovusSmartBot

async def _scan_pairs(self: 'ChovusSmartBot', limit: int = 10) -> List[Tuple[str, float, float, float, float]]:
    log_action = logging.getLogger(__name__).info
    log_action("Starting pair scanning for USDⓈ-M Futures...")

    try:
        log_action("Fetching exchange info...")
        exchange_info = await self.exchange.fetch_markets()
        markets = {m['symbol']: m for m in exchange_info if m['type'] == 'future' and m['quote'] == 'USDT'}

        available_pairs = get_config("available_pairs", "BTC/USDT,ETH/USDT,SOL/USDT")
        all_futures = available_pairs.split(",") if available_pairs else ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        all_futures = [p for p in all_futures if p in markets]
        log_action(f"Scanning {len(all_futures)} predefined pairs: {all_futures}...")

        if not all_futures:
            log_action("No valid USDⓈ-M pairs defined in config. Add pairs to scan.")
            return []

        log_action("Fetching tickers...")
        try:
            tickers = await self.exchange.fetch_tickers(all_futures)
            log_action(f"Fetched tickers for {len(tickers)} pairs: {list(tickers.keys())[:5]}...")
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
                tick_size = float(price_filter.get('tickSize', 0))

                amount = self.calculate_amount(symbol, price, min_qty, max_qty, step_size)
                if not amount:
                    log_action(f"Invalid amount for {symbol}, skipping.")
                    continue

                leverage = get_config(f"leverage_{symbol.replace('/', '_')}", 10)
                try:
                    await self.exchange.set_leverage(leverage, symbol=symbol)
                    log_action(f"Leverage set to {leverage}x for {symbol}")
                except Exception as e:
                    log_action(f"Failed to set leverage for {symbol}: {str(e)}")
                    continue

                log_action(f"Fetching candles for {symbol}...")
                df = await self.get_candles(symbol, timeframe='1h', limit=150)
                if len(df) < 150:
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


async def calculate_amount(self: 'ChovusSmartBot', symbol: str, price: float, min_qty: float, max_qty: float, step_size: float) -> float:
    try:
        balance = await self.get_available_balance()
        target_risk = balance * 0.1 / price
        amount = max(min_qty, min(max_qty, round(target_risk / step_size) * step_size))

        market = self.exchange.markets.get(symbol, {})
        precision = market.get('precision', {}).get('amount', 8)
        amount = round(amount, precision)

        if amount < min_qty or amount > max_qty:
            logging.error(f"Calculated amount {amount} for {symbol} is out of bounds [{min_qty}, {max_qty}]")
            return 0
        return amount
    except Exception as e:
        logging.error(f"Error calculating amount for {symbol}: {str(e)}")
        return 0


async def get_available_balance(self: 'ChovusSmartBot') -> float:
    """
    Dohvata raspoloživi USDT balans za USDⓈ-M Futures.
    """
    try:
        balance = await self.exchange.fetch_balance(params={"type": "future"})
        available = float(balance['USDT'].get('free', 0))  # Koristi 'free' za raspoloživi balans
        logging.info(f"Fetched available balance: {available} USDT")
        return available
    except Exception as e:
        logging.error(f"Error fetching balance: {str(e)}")
        fallback = float(get_config("balance", "0"))  # Fallback iz config-a
        logging.warning(f"Using fallback balance: {fallback} USDT")
        return fallback
        
