import sqlite3
import pandas as pd
import pytest
from history_store import HistoryStore

START=1_700_000_000.


def frame(symbol='ABCUSDT', stamp=START, price=100., direction=1, bar=1000, active=True):
    return pd.DataFrame([dict(symbol=symbol,observed_at=stamp,price=price,price_fresh=True,
                              active=active,direction=direction,activity=95.,bar_end=bar,status='Active')])


def record(store, stamp, price, direction=1, symbol='ABCUSDT', active=False, minv=1e7, bar=1000):
    return store.record(frame(symbol,stamp,price,direction,bar,active),'5m',minv,now=stamp)


def test_migration_preserves_legacy_and_makes_backup(tmp_path):
    path=tmp_path/'history.sqlite3'
    with sqlite3.connect(path) as con:
        con.execute('CREATE TABLE snapshots (ts TEXT)'); con.execute("INSERT INTO snapshots VALUES ('legacy')")
    store=HistoryStore(path)
    assert not record(store,START,100,active=True)['error']
    with sqlite3.connect(path) as con:
        assert con.execute('SELECT * FROM snapshots').fetchall()==[('legacy',)]
    assert path.with_suffix('.pre-activity-v2.sqlite3').exists()
    assert len(store.summary('5m',1e7,now=START)['events'])==1


def test_earlier_observation_waits_for_exact_hour(tmp_path):
    store=HistoryStore(tmp_path/'h.db')
    record(store,START,100,active=True)
    record(store,START+2700,110)
    assert store.summary('5m',1e7,now=START+3000)['events'][0]['outcome']=='pending'
    record(store,START+3600,90)
    event=store.summary('5m',1e7,now=START+3600)['events'][0]
    assert event['outcome']=='reversal'
    assert event['return_pct']==pytest.approx(-10)
    assert event['forward_at']==START+3600


def test_missing_symbol_and_later_tie(tmp_path):
    store=HistoryStore(tmp_path/'h.db')
    record(store,START,100,active=True)
    record(store,START+2700,100,symbol='OTHERUSDT')
    record(store,START+3300,90)
    record(store,START+3900,110)
    event=store.summary('5m',1e7,now=START+4500)['events'][0]
    assert event['outcome']=='continuation' and event['forward_at']==START+3900


def test_independent_directions_and_flat_denominator(tmp_path):
    store=HistoryStore(tmp_path/'h.db')
    for symbol,direction in [('UPUSDT',1),('DOWNUSDT',-1),('FLATUSDT',1)]:
        record(store,START,100,direction,symbol,active=True)
        record(store,START+3600,100 if symbol=='FLATUSDT' else 110,symbol=symbol)
    result=store.summary('5m',1e7,now=START+3600)['directions']
    assert result['Upward moves']['rate']==.5
    assert result['Upward moves']['flat']==1
    assert result['Downward moves']['reversal']==1


def test_dedup_cooldown_configuration_and_stale(tmp_path):
    store=HistoryStore(tmp_path/'h.db')
    assert record(store,START,100,active=True)['events']==1
    assert record(store,START,100,active=True)['events']==0
    assert record(store,START+600,101,active=True,bar=2000)['events']==0
    assert record(store,START+3600,102,active=True,bar=3000,minv=2e7)['events']==1
    assert len(store.summary('5m',1e7,now=START+3600)['events'])==1
    assert len(store.summary('5m',2e7,now=START+3600)['events'])==1
    assert not store.summary('1m',1e7,now=START+3600)['events']
    f=frame(stamp=START+7200,bar=4000); f['price_fresh']=False
    assert store.record(f,'5m',1e7,now=START+7200)['observations']==0


def test_expiry_prune_and_storage_failure(tmp_path):
    store=HistoryStore(tmp_path/'h.db')
    record(store,START,100,active=True)
    assert store.summary('5m',1e7,now=START+4500)['events'][0]['outcome']=='unmeasurable'
    store.record(pd.DataFrame(),'5m',1e7,now=START+73*3600)
    with sqlite3.connect(store.path) as con:
        assert con.execute('SELECT count(*) FROM activity_events').fetchone()[0]==0
    bad=HistoryStore(tmp_path/'missing'/'h.db')
    assert bad.record(frame(),'5m',1e7,now=START)['error']
    assert bad.summary('5m',1e7,now=START)['error']


def test_indexed_history_performance(tmp_path):
    import time
    from history_store import configuration
    store=HistoryStore(tmp_path/'performance.db')
    store.record(pd.DataFrame(),'5m',1e7,now=START)
    with sqlite3.connect(store.path) as con:
        con.executemany('INSERT INTO activity_observations VALUES (?,?,?)',
                        ((f'S{i}USDT',START+m*60,100+m*.001) for i in range(100) for m in range(720)))
        con.executemany('INSERT INTO activity_events '
                        '(symbol,timeframe,bar_end,scoring_version,config,entry_at,entry_price,direction,activity) '
                        'VALUES (?,?,?,?,?,?,?,?,?)',
                        ((f'S{i}USDT','5m',h,'activity-v2',configuration('5m',1e7),START+h*3600,100,1,90)
                         for i in range(100) for h in range(11)))
        plan=con.execute('EXPLAIN QUERY PLAN SELECT price FROM activity_observations '
                         'WHERE symbol=? AND observed_at BETWEEN ? AND ?',('S0USDT',START,START+60)).fetchall()
        assert any('SEARCH' in r[3] for r in plan)
    start=time.perf_counter()
    result=store.record(pd.DataFrame(),'5m',1e7,now=START+12*3600)
    elapsed=time.perf_counter()-start
    assert not result['error']
    assert elapsed<5, f'Indexed history resolution took {elapsed:.2f}s'
    print(f'1100 events / 72000 observations resolved in {elapsed:.3f}s')
