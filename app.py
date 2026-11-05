import streamlit as st
import pandas as pd
import time
import os
import concurrent.futures
from datetime import timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Load Streamlit Cloud secrets into environment variables if available
try:
    if "DHAN_CLIENT_ID" in st.secrets:
        os.environ["DHAN_CLIENT_ID"] = str(st.secrets["DHAN_CLIENT_ID"]).strip('"\'')
    if "DHAN_ACCESS_TOKEN" in st.secrets:
        os.environ["DHAN_ACCESS_TOKEN"] = str(st.secrets["DHAN_ACCESS_TOKEN"]).strip('"\'')
except Exception:
    pass

if os.environ.get("DHAN_CLIENT_ID"):
    os.environ["DHAN_CLIENT_ID"] = os.environ["DHAN_CLIENT_ID"].strip('"\'')
if os.environ.get("DHAN_ACCESS_TOKEN"):
    os.environ["DHAN_ACCESS_TOKEN"] = os.environ["DHAN_ACCESS_TOKEN"].strip('"\'')

import nse_data
import screener
try:
    from streamlit_autorefresh import st_autorefresh
    has_autorefresh = True
except ImportError:
    has_autorefresh = False

st.set_page_config(
    page_title="Nifty 500 Screener & Live ORB Dashboard",
    page_icon="📈",
    layout="wide"
)

st.sidebar.header("📱 Select App")
app_choice = st.sidebar.radio(
    "Choose Screener / Dashboard",
    [
        "Live ORB & Breakdown Dashboard",
        "Fundamental & Volume Screener (P/E < 20, Vol > 2x, RSI > 50)"
    ]
)

st.sidebar.header("Dashboard Controls")
auto_refresh = st.sidebar.checkbox("Auto-refresh active (every 5 mins)", value=True)
if auto_refresh:
    if has_autorefresh:
        st_autorefresh(interval=300 * 1000, key="datarefresh")
    else:
        st.markdown('<meta http-equiv="refresh" content="300">', unsafe_allow_html=True)

if st.sidebar.button("Clear Cache & Rescan Now", type="primary"):
    st.cache_data.clear()
    st.rerun()

st.write(f"**Last Updated:** {nse_data.now_ist().strftime('%Y-%m-%d %H:%M:%S')} IST")

SECTOR_MAP = {
    "TCS": "IT", "INFY": "IT", "HCLTECH": "IT", "TECHM": "IT", "WIPRO": "IT", "LTIM": "IT",
    "HDFCBANK": "Banking & Financials", "ICICIBANK": "Banking & Financials", "AXISBANK": "Banking & Financials",
    "KOTAKBANK": "Banking & Financials", "SBIN": "Banking & Financials", "BAJFINANCE": "Banking & Financials",
    "BAJAJFINSV": "Banking & Financials", "JIOFIN": "Banking & Financials", "SHRIRAMFIN": "Banking & Financials",
    "SBILIFE": "Banking & Financials", "HDFCLIFE": "Banking & Financials",
    "RELIANCE": "Energy & Oil/Gas", "ONGC": "Energy & Oil/Gas", "NTPC": "Energy & Oil/Gas",
    "POWERGRID": "Energy & Oil/Gas", "BPCL": "Energy & Oil/Gas", "COALINDIA": "Energy & Oil/Gas",
    "MARUTI": "Auto", "TATAMOTORS": "Auto", "M&M": "Auto", "BAJAJ-AUTO": "Auto", "EICHERMOT": "Auto", "HEROMOTOCO": "Auto",
    "ITC": "FMCG", "HINDUNILVR": "FMCG", "NESTLEIND": "FMCG", "TATACONSUM": "FMCG", "BRITANNIA": "FMCG",
    "TATASTEEL": "Metals", "JSWSTEEL": "Metals", "HINDALCO": "Metals", "VEDL": "Metals",
    "SUNPHARMA": "Pharma", "DRREDDY": "Pharma", "CIPLA": "Pharma", "DIVISLAB": "Pharma", "APOLLOHOSP": "Pharma", "LUPIN": "Pharma",
    "LT": "Industrials & Infra", "ULTRACEMCO": "Cement & Materials", "GRASIM": "Cement & Materials",
    "ADANIENT": "Diversified / Infra", "ADANIPORTS": "Infrastructure", "TRENT": "Consumer & Retail",
    "TITAN": "Consumer & Retail", "INDIGO": "Aviation & Services", "MAXHEALTH": "Healthcare", "BSE": "Financial Services"
}

