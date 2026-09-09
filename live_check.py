"""Bounded live verification for Activity, Strong Movers, or Market Relative."""
import sys

import numpy as np

from binance_client import BinanceClient, finite
from market_relative import compare_market


def main(mode="activity"):
    client = BinanceClient()
    tickers = client.source('/fapi/v1/ticker/24hr', weight=40,
                            validator=lambda d:isinstance(d,list) and bool(d))
    if tickers.stale or not tickers.data:
        print('LIVE_UNAVAILABLE:', tickers.error)
        return 2
    universe = client.source('/fapi/v1/exchangeInfo', ttl=3600,
                             validator=lambda d:isinstance(d,dict) and bool(d.get('symbols')))
    if universe.stale or not universe.data:
        print('LIVE_UNAVAILABLE:', universe.error)
        return 2
    contracts = {row.get('symbol') for row in universe.data['symbols']
                 if isinstance(row,dict) and row.get('status') == 'TRADING'
                 and row.get('contractType') == 'PERPETUAL'
                 and row.get('quoteAsset') == 'USDT'
                 and row.get('marginAsset') == 'USDT'}
    # Restrict the verification scan via volume. Normal app scans have no row cap.
    volume = sorted([finite(t.get('quoteVolume'),0) for t in tickers.data
                     if t.get('symbol') in contracts and
                     (mode != "relative" or t.get('symbol') != 'BTCUSDT')], reverse=True)
    wanted = 30 if mode == "relative" else 3
    if len(volume) < wanted:
        print('LIVE_UNAVAILABLE: insufficient tickers')
        return 2
    scan = (client.scan_movers(min_volume=volume[wanted - 1])
            if mode in ("movers", "relative")
            else client.scan(min_volume=volume[wanted - 1]))
    if mode == "movers" and not scan.frame.empty:
        from movers import qualify_movers
        scan.frame = qualify_movers(scan.frame)
    print('Requests:',client.requests_made)
    print('Errors:',scan.errors)
    if scan.frame.empty:
        print('LIVE_UNAVAILABLE:',scan.empty_reason)
        return 2
    if mode == "relative":
        result = compare_market(scan.frame, "4h", scan.boundary,
                                client.clock(), volume[wanted - 1])
        if result.warnings:
            print('LIVE_PARTIAL:', '; '.join(result.warnings))
            return 2
        # Independently verify the displayed headline and row gaps from raw rows.
        raw = scan.frame.copy()
        eligible = (
            raw.symbol.ne("BTCUSDT")
            & raw.volume_24h.ge(volume[wanted - 1])
            & raw.status.eq("Ready")
            & raw.spread_pct.between(0, .10)
            & raw["4h_return_valid"].eq(True)
            & raw.bar_end.eq(scan.boundary)
            & (client.clock() - raw.observed_at).between(-1, 120)
            & (client.clock() - raw.quote_at).between(-1, 120)
        )
        raw_median = float(raw.loc[eligible, "4h_change"].median())
        btc = float(scan.frame.loc[scan.frame.symbol.eq("BTCUSDT"), "4h_change"].iloc[0])
        if not np.isclose(raw_median, result.alt_median):
            print('LIVE_FAILED: alt median mismatch')
            return 1
        if not np.allclose(result.frame.vs_btc, result.frame.change - btc,
                           equal_nan=True):
            print('LIVE_FAILED: BTC gap mismatch')
            return 1
        scan.frame = result.frame
        print(f'BTC return: {btc:+.4f}%  Alt median: {result.alt_median:+.4f}%  Coverage: {result.coverage:.0%}')
        columns = ['symbol','price','change','vs_btc','vs_alts','alt_percentile','spread_pct','status']
    else:
        columns=['symbol','price','change','relative_volume','spread_pct','status']
    print(scan.frame[columns].to_string(index=False))
    if (scan.frame.change.notna().sum() if mode != "activity"
            else scan.frame.activity.notna().sum()) < 2:
        print('LIVE_PARTIAL: fewer than two ranked observations')
        return 2
    print('LIVE_OK')
    return 0


if __name__ == '__main__':
    selected = "relative" if "--relative" in sys.argv else ("movers" if "--movers" in sys.argv else "activity")
    raise SystemExit(main(selected))
