import os
import sqlite3
import json
import logging
from datetime import datetime, timedelta
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# Common symbol aliases mapped to Yahoo Finance tickers
SYMBOL_MAP = {
    # Forex
    "EURUSD": "EURUSD=X",
    "EUR/USD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "USD/JPY": "USDJPY=X",
    "AUDUSD": "AUDUSD=X",
    "AUD/USD": "AUDUSD=X",
    "USDCAD": "USDCAD=X",
    "USD/CAD": "USDCAD=X",
    "USDCHF": "USDCHF=X",
    "USD/CHF": "USDCHF=X",
    "NZDUSD": "NZDUSD=X",
    "NZD/USD": "NZDUSD=X",
    "EURJPY": "EURJPY=X",
    "EUR/JPY": "EURJPY=X",
    "GBPJPY": "GBPJPY=X",
    "GBP/JPY": "GBPJPY=X",
    "EURGBP": "EURGBP=X",
    "EUR/GBP": "EURGBP=X",

    # Commodities / Metals
    "XAUUSD": "GC=F",
    "XAU/USD": "GC=F",
    "GOLD": "GC=F",
    "EMAS": "GC=F",
    "XAGUSD": "SI=F",
    "SILVER": "SI=F",
    "USOIL": "CL=F",
    "WTI": "CL=F",

    # Indices
    "NASDAQ": "NQ=F",
    "NAS100": "NQ=F",
    "USTEC": "NQ=F",
    "NDX": "^IXIC",
    "QQQ": "QQQ",
    "US30": "YM=F",
    "DJI": "^DJI",
    "US500": "ES=F",
    "SPX": "^GSPC",
    "DAX": "^GDAXI",

    # Crypto
    "BTCUSD": "BTC-USD",
    "BTC/USD": "BTC-USD",
    "BTCUSDT": "BTC-USD",
    "ETHUSD": "ETH-USD",
    "ETH/USD": "ETH-USD",
    "ETHUSDT": "ETH-USD",
    "SOLUSD": "SOL-USD",
    "SOLUSDT": "SOL-USD",
}

CACHE_DB_PATH = os.path.join(os.path.dirname(__file__), "market_cache.db")

VALID_INTERVALS = {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "2h", "4h", "1d", "1w", "1wk", "1mo"}
VALID_PERIODS = {"1d", "5d", "7d", "30d", "60d", "180d", "1mo", "3mo", "6mo", "1y", "2y", "3y", "5y", "10y", "730d", "max"}

INTERVAL_ALIASES = {"60m": "1h", "1w": "1wk", "1wk": "1wk"}
PERIOD_ALIASES = {"7d": "7d", "1mo": "1mo", "3mo": "3mo", "6mo": "6mo", "730d": "730d"}


