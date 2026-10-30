#!/usr/bin/env python3
"""NSE data layer for trader-nse.

Yahoo Finance 5-minute charts (default, no keys) or DhanHQ intraday data
when DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN are set. Standard library only
for the Yahoo path; the Dhan path uses the installed dhanhq SDK.
"""

import csv
import io
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30), "IST")
BAR_MIN = 5
SESSION_BARS = 75
OPEN_MIN = 9 * 60 + 15
CLOSE_MIN = 15 * 60 + 30
LAST_BAR_START = CLOSE_MIN - BAR_MIN
DEFAULT_FLAT_BY = "15:15"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) trader-nse/1.0"

INDEX_ALIASES = {
    "NIFTY": "^NSEI",
    "NIFTY50": "^NSEI",
    "NIFTY 50": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "BANK NIFTY": "^NSEBANK",
    "NIFTYBANK": "^NSEBANK",
    "FINNIFTY": "^CNXFIN",
    "NIFTY FIN SERVICE": "^CNXFIN",
    "MIDCPNIFTY": "^NSEMDCP50",
    "SENSEX": "^BSESN",
}
INDEX_SECURITY_IDS = {
    "NIFTY": ("13", "NIFTY 50"),
    "BANKNIFTY": ("25", "BANK NIFTY"),
    "FINNIFTY": ("27", "FINNIFTY"),
    "MIDCPNIFTY": ("442", "MIDCPNIFTY"),
    "SENSEX": ("51", "SENSEX"),
}
YAHOO_TO_INDEX = {
    "^NSEI": "NIFTY",
    "^NSEBANK": "BANKNIFTY",
    "^CNXFIN": "FINNIFTY",
    "^NSEMDCP50": "MIDCPNIFTY",
    "^BSESN": "SENSEX",
}

NIFTY50_FALLBACK = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BSE",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL", "CIPLA",
    "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL", "GRASIM", "HCLTECH",
    "HDFCBANK", "HDFCLIFE", "HINDALCO", "HINDUNILVR", "ICICIBANK", "ITC",
    "INFY", "INDIGO", "JSWSTEEL", "JIOFIN", "KOTAKBANK", "LT", "M&M",
    "MARUTI", "MAXHEALTH", "NTPC", "NESTLEIND", "ONGC", "POWERGRID",
    "RELIANCE", "SBILIFE", "SHRIRAMFIN", "SBIN", "SUNPHARMA", "TCS",
    "TATACONSUM", "TMPV", "TATASTEEL", "TECHM", "TITAN", "TRENT",
    "ULTRACEMCO",
]
INDEX_CSV = {
    "NIFTY50": "https://archives.nseindia.com/content/indices/ind_nifty50list.csv",
    "BANKNIFTY": "https://archives.nseindia.com/content/indices/ind_niftybanklist.csv",
    "NIFTY100": "https://archives.nseindia.com/content/indices/ind_nifty100list.csv",
}
DHAN_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"


class DataError(RuntimeError):
    pass


def now_ist():
    return datetime.now(timezone.utc).astimezone(IST).replace(tzinfo=None, microsecond=0)


def epoch_to_ist(ts):
    ts = float(ts)
    if ts > 1e12:
        ts /= 1000.0
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(IST).replace(tzinfo=None)


def parse_hhmm(text):
    try:
        h, m = str(text).strip().split(":")
        h, m = int(h), int(m)
    except Exception:
        raise DataError("time must look like HH:MM (got %r)" % text)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise DataError("time must look like HH:MM (got %r)" % text)
    return h * 60 + m


