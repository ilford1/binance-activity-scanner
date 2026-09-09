"""Transparent sustained-movement metrics; no composite score."""
import numpy as np
import pandas as pd

WINDOWS = {"4h": 16, "8h": 32, "1d": 96}
DEFAULT_RETURNS = {"4h": 2.0, "8h": 3.0, "1d": 5.0}


RELATIVE_WINDOWS = {"1h": 4, **WINDOWS}


def candle_windows(bars, interval, boundary):
    """Return validity is independent of participation history and trend shape."""
    result = {"status": "Ready", "bar_end": boundary}
    def number(value):
        try:
            return float(value)
        except (ValueError, TypeError, OverflowError):
            return np.nan
    for window, count in RELATIVE_WINDOWS.items():
        prefix = window + "_"
        result.update({prefix+"status": "Insufficient history", prefix+"return_valid": False})
        try:
            if len(bars) < count:
                continue
            rows = bars[-count*15:]
            a = np.array([[number(row[j]) for j in (0,1,2,3,4,6,7)] for row in rows])
            measured = a[-count:]
            expected = boundary - np.arange(count,0,-1)*900000
            if not np.isfinite(measured[:,:6]).all() or not np.array_equal(measured[:,0],expected) or not np.array_equal(measured[:,5],expected+899999):
                raise ValueError()
            if (measured[:,1:5]<=0).any() or (measured[:,2]<measured[:,[1,3,4]].max(axis=1)).any() or (measured[:,3]>measured[:,[1,2,4]].min(axis=1)).any():
                raise ValueError()
            first,close = measured[0,1],measured[-1,4]
            high,low = measured[:,2].max(),measured[:,3].min()
            path = np.abs(np.diff(np.r_[first,measured[:,4]])).sum()
            result.update({prefix+"change":(close/first-1)*100, prefix+"return_valid":True,
                           prefix+"high":high,prefix+"low":low,prefix+"close":close,
                           prefix+"efficiency":abs(close-first)/path if path>0 else np.nan,
                           prefix+"relative_volume":np.nan})
            if len(a)<count*15:
                continue
            expected = boundary-np.arange(len(a),0,-1)*900000
            if not np.array_equal(a[:,0],expected) or not np.array_equal(a[:,5],expected+899999):
                raise ValueError()
            if not np.isfinite(a[:,6]).all() or (a[:,6]<0).any():
                result[prefix+"status"]="Unavailable volume history"
                continue
            baseline=np.median(a[:-count,6].reshape(14,count).sum(axis=1))
            result[prefix+"relative_volume"]=measured[:,6].sum()/baseline if baseline>0 else np.nan
            result[prefix+"status"]="Ready" if baseline>0 and path>0 and high>low else "Zero baseline or range"
        except (ValueError, TypeError, IndexError, OverflowError):
            result[prefix+"status"]="Invalid candle history"
    if not any(result[w+"_return_valid"] for w in RELATIVE_WINDOWS):
        result["status"]="Incomplete candles"
    return result


def qualify_movers(frame, window="4h", min_return=None, min_relative_volume=1.25,
                   min_efficiency=.35, hold_pct=20.0, max_spread=.10):
    if window not in WINDOWS:
        raise ValueError("Invalid mover window")
    limits = [DEFAULT_RETURNS[window] if min_return is None else min_return,
              min_relative_volume, min_efficiency, hold_pct, max_spread]
    if not all(np.isfinite(v) and v >= 0 for v in limits) or min_efficiency>1 or hold_pct>100:
        raise ValueError("Invalid qualification thresholds")
    df = frame.copy()
    if df.empty:
        return df.assign(qualified=False, direction=0, change=np.nan)
    for key in ("change", "relative_volume", "efficiency", "high", "low", "close"):
        df[key] = pd.to_numeric(df.get(window+"_"+key, pd.Series(np.nan,index=df.index)), errors="coerce").replace([np.inf,-np.inf],np.nan)
    df["direction"] = np.sign(df.change.fillna(0)).astype(int)
    ranges = df.high-df.low
    favorable = np.where(df.direction>0, df.high, df.low)
    df["distance_pct"] = np.maximum(0, np.where(df.direction>0, favorable-df.price, df.price-favorable))/favorable*100
    current_pos = (df.price-df.low)/ranges
    closed_pos = (df.close-df.low)/ranges
    holds = ((df.direction>0)&(current_pos>=1-hold_pct/100)&(closed_pos>=1-hold_pct/100)) | ((df.direction<0)&(current_pos<=hold_pct/100)&(closed_pos<=hold_pct/100))
    statuses=[]
    for index,row in df.iterrows():
        reasons=[]
        if row.status != "Ready": reasons.append(row.status)
        candle_status=row.get(window+"_status", "Insufficient history")
        if candle_status != "Ready": reasons.append(candle_status)
        if not np.isfinite([row.change,row.relative_volume,row.efficiency,row.price,row.spread_pct]).all() or row.price<=0 or ranges.loc[index]<=0:
            reasons.append("Incomplete metrics")
        if not np.isfinite(row.spread_pct) or row.spread_pct<0 or row.spread_pct>max_spread: reasons.append("Wide or invalid spread")
        if abs(row.change)+1e-10<limits[0] or row.direction==0: reasons.append("Return below threshold")
        if row.relative_volume+1e-10<min_relative_volume: reasons.append("Low relative volume")
        if row.efficiency+1e-10<min_efficiency: reasons.append("Low efficiency")
        if not holds.loc[index]: reasons.append("Retraced from extreme")
        statuses.append("; ".join(dict.fromkeys(reasons)) or "Qualified")
    df["status"]=statuses
    df["qualified"]=df.status.eq("Qualified")
    df["absolute_return"]=df.change.abs()
    return df.sort_values(["absolute_return","volume_24h","symbol"],ascending=[False,False,True],na_position="last",kind="stable")
