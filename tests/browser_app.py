"""Offline browser QA entry point. Production app.py never imports this file."""
import sys
import runpy
import time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import streamlit as st
import services
from tests.fixtures import Market
from history_store import HistoryStore


@st.cache_resource
def fixture():
    market = Market(n=25)
    fetch = market.fetch
    def current(endpoint, params):
        market.now = time.time()
        return fetch(endpoint, params)
    market.client.transport = current
    market.client.clock = time.time
    directory = ROOT / 'test-results'
    directory.mkdir(exist_ok=True)
    return market.client, HistoryStore(directory / 'browser.sqlite3')


services.get_client = lambda: fixture()[0]
services.get_history = lambda: fixture()[1]
runpy.run_path(str(ROOT / 'app.py'), run_name='__main__')
st.caption('Offline verification fixture · synthetic prices')
