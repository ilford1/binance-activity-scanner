"""Public Binance data with shared budgets, bounded work and aligned closed bars."""
from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import requests

from scoring import BASELINE_BARS, score_activity

BASE_URL = "https://fapi.binance.com"
INTERVALS = {"1m": 60, "5m": 300, "30m": 1800, "1h": 3600,
             "4h": 14400, "8h": 28800}
DEFAULT_MIN_VOLUME = 10_000_000
KLINE_LIMIT = BASELINE_BARS + 1
LIVE_TTL = 60
MAX_QUOTE_AGE = 120


class DataError(RuntimeError):
    """A bounded, user-visible data failure."""


class RateLimiter:
    """Rolling budgets and one shared cooldown; no worker sleeps through a ban."""
    def __init__(self, clock=time.monotonic, sleeper=time.sleep):
        self.clock, self.sleep = clock, sleeper
        self.lock = threading.Lock()
        self.windows = {"weight": (60, 2000), "oi": (300, 900), "funding": (300, 450)}
        self.used = {key: deque() for key in self.windows}
        self.until = self.next_start = 0.0

    def cooldown(self, seconds: float) -> None:
        with self.lock:
            self.until = max(self.until, self.clock() + max(1, seconds) + 1)

    def retry_seconds(self) -> float:
        with self.lock:
            return max(0, self.until - self.clock())

    def check(self) -> None:
        remaining = self.retry_seconds()
        if remaining > 0:
            raise DataError(f"Binance cooldown: retry in {math.ceil(remaining)}s")

    def acquire(self, weight: int = 1, group: str | None = None, max_wait: float = 30) -> None:
        deadline = self.clock() + max_wait
        costs = {"weight": weight}
        if group:
            costs[group] = 1
        while True:
            with self.lock:
                now = self.clock()
                if self.until > now:
                    raise DataError(f"Binance cooldown: retry in {math.ceil(self.until-now)}s")
                delay = max(0, self.next_start - now)
                for key, cost in costs.items():
                    window, budget = self.windows[key]
                    queue = self.used[key]
                    while queue and queue[0][0] <= now - window:
                        queue.popleft()
                    if sum(c for _, c in queue) + cost > budget:
                        delay = max(delay, queue[0][0] + window - now + .01)
                if now + delay > deadline:
                    raise DataError("Request budget busy; retry on the next refresh")
                if delay <= 0:
                    for key, cost in costs.items():
                        self.used[key].append((now, cost))
                    self.next_start = now + .03
                    return
            self.sleep(min(delay, .25))


LIMITER = RateLimiter()


@dataclass
class Source:
    data: Any = None
    fetched_at: float = 0.0
    stale: bool = False
    error: str = ""


@dataclass
class Scan:
    frame: pd.DataFrame
    boundary: int | None
    sources: dict[str, Source]
    errors: list[str]
    empty_reason: str = ""


def finite(value: Any, default: Any = None):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError, OverflowError):
        return default


def candle_metrics(candles: list, interval: str, boundary: int) -> dict:
    """Use one aligned candle plus 60 preceding candles; reject gaps and lookahead."""
    result = {"change": np.nan, "relative_volume": np.nan,
              "relative_trades": np.nan, "buy_pct": np.nan,
              "candle_volume": np.nan, "candle_trades": np.nan,
              "bar_end": boundary - 1, "status": "Incomplete candles"}
    step = INTERVALS[interval] * 1000
    try:
        closed = sorted((c for c in candles if int(c[6]) < boundary), key=lambda c: int(c[0]))
        window = closed[-KLINE_LIMIT:]
        expected = list(range(boundary - KLINE_LIMIT * step, boundary, step))
        if len(window) != KLINE_LIMIT or [int(c[0]) for c in window] != expected:
            return result
        if any(int(c[6]) != int(c[0]) + step - 1 for c in window):
            return result
        values = np.array([[float(c[j]) for j in (1, 2, 3, 4, 7, 8, 10)] for c in window])
        if not np.isfinite(values).all() or (values[:, :4] <= 0).any() or (values[:, 4:] < 0).any():
            return result
        if (values[:, 6] > values[:, 4]).any():
            return result
        if ((values[:, 1] < values[:, [0, 3]].max(axis=1)).any()
                or (values[:, 2] > values[:, [0, 3]].min(axis=1)).any()):
            return result
        vol_base, trades_base = np.median(values[:-1, 4:6], axis=0)
        o, high, low, close, vol, trades, buy = values[-1]
        result.update(change=(close / o - 1) * 100, candle_volume=vol, candle_trades=trades)
        if vol_base <= 0 or trades_base <= 0 or vol <= 0:
            result["status"] = "Zero activity baseline" if min(vol_base, trades_base) <= 0 else "No candle volume"
            return result
        result.update(relative_volume=vol / vol_base, relative_trades=trades / trades_base,
                      buy_pct=100 * buy / vol, status="Ready")
        return result
    except (TypeError, ValueError, IndexError, KeyError, OverflowError):
        return result