def init_cache_db():
    conn = sqlite3.connect(CACHE_DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS candle_cache (
            symbol TEXT,
            interval TEXT,
            timestamp INTEGER,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            fetched_at INTEGER DEFAULT 0,
            PRIMARY KEY (symbol, interval, timestamp)
        )
        """
    )
    columns = [row[1] for row in cursor.execute("PRAGMA table_info(candle_cache)").fetchall()]
    if "fetched_at" not in columns:
        cursor.execute("ALTER TABLE candle_cache ADD COLUMN fetched_at INTEGER DEFAULT 0")
    conn.commit()
    conn.close()


init_cache_db()


def resolve_symbol(symbol: str) -> tuple[str, str]:
    """Returns (display_name, yfinance_ticker)."""
    cleaned = symbol.strip().upper()
    if cleaned in SYMBOL_MAP:
        return cleaned, SYMBOL_MAP[cleaned]
    if len(cleaned) == 6 and cleaned.isalpha() and not cleaned.endswith("=X"):
        return cleaned, f"{cleaned}=X"
    return cleaned, cleaned


def _period_seconds(period: str) -> int:
    if period.endswith("d"):
        return int(period[:-1]) * 86400
    if period.endswith("y"):
        return int(period[:-1]) * 31536000
    if period == "max":
        return 31536000 * 10
    return 86400


def _normalize_period(fetch_interval: str, period: str) -> tuple[str, str | None]:
    warning = None

    if fetch_interval == "1m" and period not in ["1d", "5d", "7d"]:
        warning = "Periode terlalu panjang untuk 1m; dipangkas ke 7d agar sesuai batas Yahoo Finance."
        return "7d", warning
    if fetch_interval in ["2m", "5m", "15m", "30m", "90m"] and period in ["180d", "1y", "2y", "3y", "5y", "10y", "max"]:
        warning = "Periode terlalu panjang untuk interval ini; dipangkas ke 60d agar sesuai batas Yahoo Finance."
        return "60d", warning
    if fetch_interval in ["1h", "4h"] and period in ["3y", "5y", "10y", "max"]:
        warning = "Periode terlalu panjang untuk 1h/4h; dipangkas ke 730d agar sesuai batas Yahoo Finance."
        return "730d", warning
    return period, warning


def _cache_get(symbol: str, interval: str, start_ts: int, end_ts: int) -> list[dict]:
    conn = sqlite3.connect(CACHE_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT symbol, interval, timestamp, open, high, low, close, volume, fetched_at
            FROM candle_cache
            WHERE symbol = ? AND interval = ? AND timestamp BETWEEN ? AND ?
            ORDER BY timestamp ASC
            """,
            (symbol, interval, start_ts, end_ts),
        ).fetchall()
        if not rows:
            rows = conn.execute(
                """
                SELECT symbol, interval, timestamp, open, high, low, close, volume, fetched_at
                FROM candle_cache
                WHERE symbol = ? AND interval = ?
                ORDER BY timestamp DESC
                LIMIT 200
                """,
                (symbol, interval),
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _cache_put(symbol: str, interval: str, candles: list[dict]):
    if not candles:
        return
    conn = sqlite3.connect(CACHE_DB_PATH)
    try:
        cursor = conn.cursor()
        for candle in candles:
            cursor.execute(
                """
                INSERT OR REPLACE INTO candle_cache
                (symbol, interval, timestamp, open, high, low, close, volume, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    interval,
                    int(candle["time"]),
                    float(candle["open"]),
                    float(candle["high"]),
                    float(candle["low"]),
                    float(candle["close"]),
                    float(candle.get("volume", 0.0)),
                    int(__import__('time').time()),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def fetch_market_data(symbol: str, interval: str = "1h", period: str = "60d", start_date: str = None, end_date: str = None) -> dict:
    """Fetch OHLCV candles from Yahoo Finance with local caching and safe period validation."""
    display_symbol, yf_ticker = resolve_symbol(symbol)

    normalized_interval = INTERVAL_ALIASES.get(interval, interval)
    if normalized_interval not in VALID_INTERVALS:
        return {"error": f"Interval '{interval}' tidak didukung. Gunakan salah satu: {sorted(VALID_INTERVALS)}", "code": "BAD_INPUT"}
    if period not in VALID_PERIODS and not (start_date and end_date):
        return {"error": f"Period '{period}' tidak didukung. Gunakan salah satu: {sorted(VALID_PERIODS)}", "code": "BAD_INPUT"}

    fetch_interval = normalized_interval
    interval = normalized_interval
    resample_target = None
    if interval == "4h":
        fetch_interval = "1h"
        resample_target = "4h"

    if start_date and end_date:
        normalized_period = period
        warning = None
    else:
        normalized_period, warning = _normalize_period(fetch_interval, period)
        if warning:
            period = normalized_period

    now_ts = int(__import__('time').time())
    if start_date and end_date:
        start_ts = int(pd.Timestamp(start_date).timestamp())
        end_ts = int(pd.Timestamp(end_date).timestamp())
    else:
        end_ts = now_ts
        start_ts = end_ts - _period_seconds(period)

    cache_rows = _cache_get(yf_ticker, interval, start_ts, end_ts)
    interval_seconds = {
        "1m": 60,
        "2m": 120,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "90m": 5400,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400,
        "1w": 604800,
        "1mo": 2592000,
    }.get(interval, 3600)
    cache_is_fresh = bool(cache_rows) and (now_ts - int(cache_rows[-1]["fetched_at"])) <= 300 and (end_ts - int(cache_rows[-1]["timestamp"])) <= interval_seconds

    fallback_warning = None
    fallback_used = False
    note = None
    if yf_ticker.endswith("=F"):
        note = "Data ini berasal dari kontrak futures, bukan harga spot. Hasil bisa berbeda dari broker spot Anda."

    try:
        if start_date and end_date:
            df = yf.download(yf_ticker, start=start_date, end=end_date, interval=fetch_interval, progress=False)
        else:
            df = yf.download(yf_ticker, period=period, interval=fetch_interval, progress=False)

        if df is None or len(df) == 0:
            if display_symbol in ["XAUUSD", "GOLD"]:
                fallback_warning = "Data memakai XAUUSD=X sebagai fallback, skala harga bisa berbeda dari kontrak gold futures."
                df = yf.download("XAUUSD=X", start=start_date, end=end_date, period=period, interval=fetch_interval, progress=False)
            elif display_symbol in ["NASDAQ", "NAS100"]:
                fallback_warning = "Data memakai QQQ, skala harga berbeda dari indeks Nasdaq futures."
                df = yf.download("QQQ", start=start_date, end=end_date, period=period, interval=fetch_interval, progress=False)

        if df is None or len(df) == 0:
            if cache_rows:
                candles = []
                for row in cache_rows:
                    candles.append({
                        "time": int(row["timestamp"]),
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row["volume"] or 0.0),
                    })
                return {
                    "success": True,
                    "symbol": display_symbol,
                    "yf_ticker": yf_ticker,
                    "interval": interval,
                    "count": len(candles),
                    "candles": candles,
                    "from_cache": True,
                    "warning": fallback_warning or warning,
                    "note": note,
                    "period_applied": period,
                    "clipped": False,
                    "first_time": min(c["time"] for c in candles) if candles else None,
                    "last_time": max(c["time"] for c in candles) if candles else None,
                }
            return {"error": f"Tidak ada data ditemukan untuk simbol '{display_symbol}' ({yf_ticker})", "code": "NO_DATA"}

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        if resample_target:
            df = df.resample(resample_target, origin='start_day').agg({
                'Open': 'first',
                'High': 'max',
                'Low': 'min',
                'Close': 'last',
                'Volume': 'sum',
            }).dropna()

        df = df.dropna(subset=['Open', 'High', 'Low', 'Close'])
        if fallback_warning:
            fallback_used = True

        candles = []
        for idx, row in df.iterrows():
            ts = int(idx.timestamp())
            o = float(row['Open'])
            h = float(row['High'])
            l = float(row['Low'])
            c = float(row['Close'])
            v = float(row.get('Volume', 0)) if not pd.isna(row.get('Volume', 0)) else 0.0
            candles.append({
                "time": ts,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": v,
            })

        if candles:
            closes = [float(c["close"]) for c in candles]
            median_close = float(pd.Series(closes).median())
            if median_close >= 1000:
                decimals = 2
            elif median_close >= 10:
                decimals = 3
            else:
                decimals = 5
            for candle in candles:
                candle["open"] = round(float(candle["open"]), decimals)
                candle["high"] = round(float(candle["high"]), decimals)
                candle["low"] = round(float(candle["low"]), decimals)
                candle["close"] = round(float(candle["close"]), decimals)
                candle["volume"] = round(float(candle.get("volume", 0)) if not pd.isna(candle.get("volume", 0)) else 0.0, 2)

        unique_candles = []
        seen_times = set()
        for candle in sorted(candles, key=lambda x: x["time"]):
            if candle["time"] not in seen_times:
                seen_times.add(candle["time"])
                unique_candles.append(candle)

        _cache_put(yf_ticker, interval, unique_candles)

        response = {
            "success": True,
            "symbol": display_symbol,
            "yf_ticker": yf_ticker,
            "interval": interval,
            "count": len(unique_candles),
            "candles": unique_candles,
            "warning": fallback_warning or warning,
            "note": note,
            "period_applied": period,
            "clipped": bool(warning),
            "first_time": unique_candles[0]["time"] if unique_candles else None,
            "last_time": unique_candles[-1]["time"] if unique_candles else None,
        }
        if fallback_used:
            response["warning"] = fallback_warning
        return response
    except Exception as e:
        logger.exception("Error fetching market data")
        if cache_rows:
            candles = []
            for row in cache_rows:
                candles.append({
                    "time": int(row["timestamp"]),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"] or 0.0),
                })
            return {
                "success": True,
                "symbol": display_symbol,
                "yf_ticker": yf_ticker,
                "interval": interval,
                "count": len(candles),
                "candles": candles,
                "from_cache": True,
                "warning": fallback_warning or warning,
                "note": note,
                "period_applied": period,
                "clipped": bool(warning),
                "first_time": min(c["time"] for c in candles) if candles else None,
                "last_time": max(c["time"] for c in candles) if candles else None,
            }
        return {"error": str(e), "code": "UPSTREAM"}


def parse_csv_data(content: str) -> dict:
    """
    Parse uploaded CSV data into standard candle list.
    Handles MetaTrader 4/5 exports and standard TradingView exports.
    """
    import io
    try:
        sample = content[:2048]
        if '\t' in sample and ',' not in sample:
            sep = '\t'
        elif ';' in sample and ',' not in sample:
            sep = ';'
        else:
            sep = ','

        df = pd.read_csv(io.StringIO(content), sep=sep)
        df.columns = [str(c).strip().strip('<>').lower() for c in df.columns]

        if 'datetime' in df.columns:
            df['datetime'] = pd.to_datetime(df['datetime'], errors='coerce', utc=True)
        elif 'date' in df.columns and 'time' in df.columns:
            datetime_str = df['date'].astype(str) + ' ' + df['time'].astype(str)
            df['datetime'] = pd.to_datetime(datetime_str, errors='coerce', utc=True)
        elif 'date' in df.columns:
            df['datetime'] = pd.to_datetime(df['date'], errors='coerce', utc=True)
        elif 'time' in df.columns:
            if pd.to_numeric(df['time'], errors='coerce').notnull().all():
                df['datetime'] = pd.to_datetime(df['time'], unit='s', errors='coerce', utc=True)
            else:
                df['datetime'] = pd.to_datetime(df['time'], errors='coerce', utc=True)
        else:
            df['datetime'] = pd.to_datetime(df.iloc[:, 0], errors='coerce', utc=True)

        df = df.dropna(subset=['datetime']).copy()
        df = df.sort_values('datetime')
        df = df.drop_duplicates(subset='datetime', keep='last')

        open_col = next((c for c in df.columns if 'open' in c), None)
        high_col = next((c for c in df.columns if 'high' in c), None)
        low_col = next((c for c in df.columns if 'low' in c), None)
        close_col = next((c for c in df.columns if 'close' in c), None)
        vol_col = next((c for c in df.columns if c in {'tick_volume', 'tickvol', 'vol', 'volume', 'v'}), None)

        if not all([open_col, high_col, low_col, close_col]):
            return {"error": "Format CSV harus memiliki kolom Open, High, Low, Close"}

        candles = []
        for _, row in df.iterrows():
            ts = int(pd.Timestamp(row['datetime']).timestamp())
            candles.append({
                "time": ts,
                "open": float(row[open_col]),
                "high": float(row[high_col]),
                "low": float(row[low_col]),
                "close": float(row[close_col]),
                "volume": float(row[vol_col]) if vol_col and not pd.isna(row[vol_col]) else 0,
            })

        candles = sorted(candles, key=lambda x: x["time"])
        return {
            "success": True,
            "symbol": "CUSTOM_CSV",
            "interval": "custom",
            "count": len(candles),
            "candles": candles,
        }
    except Exception as e:
        return {"error": f"Gagal membaca CSV: {str(e)}"}

