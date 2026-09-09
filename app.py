"""Compact Binance USDT perpetual activity screener."""
import time

import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, DataReturnMode
from st_aggrid.shared import StAggridTheme

from binance_client import INTERVALS, finite, oi_hour_change
from grid_view import grid_data, grid_options
from scoring import score_activity
from pair_details import render_details
from movers_ui import render_movers, mover_controls, mover_settings, mover_url
from services import get_client, get_history
from relative_ui import relative_settings, relative_url, relative_controls, render_relative
from ui_helpers import REFRESH, filter_table, format_price, settings_from_url, utc_time

st.set_page_config(page_title="Market scanner", page_icon=":material/query_stats:", layout="wide")

if "activity_settings_v2" not in st.session_state:
    st.session_state.update(settings_from_url(st.query_params))
    st.session_state.activity_settings_v2 = True

if "mover_settings_v1" not in st.session_state:
    st.session_state.update(mover_settings(st.query_params))
    st.session_state.initial_scanner_tab = st.session_state.scanner_tab
    st.session_state.mover_settings_v1 = True
if "relative_settings_v1" not in st.session_state:
    st.session_state.update(relative_settings(st.query_params))
    st.session_state.relative_settings_v1 = True
for name in relative_settings({}):
    st.session_state[name] = st.session_state[name]
# Keep conditional widget values when switching tabs.
for name in ("timeframe", "active_only", "mover_window", "mover_direction", "qualified_only", "ret_4h", "ret_8h", "ret_1d", "mover_rvol", "mover_eff", "mover_hold", "mover_spread"):
    if name in st.session_state:
        st.session_state[name] = st.session_state[name]
st.title("Market scanner")
activity_tab, movers_tab, relative_tab = st.tabs(["Activity", "Strong Movers", "Market Relative"], key="scanner_tab", default=st.session_state.initial_scanner_tab, on_change="rerun")
with st.sidebar:
    st.subheader("Scan settings")
    st.number_input("Minimum volume · USDT", min_value=0, max_value=10**15,
                    step=1_000_000, key="min_volume", help="Minimum 24-hour quote volume. Default: $10 million.")
    if activity_tab.open:
        st.selectbox("Timeframe", list(INTERVALS), key="timeframe",
                     help="Movement and activity use the last completed candle.")
    elif movers_tab.open:
        mover_controls()
    else:
        relative_controls()
    st.text_input("Search symbol", placeholder="BTC, ETH…", max_chars=100, key="search")
    if activity_tab.open:
        st.toggle("Active only", key="active_only")
    st.divider()
    st.selectbox("Refresh", list(REFRESH), key="refresh_rate")
    if st.button("Refresh now", type="primary", width="stretch"):
        st.session_state.force_refresh = True
    st.caption("Binance · USDT perpetuals\n\nCompleted candles · current spread")

st.query_params.from_dict(dict(tf=st.session_state.timeframe, minv=str(st.session_state.min_volume),
                               q=st.session_state.search, rf=st.session_state.refresh_rate,
                               active="1" if st.session_state.active_only else "0", **mover_url(), **relative_url()))