class BinanceClient:
    def __init__(self, transport=None, limiter=None, clock=time.time):
        self.clock, self.transport = clock, transport
        self.limiter = limiter or LIMITER
        self.local = threading.local()
        self.lock = threading.RLock()
        self.cache: OrderedDict[tuple, tuple[float, Source]] = OrderedDict()
        self.key_locks = [threading.Lock() for _ in range(128)]
        self.requests_made = 0
        self.last_candles: OrderedDict[tuple, tuple[int, Source]] = OrderedDict()

    def _session(self):
        if not hasattr(self.local, "session"):
            self.local.session = requests.Session()
            self.local.session.headers["User-Agent"] = "Activity-Scanner/2.0"
        return self.local.session

    def _request(self, endpoint: str, params: dict, weight: int, group: str | None):
        for attempt in range(2):
            self.limiter.acquire(weight, group)
            self.limiter.check()  # also recheck after a worker has waited for a slot
            try:
                with self.lock:
                    self.requests_made += 1
                if self.transport is not None:
                    return self.transport(endpoint, params)
                response = self._session().get(BASE_URL + endpoint, params=params, timeout=(4, 10))
                if response.status_code in (418, 429):
                    wait = finite(response.headers.get("Retry-After"), 60)
                    self.limiter.cooldown(wait)
                    raise DataError(f"Binance HTTP {response.status_code}; retry in {math.ceil(wait)}s")
                used = finite(response.headers.get("X-MBX-USED-WEIGHT-1M"), 0)
                if used >= 2000:
                    self.limiter.cooldown(60)
                response.raise_for_status()
                data = response.json()
                if isinstance(data, dict) and finite(data.get("code"), 0) < 0:
                    raise DataError(str(data.get("msg", "API error"))[:160])
                return data
            except (requests.RequestException, ValueError) as exc:
                if attempt or (isinstance(exc, requests.HTTPError)
                               and exc.response is not None and exc.response.status_code < 500):
                    raise DataError(f"{endpoint}: {type(exc).__name__}") from exc
                time.sleep(.2)
        raise DataError("Request failed")

    def source(self, endpoint: str, params: dict | None = None, ttl: float = LIVE_TTL,
               weight: int = 1, group: str | None = None, force: bool = False,
               validator=None) -> Source:
        params = params or {}
        key = (endpoint, tuple(sorted(params.items())))
        with self.key_locks[hash(key) % len(self.key_locks)]:
            with self.lock:
                entry = self.cache.get(key)
                if entry and not force and entry[0] > self.clock():
                    self.cache.move_to_end(key)
                    return entry[1]
            try:
                data = self._request(endpoint, params, weight, group)
                if validator and not validator(data):
                    raise DataError(f"{endpoint}: incomplete response")
                value = Source(data, self.clock())
                expires = self.clock() + ttl
            except (DataError, TypeError, KeyError) as exc:
                old = entry[1] if entry else Source()
                value = Source(old.data, old.fetched_at, True, str(exc))
                expires = self.clock() + 5
            with self.lock:
                self.cache[key] = (expires, value)
                self.cache.move_to_end(key)
                while len(self.cache) > 4096:
                    self.cache.popitem(last=False)
            return value

    def scan_movers(self, min_volume: float = DEFAULT_MIN_VOLUME, force: bool = False) -> Scan:
        return self.scan(min_volume, "15m", force, _movers=True)

    def scan(self, min_volume: float = DEFAULT_MIN_VOLUME, interval: str = "5m", force: bool = False, _movers: bool = False) -> Scan:
        from movers import candle_windows
        intervals = {**INTERVALS, "15m": 900} if _movers else INTERVALS
        metrics = candle_windows if _movers else candle_metrics
        if interval not in intervals or finite(min_volume) is None or min_volume < 0:
            raise ValueError("Invalid scan settings")
        sources = {
            "clock": self.source("/fapi/v1/time", validator=lambda d: isinstance(d, dict) and finite(d.get("serverTime"), 0) > 0),
            "universe": self.source("/fapi/v1/exchangeInfo", ttl=3600,
                                     validator=lambda d: isinstance(d, dict) and bool(d.get("symbols"))),
            "tickers": self.source("/fapi/v1/ticker/24hr", weight=40, force=force,
                                    validator=lambda d: isinstance(d, list) and bool(d)),
            "quotes": self.source("/fapi/v1/ticker/bookTicker", weight=5, force=force,
                                   validator=lambda d: isinstance(d, list) and bool(d)),
        }
        errors = [f"{key}: {v.error}" for key, v in sources.items() if v.error]
        if any(sources[k].data is None for k in ("clock", "universe", "tickers")):
            return Scan(pd.DataFrame(), None, sources, errors, "Market data unavailable")
        server = sources["clock"]
        server_ms = int(server.data["serverTime"] + max(0, self.clock() - server.fetched_at) * 1000)
        step = intervals[interval] * 1000
        boundary = server_ms // step * step
        contracts = {d["symbol"]: d for d in sources["universe"].data["symbols"]
                     if isinstance(d, dict) and d.get("symbol") and d.get("status") == "TRADING" and d.get("contractType") == "PERPETUAL"
                     and d.get("quoteAsset") == "USDT" and d.get("marginAsset") == "USDT"}
        tickers = [d for d in sources["tickers"].data if isinstance(d, dict) and d.get("symbol") in contracts
                   and (finite(d.get("quoteVolume"), -1) >= min_volume or (_movers and d.get("symbol") == "BTCUSDT"))]
        tickers = list({d["symbol"]: d for d in tickers}.values())
        if not tickers:
            return Scan(pd.DataFrame(), boundary, sources, errors,
                        "No trading pairs meet the volume filter" if not errors else "No usable market data")
        quotes = {d.get("symbol"): d for d in (sources["quotes"].data or []) if isinstance(d, dict)}
        def build(ticker):
            sym = ticker["symbol"]
            tick = next((f.get("tickSize") for f in contracts[sym].get("filters", [])
                         if f.get("filterType") == "PRICE_FILTER"), "0.00000001")
            quote = quotes.get(sym, {})
            bid, ask = finite(quote.get("bidPrice")), finite(quote.get("askPrice"))
            spread = ((ask - bid) / ((ask + bid) / 2) * 100
                      if bid is not None and ask is not None and 0 < bid <= ask else np.nan)
            observed, quote_at = finite(ticker.get("closeTime")), finite(quote.get("time"))
            candles = self.source("/fapi/v1/klines", {"symbol": sym, "interval": interval,
                                  "endTime": boundary - 1, "limit": 1440 if _movers else KLINE_LIMIT},
                                  weight=10 if _movers else 1, ttl=86400, validator=lambda d: isinstance(d, list) and bool(d) and (not _movers or metrics(d, interval, boundary)["status"] == "Ready"))
            candle_boundary = boundary
            with self.lock:
                prior = self.last_candles.get((sym, interval))
                if candles.stale and candles.data is None and prior:
                    candle_boundary, previous = prior
                    candles = Source(previous.data, previous.fetched_at, True, candles.error)
                elif not candles.stale and metrics(candles.data or [], interval, boundary)["status"] == "Ready":
                    self.last_candles[(sym, interval)] = (boundary, candles)
                    self.last_candles.move_to_end((sym, interval))
                    if _movers:
                        # Retain only two large windows per symbol; old payloads otherwise accumulate for a day.
                        keys = [k for k in self.cache if k[0] == "/fapi/v1/klines"
                                and dict(k[1]).get("symbol") == sym and dict(k[1]).get("interval") == "15m"]
                        keys.sort(key=lambda k: dict(k[1]).get("endTime", 0), reverse=True)
                        for old_key in keys[2:]:
                            self.cache.pop(old_key, None)
                    while len(self.last_candles) > 2048:
                        self.last_candles.popitem(last=False)
            row = dict(symbol=sym, price=finite(ticker.get("lastPrice"), np.nan),
                       volume_24h=finite(ticker.get("quoteVolume"), np.nan), tick_size=tick,
                       spread_pct=spread, quote_at=quote_at / 1000 if quote_at is not None else np.nan,
                       observed_at=observed / 1000 if observed is not None else np.nan,
                       fetched_at=sources["tickers"].fetched_at, candle_fetched_at=candles.fetched_at,
                       **metrics(candles.data or [], interval, candle_boundary))
            stale = [name for name, src in sources.items() if src.stale]
            if candles.stale:
                stale.append("candles")
            if stale:
                row["status"] = "Stale " + ", ".join(stale)
            elif observed is None or not -1000 <= server_ms - observed <= MAX_QUOTE_AGE * 1000:
                row["status"] = "Stale price"
            elif quote_at is None or not -1000 <= server_ms - quote_at <= MAX_QUOTE_AGE * 1000:
                row["status"] = "Stale quote" if quote else "Missing quote"
            elif not math.isfinite(spread):
                row["status"] = "Invalid quote"
            row["price_fresh"] = (not sources["tickers"].stale and not server.stale
                                  and observed is not None and -1000 <= server_ms-observed <= MAX_QUOTE_AGE*1000
                                  and finite(row["price"], 0) > 0)
            return row
        with ThreadPoolExecutor(max_workers=12) as pool:
            rows = list(pool.map(build, tickers))
        return Scan(pd.DataFrame(rows) if _movers else score_activity(pd.DataFrame(rows)), boundary, sources, errors)

    def details(self, symbol: str) -> dict[str, Source]:
        """Fetch derivatives only for a selected pair with its details open."""
        if not isinstance(symbol, str) or not symbol.isalnum() or not symbol.endswith("USDT"):
            raise ValueError("Invalid symbol")
        return {
            "funding": self.source("/fapi/v1/premiumIndex", {"symbol": symbol}, ttl=300,
                                   validator=lambda d: isinstance(d, dict) and finite(d.get("lastFundingRate")) is not None),
            "settlements": self.source("/fapi/v1/fundingRate", {"symbol": symbol, "limit": 2},
                                       ttl=300, group="funding", validator=lambda d: isinstance(d, list)),
            "oi": self.source("/futures/data/openInterestHist", {"symbol": symbol, "period": "5m", "limit": 13},
                              ttl=300, weight=0, group="oi", validator=lambda d: isinstance(d, list)),
        }


def oi_hour_change(data: list) -> tuple[float | None, float | None, float | None]:
    """Base-quantity OI change only when observations span exactly one hour."""
    try:
        points = sorted(data, key=lambda d: int(d["timestamp"]))
        last = points[-1]
        end = int(last["timestamp"])
        first = next(d for d in points if int(d["timestamp"]) == end - 3600000)
        a, b = finite(first["sumOpenInterest"]), finite(last["sumOpenInterest"])
        if a is None or b is None or min(a, b) <= 0:
            return None, None, None
        return (b / a - 1) * 100, (end - 3600000) / 1000, end / 1000
    except (ValueError, KeyError, IndexError, TypeError, StopIteration):
        return None, None, None
