from datetime import datetime
import io
import os
import pandas as pd
import requests
import yfinance as yf

# --- CONFIGURATION PARAMETERS ---
TOTAL_TRADING_CAPITAL = 100000  # Total trading account balance (₹1,00,000)
RISK_PERCENT_PER_TRADE = 1.0  # Risk 1.0% (₹1,000) of capital per trade
MIN_DAILY_VOLUME = 500000  # Minimum 5 Lakh shares traded today for liquidity

# Telegram Configuration
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_BOT_TOKEN_HERE")
CHAT_ID = os.getenv("CHAT_ID", "YOUR_CHAT_ID_HERE")


def get_nifty200_tickers():
    """Fetches the live Nifty 200 stock list directly from the NSE website."""
    url = "https://archives.nseindia.com/content/indices/ind_nifty200list.csv"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
    }

    try:
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=headers, timeout=10)

        response = session.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        df = pd.read_csv(io.StringIO(response.text))
        tickers = [f"{symbol.strip()}.NS" for symbol in df["Symbol"]]
        print(f"Successfully fetched {len(tickers)} Nifty 200 tickers.")
        return tickers

    except Exception as e:
        print(
            f"⚠️ NSE Website busy ({e}). Falling back to core safe blue-chip stocks..."
        )
        return [
            "RELIANCE.NS",
            "TCS.NS",
            "INFY.NS",
            "HDFCBANK.NS",
            "ICICIBANK.NS",
            "LT.NS",
            "SBIN.NS",
        ]


