import numpy as np
import pandas as pd
import pytest
from scoring import score_activity
from ui_helpers import settings_from_url, filter_table, format_price, TABLE_COLUMNS
from grid_view import grid_data, grid_options
from tests.fixtures import Market


def sample():
    return pd.DataFrame(dict(symbol=["A","B","C"],price=[1,1,1],volume_24h=[1e7]*3,
                             change=[1,-2,3],relative_volume=[1,2,3],relative_trades=[1,2,3],
                             buy_pct=[50,50,50],spread_pct=[.01,.01,.01],status=["Ready"]*3))


def test_scores_direction_thresholds():
    df=score_activity(sample()).set_index("symbol")
    assert df.activity.to_dict()=={"C":100.,"B":50.,"A":0.}
    assert df.active.to_dict()=={"C":True,"B":False,"A":False}
    assert df.loc["B","direction"]==-1


def test_ties_no_false_extremes():
    f=sample(); f[["change","relative_volume","relative_trades"]]=1.
    result=score_activity(f)
    assert (result.activity==50).all()
    assert not result.active.any()


@pytest.mark.parametrize("column,value",[("price",0),("relative_volume",np.inf),("relative_trades",np.nan),
                                         ("buy_pct",101),("spread_pct",-.1)])
def test_invalid_observation_never_ranked(column,value):
    f=sample(); f[column]=f[column].astype(float); f.loc[0,column]=value
    r=score_activity(f).set_index("symbol")
    assert pd.isna(r.loc["A","activity"])
    assert not r.loc["A","active"]


def test_wide_stale_and_single_pair():
    f=sample(); f.loc[0,"spread_pct"]=.11; f.loc[1,"status"]="Stale quote"
    r=score_activity(f).set_index("symbol")
    assert r.activity.isna().all()
    assert r.loc["A","status"]=="Wide spread"
    assert r.loc["C","status"]=="Insufficient peers"
    assert score_activity(f.iloc[:0]).empty


@pytest.mark.parametrize("value",["inf","nan","-1","1e999","bad","9999999999999999999999"])
def test_invalid_volume_url(value):
    assert settings_from_url({"minv":value})["min_volume"]==10_000_000


def test_url_defaults_and_backwards_compatibility():
    assert settings_from_url({})["timeframe"]=="5m"
    r=settings_from_url(dict(tf="1h",minv="5000000",q=" eth ",rf="60s",m="lastPrice",thr="1"))
    assert (r["timeframe"],r["min_volume"],r["search"],r["refresh_rate"])==("1h",5_000_000,"ETH","60s")
    assert "metrics" not in r


def test_literal_search_and_numeric_table():
    df=Market().client.scan().frame
    assert filter_table(df,"[").empty
    assert len(filter_table(df,"btc"))==1
    table=grid_data(df)
    assert list(table.columns[:9])==TABLE_COLUMNS
    assert all(pd.api.types.is_numeric_dtype(table[c]) for c in TABLE_COLUMNS[1:])
    assert format_price(.00001234,"0.00000001")=="$0.00001234"
    assert format_price(float('nan'),"0.01")=="—"
    options=grid_options("5m")
    assert [d["field"] for d in options["columnDefs"] if not d.get("hide")]==TABLE_COLUMNS
    state={"sort":{"sortModel":[{"colId":"volume_24h","sort":"asc"}]},"rowSelection":["BTCUSDT"]}
    assert grid_options("1h",state)["initialState"]==dict(partialColumnState=True,**state)