def calculate_indicators(bars):
    closes = pd.Series([b["c"] for b in bars])
    vols = pd.Series([b["v"] for b in bars])

    delta = closes.diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=14, min_periods=1).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=14, min_periods=1).mean()
    rs = gain / (loss + 1e-9)
    rsi = 100 - (100 / (1 + rs))
    current_rsi = rsi.iloc[-1] if not rsi.empty else 50.0

    ema12 = closes.ewm(span=12, adjust=False).mean()
    ema26 = closes.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = macd_line - signal_line
    current_hist = macd_hist.iloc[-1] if not macd_hist.empty else 0.0
    prev_hist = macd_hist.iloc[-2] if len(macd_hist) > 1 else 0.0

    return current_rsi, current_hist, prev_hist

def compute_confidence(side, vol_ratio, rsi, macd_hist, prev_hist, bars, cmp_val):
    score = 0
    if vol_ratio >= 2.0:
        score += 25
    elif vol_ratio >= 1.5:
        score += 18
    elif vol_ratio >= 1.0:
        score += 10
    else:
        score += 3

    if side == "BUY (Long)":
        if 55 <= rsi <= 70:
            score += 20
        elif 50 <= rsi < 55:
            score += 15
        elif rsi > 70:
            score += 10
        else:
            score += 3
    else:
        if 30 <= rsi <= 45:
            score += 20
        elif 45 < rsi <= 50:
            score += 15
        elif rsi < 30:
            score += 10
        else:
            score += 3

    if side == "BUY (Long)":
        if macd_hist > 0 and macd_hist > prev_hist:
            score += 20
        elif macd_hist > 0:
            score += 12
        else:
            score += 3
    else:
        if macd_hist < 0 and macd_hist < prev_hist:
            score += 20
        elif macd_hist < 0:
            score += 12
        else:
            score += 3

    ranges = [b["h"] - b["l"] for b in bars]
    avg_range = sum(ranges) / len(ranges) if ranges else 1
    latest_range = bars[-1]["h"] - bars[-1]["l"]
    range_ratio = latest_range / avg_range if avg_range > 0 else 1.0
    if range_ratio >= 1.5:
        score += 20
    elif range_ratio >= 1.0:
        score += 12
    else:
        score += 5

    if len(bars) >= 2:
        prev_vol = bars[-2].get("v", 0)
        curr_vol = bars[-1].get("v", 0)
        is_pullback = (side == "BUY (Long)" and cmp_val < bars[-2]["c"]) or (side == "SELL (Short)" and cmp_val > bars[-2]["c"])
        if is_pullback and curr_vol < prev_vol:
            score += 15
        else:
            score += 5

    return min(100, max(0, int(score)))