def hhmm(minutes):
    return "%02d:%02d" % (minutes // 60, minutes % 60)


def bar_close(b):
    return b["t"] + timedelta(minutes=BAR_MIN)


def bar_minute(b):
    return b["t"].hour * 60 + b["t"].minute


def normalize_symbol(raw):
    s = str(raw or "").strip().upper()
    if not s:
        raise DataError("empty symbol")
    if s.startswith("NSE:"):
        s = s[4:]
    elif s.startswith("BSE:"):
        s = s[4:]
        return s if s.endswith(".BO") else s + ".BO"
    if s in INDEX_ALIASES:
        return INDEX_ALIASES[s]
    if s.startswith("^"):
        return s
    if s.endswith(".NS") or s.endswith(".BO"):
        return s
    if s.endswith("-FUT"):
        return s
    return s + ".NS"


def bare_symbol(sym):
    s = str(sym or "").strip().upper()
    if s.startswith("^"):
        return YAHOO_TO_INDEX.get(s, s.lstrip("^"))
    for suffix in (".NS", ".BO"):
        if s.endswith(suffix):
            return s[: -len(suffix)]
    if s.endswith("-FUT"):
        return s[: -len("-FUT")]
    return s


def is_index(sym):
    s = str(sym or "").strip().upper()
    return s.startswith("^") or s in INDEX_SECURITY_IDS


def display_symbol(sym):
    s = str(sym or "").strip().upper()
    if is_future(s):
        return bare_symbol(s) + " FUT"
    if s.startswith("^"):
        return YAHOO_TO_INDEX.get(s, s.lstrip("^"))
    return bare_symbol(s)


def is_future(sym):
    return str(sym or "").strip().upper().endswith("-FUT")


def yahoo_proxy(sym):
    """Yahoo has no NSE futures; use the spot index as the data proxy."""
    if not is_future(sym):
        return sym
    under = bare_symbol(sym).upper()
    alias = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "FINNIFTY": "^CNXFIN",
             "MIDCPNIFTY": "^NSEMDCP50"}.get(under)
    return alias or sym


def safe_name(sym):
    return "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in sym)


def index_constituents(name):
    key = str(name or "").strip().upper().replace(" ", "")
    if key in ("NIFTY", "NIFTY50", "NIFTY_50"):
        key = "NIFTY50"
    if key == "BANKNIFTY":
        key = "BANKNIFTY"
    url = INDEX_CSV.get(key)
    if url:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            raw = urllib.request.urlopen(req, timeout=25).read()
            text = raw.decode("utf-8-sig", "replace")
            rows = list(csv.DictReader(io.StringIO(text)))
            syms = [r.get("Symbol", "").strip().upper() for r in rows if r.get("Symbol")]
            if syms:
                return syms
        except Exception:
            if key == "NIFTY50":
                return list(NIFTY50_FALLBACK)
            raise DataError("could not fetch the %s constituent list from NSE" % key)
    if key == "NIFTY50":
        return list(NIFTY50_FALLBACK)
    raise DataError("unknown index list %r (try NIFTY50 or BANKNIFTY)" % name)


def _parse_yahoo_doc(doc, sym, want_meta=False):
    chart = (doc.get("chart") or {})
    if chart.get("error"):
        raise DataError("Yahoo: %s" % (chart["error"].get("description") or chart["error"]))
    result = (chart.get("result") or [None])[0]
    if not result:
        raise DataError("Yahoo returned no chart for %s" % sym)
    meta = result.get("meta") or {}
    stamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    vols = quote.get("volume") or []
    bars = []
    for i, ts in enumerate(stamps):
        try:
            o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        except IndexError:
            break
        if o is None or h is None or l is None or c is None:
            continue
        if c <= 0:
            continue
        v = vols[i] if i < len(vols) and vols[i] is not None else 0
        bars.append({"t": epoch_to_ist(ts), "o": float(o), "h": float(h),
                     "l": float(l), "c": float(c), "v": float(v)})
    bars.sort(key=lambda b: b["t"])
    out = {"symbol": sym, "meta": meta, "bars": bars}
    if want_meta:
        out["price"] = meta.get("regularMarketPrice")
        ts = meta.get("regularMarketTime")
        out["asof"] = epoch_to_ist(ts) if ts else None
    return out


def _yahoo_get(path, query, timeout=30):
    last = None
    for host in ("query1", "query2"):
        url = "https://%s.finance.yahoo.com%s?%s" % (host, path, query)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            raw = urllib.request.urlopen(req, timeout=timeout).read()
            return json.loads(raw.decode("utf-8", "replace"))
        except Exception as e:
            last = e
            time.sleep(0.4)
    raise DataError("Yahoo request failed for %s (%s)" % (path, last))


