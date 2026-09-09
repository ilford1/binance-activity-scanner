"""Pure settings, filtering and formatting helpers shared with UI tests."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import math

from binance_client import DEFAULT_MIN_VOLUME, INTERVALS, finite

REFRESH = {"Manual": None, "60s": 60, "5 min": 300, "30 min": 1800}
TABLE_COLUMNS = ["symbol", "price", "activity", "change", "relative_volume",
                 "relative_trades", "buy_pct", "spread_pct", "volume_24h"]


def settings_from_url(params) -> dict:
    result = dict(timeframe="5m", min_volume=DEFAULT_MIN_VOLUME, search="",
                  refresh_rate="Manual", active_only=False)
    if params.get("tf") in INTERVALS:
        result["timeframe"] = params["tf"]
    if params.get("rf") in REFRESH:
        result["refresh_rate"] = params["rf"]
    volume = finite(params.get("minv"))
    if volume is not None and 0 <= volume <= 1e15:
        result["min_volume"] = int(volume)
    result["search"] = str(params.get("q", "")).strip().upper()[:100]
    result["active_only"] = params.get("active") == "1"
    return result


def filter_table(df, query: str = "", active_only: bool = False):
    if df.empty:
        return df.copy()
    mask = df.symbol.str.contains(query.strip(), case=False, regex=False, na=False)
    if active_only:
        mask &= df.active
    return df.loc[mask].copy()


def price_decimals(tick_size) -> int:
    try:
        tick = Decimal(str(tick_size))
        if not tick.is_finite() or tick <= 0:
            return 8
        return min(16, max(0, -tick.normalize().as_tuple().exponent))
    except (InvalidOperation, ValueError):
        return 8


def format_price(value, tick_size) -> str:
    number = finite(value)
    return "—" if number is None else f"${number:,.{price_decimals(tick_size)}f}"


def utc_time(value) -> str:
    number = finite(value)
    if number is None or number <= 0:
        return "Unavailable"
    return datetime.fromtimestamp(number, timezone.utc).strftime("%d %b %H:%M:%S UTC")
