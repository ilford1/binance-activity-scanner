"""One session snapshot shared by the two rolling-window screens."""
import time
import streamlit as st
import services
from ui_helpers import REFRESH


def get_window_scan():
    s=st.session_state
    interval=REFRESH[s.refresh_rate]
    force=s.pop("force_refresh",False)
    due=interval is not None and time.time()-s.get("movers_started",0)>=interval
    if force or due or "movers_scan" not in s or s.get("movers_minv")!=s.min_volume:
        s.movers_started=time.time()
        with st.spinner("Loading completed 15-minute histories…"):
            s.movers_scan=services.get_client().scan_movers(s.min_volume,force=force)
        s.movers_minv=s.min_volume
    return s.movers_scan
