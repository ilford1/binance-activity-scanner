"""Activity ranks describe a scan, not prediction probabilities."""
import numpy as np
import pandas as pd

SCORING_VERSION = "activity-v2"
BASELINE_BARS = 60
MAX_SPREAD_PCT = 0.10
ACTIVE_SCORE = 80.0
ACTIVE_RVOL = 1.5
ACTIVE_RTRADES = 1.25


def score_activity(frame: pd.DataFrame) -> pd.DataFrame:
    """Score fresh, complete, tight-spread pairs using average-tie percentiles."""
    df = frame.copy()
    df["activity"] = np.nan
    df["active"] = False
    if "status" not in df:
        df["status"] = "Ready"
    df.loc[df["status"].eq("Active"), "status"] = "Ready"
    required = ["price", "change", "relative_volume", "relative_trades",
                "buy_pct", "spread_pct", "volume_24h"]
    for col in required:
        if col not in df:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors="coerce").replace([np.inf, -np.inf], np.nan)
    valid = df[required].notna().all(axis=1)
    valid &= (df["price"] > 0) & (df["volume_24h"] >= 0)
    valid &= (df["relative_volume"] >= 0) & (df["relative_trades"] >= 0)
    valid &= df["buy_pct"].between(0, 100) & (df["spread_pct"] >= 0)
    ready = df["status"].eq("Ready")
    df.loc[ready & ~valid, "status"] = "Incomplete data"
    df.loc[ready & valid & (df["spread_pct"] > MAX_SPREAD_PCT), "status"] = "Wide spread"
    eligible = df["status"].eq("Ready") & valid
    df["eligible"] = eligible
    n = int(eligible.sum())
    if n >= 2:
        def rank(values):
            return (values.rank(method="average") - 1) / (n - 1)
        df.loc[eligible, "activity"] = 100 * (
            .4 * rank(df.loc[eligible, "relative_volume"])
            + .3 * rank(df.loc[eligible, "relative_trades"])
            + .3 * rank(df.loc[eligible, "change"].abs()))
        df["active"] = (eligible & (df["activity"] >= ACTIVE_SCORE)
                        & (df["relative_volume"] >= ACTIVE_RVOL)
                        & (df["relative_trades"] >= ACTIVE_RTRADES))
        df.loc[df["active"], "status"] = "Active"
    else:
        df.loc[eligible, "status"] = "Insufficient peers"
    df["direction"] = np.sign(df["change"].fillna(0)).astype(int)
    return df.sort_values(["activity", "volume_24h", "symbol"],
                          ascending=[False, False, True], na_position="last",
                          kind="stable").reset_index(drop=True)
