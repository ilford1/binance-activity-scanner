"""Process-shared clients; market data caches live below the Streamlit UI."""
import streamlit as st
from binance_client import BinanceClient
from history_store import HistoryStore, DB_PATH


@st.cache_resource
def get_client():
    return BinanceClient()


@st.cache_resource
def get_history(path=DB_PATH):
    return HistoryStore(path)
