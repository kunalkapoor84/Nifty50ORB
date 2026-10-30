import time
import pandas as pd
import numpy as np
import yfinance as yf

# Sample robust list of NSE Nifty 50 / top liquid stocks for screening
DEFAULT_NSE_SYMBOLS = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "ITC", "SBIN", "BHARTIARTL",
    "LTIM", "AXISBANK", "ASIANPAINT", "MARUTI", "SUNPHARMA", "TITAN", "BAJFINANCE",
    "HCLTECH", "TATAMOTORS", "WIPRO", "NTPC", "POWERGRID", "TATASTEEL", "ADANIENT",
    "ADANIPORTS", "GRASIM", "BAJAJFINSV", "ONGC", "JSWSTEEL", "COALINDIA", "HINDALCO",
    "BPCL", "HEROMOTOCO", "EICHERMOT", "DIVISLAB", "DRREDDY", "BRITANNIA", "NESTLEIND",
    "TATACONSUM", "SBILIFE", "HDFCLIFE", "APOLLOHOSP", "ULTRACEMCO", "TECHM", "M&M",
    "INDUSINDBK", "BAJAJ-AUTO", "CIPLA", "SHRIRAMFIN", "BPCL", "LUPIN", "VEDL"
]

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def screen_stocks(symbols=None, max_stocks=30, progress_callback=None):
    if symbols is None:
        symbols = DEFAULT_NSE_SYMBOLS

    results = []
    total = min(len(symbols), max_stocks)

    for i, sym in enumerate(symbols[:max_stocks]):
        ticker_str = f"{sym}.NS" if not sym.endswith(".NS") else sym
        if progress_callback:
            progress_callback(i + 1, total, sym)

        try:
            tk = yf.Ticker(ticker_str)
            hist = tk.history(period="3mo")
            if hist.empty or len(hist) < 25:
                time.sleep(0.5)
                continue

            # Calculate indicators
            hist['RSI'] = calculate_rsi(hist['Close'], 14)
            hist['Vol_MA20'] = hist['Volume'].rolling(window=20).mean()
            
            latest = hist.iloc[-1]
            prev_vol_ma = latest['Vol_MA20']
            current_vol = latest['Volume']
            
            vol_ratio = (current_vol / prev_vol_ma) if prev_vol_ma > 0 else 0
            rsi = latest['RSI']
            close_price = latest['Close']

            info = tk.info
            pe = info.get('trailingPE') or info.get('forwardPE')
            if pe is None or pe <= 0:
                time.sleep(0.5)
                continue

            # Filters: PE < 20, Volume Spike > 2.0x, RSI > 50
            if pe < 20 and vol_ratio > 2.0 and rsi > 50:
                results.append({
                    "Ticker": sym,
                    "Price (INR)": round(close_price, 2),
                    "P/E Ratio": round(pe, 2),
                    "Volume Ratio": round(vol_ratio, 2),
                    "RSI (14)": round(rsi, 2)
                })

        except Exception as e:
            pass

        time.sleep(0.5)  # Rate limiting delay as requested

    df = pd.DataFrame(results)
    if not df.empty:
        df = df.sort_values(by="Volume Ratio", ascending=False).reset_index(drop=True)
    return df
