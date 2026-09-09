import numpy as np
import pandas as pd
import pytest
from pathlib import Path
from streamlit.testing.v1 import AppTest
from market_relative import compare_market
from movers import candle_windows, RELATIVE_WINDOWS
from relative_ui import relative_settings, relative_grid
from tests.test_movers import bars
from tests.fixtures import Market
import services

NOW=2000000.
BOUNDARY=1999800000


def market(returns=None,btc=-4.):
    values=list(returns) if returns is not None else [-5.]*19+[-1.]
    rows=[]
    for i,value in enumerate([btc]+values):
        row=dict(symbol='BTCUSDT' if i==0 else f'ALT{i}',price=100.,volume_24h=20e6,
                 spread_pct=.05,observed_at=NOW,quote_at=NOW,bar_end=BOUNDARY,status='Ready',tick_size='.01')
        for w in RELATIVE_WINDOWS:
            row.update({w+'_change':value,w+'_return_valid':True,w+'_relative_volume':np.nan})
        rows.append(row)
    return pd.DataFrame(rows)


def compare(df,window='4h',**kwargs):
    return compare_market(df,window,BOUNDARY,NOW,**kwargs)


@pytest.mark.parametrize('window',['1h','4h','8h','1d'])
def test_resilient_negative_returns(window):
    r=compare(market(),window)
    top=r.frame.loc[r.frame.symbol.eq('ALT20')].iloc[0]
    assert top.qualified and top.change==-1
    assert top.vs_btc==3 and top.vs_alts==4 and top.alt_percentile==100
    assert top.peer_median==-5 and r.alt_median==-5
    assert r.eligible_count==20 and r.coverage==1


def test_weakness_rising_and_ties():
    df=market([5.]*19+[1.],btc=4)
    r=compare(df,mode='Relative weakness')
    assert r.frame.iloc[0].symbol=='ALT20' and r.frame.iloc[0].qualified
    assert r.frame.iloc[0].change>0
    tied=compare(market([0.]*20,btc=0))
    assert (tied.frame.alt_percentile==50).all() and not tied.frame.qualified.any()


def test_leave_one_out_and_exact_thresholds():
    values=list(range(20))
    r=compare(market(values,btc=18),min_gap=1)
    high=r.frame.loc[r.frame.symbol.eq('ALT20')].iloc[0]
    low=r.frame.loc[r.frame.symbol.eq('ALT1')].iloc[0]
    assert high.peer_median==9 and low.peer_median==10
    assert high.qualified and high.vs_btc==1
    assert not compare(market(values,btc=18),min_gap=1.001).frame.iloc[0].qualified


def test_coverage_and_minimum_peers():
    df=market([-5.]*24+[-1.])
    df.loc[1:5,'4h_return_valid']=False
    r=compare(df)
    assert r.coverage==.8 and r.eligible_count==20 and r.frame.qualified.any()
    df.loc[6,'4h_return_valid']=False
    r=compare(df)
    assert r.coverage<.8 and not r.frame.qualified.any()
    assert any('80%' in warning for warning in r.warnings)
    assert not compare(market([-5.]*18+[-1.])).frame.qualified.any()


@pytest.mark.parametrize('field,value',[('quote_at',NOW-121),('bar_end',BOUNDARY-900000),('spread_pct',.11)])
def test_bad_btc_disables_qualification(field,value):
    df=market(); df.loc[0,field]=value
    r=compare(df)
    assert r.btc_return is None and not r.frame.qualified.any()


def test_volume_benchmark_filters_and_empty():
    df=market(); df.loc[0,'volume_24h']=1
    assert compare(df).btc_return==-4
    df.loc[1,'volume_24h']=1
    assert compare(df).universe_count==19
    r=compare(pd.DataFrame())
    assert r.frame.empty and r.coverage==0 and r.btc_return is None


def test_return_independent_of_baseline_and_flat_prices():
    rows,end=bars()
    for row in rows: row[1:5]=[100]*4; row[7]=0
    metrics=candle_windows(rows,'15m',end)
    assert metrics['status']=='Ready'
    assert metrics['1h_return_valid'] and metrics['1h_change']==0
    assert np.isnan(metrics['1h_relative_volume'])
    # Four bars suffice for a return; participation still needs 60 bars.
    short=candle_windows(rows[-4:],'15m',end)
    assert short['1h_return_valid'] and short['1h_status']=='Insufficient history'
    bad=candle_windows(rows,'15m',end+900000)
    assert not bad['1h_return_valid']


def test_one_hour_baseline_exclusion():
    rows,end=bars()
    for row in rows: row[7]=100
    for row in rows[-4:]: row[7]=200
    metrics=candle_windows(rows,'15m',end)
    assert metrics['1h_relative_volume']==2
    assert metrics['1h_change']==pytest.approx((104/103-1)*100)


def test_btc_requested_outside_volume_and_cache_reuse():
    m=Market(n=25)
    first=m.client.scan_movers(11e6)
    assert 'BTCUSDT' in set(first.frame.symbol)
    calls=m.calls.copy()
    for w in RELATIVE_WINDOWS: compare_market(first.frame,w,first.boundary,m.now,11e6)
    m.client.scan_movers(11e6)
    assert m.calls==calls
    assert m.calls['/fapi/v1/klines']==25


def test_url_grid_and_invalid_settings():
    s=relative_settings({'mr_window':'1h','mr_percentile':'inf','mr_gap_1h':'-1'})
    assert s['mr_window']=='1h' and s['mr_percentile']==80 and s['mr_gap_1h']==.5
    r=compare(market())
    data,options=relative_grid(r.frame,'Relative strength')
    assert pd.api.types.is_numeric_dtype(data.vs_btc)
    assert len([c for c in options['columnDefs'] if not c.get('hide')])==9
    assert options['initialState']['sort']['sortModel'][0]=={'colId':'vs_alts','sort':'desc'}
    with pytest.raises(ValueError): compare(market(),min_gap=np.inf)


def test_relative_app_filters_and_no_history(monkeypatch):
    m=Market(n=25)
    monkeypatch.setattr(services,'get_client',lambda:m.client)
    def no_history(): raise AssertionError('Relative must not access history')
    monkeypatch.setattr(services,'get_history',no_history)
    at=AppTest.from_file(Path(__file__).resolve().parents[1]/'app.py',default_timeout=30)
    at.query_params={'tab':'relative'}
    at.run()
    assert not at.exception and m.calls['/fapi/v1/klines']==25
    calls=m.calls.copy()
    at.selectbox(key='mr_window').set_value('1h').run()
    at.selectbox(key='mr_mode').set_value('Relative weakness').run()
    at.toggle(key='mr_qualified').set_value(False).run()
    at.number_input(key='mr_gap_1h').set_value(.75).run()
    benchmark=[x.value for x in at.metric]
    at.text_input(key='search').set_value('[').run()
    assert [x.value for x in at.metric]==benchmark
    assert not at.exception and m.calls==calls
    assert m.calls['/fapi/v1/fundingRate']==0
