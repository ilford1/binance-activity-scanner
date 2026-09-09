"""Market-relative returns and explicit, non-predictive qualification."""
from dataclasses import dataclass
import numpy as np
import pandas as pd
from movers import RELATIVE_WINDOWS

GAPS = {"1h": .5, "4h": 1., "8h": 1.5, "1d": 2.}


@dataclass
class RelativeResult:
    frame: pd.DataFrame
    btc_return: float | None
    alt_median: float | None
    eligible_count: int
    universe_count: int
    coverage: float
    warnings: list[str]


def compare_market(frame, window, boundary, now, min_volume=10_000_000,
                   percentile=80., min_gap=None, mode="Relative strength"):
    gap=GAPS.get(window) if min_gap is None else min_gap
    if window not in RELATIVE_WINDOWS or mode not in ("Relative strength","Relative weakness"):
        raise ValueError("Invalid relative window or mode")
    if not all(np.isfinite(x) for x in (now,min_volume,percentile,gap)) or min_volume<0 or not 50<=percentile<=100 or gap<0:
        raise ValueError("Invalid relative thresholds")
    df=frame.copy().reset_index(drop=True)
    def numeric(name):
        return pd.to_numeric(df.get(name,pd.Series(np.nan,index=df.index)),errors="coerce").replace([np.inf,-np.inf],np.nan)
    for name in ("price","volume_24h","spread_pct","observed_at","quote_at","bar_end"):
        df[name]=numeric(name)
    df["change"]=numeric(window+"_change")
    df["relative_volume"]=numeric(window+"_relative_volume")
    ready=df.get("status",pd.Series("Unavailable",index=df.index)).eq("Ready")
    aligned=df.bar_end.eq(boundary)
    valid_return=df.get(window+"_return_valid",pd.Series(False,index=df.index)).eq(True)&df.change.notna()&aligned&ready
    fresh=(now-df.observed_at).between(-1,120)&(now-df.quote_at).between(-1,120)
    usable=valid_return&fresh&df.price.gt(0)
    tight=df.spread_pct.between(0,.10)
    symbols=df.get("symbol",pd.Series("",index=df.index))
    btc=usable&tight&symbols.eq("BTCUSDT")
    btc_return=float(df.loc[btc,"change"].iloc[0]) if btc.any() else None
    df["symbol"]=symbols
    universe=symbols.ne("BTCUSDT")&df.volume_24h.ge(min_volume)
    eligible=universe&usable&tight
    n=int(eligible.sum()); total=int(universe.sum())
    coverage=float((universe&usable).sum()/total) if total else 0.
    median=float(df.loc[eligible,"change"].median()) if n else None
    warnings=[]
    if btc_return is None: warnings.append("BTC benchmark unavailable, stale, or misaligned")
    if n<20: warnings.append(f"At least 20 eligible altcoins required; available: {n}")
    if coverage<.8: warnings.append(f"Valid-return coverage below 80%: {coverage:.0%}")
    df["alt_percentile"]=np.nan
    if n>=2:
        df.loc[eligible,"alt_percentile"]=(df.loc[eligible,"change"].rank(method="average")-1)/(n-1)*100
    df["peer_median"]=np.nan
    for index in df.index[universe]:
        peers=df.loc[eligible & df.index.to_series().ne(index),"change"]
        if len(peers): df.loc[index,"peer_median"]=peers.median()
    df["vs_btc"]=df.change-btc_return if btc_return is not None else np.nan
    df["vs_alts"]=df.change-df.peer_median
    strong=(df.alt_percentile>=percentile)&(df.vs_btc+1e-10>=gap)&(df.vs_alts+1e-10>=gap)
    weak=(df.alt_percentile<=100-percentile)&(-df.vs_btc+1e-10>=gap)&(-df.vs_alts+1e-10>=gap)
    df["classification"]=np.select([eligible&strong,eligible&weak],["Relative strength","Relative weakness"],default="Neutral")
    df["direction"]=np.sign(df.change.fillna(0)).astype(int)
    statuses=[]
    for index,row in df.iterrows():
        reasons=[]
        if not ready.loc[index]: reasons.append(str(row.get("status","Unavailable")))
        if not aligned.loc[index]: reasons.append("Misaligned candle window")
        if not valid_return.loc[index]: reasons.append("Unavailable window return")
        if not fresh.loc[index] or not row.price>0: reasons.append("Stale or invalid price/quote")
        if not tight.loc[index]: reasons.append("Wide or invalid spread")
        reasons.extend(warnings)
        if not reasons:
            if mode=="Relative strength":
                if not row.alt_percentile>=percentile: reasons.append("Below strength percentile")
                if not row.vs_btc+1e-10>=gap: reasons.append("Insufficient gap vs BTC")
                if not row.vs_alts+1e-10>=gap: reasons.append("Insufficient gap vs alts")
            else:
                if not row.alt_percentile<=100-percentile: reasons.append("Above weakness percentile")
                if not -row.vs_btc+1e-10>=gap: reasons.append("Insufficient weakness vs BTC")
                if not -row.vs_alts+1e-10>=gap: reasons.append("Insufficient weakness vs alts")
        statuses.append("; ".join(dict.fromkeys(reasons)) or "Qualified")
    df["status"]=statuses
    df["qualified"]=eligible & df.status.eq("Qualified")
    if warnings: df["classification"]="Unavailable benchmark"
    ascending=mode=="Relative weakness"
    result=df.loc[universe].sort_values(["vs_alts","vs_btc","volume_24h","symbol"],ascending=[ascending,ascending,False,True],na_position="last",kind="stable")
    return RelativeResult(result,btc_return,median,n,total,coverage,warnings)
