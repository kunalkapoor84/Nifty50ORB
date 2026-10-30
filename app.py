import streamlit as st
import pandas as pd
import time
import os
import nse_data

st.set_page_config(
    page_title="Nifty 50 Live ORB & Breakdown Dashboard",
    page_icon="📈",
    layout="wide"
)

st.title("📈 Nifty 50 Live Opening-Range Breakout & Breakdown Dashboard")
st.markdown("""
This dashboard monitors the **Nifty 50 universe** for 15-minute Opening-Range breakouts (Long) and breakdowns (Short) based on **completed 5-minute candle closes**.

### 📐 Strategy & Risk Rules:
- **Candle Close Rule:** Strictly waits for the 5-minute candle to close before triggering ORB / Breakdown and calculating indicators.
- **Side:** BUY (Completed Close > OR High) or SELL (Completed Close < OR Low)
- **Entry Model:** 
  - If % moved from OR $\\le$ 0.3%, **Entry** = Completed Close (Momentum entry).
  - If % moved from OR > 0.3%, **Entry** is modeled at **OR High / OR Low** (Retest / Retracement entry).
- **SL (Stop Loss):** Opening-Range Midpoint (`or-mid` = $\\frac{\\text{OR High} + \\text{OR Low}}{2}$)
- **Target (1R):** 1:1 Risk-to-Reward ratio (`Target = Entry ± Risk`)
- **Position Size (Qty):** Sized for a fixed **Max Loss of ₹1,000**
- **Volume Ratio:** Breakout candle volume divided by the **10-period SMA of 5-minute volume**.
- **Confidence (%) (0–100%):** Composite score factoring in **Volume Ratio (30 pts)**, **RSI (20 pts)**, **MACD (20 pts)**, **Breakout Candle Size (20 pts)**, and **Pullback Volume (10 pts)**. **Rows with >= 80% are highlighted in yellow.**
- **Auto-refreshes every 5 minutes.**
""")

def calculate_indicators(bars):
    closes = pd.Series([b["c"] for b in bars])
    vols = pd.Series([b["v"] for b in bars])

    # RSI (14)
    delta = closes.diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=14, min_periods=1).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=14, min_periods=1).mean()
    rs = gain / (loss + 1e-9)
    rsi = 100 - (100 / (1 + rs))
    current_rsi = rsi.iloc[-1] if not rsi.empty else 50.0

    # MACD (12, 26, 9)
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
    # 1. Volume Ratio (0-30)
    if vol_ratio >= 2.0:
        score += 30
    elif vol_ratio >= 1.5:
        score += 22
    elif vol_ratio >= 1.0:
        score += 15
    else:
        score += 5

    # 2. RSI (0-20)
    if side == "BUY (Long)":
        if 55 <= rsi <= 75:
            score += 20
        elif 50 <= rsi < 55:
            score += 15
        elif rsi > 75:
            score += 12
        else:
            score += 5
    else: # Short
        if 25 <= rsi <= 45:
            score += 20
        elif 45 < rsi <= 50:
            score += 15
        elif rsi < 25:
            score += 12
        else:
            score += 5

    # 3. MACD Momentum (0-20)
    if side == "BUY (Long)":
        if macd_hist > 0 and macd_hist > prev_hist:
            score += 20
        elif macd_hist > 0:
            score += 15
        else:
            score += 5
    else:
        if macd_hist < 0 and macd_hist < prev_hist:
            score += 20
        elif macd_hist < 0:
            score += 15
        else:
            score += 5

    # 4. Breakout Candle Size vs Average Range (0-20)
    ranges = [b["h"] - b["l"] for b in bars]
    avg_range = sum(ranges) / len(ranges) if ranges else 1
    latest_range = bars[-1]["h"] - bars[-1]["l"]
    range_ratio = latest_range / avg_range if avg_range > 0 else 1.0
    if range_ratio >= 1.5:
        score += 20
    elif range_ratio >= 1.0:
        score += 15
    else:
        score += 10

    # 5. Decreasing volume on retracement bonus (0-10)
    if len(bars) >= 2:
        prev_vol = bars[-2].get("v", 0)
        curr_vol = bars[-1].get("v", 0)
        is_pullback = (side == "BUY (Long)" and cmp_val < bars[-2]["c"]) or (side == "SELL (Short)" and cmp_val > bars[-2]["c"])
        if is_pullback and curr_vol < prev_vol:
            score += 10
        else:
            score += 5

    return min(100, max(0, int(score)))