def yahoo_chart(sym, range_="60d", interval="5m"):
    q = urllib.parse.urlencode({"range": range_, "interval": interval,
                                "includePrePost": "false"})
    path = "/v8/finance/chart/%s" % urllib.parse.quote(sym, safe="")
    return _parse_yahoo_doc(_yahoo_get(path, q), sym)


def load_bars(sym, source="yahoo", days=60, offline=None, cache=None):
    """Bars for one instrument, oldest first. source: yahoo|dhan|auto."""
    sym = normalize_symbol(sym)
    if offline:
        p = Path(offline)
        if p.is_dir():
            cands = [p / (safe_name(sym) + ".json"), p / (safe_name(sym) + ".json.gz")]
            p = next((x for x in cands if x.exists()), p / (safe_name(sym) + ".json"))
        import gzip
        if not p.exists():
            raise DataError("offline file not found: %s" % p)
        opener = gzip.open if str(p).endswith(".gz") else open
        with opener(p, "rt", encoding="utf-8") as fh:
            doc = json.load(fh)
        return _parse_yahoo_doc(doc, sym)["bars"]
    source = (source or "yahoo").lower()
    if source in ("auto", "dhan") and os.environ.get("DHAN_CLIENT_ID") and os.environ.get("DHAN_ACCESS_TOKEN"):
        try:
            return dhan_bars(sym, days=days)
        except Exception as e:
            if source == "dhan":
                raise
            source = "yahoo"
    if source == "dhan":
        raise DataError("Dhan data needs DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN in the environment")
    if is_future(sym):
        sym = yahoo_proxy(sym)
    rng = "60d" if days <= 60 else "60d"
    return yahoo_chart(sym, range_=rng, interval="5m")["bars"]


def latest_price(sym, source="yahoo"):
    sym = normalize_symbol(sym)
    if (source or "yahoo").lower() == "dhan" and os.environ.get("DHAN_CLIENT_ID"):
        try:
            return dhan_price(sym)
        except Exception:
            pass
    if is_future(sym):
        sym = yahoo_proxy(sym)
    doc = yahoo_chart(sym, range_="1d", interval="5m")
    price = doc.get("meta", {}).get("regularMarketPrice")
    ts = doc.get("meta", {}).get("regularMarketTime")
    if price is None:
        raise DataError("Yahoo has no live price for %s" % sym)
    return float(price), (epoch_to_ist(ts) if ts else None), "yahoo"


# ---------------------------------------------------------------------------
# DhanHQ
# ---------------------------------------------------------------------------

def dhan_ready():
    return bool(os.environ.get("DHAN_CLIENT_ID") and os.environ.get("DHAN_ACCESS_TOKEN"))


def get_dhan():
    if not dhan_ready():
        raise DataError("Dhan needs DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN in the environment")
    try:
        from dhanhq import DhanContext, dhanhq
    except Exception as e:
        raise DataError("install the Dhan SDK first: pip install dhanhq (%s)" % e)
    return dhanhq(DhanContext(os.environ["DHAN_CLIENT_ID"], os.environ["DHAN_ACCESS_TOKEN"]))


def _cache_dir():
    p = Path.cwd() / "trader" / ".cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_dhan_master(max_age_hours=24):
    path = _cache_dir() / "dhan_scrip_master.csv"
    fresh = path.exists() and (time.time() - path.stat().st_mtime) < max_age_hours * 3600
    if not fresh:
        try:
            req = urllib.request.Request(DHAN_MASTER_URL, headers={"User-Agent": UA})
            raw = urllib.request.urlopen(req, timeout=90).read()
            path.write_bytes(raw)
        except Exception as e:
            if not path.exists():
                raise DataError("could not download the Dhan security master (%s)" % e)
    rows = []
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        for r in csv.DictReader(fh):
            if r.get("SEM_EXM_EXCH_ID") == "NSE":
                rows.append(r)
    return rows


def _pick_equity(rows, bare):
    candidates = [r for r in rows
                  if r.get("SEM_SEGMENT") == "E"
                  and r.get("SEM_TRADING_SYMBOL", "").upper() == bare]
    if not candidates:
        return None
    order = {"EQ": 0, "BE": 1, "BZ": 2, "ES": 3, "ETF": 4}
    candidates.sort(key=lambda r: order.get(r.get("SEM_SERIES", ""), 9))
    return candidates[0]


