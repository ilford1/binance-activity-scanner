import copy
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
import pytest
from binance_client import candle_metrics, INTERVALS, KLINE_LIMIT, RateLimiter, DataError, BinanceClient, oi_hour_change
from tests.fixtures import Market


def test_aligned_closed_baseline_excludes_measured_bar():
    m=Market(); end=int(m.now*1000)//300000*300000
    candles=m.fetch('/fapi/v1/klines',dict(symbol="ETHUSDT",interval="5m",endTime=end-1,limit=KLINE_LIMIT))
    candles[-1][7]="1000000"; candles[-1][10]="600000"
    r=candle_metrics(candles,"5m",end)
    assert r["relative_volume"]==10
    assert r["relative_trades"]==1.25
    future=copy.deepcopy(candles[-1]); future[0]=end; future[6]=end+299999
    assert candle_metrics(candles+[future],"5m",end)==r
    assert candle_metrics(candles[:-1],"5m",end)["status"]=="Incomplete candles"
    bad=copy.deepcopy(candles); bad[2][0]+=300000
    assert candle_metrics(bad,"5m",end)["status"]=="Incomplete candles"
    for c in candles[:-1]: c[7]="0"; c[10]="0"
    assert candle_metrics(candles,"5m",end)["status"]=="Zero activity baseline"


def test_cache_filter_force_and_boundary_requests():
    m=Market()
    first=m.client.scan()
    assert len(first.frame)==m.n
    assert m.calls['/fapi/v1/klines']==m.n
    calls=m.calls.copy()
    second=m.client.scan()
    pd.testing.assert_frame_equal(first.frame,second.frame)
    assert m.calls==calls
    m.client.scan(min_volume=15e6)
    assert m.calls==calls
    m.client.scan(force=True)
    assert m.calls['/fapi/v1/klines']==m.n
    assert m.calls['/fapi/v1/ticker/24hr']==2
    assert '/fapi/v1/openInterest' not in m.calls and '/fapi/v1/fundingRate' not in m.calls
    m.now+=300
    m.client.scan()
    assert m.calls['/fapi/v1/klines']==2*m.n


def test_failure_retains_real_values_but_excludes_ranking_and_recovers():
    m=Market(); good=m.client.scan(); m.now+=65
    m.fail.add('/fapi/v1/ticker/24hr')
    bad=m.client.scan()
    assert bad.frame.activity.isna().all()
    assert not bad.frame.price_fresh.any()
    assert set(bad.frame.price)==set(good.frame.price)
    assert bad.sources['tickers'].fetched_at==good.sources['tickers'].fetched_at
    m.fail.clear(); m.now+=6
    assert m.client.scan().frame.activity.notna().all()


def test_universe_metadata_and_empty_filter():
    m=Market(); original=m.fetch
    def modified(endpoint,params):
        data=original(endpoint,params)
        if endpoint.endswith('/exchangeInfo'):
            data['symbols'][0]['status']='SETTLING'
            data['symbols'][1]['contractType']='CURRENT_QUARTER'
        return data
    m.client.transport=modified
    df=m.client.scan().frame
    assert not set(df.symbol)&{'BTCUSDT','ETHUSDT'}
    m2=Market()
    assert m2.client.scan(min_volume=1e15).empty_reason=='No trading pairs meet the volume filter'
    assert m2.calls['/fapi/v1/klines']==0


def test_details_lazy_cached_and_one_hour_oi():
    m=Market(); m.client.scan()
    assert m.calls['/fapi/v1/fundingRate']==0
    detail=m.client.details('BTCUSDT'); calls=m.calls.copy()
    m.client.details('BTCUSDT')
    assert m.calls==calls
    result=oi_hour_change(detail['oi'].data)
    assert result[0]==pytest.approx(1.2)
    assert result[2]-result[1]==3600
    assert oi_hour_change(detail['oi'].data[1:])==(None,None,None)


class Clock:
    def __init__(self): self.now=100.
    def __call__(self): return self.now
    def sleep(self,s): self.now+=s


def test_weight_and_separate_budget_bounded():
    clock=Clock(); limiter=RateLimiter(clock,clock.sleep)
    limiter.windows={'weight':(60,3),'oi':(300,2),'funding':(300,2)}
    limiter.acquire(2)
    with pytest.raises(DataError,match='budget'): limiter.acquire(2,max_wait=1)
    limiter.acquire(0,'oi'); limiter.acquire(0,'oi')
    with pytest.raises(DataError,match='budget'): limiter.acquire(0,'oi',max_wait=1)
    limiter.cooldown(120)
    with pytest.raises(DataError,match='cooldown'): limiter.acquire(0)
    clock.now+=122
    limiter.acquire(1)


@pytest.mark.parametrize('code',[418,429])
def test_http_cooldown_stops_other_workers(code):
    limiter=RateLimiter()
    class Response:
        status_code=code
        headers={'Retry-After':'30'}
    class Session:
        def __init__(self): self.calls=0
        def get(self,*a,**k): self.calls+=1; return Response()
    session=Session(); client=BinanceClient(limiter=limiter)
    client._session=lambda:session
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses=list(pool.map(lambda i:client.source('/test',{'i':i}),range(8)))
    assert session.calls==1
    assert all(r.stale for r in responses)


def test_concurrent_same_key_coalesced():
    m=Market()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _:m.client.source('/fapi/v1/time'),range(8)))
    assert m.calls['/fapi/v1/time']==1


def test_new_boundary_failure_retains_last_candles_without_scoring():
    m=Market(); first=m.client.scan(); m.now+=300
    m.fail.add('/fapi/v1/klines')
    failed=m.client.scan()
    assert failed.frame.activity.isna().all()
    assert set(failed.frame.change)==set(first.frame.change)
    assert set(failed.frame.bar_end)==set(first.frame.bar_end)
    assert failed.frame.status.str.contains('Stale candles').all()
    m.fail.clear(); m.now+=6
    recovered=m.client.scan()
    assert recovered.frame.activity.notna().all()
    assert set(recovered.frame.bar_end)!=(set(first.frame.bar_end))


def test_invalid_quotes_do_not_create_activity():
    m=Market(); original=m.fetch
    def malformed(endpoint,params):
        data=original(endpoint,params)
        if endpoint.endswith('/bookTicker'):
            data[0]['bidPrice']='NaN'
            data[1]['time']=int((m.now-500)*1000)
            data[2]['askPrice']='-1'
        return data
    m.client.transport=malformed
    r=m.client.scan().frame.set_index('symbol')
    assert r.loc[['BTCUSDT','ETHUSDT','TINYUSDT'],'activity'].isna().all()
