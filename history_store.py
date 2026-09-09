"""Versioned activity events and descriptive forward price outcomes.

Legacy snapshots are preserved, but never mixed with activity-v2 outcomes.
Only exact-hour observations finalize early; other outcomes wait until the
75-minute observation window closes, allowing nearer observations to arrive.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

import pandas as pd

from scoring import SCORING_VERSION, MAX_SPREAD_PCT, ACTIVE_SCORE, ACTIVE_RVOL, ACTIVE_RTRADES

DB_PATH = os.environ.get("SCANNER_HISTORY_DB") or str(Path(__file__).with_name("scanner_history.sqlite3"))
HORIZON = 3600
TOLERANCE = 900
RETENTION = 72 * 3600
SCHEMA = """
CREATE TABLE IF NOT EXISTS scanner_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS activity_observations (
    symbol TEXT NOT NULL, observed_at REAL NOT NULL, price REAL NOT NULL CHECK(price>0),
    PRIMARY KEY (symbol, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_activity_observation_age ON activity_observations(observed_at);
CREATE TABLE IF NOT EXISTS activity_events (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL, timeframe TEXT NOT NULL, bar_end INTEGER NOT NULL,
    scoring_version TEXT NOT NULL, config TEXT NOT NULL,
    entry_at REAL NOT NULL, entry_price REAL NOT NULL CHECK(entry_price>0),
    direction INTEGER NOT NULL CHECK(direction IN (-1,1)), activity REAL NOT NULL,
    outcome TEXT NOT NULL DEFAULT 'pending', forward_at REAL, forward_price REAL,
    return_pct REAL, resolved_at REAL,
    UNIQUE(symbol,timeframe,bar_end)
);
CREATE INDEX IF NOT EXISTS idx_activity_event_symbol ON activity_events(symbol,timeframe,entry_at);
CREATE INDEX IF NOT EXISTS idx_activity_event_pending ON activity_events(outcome,entry_at);
CREATE INDEX IF NOT EXISTS idx_activity_event_config ON activity_events(config,entry_at);
"""


def configuration(timeframe: str, min_volume: float) -> str:
    return json.dumps(dict(timeframe=timeframe, min_volume=float(min_volume),
                          version=SCORING_VERSION, max_spread=MAX_SPREAD_PCT,
                          active_score=ACTIVE_SCORE, rvol=ACTIVE_RVOL, rtrades=ACTIVE_RTRADES),
                      sort_keys=True, separators=(",", ":"))


def numeric(value) -> float | None:
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


class HistoryStore:
    def __init__(self, path: str = DB_PATH):
        self.path = str(path)
        self.lock = threading.RLock()
        self.initialized = False

    def _connect(self):
        con = sqlite3.connect(self.path, timeout=3)
        con.execute("PRAGMA busy_timeout=3000")
        return con

    def _initialize(self, con) -> None:
        if self.initialized:
            return
        legacy = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='snapshots'").fetchone()
        versioned = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='activity_events'").fetchone()
        if legacy and not versioned:
            backup = Path(self.path).with_suffix(".pre-activity-v2.sqlite3")
            if not backup.exists():
                with closing(sqlite3.connect(backup)) as dest:
                    con.backup(dest)
        con.executescript(SCHEMA)
        con.execute("INSERT OR REPLACE INTO scanner_meta VALUES ('schema_version','2')")
        con.commit()
        self.initialized = True

    def record(self, frame: pd.DataFrame, timeframe: str, min_volume: float,
               now: float | None = None) -> dict:
        """Idempotently observe fresh prices, create spaced events, resolve and prune."""
        now = time.time() if now is None else now
        result = {"events": 0, "observations": 0, "error": ""}
        try:
            with self.lock, closing(self._connect()) as con:
                self._initialize(con)
                con.execute("BEGIN IMMEDIATE")
                config = configuration(timeframe, min_volume)
                for row in frame.to_dict("records"):
                    price, stamp = numeric(row.get("price")), numeric(row.get("observed_at"))
                    if (not row.get("price_fresh", False) or price is None or price <= 0
                            or stamp is None or not 0 <= now - stamp <= 120):
                        continue
                    sym = row["symbol"]
                    result["observations"] += con.execute(
                        "INSERT OR IGNORE INTO activity_observations VALUES (?,?,?)", (sym, stamp, price)).rowcount
                    direction = numeric(row.get("direction"))
                    if not row.get("active", False) or direction not in (-1, 1):
                        continue
                    last = con.execute("SELECT MAX(entry_at) FROM activity_events WHERE symbol=? AND timeframe=?",
                                       (sym, timeframe)).fetchone()[0]
                    if last is not None and stamp - last < HORIZON:
                        continue
                    score, bar = numeric(row.get("activity")), numeric(row.get("bar_end"))
                    if score is None or bar is None or row.get("status") != "Active":
                        continue
                    result["events"] += con.execute(
                        "INSERT OR IGNORE INTO activity_events "
                        "(symbol,timeframe,bar_end,scoring_version,config,entry_at,entry_price,direction,activity) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (sym,timeframe,int(bar),SCORING_VERSION,config,stamp,price,int(direction),score)).rowcount
                self._resolve(con, now)
                con.execute("DELETE FROM activity_events WHERE entry_at < ?", (now-RETENTION,))
                con.execute("DELETE FROM activity_observations WHERE observed_at < ?", (now-RETENTION,))
                con.commit()
        except (sqlite3.Error, OSError, ValueError, TypeError, KeyError) as exc:
            result.update(events=0, observations=0, error=f"History unavailable: {type(exc).__name__}: {exc}")
        return result

    def _resolve(self, con, now: float) -> None:
        # Indexed same-symbol range lookup, batched updates; no full timestamp walks.
        pending = con.execute("SELECT id,symbol,entry_at,entry_price,direction FROM activity_events "
                              "WHERE outcome='pending' AND entry_at<=?", (now-HORIZON,)).fetchall()
        updates = []
        for event_id, sym, entry_at, price, direction in pending:
            target = entry_at + HORIZON
            row = con.execute(
                "SELECT observed_at,price FROM activity_observations "
                "WHERE symbol=? AND observed_at BETWEEN ? AND ? "
                "ORDER BY ABS(observed_at-?), observed_at DESC LIMIT 1",
                (sym,target-TOLERANCE,min(now,target+TOLERANCE),target)).fetchone()
            exact = row is not None and abs(row[0]-target) < .001
            if now < target+TOLERANCE and not exact:
                continue
            if row is None:
                updates.append(("unmeasurable",None,None,None,now,event_id))
                continue
            stamp, later = row
            ret = (later/price-1)*100
            outcome = "flat" if later == price else ("continuation" if ret*direction > 0 else "reversal")
            updates.append((outcome,stamp,later,ret,now,event_id))
        con.executemany("UPDATE activity_events SET outcome=?,forward_at=?,forward_price=?,return_pct=?,resolved_at=? "
                        "WHERE id=?", updates)

    def summary(self, timeframe: str, min_volume: float, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        result = {"directions": {}, "events": [], "error": ""}
        try:
            with self.lock, closing(self._connect()) as con:
                self._initialize(con)
                self._resolve(con, now)
                con.commit()
                rows = con.execute("SELECT symbol,entry_at,direction,outcome,return_pct,forward_at FROM activity_events "
                                   "WHERE config=? AND entry_at>=? ORDER BY entry_at DESC",
                                   (configuration(timeframe,min_volume),now-24*3600)).fetchall()
            for direction, label in ((1,"Upward moves"),(-1,"Downward moves")):
                group = [r for r in rows if r[2] == direction]
                counts = {k: sum(r[3] == k for r in group) for k in
                          ("continuation","reversal","flat","pending","unmeasurable")}
                resolved = sum(counts[k] for k in ("continuation","reversal","flat"))
                counts["rate"] = counts["continuation"]/resolved if resolved else None
                returns = [r[4]*direction for r in group if r[4] is not None]
                counts["mean_directional_return"] = sum(returns)/len(returns) if returns else None
                result["directions"][label] = counts
            result["events"] = [dict(symbol=r[0],entry_at=r[1],direction=r[2],outcome=r[3],
                                     return_pct=r[4],forward_at=r[5]) for r in rows[:100]]
        except (sqlite3.Error, OSError) as exc:
            result["error"] = f"History unavailable: {exc}"
        return result
