"""Nine visible numeric columns; hidden metadata powers status and formatting."""
from st_aggrid import JsCode
from ui_helpers import TABLE_COLUMNS, price_decimals


def grid_data(df):
    result = df[TABLE_COLUMNS + ["status", "active", "tick_size", "quote_at"]].copy()
    result["price_decimals"] = result.tick_size.map(price_decimals)
    return result.drop(columns="tick_size")


def grid_options(timeframe: str, saved_state: dict | None = None) -> dict:
    null_guard = "if (p.value == null || !Number.isFinite(p.value)) return '—';"
    def fmt(body):
        return JsCode("function(p){" + null_guard + body + "}")
    headers = ["Symbol", "Price", "Activity", f"Change {timeframe}",
               "Relative volume", "Relative trades", "Taker buy %", "Spread %", "Volume 24h"]
    formats = {
        "price": fmt("return '$'+p.value.toLocaleString('en-US', {minimumFractionDigits:p.data.price_decimals,maximumFractionDigits:p.data.price_decimals});"),
        "activity": JsCode("function(p){if(p.value==null || !Number.isFinite(p.value)) return p.data.status; return p.value.toFixed(0)+(p.data.active?' · Active':'');}"),
        "change": fmt("return (p.value>0?'+':'')+p.value.toFixed(2)+'%';"),
        "relative_volume": fmt("return p.value.toFixed(2)+'×';"),
        "relative_trades": fmt("return p.value.toFixed(2)+'×';"),
        "buy_pct": fmt("return p.value.toFixed(1)+'%';"),
        "spread_pct": fmt("return p.value.toFixed(3)+'%';"),
        "volume_24h": fmt("const a=Math.abs(p.value); for(const [u,k] of [[1e12,'T'],[1e9,'B'],[1e6,'M'],[1e3,'K']]){if(a>=u)return '$'+(p.value/u).toFixed(2)+k;} return '$'+p.value.toFixed(0);"),
    }
    tooltips = {
        "activity": "0–100 rank: 40% relative volume, 30% relative trades, 30% absolute movement. Active requires ≥80, volume ≥1.5× and trades ≥1.25×. Unranked rows show their reason.",
        "change": f"Open-to-close move of the last completed {timeframe} candle.",
        "relative_volume": "Completed candle quote volume / median of the preceding 60 candles.",
        "relative_trades": "Completed candle trades / median of the preceding 60 candles.",
        "buy_pct": "Taker-buy share of quote volume in the completed candle; 50% means balanced flow.",
        "spread_pct": "Current (ask − bid) / midpoint. Ranking requires ≤0.10%. Quotes older than 120s are excluded.",
        "price": "Latest ticker price, formatted to the contract tick size.",
    }
    defs = []
    for field, header in zip(TABLE_COLUMNS, headers):
        col = dict(field=field, headerName=header, minWidth=92, flex=1)
        if field != "symbol":
            col["type"] = "numericColumn"
        if field == "symbol":
            col.update(pinned="left", minWidth=130, width=130, flex=0)
        if field == "activity":
            col.update(minWidth=140, tooltipField="status", cellStyle=JsCode(
                "function(p){return p.data.active?{color:'#e8bc72',fontWeight:'600'}:{};}"))
        if field == "change":
            col["cellStyle"] = JsCode("function(p){return {color:p.value>0?'#68c4a3':p.value<0?'#e88d92':'#a4aebd'};}")
        if field in formats:
            col["valueFormatter"] = formats[field]
        if field in tooltips:
            col["headerTooltip"] = tooltips[field]
        defs.append(col)
    defs += [dict(field=k, hide=True) for k in ("status", "active", "quote_at", "price_decimals")]
    initial = {"partialColumnState": True, "sort": {"sortModel": [
        {"colId":"activity","sort":"desc"}, {"colId":"volume_24h","sort":"desc"}]}}
    if saved_state:
        initial.update({k: saved_state[k] for k in ("sort", "rowSelection") if k in saved_state})
    return dict(columnDefs=defs, defaultColDef=dict(sortable=True,resizable=True,
                wrapHeaderText=True,autoHeaderHeight=True),
                rowSelection=dict(mode="singleRow",checkboxes=False,enableClickSelection=True),
                getRowId=JsCode("function(p){return p.data.symbol;}"),
                initialState=initial, rowHeight=38, headerHeight=52,
                animateRows=False, tooltipShowDelay=200, suppressMovableColumns=True)
