# Claude.md — Nifty 500 Screener & Near Real-Time UI Project

## Overview
This project provides a real-time stock screener and Streamlit dashboard for Indian NSE-listed equities (Nifty 500 universe). It filters stocks based on:
- **P/E Ratio** < 20
- **Volume Spike** > 2.0x 20-day average volume
- **RSI (14)** > 50

## Tech Stack
- **Python 3.10+**
- **yfinance** for market data, historical bars, and fundamental metrics
- **pandas / numpy** for technical calculations (RSI, volume ratios)
- **Streamlit** for interactive, near real-time web UI dashboard

## Execution Steps
1. Install dependencies:
   ```powershell
   pip install yfinance pandas streamlit numpy
   ```
2. Run the Streamlit dashboard:
   ```powershell
   streamlit run app.py
   ```
3. Access UI at `http://localhost:8501`
