from datetime import datetime
import io
import os
import pandas as pd
import requests
import yfinance as yf

# Configuration Parameters
TOTAL_TRADING_CAPITAL = 100000
RISK_PERCENT_PER_TRADE = 1.0
MIN_DAILY_VOLUME = 500000

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_BOT_TOKEN_HERE")
CHAT_ID = os.getenv("CHAT_ID", "YOUR_CHAT_ID_HERE")


def get_nifty200_tickers():
    url = "https://archives.nseindia.com/content/indices/ind_nifty200list.csv"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=headers, timeout=10)
        response = session.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        df = pd.read_csv(io.StringIO(response.text))
        return [f"{symbol.strip()}.NS" for symbol in df["Symbol"]]
    except Exception as e:
        print(f"⚠️ Error fetching Nifty 200 list, using fallback: {e}")
        return [
            "RELIANCE.NS",
            "TCS.NS",
            "INFY.NS",
            "HDFCBANK.NS",
            "ICICIBANK.NS",
            "LT.NS",
            "SBIN.NS",
        ]


def analyze_stock(df, ticker):
    """Analyzes a single stock dataframe for both 44 SMA and Darvas Box signals."""
    if len(df) < 60 or df["Volume"].iloc[-1] < MIN_DAILY_VOLUME:
        return None, None

    ticker_clean = ticker.replace(".NS", "")
    allowed_loss_budget = TOTAL_TRADING_CAPITAL * (
        RISK_PERCENT_PER_TRADE / 100
    )

    sma_setup = None
    darvas_setup = None

    # --- 1. 44 SMA PULLBACK LOGIC ---
    df["44_SMA"] = df["Close"].rolling(window=44).mean()
    open_p, close = df["Open"].iloc[-1], df["Close"].iloc[-1]
    high, low = df["High"].iloc[-1], df["Low"].iloc[-1]
    ma_today, ma_yesterday, ma_5days_ago = (
        df["44_SMA"].iloc[-1],
        df["44_SMA"].iloc[-2],
        df["44_SMA"].iloc[-6],
    )

    is_trending_up = (ma_today > ma_yesterday) and (
        ma_yesterday > ma_5days_ago
    )
    is_touching_ma = (low <= ma_today * 1.015) and (high >= ma_today * 0.985)
    is_green_candle = close > open_p
    candle_range = high - low
    is_bullish_bounce = (
        (close >= (low + (candle_range * 0.5))) if candle_range > 0 else False
    )

    if (
        is_trending_up
        and is_touching_ma
        and is_green_candle
        and is_bullish_bounce
    ):
        buffer = round(close * 0.002, 2)
        entry_price = round(high + buffer, 2)
        stop_loss = round(low - buffer, 2)
        risk_per_share = round(entry_price - stop_loss, 2)

        if risk_per_share > 0:
            target_price = round(entry_price + (risk_per_share * 2), 2)
            sma_setup = {
                "Ticker": ticker_clean,
                "Level": round(ma_today, 1),
                "Entry": entry_price,
                "SL": stop_loss,
                "Target": target_price,
            }

    # --- 2. DARVAS BOX BREAKOUT LOGIC ---
    df["20_Vol_Avg"] = df["Volume"].rolling(window=20).mean()

    # Box Top: Local peak 3 days ago that held for 3 consecutive sessions
    t_high = df["High"].iloc[-4]
    t_high_prev = df["High"].iloc[-5]
    is_box_top = (
        (t_high > t_high_prev)
        and (df["High"].iloc[-3] < t_high)
        and (df["High"].iloc[-2] < t_high)
    )

    if is_box_top:
        box_top = t_high
        box_bottom = min(
            df["Low"].iloc[-4], df["Low"].iloc[-3], df["Low"].iloc[-2]
        )
        latest_vol = df["Volume"].iloc[-1]
        avg_vol = df["20_Vol_Avg"].iloc[-1]

        # Breakout: Close > Box Top on high volume expansion (>= 1.3x avg)
        if (close > box_top) and (latest_vol >= (avg_vol * 1.3)):
            entry_price = round(close, 2)
            stop_loss = round(box_bottom, 2)
            box_height = box_top - box_bottom
            target_price = round(entry_price + box_height, 2)

            darvas_setup = {
                "Ticker": ticker_clean,
                "Level": round(box_top, 1),
                "Entry": entry_price,
                "SL": stop_loss,
                "Target": target_price,
            }

    return sma_setup, darvas_setup


def run_full_scan():
    watchlist = get_nifty200_tickers()
    print(f"🔄 Downloading data for {len(watchlist)} stocks...")

    data = yf.download(
        watchlist, period="6mo", interval="1d", group_by="ticker", threads=True
    )

    sma_results = []
    darvas_results = []

    for ticker in watchlist:
        try:
            df = data[ticker].dropna() if len(watchlist) > 1 else data.dropna()
            sma_s, darvas_s = analyze_stock(df, ticker)
            if sma_s:
                sma_results.append(sma_s)
            if darvas_s:
                darvas_results.append(darvas_s)
        except Exception:
            continue

    return sma_results, darvas_results


def format_table_message(title, results):
    if not results:
        return f"❌ <b>No stocks qualified for {title} setup today.</b>"

    date_str = datetime.today().strftime("%Y-%m-%d")
    msg = f"🚀 <b>{title} SETUPS — {date_str}</b>\n\n<code>"
    msg += f"{'TICKER':<9} {'LEVEL':<6} {'ENTRY':<6} {'SL':<5} {'TGT':<6}\n"
    msg += "─" * 32 + "\n"

    for r in results:
        ticker = r["Ticker"][:8]
        level = f"{r['Level']:.0f}"
        entry = f"{r['Entry']:.1f}"
        sl = f"{r['SL']:.1f}"
        target = f"{r['Target']:.1f}"
        msg += f"{ticker:<9} {level:<6} {entry:<6} {sl:<5} {target:<6}\n"

    msg += "</code>\n💡 <i>Order Type: Stop-Loss Limit (SL-L)</i>"
    return msg


def send_telegram_broadcast(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    chat_ids = [c.strip() for c in CHAT_ID.split(",") if c.strip()]

    for cid in chat_ids:
        payload = {
            "chat_id": cid,
            "text": message,
            "parse_mode": "HTML",
        }
        try:
            res = requests.post(url, json=payload, timeout=10)
            if res.status_code == 200:
                print(f"✅ Successfully sent alert to Chat ID: {cid}")
            else:
                print(
                    f"❌ Failed to send to {cid}: {res.status_code} - {res.text}"
                )
        except Exception as e:
            print(f"⚠️ Error broadcasting to {cid}: {e}")


if __name__ == "__main__":
    print("🔎 Starting Scan...")
    sma_signals, darvas_signals = run_full_scan()

    sma_msg = format_table_message("44 SMA PULLBACK", sma_signals)
    darvas_msg = format_table_message("DARVAS BOX BREAKOUT", darvas_signals)

    print("\n--- 44 SMA Broadcast ---")
    send_telegram_broadcast(sma_msg)

    print("\n--- Darvas Box Broadcast ---")
    send_telegram_broadcast(darvas_msg)
    print("\n🎉 Scan & Broadcast Completed!")