@st.fragment(run_every="300s")
def render_dashboard():
    st.sidebar.header("Dashboard Controls")
    auto_refresh = st.sidebar.checkbox("Auto-refresh active (every 5 mins)", value=True)
    
    if st.sidebar.button("Run Manual Scan Now", type="primary"):
        st.cache_data.clear()

    st.write(f"**Last Updated:** {nse_data.now_ist().strftime('%Y-%m-%d %H:%M:%S')} IST")

    with st.spinner("Scanning Nifty 50 stocks on completed 5-min candle closes..."):
        try:
            symbols = nse_data.index_constituents("NIFTY50")
            results = []
            now = nse_data.now_ist()

            for sym in symbols:
                try:
                    bars = nse_data.load_bars(sym, days=5)
                    if not bars:
                        continue
                    day_bars = nse_data.day_bars(bars, bars[-1]["t"].date())
                    if len(day_bars) < 3:
                        continue
                    
                    # Restrict to completed 5-minute bars (bar close time <= now)
                    completed_bars = [b for b in day_bars if nse_data.bar_close(b) <= now]
                    if not completed_bars:
                        completed_bars = day_bars

                    cmp_val = completed_bars[-1]["c"]
                    day_open = day_bars[0]["o"]
                    pct_chg = round(((cmp_val - day_open) / day_open) * 100, 2)

                    or_bars = day_bars[:3]
                    or_high = max(b["h"] for b in or_bars)
                    or_low = min(b["l"] for b in or_bars)
                    or_mid = round((or_high + or_low) / 2, 2)
                    
                    rsi_val, macd_hist, prev_hist = calculate_indicators(completed_bars)

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

                    vols = [b.get("v", 0) for b in completed_bars]
                    non_zero_vols = [v for v in vols if v > 0]
                    vol_sma10 = sum(non_zero_vols[-10:]) / len(non_zero_vols[-10:]) if len(non_zero_vols) >= 3 else (sum(non_zero_vols) / len(non_zero_vols) if non_zero_vols else 1.0)
                    if vol_sma10 <= 0:
                        vol_sma10 = 1.0

                    calc_vol_ratio = 1.0
                    for idx, b in enumerate(completed_bars[3:], start=3):
                        if b["c"] > or_high or b["c"] < or_low:
                            b_vol = b.get("v", 0)
                            if b_vol <= 0 and non_zero_vols:
                                b_vol = non_zero_vols[-1]
                            prior_vols = [completed_bars[j]["v"] for j in range(max(0, idx - 10), idx) if completed_bars[j].get("v", 0) > 0]
                            sma_p = sum(prior_vols) / len(prior_vols) if prior_vols else vol_sma10
                            if sma_p > 0:
                                calc_vol_ratio = round(b_vol / sma_p, 2)

                    if cmp_val > or_high:
                        triggered = True
                        side = "BUY (Long)"
                        pct_moved = round(((cmp_val - or_high) / or_high) * 100, 2)
                        
                        if pct_moved > 0.3:
                            entry = round(or_high, 2)
                        else:
                            entry = round(cmp_val, 2)
                            
                        sl = or_mid
                        risk = entry - sl
                        target = round(entry + risk, 2)
                        qty = max(1, int(1000 / risk)) if risk > 0 else 1
                        max_loss = 1000
                        vol_ratio = calc_vol_ratio
                        confidence = compute_confidence(side, vol_ratio, rsi_val, macd_hist, prev_hist, completed_bars, cmp_val)

                    elif cmp_val < or_low:
                        triggered = True
                        side = "SELL (Short)"
                        pct_moved = round(((or_low - cmp_val) / or_low) * 100, 2)
                        
                        if pct_moved > 0.3:
                            entry = round(or_low, 2)
                        else:
                            entry = round(cmp_val, 2)
                            
                        sl = or_mid
                        risk = sl - entry
                        target = round(entry - risk, 2)
                        qty = max(1, int(1000 / risk)) if risk > 0 else 1
                        max_loss = 1000
                        vol_ratio = calc_vol_ratio
                        confidence = compute_confidence(side, vol_ratio, rsi_val, macd_hist, prev_hist, completed_bars, cmp_val)

                    status = "TRIGGERED" if triggered else "WAITING"

                    results.append({
                        "Symbol": nse_data.bare_symbol(sym),
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
                    })
                except Exception:
                    pass

            df = pd.DataFrame(results)
            if not df.empty:
                triggered_df = df[df["Status"] == "TRIGGERED"].sort_values(by="Volume Ratio", ascending=False)
                waiting_df = df[df["Status"] == "WAITING"].sort_values(by="% Change", ascending=False)

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
                    st.info("No active breakouts or breakdowns at completed candle closes.")

                st.subheader(f"⏳ Waiting / Scanning [{len(waiting_df)}]")
                st.dataframe(
                    style_df(waiting_df.style),
                    use_container_width=True
                )

                # Download button
                csv = df.to_csv(index=False).encode("utf-8")
                st.download_button(
                    label="Download Full Scan CSV",
                    data=csv,
                    file_name=f"nifty50_orb_breakdown_scan_{nse_data.now_ist().strftime('%Y%m%d_%H%M')}.csv",
                    mime="text/csv"
                )
            else:
                st.warning("No data retrieved from Yahoo Finance.")
        except Exception as e:
            st.error(f"Error running scan: {e}")

render_dashboard()