def process_single_symbol(sym, source, now):
    try:
        bars = nse_data.load_bars(sym, source=source, days=5)
        if not bars:
            return None
        day_bars = nse_data.day_bars(bars, bars[-1]["t"].date())
        if len(day_bars) < 3:
            return None
        
        completed_bars = [b for b in day_bars if nse_data.bar_close(b) <= now]
        if not completed_bars:
            completed_bars = day_bars

        meta = {}
        if source != "dhan":
            try:
                meta = nse_data.yahoo_chart(nse_data.normalize_symbol(sym), range_="5d", interval="5m").get("meta", {})
            except Exception:
                pass

        cmp_val = meta.get("regularMarketPrice") or completed_bars[-1]["c"]
        day_open = day_bars[0]["o"]
        
        prev_close = meta.get("previousClose") or meta.get("chartPreviousClose")
        if not prev_close:
            s_map = nse_data.session_map(bars)
            dates = sorted(s_map.keys())
            if len(dates) >= 2:
                prev_day = dates[-2]
                prev_day_bars = [s_map[prev_day][m] for m in sorted(s_map[prev_day])]
                prev_close = prev_day_bars[-1]["c"]
            else:
                prev_close = day_open

        pct_chg = round(((cmp_val - prev_close) / prev_close) * 100, 2)

        bare_sym = nse_data.bare_symbol(sym)
        sector = SECTOR_MAP.get(bare_sym, "Other")

        or_bars = day_bars[:3]
        or_high = max(b["h"] for b in or_bars)
        or_low = min(b["l"] for b in or_bars)
        or_mid = round((or_high + or_low) / 2, 2)
        
        triggered = False
        side = "WAITING"
        entry = "-"
        sl = "-"
        target = "-"
        qty = "-"
        max_loss = "-"
        vol_ratio = "-"
        pct_moved = "-"
        confidence = "-"

        anchor_breakout_bar = None
        anchor_idx = -1
        break_side = "WAITING"

        for idx, b in enumerate(completed_bars[3:], start=3):
            if b["c"] > or_high:
                anchor_breakout_bar = b
                anchor_idx = idx
                break_side = "BUY (Long)"
            elif b["c"] < or_low:
                anchor_breakout_bar = b
                anchor_idx = idx
                break_side = "SELL (Short)"
            elif or_low <= b["c"] <= or_high:
                anchor_breakout_bar = None
                anchor_idx = -1
                break_side = "WAITING"

        if anchor_breakout_bar is not None:
            if break_side == "BUY (Long)" and cmp_val >= or_high:
                triggered = True
                side = "BUY (Long)"
                breakout_close = anchor_breakout_bar["c"]
                pct_moved = round(((cmp_val - or_high) / or_high) * 100, 2)
                
                if pct_moved > 0.3:
                    entry = round(or_high, 2)
                else:
                    entry = round(breakout_close, 2)
                    
                sl = or_mid
                risk = entry - sl
                target = round(entry + risk, 2)
                qty = max(1, int(1000 / risk)) if risk > 0 else 1
                max_loss = 1000

                vols = [b.get("v", 0) for b in completed_bars]
                non_zero_vols = [v for v in vols if v > 0]
                b_vol = anchor_breakout_bar.get("v", 0)
                if b_vol <= 0 and non_zero_vols:
                    b_vol = non_zero_vols[-1]
                prior_vols = [completed_bars[j]["v"] for j in range(max(0, anchor_idx - 10), anchor_idx) if completed_bars[j].get("v", 0) > 0]
                sma_vol = sum(prior_vols) / len(prior_vols) if prior_vols else (sum(non_zero_vols) / len(non_zero_vols) if non_zero_vols else 1.0)
                vol_ratio = round(b_vol / sma_vol, 2) if sma_vol > 0 else 1.0

                rsi_val, macd_hist, prev_hist = calculate_indicators(completed_bars[:anchor_idx+1])
                confidence = compute_confidence(side, vol_ratio, rsi_val, macd_hist, prev_hist, completed_bars[:anchor_idx+1], cmp_val)

            elif break_side == "SELL (Short)" and cmp_val <= or_low:
                triggered = True
                side = "SELL (Short)"
                breakout_close = anchor_breakout_bar["c"]
                pct_moved = round(((or_low - breakout_close) / or_low) * 100, 2)
                
                if pct_moved > 0.3:
                    entry = round(or_low, 2)
                else:
                    entry = round(breakout_close, 2)
                    
                sl = or_mid
                risk = sl - entry
                target = round(entry - risk, 2)
                qty = max(1, int(1000 / risk)) if risk > 0 else 1
                max_loss = 1000

                vols = [b.get("v", 0) for b in completed_bars]
                non_zero_vols = [v for v in vols if v > 0]
                b_vol = anchor_breakout_bar.get("v", 0)
                if b_vol <= 0 and non_zero_vols:
                    b_vol = non_zero_vols[-1]
                prior_vols = [completed_bars[j]["v"] for j in range(max(0, anchor_idx - 10), anchor_idx) if completed_bars[j].get("v", 0) > 0]
                sma_vol = sum(prior_vols) / len(prior_vols) if prior_vols else (sum(non_zero_vols) / len(non_zero_vols) if non_zero_vols else 1.0)
                vol_ratio = round(b_vol / sma_vol, 2) if sma_vol > 0 else 1.0

                rsi_val, macd_hist, prev_hist = calculate_indicators(completed_bars[:anchor_idx+1])
                confidence = compute_confidence(side, vol_ratio, rsi_val, macd_hist, prev_hist, completed_bars[:anchor_idx+1], cmp_val)

        status = "TRIGGERED" if triggered else "WAITING"

        return {
            "Symbol": bare_sym,
            "Sector": sector,
            "Status": status,
            "Side": side,
            "CMP (INR)": round(cmp_val, 2),
            "% Change": pct_chg,
            "Entry (INR)": entry,
            "SL (INR)": sl,
            "Target (INR)": target,
            "Qty": qty,
            "Max Loss (INR)": max_loss,
            "OR High": round(or_high, 2),
            "OR Low": round(or_low, 2),
            "% Moved from OR": pct_moved,
            "Volume Ratio": vol_ratio if triggered else "-",
            "Confidence (%)": confidence if triggered else "-"
        }
    except Exception:
        return None

