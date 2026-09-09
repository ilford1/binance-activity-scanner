"""Deterministic exchange fixture; never used by the production entry point."""
import time
from collections import Counter
from binance_client import BinanceClient, DataError, INTERVALS


class NoLimiter:
    def acquire(self, *a, **kw): pass
    def check(self): pass
    def retry_seconds(self): return 0


class Market:
    def __init__(self, n=12):
        self.now = time.time()
        self.n = n
        self.calls = Counter()
        self.fail = set()
        self.symbols = ["BTCUSDT", "ETHUSDT", "TINYUSDT"] + [f"COIN{i}USDT" for i in range(3,n)]
        self.client = BinanceClient(transport=self.fetch, limiter=NoLimiter(), clock=lambda:self.now)

    def fetch(self, endpoint, params):
        self.calls[endpoint] += 1
        if endpoint in self.fail:
            raise DataError("Simulated outage")
        now = int(self.now*1000)
        if endpoint.endswith("/time"):
            return {"serverTime":now}
        if endpoint.endswith("/exchangeInfo"):
            return {"symbols":[dict(symbol=s,status="TRADING",contractType="PERPETUAL",
                                    quoteAsset="USDT",marginAsset="USDT",filters=[
                                        {"filterType":"PRICE_FILTER","tickSize":"0.00000001" if s=="TINYUSDT" else "0.10"}])
                               for s in self.symbols]}
        if endpoint.endswith("/24hr"):
            return [dict(symbol=s,lastPrice="0.00001234" if s=="TINYUSDT" else str(100+i),
                         quoteVolume=str(10e6+i*1e6),closeTime=now) for i,s in enumerate(self.symbols)]
        if endpoint.endswith("/bookTicker"):
            return [dict(symbol=s,bidPrice="100",askPrice="100.02",time=now) for s in self.symbols]
        if endpoint.endswith("/klines"):
            i = self.symbols.index(params["symbol"])
            step = {**INTERVALS, "15m":900}[params["interval"]]*1000
            boundary = params["endTime"]+1
            bars=[]
            for k in range(params["limit"]):
                start=boundary-(params["limit"]-k)*step
                last=k==params["limit"]-1
                factor=1+i*.25 if last else 1
                close=100+(i*.2 if i%2==0 else -i*.2) if last else 100.1
                qv=100000*factor
                bars.append([start,"100",str(max(100,close)+1),str(min(100,close)-1),str(close),
                             "1000",start+step-1,str(qv),100*factor,"500",str(qv*.6),"0"])
            if params["interval"] == "15m":
                price = .00001234 if params["symbol"] == "TINYUSDT" else 100+i
                direction = 1 if i%2==0 else -1
                initial = price/(1+direction*(.04+i*.0025))
                for k,bar in enumerate(bars):
                    progress=max(0,k-(len(bars)-16))
                    opening=initial+(price-initial)*progress/16
                    closing=initial+(price-initial)*min(16,progress+1)/16 if k>=len(bars)-16 else initial
                    bar[1:5]=[opening,max(opening,closing),min(opening,closing),closing]
                    bar[7]=300000 if k>=len(bars)-16 else 100000
            return bars
        if endpoint.endswith("/premiumIndex"):
            return dict(symbol=params["symbol"],lastFundingRate="0.0001",time=now)
        if endpoint.endswith("/fundingRate"):
            return [dict(symbol=params["symbol"],fundingTime=now-k*8*3600000,fundingRate="0.0001") for k in (1,0)]
        if endpoint.endswith("/openInterestHist"):
            end=now//300000*300000
            return [dict(timestamp=end-(12-k)*300000,sumOpenInterest=str(1000+k)) for k in range(13)]
        raise AssertionError(endpoint)