def resolve_instrument(sym, expiry=None):
    """-> dict(security_id, exchange_segment, instrument_type, lot_size, tick_size, label)."""
    sym = str(sym or "").strip().upper()
    bare = bare_symbol(sym)
    if is_index(sym) or (bare in INDEX_SECURITY_IDS and not is_future(sym)):
        key = YAHOO_TO_INDEX.get(sym, bare)
        if key not in INDEX_SECURITY_IDS:
            raise DataError("unknown index %r" % sym)
        sid, label = INDEX_SECURITY_IDS[key]
        return {"security_id": sid, "exchange_segment": "IDX_I",
                "instrument_type": "INDEX", "lot_size": 1.0, "tick_size": 0.05,
                "label": "%s index" % label, "tradable": False}
    rows = load_dhan_master()
    if is_future(sym) or (expiry and bare in INDEX_SECURITY_IDS):
        under = bare
        cand = [r for r in rows
                if r.get("SEM_SEGMENT") == "D"
                and r.get("SEM_INSTRUMENT_NAME") == "FUTIDX"
                and r.get("SEM_TRADING_SYMBOL", "").upper().startswith(under + "-")]
        if expiry:
            cand = [r for r in cand if r.get("SEM_EXPIRY_DATE", "").startswith(expiry)]
        today = now_ist().date().isoformat()
        live = [r for r in cand if (r.get("SEM_EXPIRY_DATE") or "")[:10] >= today]
        cand = live or cand
        if not cand:
            raise DataError("no %s futures found in the Dhan master" % under)
        cand.sort(key=lambda r: r.get("SEM_EXPIRY_DATE", ""))
        r = cand[0]
        return {"security_id": r["SEM_SMST_SECURITY_ID"], "exchange_segment": "NSE_FNO",
                "instrument_type": "FUTIDX", "lot_size": float(r.get("SEM_LOT_UNITS") or 1),
                "tick_size": 0.05,
                "label": "%s (%s)" % (r.get("SEM_CUSTOM_SYMBOL") or r.get("SEM_TRADING_SYMBOL"),
                                      r.get("SEM_EXPIRY_DATE", "")[:10])}
    r = _pick_equity(rows, bare)
    if not r:
        raise DataError("Dhan has no NSE equity named %s; pass --security-id if you know it" % bare)
    return {"security_id": r["SEM_SMST_SECURITY_ID"], "exchange_segment": "NSE_EQ",
            "instrument_type": "EQUITY", "lot_size": float(r.get("SEM_LOT_UNITS") or 1),
            "tick_size": 0.05,
            "label": r.get("SEM_CUSTOM_SYMBOL") or bare}


def dhan_bars(sym, days=60, interval=5):
    inst = resolve_instrument(sym)
    dhan = get_dhan()
    end = now_ist()
    start = end - timedelta(days=max(days + 2, 7))
    resp = dhan.intraday_minute_data(
        security_id=inst["security_id"],
        exchange_segment=inst["exchange_segment"],
        instrument_type=inst["instrument_type"],
        from_date=start.strftime("%Y-%m-%d %H:%M:%S"),
        to_date=end.strftime("%Y-%m-%d %H:%M:%S"),
        interval=interval,
        oi=False,
    )
    if not isinstance(resp, dict) or resp.get("status") != "success":
        msg = resp.get("remarks") if isinstance(resp, dict) else resp
        raise DataError("Dhan intraday data failed: %s" % msg)
    d = resp.get("data") or {}
    if isinstance(d, list):
        rows = d
    else:
        stamps = d.get("timestamp") or []
        rows = []
        for i, ts in enumerate(stamps):
            try:
                rows.append({"timestamp": ts, "open": d["open"][i], "high": d["high"][i],
                             "low": d["low"][i], "close": d["close"][i],
                             "volume": (d.get("volume") or [0] * len(stamps))[i]})
            except (KeyError, IndexError):
                break
    bars = []
    for r in rows:
        try:
            bars.append({"t": epoch_to_ist(r["timestamp"]), "o": float(r["open"]),
                         "h": float(r["high"]), "l": float(r["low"]),
                         "c": float(r["close"]), "v": float(r.get("volume") or 0)})
        except (KeyError, TypeError, ValueError):
            continue
    bars.sort(key=lambda b: b["t"])
    if not bars:
        raise DataError("Dhan returned no intraday bars for %s" % sym)
    return bars