@st.cache_data(ttl=300)
def run_sector_index_scan(source="auto"):
    use_dhan = (source in ("auto", "dhan")) and nse_data.dhan_ready()
    results = []

    sector_name_map = {
        "NIFTY OIL AND GAS": "Energy & Oil/Gas",
        "NIFTY INFRA": "Infrastructure",
        "NIFTY METAL": "Metals",
        "NIFTY CONSUMER DURABLE": "Consumer & Retail",
        "NIFTY AUTO": "Auto",
        "NIFTY REALTY": "Cement & Materials",
        "NIFTY FMCG": "FMCG",
        "NIFTY BANK": "Banking & Financials",
        "NIFTY PHARMA": "Pharma",
        "NIFTY IT": "IT",
        "NIFTY ENERGY": "Energy & Oil/Gas"
    }

    if use_dhan:
        try:
            dhan = nse_data.get_dhan()
            rows = nse_data.load_dhan_master()
            indices = [r for r in rows if r.get('SEM_INSTRUMENT_NAME') == 'INDEX' and r.get('SEM_SMST_SECURITY_ID')]
            sec_map = {int(r['SEM_SMST_SECURITY_ID']): r for r in indices}
            ids = list(sec_map.keys())
            
            today_date = nse_data.now_ist().strftime('%Y-%m-%d')
            past_date = (nse_data.now_ist() - timedelta(days=7)).strftime('%Y-%m-%d')

            for i in range(0, len(ids), 25):
                chunk = ids[i:i+25]
                resp = dhan.ohlc_data({'IDX_I': chunk})
                if resp.get('status') == 'success':
                    data = resp.get('data', {}).get('IDX_I', {})
                    for sid_str, q in data.items():
                        r = sec_map.get(int(sid_str))
                        if r and q:
                            name = (r.get('SEM_CUSTOM_SYMBOL') or r.get('SEM_TRADING_SYMBOL') or '').strip().upper()
                            for k, mapped_sec in sector_name_map.items():
                                if k in name:
                                    ltp = float(q.get('last_price') or 0)
                                    try:
                                        hist = dhan.historical_daily_data(
                                            security_id=sid_str,
                                            exchange_segment='IDX_I',
                                            instrument_type='INDEX',
                                            expiry_code=0,
                                            from_date=past_date,
                                            to_date=today_date
                                        )
                                        if hist.get('status') == 'success':
                                            closes = hist.get('data', {}).get('close', [])
                                            if closes:
                                                prev_close = closes[-1]
                                                if ltp > 0 and prev_close > 0:
                                                    chg_pct = round(((ltp - prev_close) / prev_close) * 100, 2)
                                                    results.append({"Sector": mapped_sec, "Avg % Change": chg_pct})
                                    except Exception:
                                        pass
        except Exception:
            pass

    if not results:
        yahoo_indices = {
            "Energy & Oil/Gas": "^CNXENERGY",
            "Infrastructure": "^CNXINFRA",
            "Metals": "^CNXMETAL",
            "Auto": "^CNXAUTO",
            "FMCG": "^CNXFMCG",
            "Banking & Financials": "^NSEBANK",
            "Pharma": "^CNXPHARMA",
            "IT": "^CNXIT"
        }
        for sec_name, idx_sym in yahoo_indices.items():
            try:
                bars = nse_data.load_bars(idx_sym, source="yahoo", days=5)
                if bars and len(bars) >= 2:
                    meta = {}
                    try:
                        meta = nse_data.yahoo_chart(nse_data.normalize_symbol(idx_sym), range_="5d", interval="5m").get("meta", {})
                    except Exception:
                        pass
                    cmp_val = meta.get("regularMarketPrice") or bars[-1]['c']
                    prev_close = meta.get("previousClose") or meta.get("chartPreviousClose")
                    if not prev_close:
                        s_map = nse_data.session_map(bars)
                        dates = sorted(s_map.keys())
                        if len(dates) >= 2:
                            prev_day = dates[-2]
                            prev_day_bars = [s_map[prev_day][m] for m in sorted(s_map[prev_day])]
                            prev_close = prev_day_bars[-1]["c"]
                        else:
                            prev_close = bars[0]['o']
                    if prev_close and prev_close > 0:
                        pct_chg = round(((cmp_val - prev_close) / prev_close) * 100, 2)
                        results.append({"Sector": sec_name, "Avg % Change": pct_chg})
            except Exception:
                pass

    sec_df = pd.DataFrame(results)
    if not sec_df.empty:
        sec_df = sec_df.drop_duplicates(subset=["Sector"]).sort_values(by="Avg % Change", ascending=False).reset_index(drop=True)
        if not any(sec_df["Sector"] == "Other"):
            sec_df = pd.concat([sec_df, pd.DataFrame([{"Sector": "Other", "Avg % Change": 0.0}])], ignore_index=True)
        return sec_df
    return pd.DataFrame([{"Sector": "Other", "Avg % Change": 0.0}], columns=["Sector", "Avg % Change"])