@st.fragment(run_every=REFRESH[st.session_state.refresh_rate])
def scanner():
    client, history = get_client(), get_history()
    tf, minv = st.session_state.timeframe, st.session_state.min_volume
    interval = REFRESH[st.session_state.refresh_rate]
    force = st.session_state.pop("force_refresh", False)
    due = interval is not None and time.time() - st.session_state.get("scan_started", 0) >= interval
    needs_scan = force or due or st.session_state.get("scan_config") != (tf,minv) or "scan" not in st.session_state
    if needs_scan:
        started = time.time()
        with st.spinner("Updating market observations…"):
            scan = client.scan(minv, tf, force=force)
        st.session_state.scan = scan
        st.session_state.scan_config = (tf,minv)
        st.session_state.scan_started = started
        st.session_state.history_error = history.record(scan.frame, tf, minv)["error"]
    scan = st.session_state.scan
    if scan.errors:
        st.warning("Some market data is unavailable. Retained observations are marked stale and excluded from ranking.")
    if st.session_state.get("history_error"):
        st.warning(st.session_state.history_error)
    if scan.frame.empty:
        st.info(scan.empty_reason or "No market data available.")
        if scan.errors:
            st.caption(" · ".join(scan.errors))
        return
    # UI-only reruns reuse the scan, but never present aged quotes as eligible.
    df = scan.frame.copy()
    now = time.time()
    stale = (now - df.observed_at > 120) | (now - df.quote_at > 120)
    df.loc[stale, "status"] = "Stale observation"
    df.loc[df.status.eq("Active"), "status"] = "Ready"
    df = score_activity(df)
    view = filter_table(df, st.session_state.search, st.session_state.active_only)
    a,b,c,d = st.columns(4)
    a.metric("Pairs", len(df))
    b.metric("Ranked", int(df.activity.notna().sum()))
    c.metric("Active", int(df.active.sum()))
    d.metric("Unavailable / wide spread", int(df.activity.isna().sum()))
    ticker_time = scan.sources.get("tickers")
    stamp = utc_time(ticker_time.fetched_at if ticker_time else None)
    st.caption(f"Prices fetched {stamp} · Candle closes {utc_time(scan.boundary/1000 if scan.boundary else None)} · Showing {len(view)} of {len(df)} pairs")
    if view.empty:
        st.info("No pairs match these display filters. Try a different search or turn off Active only.")
    else:
        response = AgGrid(grid_data(view), gridOptions=grid_options(tf, st.session_state.get("table_state")),
                          height=min(580, len(view)*38+60),
                          theme=StAggridTheme("quartz").withParams(
                              backgroundColor="#151a22", foregroundColor="#e0e5ed",
                              headerBackgroundColor="#1c2430", headerTextColor="#aeb9c8",
                              borderColor="#2a3442", rowHoverColor="#202b38",
                              selectedRowBackgroundColor="#2c3340", accentColor="#dcb575",
                              fontSize=13, spacing=5), key="activity_table",
                          update_on=["selectionChanged", "sortChanged"],
                          data_return_mode=DataReturnMode.AS_INPUT, allow_unsafe_jscode=True,
                          enable_enterprise_modules=False, server_sync_strategy="server_wins")
        if response.grid_state:
            st.session_state.table_state = response.grid_state
        selected = response.selected_rows
        if isinstance(selected, pd.DataFrame) and not selected.empty:
            st.session_state.selected_symbol = selected.iloc[0]["symbol"]
    st.caption("Active = score ≥80, volume ≥1.5× and trades ≥1.25× baseline. Ranking requires spread ≤0.10%. Select a row for details.")
    render_details(client, df)
    stats = st.expander("Observed follow-through · last 24 hours", key="history_details", on_change="rerun")
    if stats.open:
        with stats:
            result = history.summary(tf, minv)
            if result["error"]:
                st.warning(result["error"])
            st.caption("Actual price returns around one hour after activity was detected. Matches the current timeframe and volume setting. Flat outcomes count in the continuation-rate denominator. These observations do not include trading costs.")
            for col, (label, counts) in zip(st.columns(2), result["directions"].items()):
                with col:
                    rate = counts["rate"]
                    st.metric(label, "—" if rate is None else f"{rate:.0%} continuation")
                    st.caption(f"{counts['continuation']} continuation · {counts['reversal']} reversal · {counts['flat']} flat · {counts['pending']} pending · {counts['unmeasurable']} unmeasurable")
                    ret = counts["mean_directional_return"]
                    if ret is not None:
                        st.caption(f"Mean return in observed direction: {ret:+.3f}%")
            if result["events"]:
                events = pd.DataFrame(result["events"])
                events["entry_at"] = events.entry_at.map(utc_time)
                events["forward_at"] = events.forward_at.map(utc_time)
                st.dataframe(events, hide_index=True)
            else:
                st.info("No activity events for these settings yet.")
    diagnostics = st.expander("Data coverage", key="coverage_details", on_change="rerun")
    if diagnostics.open:
        with diagnostics:
            st.dataframe(df.groupby("status").size().rename("Pairs").reset_index(), hide_index=True)
            for name, src in scan.sources.items():
                st.caption(f"{name.title()}: {utc_time(src.fetched_at)}" + (f" · {src.error}" if src.error else ""))
    st.caption("Activity is relative to this scan’s eligible pairs. Completed-candle signals and current quotes have different observation times. Scores describe activity, not expected profit.")


if activity_tab.open:
    with activity_tab:
        scanner()
elif movers_tab.open:
    with movers_tab:
        st.fragment(run_every=REFRESH[st.session_state.refresh_rate])(render_movers)()

if relative_tab.open:
    with relative_tab:
        st.fragment(run_every=REFRESH[st.session_state.refresh_rate])(render_relative)()
