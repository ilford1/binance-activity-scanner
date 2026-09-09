"""Lazy shared derivatives panel."""
import streamlit as st
from binance_client import finite, oi_hour_change
from ui_helpers import format_price, utc_time

def render_details(client, df, prefix=""):
    details = st.expander("Selected pair details", key=prefix+"pair_details", on_change="rerun")
    if details.open:
        with details:
            sym = st.session_state.get(prefix+"selected_symbol")
            if not sym or sym not in set(df.symbol):
                st.info("Select a pair in the table to load its details.")
            else:
                row = df.loc[df.symbol.eq(sym)].iloc[0]
                st.subheader(sym)
                st.caption(f"{row.status} · Price {format_price(row.price,row.tick_size)} · Quote observed {utc_time(row.quote_at)}")
                with st.spinner("Loading this pair’s funding and open interest…"):
                    data = client.details(sym)
                for name, source in data.items():
                    if source.stale or source.error:
                        st.warning(f"{name.title()} unavailable or stale: {source.error}")
                funding = data["funding"]
                rate = finite((funding.data or {}).get("lastFundingRate"))
                oi = data["oi"]
                change, begin, end = oi_hour_change(oi.data or [])
                x,y = st.columns(2)
                x.metric("Current funding", "—" if rate is None else f"{rate*100:.4f}%")
                x.caption(f"Fetched {utc_time(funding.fetched_at)}")
                y.metric("OI change · 1 hour", "—" if change is None else f"{change:+.2f}%")
                y.caption(f"{utc_time(begin)} → {utc_time(end)}" if end else "A complete one-hour OI window is unavailable.")
                events = sorted(data["settlements"].data or [], key=lambda r:r.get("fundingTime",0))[-2:]
                st.caption("Last two funding settlements")
                for event in events:
                    value = finite(event.get("fundingRate"))
                    timestamp = finite(event.get("fundingTime"))
                    if value is not None and timestamp is not None:
                        st.write(f"{utc_time(timestamp/1000)} · {value*100:+.4f}%")
                if len(events) < 2:
                    st.caption("Fewer than two settlements are available; no drift is inferred.")