def scan_nifty200_44sma(watchlist):
    """Downloads batch market data and applies 44 SMA pullback strategy math."""
    print("⏳ Downloading bulk 6-month data for all tickers via yfinance...")

    data = yf.download(
        watchlist, period="6mo", interval="1d", group_by="ticker", threads=True
    )

    qualified_setups = []
    allowed_loss_budget = TOTAL_TRADING_CAPITAL * (
        RISK_PERCENT_PER_TRADE / 100
    )

    for ticker in watchlist:
        try:
            if len(watchlist) == 1:
                df = data.dropna()
            else:
                df = data[ticker].dropna()

            if len(df) < 50:
                continue

            # 1. Volume Filter
            current_volume = df["Volume"].iloc[-1]
            if current_volume < MIN_DAILY_VOLUME:
                continue

            # 2. Compute Indicators
            df["44_SMA"] = df["Close"].rolling(window=44).mean()

            open_p = df["Open"].iloc[-1]
            close = df["Close"].iloc[-1]
            high = df["High"].iloc[-1]
            low = df["Low"].iloc[-1]

            ma_today = df["44_SMA"].iloc[-1]
            ma_yesterday = df["44_SMA"].iloc[-2]
            ma_5days_ago = df["44_SMA"].iloc[-6]

            # 3. Strategy Rules
            is_trending_up = (ma_today > ma_yesterday) and (
                ma_yesterday > ma_5days_ago
            )
            is_touching_ma = (low <= ma_today * 1.015) and (
                high >= ma_today * 0.985
            )
            is_green_candle = close > open_p
            candle_range = high - low
            is_bullish_bounce = (
                (close >= (low + (candle_range * 0.5)))
                if candle_range > 0
                else False
            )

            # 4. Position Sizing
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
                if risk_per_share <= 0:
                    continue

                shares_to_buy = int(allowed_loss_budget // risk_per_share)
                if shares_to_buy <= 0:
                    continue

                target_price = round(entry_price + (risk_per_share * 2), 2)
                total_capital_required = round(shares_to_buy * entry_price, 2)

                qualified_setups.append(
                    {
                        "Ticker": ticker.replace(".NS", ""),
                        "Signal Date": datetime.today().strftime("%Y-%m-%d"),
                        "Current Close (₹)": round(close, 2),
                        "44 SMA Line (₹)": round(ma_today, 2),
                        "Entry (Buy Above ₹)": entry_price,
                        "Stop Loss (₹)": stop_loss,
                        "Target (1:2 ₹)": target_price,
                        "Risk/Share (₹)": risk_per_share,
                        "Quantity": shares_to_buy,
                        "Total Capital Needed (₹)": total_capital_required,
                    }
                )

        except Exception:
            continue

    return qualified_setups


def format_telegram_5_fields(results):
    """Formats scanner results using strictly 5 requested fields in a portrait-optimized table."""
    if not results:
        return (
            "❌ <b>No stocks qualified for 44 SMA setup today.</b>\n\n"
            "💡 <i>Market Insight: Stocks are either extended or consolidating without a clean bounce.</i>"
        )

    date_str = results[0]["Signal Date"]
    msg = f"🚀 <b>44 SMA SETUPS — {date_str}</b>\n\n"
    msg += "<code>"
    # Header width trimmed down to 32 chars total to prevent line-wrapping on portrait mobile screens
    msg += f"{'TICKER':<9} {'44SMA':<6} {'ENTRY':<6} {'SL':<5} {'TGT':<6}\n"
    msg += "─" * 32 + "\n"

    for row in results:
        ticker = row["Ticker"][:8]  # Keep ticker short
        ma_line = f"{row['44 SMA Line (₹)']:.0f}"  # Whole number for space
        buy_above = f"{row['Entry (Buy Above ₹)']:.1f}"
        sl = f"{row['Stop Loss (₹)']:.1f}"
        target = f"{row['Target (1:2 ₹)']:.1f}"

        msg += f"{ticker:<9} {ma_line:<6} {buy_above:<6} {sl:<5} {target:<6}\n"

    msg += "</code>\n"
    msg += "💡 <i>Order Type: Stop-Loss Limit (SL-L)</i>"

    return msg


def send_telegram(message: str):
    """Sends HTML formatted messages to Telegram with chunking for safety."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    if len(message) > 4000:
        chunks = [message[i : i + 4000] for i in range(0, len(message), 4000)]
        for chunk in chunks:
            payload = {
                "chat_id": CHAT_ID,
                "text": chunk,
                "parse_mode": "HTML",
            }
            requests.post(url, json=payload)
    else:
        payload = {
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
        }
        requests.post(url, json=payload)


# --- MAIN RUNNER ---
if __name__ == "__main__":
    allowed_loss = TOTAL_TRADING_CAPITAL * (RISK_PERCENT_PER_TRADE / 100)
    print(
        f"🤖 Starting 44 SMA Fast Scanner | Capital: ₹{TOTAL_TRADING_CAPITAL:,} | Max Risk/Trade: {RISK_PERCENT_PER_TRADE}% (₹{allowed_loss:,.0f})"
    )

    print("📥 Step 1: Fetching Nifty 200 watchlist...")
    watchlist = get_nifty200_tickers()

    print(f"\n🔍 Step 2: Scanning {len(watchlist)} stocks...")
    results = scan_nifty200_44sma(watchlist)

    print("\n📊 Step 3: Compiling Trade Setups...")
    if not results:
        print("❌ No stocks qualified for the 44 SMA setup today.")
    else:
        result_df = pd.DataFrame(results)
        print("\n🚀 ACTIONABLE 44 SMA TRADE SETUPS FOUND:")
        pd.set_option("display.max_columns", None)
        pd.set_option("display.width", 1000)
        print(result_df.to_string(index=False))

        file_name = (
            f"Nifty200_44SMA_Setups_{datetime.today().strftime('%Y%m%d')}.xlsx"
        )
        result_df.to_excel(file_name, index=False)
        print(f"\n💾 Saved qualified setups to Excel: '{file_name}'")

    print("\n📱 Step 4: Formatting and sending Telegram alert...")
    formatted_msg = format_telegram_5_fields(results)
    send_telegram(formatted_msg)
    print("✅ Telegram notification sent successfully!")