@st.cache_data(ttl=300)
def run_market_scan(index_name="NIFTY50", source="auto"):
    symbols = nse_data.index_constituents(index_name)
    now = nse_data.now_ist()
    results = []

    chunk_size = 25
    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i:i+chunk_size]
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(process_single_symbol, sym, source, now): sym for sym in chunk}
            for future in concurrent.futures.as_completed(futures):
                res = future.result()
                if res:
                    results.append(res)
        if len(symbols) > 50:
            time.sleep(0.15)

    return pd.DataFrame(results)

@st.cache_data(ttl=300)
def run_fundamental_scan(index_name="NIFTY250"):
    try:
        symbols = nse_data.index_constituents(index_name)
    except Exception:
        symbols = None
    max_len = len(symbols) if symbols else 250
    return screener.screen_stocks(symbols=symbols, max_stocks=max_len)

if app_choice == "Live ORB & Breakdown Dashboard":
    st.title("📈 Nifty Live Opening-Range Breakout & Breakdown Dashboard")
    st.markdown("""
    This dashboard monitors Indian equity indices for 15-minute Opening-Range breakouts (Long) and breakdowns (Short), backed by **fast parallel scanning** and an **official Sector Index Strength leaderboard with interactive checkboxes**.
    """)

    index_choice = st.sidebar.selectbox(
        "Select Index / Universe",
        ["NIFTY50", "BANKNIFTY", "NIFTY100", "NIFTY250"],
        index=0,
        format_func=lambda x: {
            "NIFTY50": "Nifty 50",
            "BANKNIFTY": "Nifty Bank",
            "NIFTY100": "Nifty 100",
            "NIFTY250": "Nifty 250 Large & Midcap"
        }.get(x, x)
    )
    data_source = st.sidebar.selectbox(
        "Data Source",
        ["auto", "dhan", "yahoo"],
        index=0,
        format_func=lambda x: {"auto": "Auto (Dhan if configured, else Yahoo)", "dhan": "DhanHQ (Exchange Feed)", "yahoo": "Yahoo Finance"}[x]
    )

    st.sidebar.markdown("---")
    if nse_data.dhan_ready():
        st.sidebar.success("✅ Dhan API Connected")
    else:
        st.sidebar.warning("⚠️ Yahoo Mode (Dhan credentials missing in Secrets)")

    if data_source == "dhan" and not nse_data.dhan_ready():
        st.error("DhanHQ requested but credentials are missing or invalid in Streamlit Secrets. Please check Settings -> Secrets.")

    with st.spinner(f"Loading market scan for {index_choice} & overall sector performance using {data_source.upper()}..."):
        df = run_market_scan(index_choice, source=data_source)
        sector_df = run_sector_index_scan(source=data_source)

    if not df.empty:
        st.subheader("📊 Overall Sector Strength Leaderboard & Filter")
        
        select_all = st.checkbox("Select All Sectors", value=True, key="select_all_sectors")
        sector_df["Select"] = select_all
        
        edited_sector_df = st.data_editor(
            sector_df[["Select", "Sector", "Avg % Change"]],
            hide_index=True,
            use_container_width=True,
            key="sector_grid_editor"
        )

        selected_sectors = edited_sector_df[edited_sector_df["Select"] == True]["Sector"].tolist()
        for s in df["Sector"].unique():
            if s not in selected_sectors and s not in edited_sector_df["Sector"].values:
                selected_sectors.append(s)
        filtered_df = df[df["Sector"].isin(selected_sectors)]

        triggered_df = filtered_df[filtered_df["Status"] == "TRIGGERED"].sort_values(by="Volume Ratio", ascending=False)
        waiting_df = filtered_df[filtered_df["Status"] == "WAITING"].sort_values(by="% Change", ascending=False)

        def color_pct(val):
            try:
                v = float(val)
                if v > 0:
                    return "color: #2e7d32; font-weight: bold;"
                elif v < 0:
                    return "color: #c62828; font-weight: bold;"
            except:
                pass
            return ""

        def highlight_high_confidence(row):
            try:
                conf = row.get("Confidence (%)", "")
                if isinstance(conf, str) and conf.endswith("%"):
                    val = float(conf.rstrip("%"))
                else:
                    val = float(conf)
                if val >= 80:
                    return ["background-color: #fff9c4; color: #000000; font-weight: bold;" for _ in row]
            except:
                pass
            return ["" for _ in row]

        def style_df(styler):
            fmt = {
                "CMP (INR)": lambda x: f"{x:.2f}" if isinstance(x, (int, float)) else x,
                "% Change": lambda x: f"{x:.2f}" if isinstance(x, (int, float)) else x,
                "Entry (INR)": lambda x: f"{x:.2f}" if isinstance(x, (int, float)) else x,
                "SL (INR)": lambda x: f"{x:.2f}" if isinstance(x, (int, float)) else x,
                "Target (INR)": lambda x: f"{x:.2f}" if isinstance(x, (int, float)) else x,
                "OR High": lambda x: f"{x:.2f}" if isinstance(x, (int, float)) else x,
                "OR Low": lambda x: f"{x:.2f}" if isinstance(x, (int, float)) else x,
                "% Moved from OR": lambda x: f"{x:.2f}" if isinstance(x, (int, float)) else x,
                "Volume Ratio": lambda x: f"{x:.2f}" if isinstance(x, (int, float)) else x,
                "Confidence (%)": lambda x: f"{x}%" if isinstance(x, (int, float)) else x,
            }
            try:
                return styler.map(color_pct, subset=["% Change", "% Moved from OR"]).format(fmt, na_rep="-").apply(highlight_high_confidence, axis=1)
            except AttributeError:
                return styler.applymap(color_pct, subset=["% Change", "% Moved from OR"]).format(fmt, na_rep="-").apply(highlight_high_confidence, axis=1)

        st.subheader(f"🚀 Triggered Setups (Breakouts & Breakdowns) [{len(triggered_df)}]")
        if not triggered_df.empty:
            st.dataframe(
                style_df(triggered_df.style),
                use_container_width=True
            )
        else:
            st.info("No active breakouts or breakdowns for selected sector(s).")

        st.subheader(f"⏳ Waiting / Scanning [{len(waiting_df)}]")
        st.dataframe(
            style_df(waiting_df.style),
            use_container_width=True
        )

        csv = filtered_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="Download Filtered Scan CSV",
            data=csv,
            file_name=f"nifty50_sector_scan_{nse_data.now_ist().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv"
        )
    else:
        st.warning("No data retrieved from market feed.")

else:
    st.title("🔍 Fundamental & Volume Stock Screener")
    st.markdown("""
    This screener filters stocks based on core criteria:
    - **P/E Ratio** < 20
    - **Volume Spike** > 2.0x 20-day average volume
    - **RSI (14)** > 50
    """)

    screener_universe = st.selectbox(
        "Select Screener Universe",
        ["NIFTY250", "NIFTY100", "NIFTY50"],
        index=0,
        format_func=lambda x: {
            "NIFTY50": "Nifty 50",
            "NIFTY100": "Nifty 100",
            "NIFTY250": "Nifty 250 Large & Midcap"
        }.get(x, x)
    )

    with st.spinner(f"Running fundamental and technical volume screen for {screener_universe}..."):
        fund_df = run_fundamental_scan(screener_universe)

    if not fund_df.empty:
        st.success(f"Found {len(fund_df)} stocks matching criteria!")
        st.dataframe(fund_df, use_container_width=True)

        csv = fund_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="Download Screener Results CSV",
            data=csv,
            file_name=f"fundamental_screener_{nse_data.now_ist().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv"
        )
    else:
        st.info("No stocks currently match all filters (P/E < 20, Vol Ratio > 2.0, RSI > 50).")
