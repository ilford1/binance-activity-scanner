from streamlit.testing.v1 import AppTest
from pathlib import Path
import services
from history_store import HistoryStore
from tests.fixtures import Market


def test_offline_app_controls_and_no_derivatives(monkeypatch,tmp_path):
    m=Market(); store=HistoryStore(tmp_path/'app.sqlite3')
    monkeypatch.setattr(services,'get_client',lambda:m.client)
    monkeypatch.setattr(services,'get_history',lambda:store)
    at=AppTest.from_file(Path(__file__).resolve().parents[1]/'app.py',default_timeout=20).run()
    assert not at.exception
    assert at.number_input[0].value==10_000_000
    assert at.selectbox[0].value=='5m'
    assert at.selectbox[1].value=='Manual'
    calls=m.calls.copy()
    at.text_input[0].set_value('[').run()
    assert not at.exception
    assert any('No pairs match' in x.value for x in at.info)
    assert m.calls==calls
    at.text_input[0].set_value('').run()
    at.toggle[0].set_value(True).run()
    assert not at.exception and m.calls==calls
    at.selectbox[0].set_value('1m').run()
    assert not at.exception
    assert m.calls['/fapi/v1/klines']==2*m.n
    assert m.calls['/fapi/v1/fundingRate']==0
    assert m.calls['/futures/data/openInterestHist']==0


def test_app_invalid_url_and_empty_filter(monkeypatch,tmp_path):
    m=Market()
    monkeypatch.setattr(services,'get_client',lambda:m.client)
    monkeypatch.setattr(services,'get_history',lambda:HistoryStore(tmp_path/'empty.sqlite3'))
    at=AppTest.from_file(Path(__file__).resolve().parents[1]/'app.py',default_timeout=20)
    at.query_params={'minv':'inf','tf':'invalid','m':'quoteVolume','q':'['}
    at.run()
    assert not at.exception and at.number_input[0].value==10_000_000
    at.number_input[0].set_value(10**15).run()
    assert not at.exception
    assert any('No trading pairs' in x.value for x in at.info)
