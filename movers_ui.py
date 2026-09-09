"""Lazy Strong Movers view and validated shareable settings."""
import time
import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, DataReturnMode, JsCode
from st_aggrid.shared import StAggridTheme
from grid_view import grid_options
from movers import WINDOWS, qualify_movers
from binance_client import finite
import services
from pair_details import render_details
from window_data import get_window_scan
from ui_helpers import REFRESH, utc_time, price_decimals

LIMITS = {"ret_4h": (2.,1000.), "ret_8h": (3.,1000.), "ret_1d": (5.,1000.),
          "mover_rvol": (1.25,1000.), "mover_eff": (.35,1.), "mover_hold": (20.,100.), "mover_spread": (.10,100.)}


def mover_settings(params):
    result = dict(scanner_tab={"movers":"Strong Movers","relative":"Market Relative"}.get(params.get("tab"),"Activity"),
                  mover_window=params.get("window") if params.get("window") in WINDOWS else "4h",
                  mover_direction=params.get("direction") if params.get("direction") in ("Both","Up","Down") else "Both",
                  qualified_only=params.get("qualified", "1")!="0")
    for key,(default,maximum) in LIMITS.items():
        value=finite(params.get(key))
        result[key]=value if value is not None and 0<=value<=maximum else default
    return result


def mover_url():
    s=st.session_state
    return dict(tab={"Strong Movers":"movers","Market Relative":"relative"}.get(s.scanner_tab,"activity"), window=s.mover_window,
                direction=s.mover_direction,qualified="1" if s.qualified_only else "0",
                **{k:str(s[k]) for k in LIMITS})


def mover_controls():
    st.selectbox("Window", list(WINDOWS),key="mover_window")
    st.selectbox("Direction",["Both","Up","Down"],key="mover_direction")
    st.toggle("Qualified only",key="qualified_only")
    with st.expander("Qualification thresholds"):
        labels={"ret_4h":"Minimum 4h return %", "ret_8h":"Minimum 8h return %", "ret_1d":"Minimum 1d return %",
                "mover_rvol":"Minimum relative volume", "mover_eff":"Minimum efficiency",
                "mover_hold":"Outer range %", "mover_spread":"Maximum spread %"}
        for key,(_,maximum) in LIMITS.items():
            st.number_input(labels[key],min_value=0.,max_value=maximum,step=.05 if maximum<=1 else .25,key=key)
        st.caption("Screening defaults, not validated trading signals. Both the completed close and current price must hold the chosen outer range.")


def mover_grid(df, window, saved=None):
    options=grid_options(window,saved)
    original={c['field']:c for c in options['columnDefs']}
    fields=["symbol","price","change","relative_volume","efficiency","distance_pct","spread_pct","volume_24h"]
    columns=[]
    for field in fields:
        col=original.get(field,dict(field=field,type="numericColumn",minWidth=110,flex=1)).copy()
        col['tooltipField']='status'
        if field=='change':
            col.update(headerName="Return %", minWidth=110,headerTooltip="Rolling window return; default sorting uses absolute magnitude.", comparator=JsCode("function(a,b){if(a==null)return b==null?0:-1;if(b==null)return 1;return Math.abs(a)-Math.abs(b);}"))
        if field=='volume_24h': col['minWidth']=120
        if field=='relative_volume': col['headerTooltip']='Window quote volume / median of 14 preceding non-overlapping equal windows.'
        if field in ('efficiency','distance_pct'):
            col.update(headerName='Efficiency' if field=='efficiency' else 'Distance from high/low %',
                       valueFormatter=JsCode("function(p){return p.value==null||!Number.isFinite(p.value)?'—':p.value.toFixed(2)"+("+'%'" if field=='distance_pct' else "")+";}"),
                       headerTooltip='Net movement / total close-to-close path.' if field=='efficiency' else 'Current price distance from the favorable window extreme; zero beyond it. Qualification uses the full high-low range.')
        columns.append(col)
    options['columnDefs']=columns+[dict(field=k,hide=True) for k in ('status','price_decimals')]
    if not saved or 'sort' not in saved:
        options['initialState']['sort']={'sortModel':[{'colId':'change','sort':'desc'},{'colId':'volume_24h','sort':'desc'}]}
    data=df[fields+['status']].copy()
    data['price_decimals']=df.tick_size.map(price_decimals)
    return data,options


def render_movers():
    s=st.session_state
    client=services.get_client()
    scan=get_window_scan()
    st.caption('Sustained moves holding near their highs or lows, supported by participation.')
    if scan.errors: st.warning('Some sources failed. Retained data are stale and cannot qualify.')
    if scan.frame.empty or not scan.frame.volume_24h.ge(s.min_volume).any():
        st.info(scan.empty_reason or 'No market data available.')
        return
    df=scan.frame.loc[scan.frame.volume_24h.ge(s.min_volume)].copy()
    age=time.time()-df.observed_at
    quote_age=time.time()-df.quote_at
    df.loc[~age.between(-1,120)|~quote_age.between(-1,120),'status']='Stale observation'
    df=qualify_movers(df,s.mover_window,s['ret_'+s.mover_window],s.mover_rvol,s.mover_eff,s.mover_hold,s.mover_spread)
    mask=df.symbol.str.contains(s.search.strip(),case=False,regex=False,na=False)
    if s.qualified_only: mask &= df.qualified
    if s.mover_direction!='Both': mask &= df.direction.eq(1 if s.mover_direction=='Up' else -1)
    view=df.loc[mask]
    a,b,c=st.columns(3)
    a.metric('Pairs',len(df)); b.metric('Qualified',int(df.qualified.sum())); c.metric('Showing',len(view))
    ticker=scan.sources.get('tickers')
    st.caption(f'Prices fetched {utc_time(ticker.fetched_at if ticker else None)} · Window ends {utc_time(scan.boundary/1000 if scan.boundary else None)} · Rolling {s.mover_window} · Completed 15m candles')
    if view.empty:
        st.info('No pairs match. Turn off Qualified only to inspect qualification reasons, or adjust the filters.')
    else:
        data,options=mover_grid(view,s.mover_window,s.get('movers_table_state'))
        response=AgGrid(data,gridOptions=options,key='movers_table',height=min(580,len(view)*38+110),
                        theme=StAggridTheme('quartz').withParams(backgroundColor='#151a22',foregroundColor='#e0e5ed',headerBackgroundColor='#1c2430',headerTextColor='#aeb9c8',borderColor='#2a3442',rowHoverColor='#202b38',selectedRowBackgroundColor='#2c3340',accentColor='#dcb575',fontSize=13,spacing=5),update_on=['selectionChanged','sortChanged'],data_return_mode=DataReturnMode.AS_INPUT,
                        allow_unsafe_jscode=True,enable_enterprise_modules=False,server_sync_strategy='server_wins')
        if response.grid_state: s.movers_table_state=response.grid_state
        selected=response.selected_rows
        if isinstance(selected,pd.DataFrame) and not selected.empty: s.movers_selected_symbol=selected.iloc[0]['symbol']
    st.caption('Hover a row for qualification or failure reasons. Return sorting uses absolute magnitude; color shows direction.')
    render_details(client,df,'movers_')
    with st.expander('Data coverage'):
        st.dataframe(df.groupby('status').size().rename('Pairs').reset_index(),hide_index=True)
        for name,source in scan.sources.items():
            st.caption(f'{name}: {utc_time(source.fetched_at)}'+(f' · {source.error}' if source.error else ''))