def dhan_price(sym):
    inst = resolve_instrument(sym)
    dhan = get_dhan()
    resp = dhan.ticker_data({inst["exchange_segment"]: [int(inst["security_id"])]})
    if not isinstance(resp, dict) or resp.get("status") != "success":
        raise DataError("Dhan quote failed: %s" % (resp.get("remarks") if isinstance(resp, dict) else resp))
    data = resp.get("data") or {}
    block = data.get(inst["exchange_segment"]) or {}
    row = block.get(str(inst["security_id"])) or block.get(int(inst["security_id"]))
    if not row and block:
        row = next(iter(block.values()))
    if not row:
        raise DataError("Dhan quote had no row for %s" % sym)
    price = row.get("last_price") or row.get("LTP")
    if price is None:
        raise DataError("Dhan quote had no last price for %s" % sym)
    return float(price), now_ist(), "dhan"


# ---------------------------------------------------------------------------
# Sessions / setups
# ---------------------------------------------------------------------------

def session_map(bars):
    out = {}
    for b in bars:
        m = bar_minute(b)
        if OPEN_MIN <= m < CLOSE_MIN:
            out.setdefault(b["t"].date(), {})[m] = b
    return out


def day_bars(bars, day):
    slots = session_map(bars).get(day, {})
    return [slots[m] for m in sorted(slots)]


def complete_sessions(bars, min_bars=70):
    """Full 75-slot sessions. Yahoo often drops the 15:20/15:25 bars, so a
    session with at least `min_bars` slots is padded flat from the last close."""
    out = []
    want = list(range(OPEN_MIN, CLOSE_MIN, BAR_MIN))
    for d, slots in sorted(session_map(bars).items()):
        if len(slots) < min_bars:
            continue
        rows, last = [], None
        for m in want:
            b = slots.get(m)
            if b is None:
                if last is None:
                    nxt = next((slots[k] for k in want if k > m and k in slots), None)
                    if nxt is None:
                        break
                    px = nxt["o"]
                else:
                    px = last
                b = {"t": datetime.combine(d, dtime(m // 60, m % 60)),
                     "o": px, "h": px, "l": px, "c": px, "v": 0.0}
            rows.append(b)
            last = b["c"]
        if len(rows) == len(want):
            out.append((d, rows))
    return out


def opening_range(rows, or_minutes=15):
    n = int(or_minutes) // BAR_MIN
    if n < 1 or len(rows) < n:
        raise DataError("not enough bars for a %s-minute opening range" % or_minutes)
    head = rows[:n]
    return {"minutes": int(or_minutes), "high": max(b["h"] for b in head),
            "low": min(b["l"] for b in head), "bars": n,
            "from": head[0]["t"], "to": head[-1]["t"] + timedelta(minutes=BAR_MIN)}


def orb_trigger(rows, or_minutes=15, cutoff=None):
    """First 5-minute close above the opening range after it completes."""
    rng = opening_range(rows, or_minutes)
    for i in range(rng["bars"], len(rows)):
        b = rows[i]
        if cutoff and bar_close(b) > cutoff:
            break
        if b["c"] > rng["high"]:
            base = [x["v"] for x in rows[:rng["bars"]] if x["v"]]
            avg = sum(base) / len(base) if base else 0
            return {"bar": i, "entry": b["c"], "at": bar_close(b),
                    "pct_over": (b["c"] / rng["high"] - 1) * 100,
                    "volume_ratio": (b["v"] / avg) if avg else None,
                    "opening_range": rng}
    return None


def level_trigger(rows, level, cutoff=None):
    for i, b in enumerate(rows):
        if cutoff and bar_close(b) > cutoff:
            break
        if b["c"] > level:
            return {"bar": i, "entry": b["c"], "at": bar_close(b), "level": level}
    return None


def entry_bar_index(entry_min, open_min=OPEN_MIN):
    """Index of the 5-minute bar that CLOSES at entry_min."""
    return max(0, (entry_min - open_min) // BAR_MIN - 1)
