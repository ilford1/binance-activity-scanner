"""Market Relative controls, independent grid state, and lazy rendering."""
import time
import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, DataReturnMode, JsCode
from st_aggrid.shared import StAggridTheme
from binance_client import finite
from grid_view import grid_options
from market_relative import GAPS, compare_market
from movers import RELATIVE_WINDOWS
from pair_details import render_details
from ui_helpers import price_decimals, utc_time
from window_data import get_window_scan
import services

MODES=["Relative strength","Relative weakness"]


def relative_settings(params):
    result=dict(mr_window=params.get('mr_window') if params.get('mr_window') in RELATIVE_WINDOWS else '4h',
                mr_mode=params.get('mr_mode') if params.get('mr_mode') in MODES else MODES[0],
                mr_qualified=params.get('mr_qualified','1')!='0')
    for key,default,minimum,maximum in [('mr_percentile',80.,50.,100.)]+[(f'mr_gap_{w}',v,0.,1000.) for w,v in GAPS.items()]:
        value=finite(params.get(key))
        result[key]=value if value is not None and minimum<=value<=maximum else default
    return result


def relative_url():
    return {key:('1' if value else '0') if key=='mr_qualified' else str(value)
            for key in relative_settings({}) for value in [st.session_state[key]]}


def relative_controls():
    st.selectbox('Window',list(RELATIVE_WINDOWS),key='mr_window')
    st.selectbox('Compare',MODES,key='mr_mode')
    st.toggle('Qualified only',key='mr_qualified')
    with st.expander('Relative thresholds'):
        st.number_input('Strength percentile',min_value=50.,max_value=100.,step=1.,key='mr_percentile',
                        help='Weakness uses the symmetric cutoff: 100 minus this value.')
        for window in GAPS:
            st.number_input(f'Minimum {window} gap · pp',min_value=0.,max_value=1000.,step=.25,key='mr_gap_'+window)
        st.caption('The gap must pass against both BTC and the peer median. No positive-return or participation requirement.')


def relative_grid(df, mode, saved=None):
    options=grid_options('4h',saved)
    original={c['field']:c for c in options['columnDefs']}
    fields=['symbol','price','change','vs_btc','vs_alts','alt_percentile','relative_volume','spread_pct','volume_24h']
    definitions=[]
    labels={'change':'Return %','vs_btc':'vs BTC (pp)','vs_alts':'vs Alts (pp)','alt_percentile':'Alt percentile'}
    color=JsCode("function(p){return {color:p.value>0?'#68c4a3':p.value<0?'#e88d92':'#a4aebd'};}")
    for field in fields:
        col=original.get(field,dict(field=field,type='numericColumn',minWidth=110,flex=1)).copy()
        col['tooltipField']='status'
        if field in labels: col['headerName']=labels[field]
        if field in ('vs_btc','vs_alts','alt_percentile'):
            col['valueFormatter']=JsCode("function(p){return p.value==null||!Number.isFinite(p.value)?'—':"+("(p.value>0?'+':'')+" if field!='alt_percentile' else '')+"p.value.toFixed(2);}")
        if field in ('vs_btc','vs_alts'): col['cellStyle']=color
        if field=='change': col['headerTooltip']='Actual completed-window price return, independent of relative strength.'
        if field=='vs_btc': col['headerTooltip']='Pair return minus BTC return, in percentage points.'
        if field=='vs_alts': col['headerTooltip']='Pair return minus median eligible peer return, excluding this pair. Percentage points.'
        if field=='alt_percentile': col['headerTooltip']='0–100 return percentile among eligible altcoins, with average ranks for ties.'
        if field=='relative_volume': col['headerTooltip']='Quote volume / median of 14 prior equal windows. Informational; not required for qualification.'
        if field=='volume_24h': col['minWidth']=120
        definitions.append(col)
    options['columnDefs']=definitions+[dict(field=k,hide=True) for k in ('status','price_decimals')]
    if not saved or 'sort' not in saved:
        order='desc' if mode==MODES[0] else 'asc'
        options['initialState']['sort']={'sortModel':[{'colId':'vs_alts','sort':order},{'colId':'vs_btc','sort':order},{'colId':'volume_24h','sort':'desc'}]}
    data=df[fields+['status']].copy()
    data['price_decimals']=df.tick_size.map(price_decimals)
    return data,options


