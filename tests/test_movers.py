import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest
from pathlib import Path
import services
from movers import candle_windows, qualify_movers
from movers_ui import mover_settings, mover_grid
from tests.fixtures import Market


def bars(direction=1):
    end=1440*900000
    rows=[]
    for i in range(1440):
        start=i*900000
        o=100 if i<1424 else 100+direction*(i-1424)*.25
        c=100 if i<1424 else o+direction*.25
        rows.append([start,o,max(o,c),min(o,c),c,1,start+899999,100 if i<1424 else 200,10,1,50,0])
    return rows,end


def frame(direction=1):
    rows,end=bars(direction)
    return pd.DataFrame([dict(symbol="TEST",price=104 if direction>0 else 96,volume_24h=20e6,
        spread_pct=.1,tick_size=".01",**candle_windows(rows,"15m",end))])


@pytest.mark.parametrize("direction",[1,-1])
def test_holding_direction_and_retracement(direction):
    df=frame(direction)
    result=qualify_movers(df)
    assert result.iloc[0].qualified
    assert result.iloc[0].direction==direction
    assert result.iloc[0].relative_volume==2
    assert result.iloc[0].efficiency==1
    df.price=100
    assert not qualify_movers(df).iloc[0].qualified
    df.price=110 if direction>0 else 90
    assert qualify_movers(df).iloc[0].qualified
    assert qualify_movers(df).iloc[0].distance_pct==0


def test_both_close_and_current_must_hold():
    df=frame(); df['4h_close']=101
    assert not qualify_movers(df).iloc[0].qualified


def test_thresholds_missing_spread_and_stale():
    df=frame()
    assert qualify_movers(df,min_return=4,min_relative_volume=2,min_efficiency=1).iloc[0].qualified
    for key,value in [('spread_pct',.1001),('spread_pct',np.nan),('status','Stale quote'),('4h_relative_volume',np.nan)]:
        broken=df.copy(); broken[key]=value
        assert not qualify_movers(broken).iloc[0].qualified


def test_alignment_baselines_and_short_history():
    rows,end=bars()
    assert candle_windows(rows,'15m',end)['4h_relative_volume']==2
    assert candle_windows(rows,'15m',end+900000)['4h_status']=='Invalid candle history'
    assert candle_windows(rows[-240:],'15m',end)['4h_status']=='Ready'
    assert candle_windows(rows[-240:],'15m',end)['1d_status']=='Insufficient history'
    for row in rows[:-16]: row[7]=0
    assert np.isnan(candle_windows(rows,'15m',end)['4h_relative_volume'])
    for row in rows: row[1:5]=[100]*4
    assert candle_windows(rows,'15m',end)['4h_status']=='Zero baseline or range'


def test_movers_cache_windows_failures_and_weights():
    m=Market(n=3)
    weights=[]
    m.client.limiter.acquire=lambda weight,*a,**kw: weights.append(weight)
    scan=m.client.scan_movers()
    assert m.calls['/fapi/v1/klines']==3 and weights.count(10)==3
    for w in ('4h','8h','1d'): qualify_movers(scan.frame,w)
    m.client.scan_movers(force=True)
    assert m.calls['/fapi/v1/klines']==3
    m.now+=900
    m.fail.add('/fapi/v1/klines')
    failed=m.client.scan_movers()
    assert failed.frame.status.str.contains('Stale').all()
    assert not qualify_movers(failed.frame).qualified.any()
    m.fail.clear(); m.now+=6
    recovered=m.client.scan_movers()
    assert recovered.frame.status.eq('Ready').all()
    assert m.calls['/fapi/v1/fundingRate']==0


def test_url_and_numeric_grid():
    s=mover_settings({'tab':'movers','window':'bad','mover_eff':'inf','ret_4h':'-1'})
    assert s['scanner_tab']=='Strong Movers' and s['mover_window']=='4h'
    assert s['mover_eff']==.35 and s['ret_4h']==2
    data,options=mover_grid(qualify_movers(frame()),'4h')
    assert pd.api.types.is_numeric_dtype(data.price)
    assert len([c for c in options['columnDefs'] if not c.get('hide')])==8
    assert options['initialState']['sort']['sortModel'][0]['colId']=='change'


def test_movers_app_lazy_and_filters(monkeypatch):
    m=Market(n=3)
    monkeypatch.setattr(services,'get_client',lambda:m.client)
    def no_history(): raise AssertionError('Movers must not use Activity history')
    monkeypatch.setattr(services,'get_history',no_history)
    at=AppTest.from_file(Path(__file__).resolve().parents[1]/'app.py',default_timeout=20)
    at.query_params={'tab':'movers'}
    at.run()
    assert not at.exception
    assert m.calls['/fapi/v1/klines']==3
    calls=m.calls.copy()
    at.selectbox(key='mover_window').set_value('1d').run()
    at.toggle(key='qualified_only').set_value(False).run()
    at.number_input(key='mover_eff').set_value(.2).run()
    at.text_input(key='search').set_value('[').run()
    assert not at.exception and m.calls==calls
    assert m.calls['/fapi/v1/fundingRate']==0


def test_large_history_cache_is_bounded():
    m=Market(n=3)
    for _ in range(4):
        m.client.scan_movers()
        m.now+=900
    keys=[k for k in m.client.cache if k[0].endswith('/klines')]
    assert len(keys)==6
    assert m.calls['/fapi/v1/klines']==12


def test_subsecond_quote_clock_rounding():
    m=Market(n=3)
    original=m.client.transport
    def fetch(endpoint,params):
        result=original(endpoint,params)
        if endpoint.endswith('/bookTicker'):
            for row in result: row['time']+=1
        return result
    m.client.transport=fetch
    scan=m.client.scan_movers()
    assert qualify_movers(scan.frame).qualified.all()


def test_holding_boundary_and_either_side_of_it():
    df=frame()
    df['price']=103.2
    assert qualify_movers(df).iloc[0].qualified
    df['price']=103.19
    assert not qualify_movers(df).iloc[0].qualified
