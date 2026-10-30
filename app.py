import streamlit as st
import pandas as pd
import time
import os
import sys

# Add trader-nse skill path to sys.path
sys.path.insert(0, os.path.expanduser(r"C:\Users\Pc\.agents\skills\trader-nse"))
import nse_data

st.set_page_config(
    page_title="Nifty 50 Live ORB Dashboard",
    page_icon="📈",
    layout="wide"
)

st.title("📈 Nifty 50 Live Opening-Range Breakout (ORB) Dashboard")
st.markdown("""
This dashboard monitors the **Nifty 50 universe** for 15-minute Opening-Range Breakouts (ORB) in near real-time. 
- **CMP:** Current Market Price
- **SL (Stop Loss):** Opening-Range Midpoint (`or-mid`)
- **Volume Ratio:** Breakout bar volume vs. opening range average volume
- **Auto-refreshes every 5 minutes.**
""")

@st.fragment(run_every="300s")
def render_dashboard():
    st.sidebar.header("Dashboard Controls")
    auto_refresh = st.sidebar.checkbox("Auto-refresh active (every 5 mins)", value=True)
    
    if st.sidebar.button("Run Manual Scan Now", type="primary"):
        st.cache_data.clear()

    st.write(f"**Last Updated:** {nse_data.now_ist().strftime('%Y-%m-%d %H:%M:%S')} IST")

    with st.spinner("Scanning Nifty 50 stocks for ORB setups..."):
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
                    or_bars = day_bars[:3]
                    or_high = max(b["h"] for b in or_bars)
                    or_low = min(b["l"] for b in or_bars)
                    or_mid = round((or_high + or_low) / 2, 2)
                    triggered = any(b["c"] > or_high for b in day_bars[3:])
                    status = "TRIGGERED" if triggered else "WAITING"
                    
                    # Volume ratio of breakout bar if triggered
                    vol_ratio = 1.0
                    for b in day_bars[3:]:
                        if b["c"] > or_high:
                            base = [x["v"] for x in or_bars if x["v"]]
                            avg = sum(base) / len(base) if base else 1
                            vol_ratio = round(b["v"] / avg, 2) if avg else 1.0
                            break

                    results.append({
                        "Symbol": nse_data.bare_symbol(sym),
                        "CMP (INR)": round(cmp_val, 2),
                        "OR High": round(or_high, 2),
                        "OR Low": round(or_low, 2),
                        "SL (OR-Mid)": round(or_mid, 2),
                        "Volume Ratio": vol_ratio,
                        "Status": status
                    })
                except Exception:
                    pass

            df = pd.DataFrame(results)
            if not df.empty:
                triggered_df = df[df["Status"] == "TRIGGERED"].sort_values(by="Volume Ratio", ascending=False)
                waiting_df = df[df["Status"] == "WAITING"]

                st.subheader(f"🚀 Triggered Breakouts ({len(triggered_df)})")
                if not triggered_df.empty:
                    st.dataframe(triggered_df, use_container_width=True)
                else:
                    st.info("No breakouts triggered yet.")

                st.subheader(f"⏳ Waiting / Scanning ({len(waiting_df)})")
                with st.expander("View Waiting Stocks"):
                    st.dataframe(waiting_df, use_container_width=True)

                # Download button
                csv = df.to_csv(index=False).encode("utf-8")
                st.download_button(
                    label="Download Full Scan CSV",
                    data=csv,
                    file_name=f"nifty50_orb_scan_{nse_data.now_ist().strftime('%Y%m%d_%H%M')}.csv",
                    mime="text/csv"
                )
            else:
                st.warning("No data retrieved from Yahoo Finance.")
        except Exception as e:
            st.error(f"Error running scan: {e}")

render_dashboard()