def render_relative():
    s=st.session_state
    scan=get_window_scan()
    st.caption('Compare each altcoin with BTC and its peers. A negative return can still show relative strength.')
    if scan.errors: st.warning('Some market sources failed; stale data cannot qualify.')
    result=compare_market(scan.frame,s.mr_window,scan.boundary,time.time(),s.min_volume,
                          s.mr_percentile,s['mr_gap_'+s.mr_window],s.mr_mode)
    for warning in result.warnings: st.warning(warning)
    a,b,c,d=st.columns(4)
    a.metric('BTC return','—' if result.btc_return is None else f'{result.btc_return:+.2f}%')
    b.metric('Alt median','—' if result.alt_median is None else f'{result.alt_median:+.2f}%')
    c.metric('Eligible alts',f'{result.eligible_count} / {result.universe_count}')
    d.metric('Return coverage',f'{result.coverage:.0%}')
    ticker=scan.sources.get('tickers')
    st.caption(f'Prices fetched {utc_time(ticker.fetched_at if ticker else None)} · Window ends {utc_time(scan.boundary/1000 if scan.boundary else None)} · Rolling {s.mr_window}')
    st.caption('Rows use a peer median excluding that pair; the headline median includes all eligible alts. Search never changes either benchmark.')
    df=result.frame
    if df.empty:
        st.info(scan.empty_reason or 'No altcoins meet the daily volume filter.')
        return
    mask=df.symbol.str.contains(s.search.strip(),case=False,regex=False,na=False)
    if s.mr_qualified: mask &= df.qualified
    view=df.loc[mask]
    st.caption(f'{int(df.qualified.sum())} qualified · Showing {len(view)} of {len(df)} altcoins')
    if view.empty:
        st.info('No pairs match. Turn off Qualified only to inspect the return gaps and failure reasons.')
    else:
        # Preserve manual sorting independently in each comparison direction.
        state_key='relative_table_state_'+s.mr_mode
        data,options=relative_grid(view,s.mr_mode,s.get(state_key))
        saved=options['initialState']
        if s.get('relative_selected_symbol'): saved['rowSelection']=[s.relative_selected_symbol]
        response=AgGrid(data,gridOptions=options,key='relative_table_'+s.mr_mode,height=min(580,len(view)*38+110),
                        theme=StAggridTheme('quartz').withParams(backgroundColor='#151a22',foregroundColor='#e0e5ed',headerBackgroundColor='#1c2430',headerTextColor='#aeb9c8',borderColor='#2a3442',rowHoverColor='#202b38',selectedRowBackgroundColor='#2c3340',accentColor='#dcb575',fontSize=13,spacing=5),
                        update_on=['selectionChanged','sortChanged'],data_return_mode=DataReturnMode.AS_INPUT,
                        allow_unsafe_jscode=True,enable_enterprise_modules=False,server_sync_strategy='server_wins')
        if response.grid_state: s[state_key]=response.grid_state
        selected=response.selected_rows
        if isinstance(selected,pd.DataFrame) and not selected.empty: s.relative_selected_symbol=selected.iloc[0]['symbol']
    render_details(services.get_client(),df,'relative_')
    with st.expander('Data coverage'):
        st.dataframe(df.groupby('status').size().rename('Pairs').reset_index(),hide_index=True)
        for name,source in scan.sources.items():
            st.caption(f'{name}: {utc_time(source.fetched_at)}'+(f' · {source.error}' if source.error else ''))
    st.caption('Relative performance is descriptive. These thresholds have not been validated as profitable trading signals.')
