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
This dashboard monitors the **Nifty 50 universe** for 15-minute Opening-Range breakouts (Long) and breakdowns (Short).

### 📐 Strategy & Risk Rules:
- **Side:** BUY (Breakout > OR High) or SELL (Breakdown < OR Low)
- **SL (Stop Loss):** Opening-Range Midpoint (`or-mid` = $\\frac{\\text{OR High} + \\text{OR Low}}{2}$)
- **Target (1R):** 1:1 Risk-to-Reward ratio:
  - *Long:* $\\text{Target} = \\text{Entry} + (\\text{Entry} - \\text{SL})$
  - *Short:* $\\text{Target} = \\text{Entry} - (\\text{SL} - \\text{Entry})$
- **Position Size (Qty):** Sized for a fixed **Max Loss of ₹1,000** ($\\text{Qty} = \\frac{1000}{|\\text{Entry} - \\text{SL}|}$)
- **% Moved from OR:** Percentage distance moved past OR High (for breakouts) or OR Low (for breakdowns).
- **Auto-refreshes every 5 minutes.**
""")

@st.fragment(run_every="300s")
def render_dashboard():
    st.sidebar.header("Dashboard Controls")
    auto_refresh = st.sidebar.checkbox("Auto-refresh active (every 5 mins)", value=True)
    
    if st.sidebar.button("Run Manual Scan Now", type="primary"):
        st.cache_data.clear()

    st.write(f"**Last Updated:** {nse_data.now_ist().strftime('%Y-%m-%d %H:%M:%S')} IST")

    with st.spinner("Scanning Nifty 50 stocks for breakouts & breakdowns..."):
        try:
            symbols = nse_data.index_constituents("NIFTY50")
            results = []
            for sym in symbols:
                try:
                    bars = nse_data.load_bars(sym, days=5)
                    if not bars:
                        continue
                    day_bars = nse_data.day_bars(bars, bars[-1]["t"].date())
                    if len(day_bars) < 3:
                        continue
                    
                    cmp_val = day_bars[-1]["c"]
                    day_open = day_bars[0]["o"]
                    pct_chg = round(((cmp_val - day_open) / day_open) * 100, 2)

                    or_bars = day_bars[:3]
                    or_high = max(b["h"] for b in or_bars)
                    or_low = min(b["l"] for b in or_bars)
                    or_mid = round((or_high + or_low) / 2, 2)
                    
                    # Check for trigger after opening range (bar index >= 3)
                    triggered = False
                    side = "WAITING"
                    entry = "-"
                    sl = "-"
                    target = "-"
                    qty = "-"
                    max_loss = "-"
                    vol_ratio = "-"
                    pct_moved = "-"

                    for b in day_bars[3:]:
                        # Long Breakout
                        if b["c"] > or_high:
                            triggered = True
                            side = "BUY (Long)"
                            entry = round(b["c"], 2)
                            sl = or_mid
                            risk = entry - sl
                            target = round(entry + risk, 2)
                            qty = max(1, int(1000 / risk)) if risk > 0 else 1
                            max_loss = 1000
                            base = [x["v"] for x in or_bars if x["v"]]
                            avg = sum(base) / len(base) if base else 1
                            vol_ratio = round(b["v"] / avg, 2) if avg else 1.0
                            pct_moved = round(((entry - or_high) / or_high) * 100, 2)
                            break
                        # Short Breakdown
                        elif b["c"] < or_low:
                            triggered = True
                            side = "SELL (Short)"
                            entry = round(b["c"], 2)
                            sl = or_mid
                            risk = sl - entry
                            target = round(entry - risk, 2)
                            qty = max(1, int(1000 / risk)) if risk > 0 else 1
                            max_loss = 1000
                            base = [x["v"] for x in or_bars if x["v"]]
                            avg = sum(base) / len(base) if base else 1
                            vol_ratio = round(b["v"] / avg, 2) if avg else 1.0
                            pct_moved = round(((or_low - entry) / or_low) * 100, 2)
                            break

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
                        "Volume Ratio": vol_ratio
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

                def style_df(styler):
                    try:
                        return styler.map(color_pct, subset=["% Change", "% Moved from OR"])
                    except AttributeError:
                        return styler.applymap(color_pct, subset=["% Change", "% Moved from OR"])

                st.subheader(f"🚀 Triggered Setups (Breakouts & Breakdowns) [{len(triggered_df)}]")
                if not triggered_df.empty:
                    st.dataframe(
                        style_df(triggered_df.style),
                        use_container_width=True
                    )
                else:
                    st.info("No breakouts or breakdowns triggered yet.")

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
