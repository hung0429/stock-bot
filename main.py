import yfinance as yf
import pandas as pd
import requests
import mplfinance as mpf
import json
import os
import time
import urllib3
import warnings
from datetime import datetime, time as dtime

warnings.filterwarnings("ignore")
urllib3.disable_warnings()

# ===== Telegram 設定 =====
BOT_TOKEN = "8697923275:AAE_fft87jcIRMwFu6tDnZeZ4gq-nk-ecJ8"
CHAT_ID = "5715057919"

NOTIFIED_FILE = "notified_final.json"

# ===== 你的股票 / 持股 / 關注股 =====
my_stocks = [
    "2330.TW",
    "3023.TW",
    "3105.TWO",
    "2885.TW",
    "0050.TW",
    "00878.TW",
    "00981A.TW"
]

# ===== 盤中掃描設定 =====
SCAN_INTERVAL_SECONDS = 300  # 5分鐘
MARKET_START = dtime(9, 0)
MARKET_END = dtime(13, 35)

# True = 掃全市場，會比較久
SCAN_ALL_MARKET = True


def send_message(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": message})


def send_photo(message, image):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    with open(image, "rb") as img:
        requests.post(
            url,
            data={"chat_id": CHAT_ID, "caption": message},
            files={"photo": img}
        )


def get_updates(offset=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"timeout": 10, "offset": offset}
    return requests.get(url, params=params).json()


def load_notified():
    if not os.path.exists(NOTIFIED_FILE):
        return {}
    with open(NOTIFIED_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_notified(data):
    with open(NOTIFIED_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def fix_df(df):
    if df.empty:
        return df
    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df.columns = df.columns.get_level_values(0)
    return df


def get_value(row, col):
    value = row[col]
    if hasattr(value, "iloc"):
        return float(value.iloc[0])
    return float(value)


def convert_symbol(text):
    text = text.strip().upper()

    if text.endswith(".TW") or text.endswith(".TWO"):
        return text

    otc_codes = ["3105"]

    if text in otc_codes:
        return text + ".TWO"

    return text + ".TW"


def get_all_tw_stocks():
    stock_list = []

    markets = [
        {
            "url": "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2",
            "suffix": ".TW"
        },
        {
            "url": "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4",
            "suffix": ".TWO"
        }
    ]

    headers = {"User-Agent": "Mozilla/5.0"}

    for market in markets:
        try:
            response = requests.get(
                market["url"],
                headers=headers,
                timeout=20,
                verify=False
            )
            response.encoding = "big5"

            df = pd.read_html(response.text)[0]
            df.columns = df.iloc[0]
            df = df[1:]

            for item in df["有價證券代號及名稱"]:
                code = str(item).split()[0]
                if code.isdigit() and len(code) == 4:
                    stock_list.append(code + market["suffix"])

        except Exception as e:
            print("抓股票清單失敗：", e)

    return sorted(list(set(stock_list)))


def analyze_daily(symbol):
    df = yf.download(
        symbol,
        period="6mo",
        interval="1d",
        progress=False,
        auto_adjust=False
    )

    df = fix_df(df)

    if df.empty or len(df) < 60:
        return None

    df["EMA5"] = df["Close"].ewm(span=5).mean()
    df["EMA20"] = df["Close"].ewm(span=20).mean()
    df["EMA60"] = df["Close"].ewm(span=60).mean()
    df["VOL20"] = df["Volume"].rolling(20).mean()
    df["HIGH20"] = df["High"].shift(1).rolling(20).max()
    df["LOW10"] = df["Low"].rolling(10).min()

    last = df.iloc[-1]

    close = get_value(last, "Close")
    ema5 = get_value(last, "EMA5")
    ema20 = get_value(last, "EMA20")
    ema60 = get_value(last, "EMA60")
    volume = get_value(last, "Volume")
    vol20 = get_value(last, "VOL20")
    high20 = get_value(last, "HIGH20")
    low10 = get_value(last, "LOW10")

    if vol20 == 0:
        return None

    trend = ema5 > ema20 > ema60
    breakout = close > high20
    volume_strong = volume > vol20 * 1.8

    score = 0
    if trend:
        score += 35
    if breakout:
        score += 35
    if volume_strong:
        score += 30

    stop_loss = min(ema20, low10)
    risk = close - stop_loss
    target = close + risk * 2

    if score >= 80:
        status = "偏強，有機會發動🔥"
    elif score >= 60:
        status = "整理偏強，觀察突破"
    elif score >= 40:
        status = "普通，先觀察"
    else:
        status = "偏弱，不急"

    return {
        "symbol": symbol,
        "close": round(close, 2),
        "score": score,
        "volume_rate": round(volume / vol20, 2),
        "high20": round(high20, 2),
        "ema5": round(ema5, 2),
        "ema20": round(ema20, 2),
        "ema60": round(ema60, 2),
        "stop_loss": round(stop_loss, 2),
        "target": round(target, 2),
        "status": status
    }


def analyze_intraday(symbol):
    df = yf.download(
        symbol,
        period="5d",
        interval="5m",
        progress=False,
        auto_adjust=False
    )

    df = fix_df(df)

    if df.empty or len(df) < 80:
        return None

    df["EMA5"] = df["Close"].ewm(span=5).mean()
    df["EMA20"] = df["Close"].ewm(span=20).mean()
    df["VOL20"] = df["Volume"].rolling(20).mean()
    df["HIGH20"] = df["High"].shift(1).rolling(20).max()
    df["LOW10"] = df["Low"].rolling(10).min()

    last = df.iloc[-1]
    prev = df.iloc[-2]

    close = get_value(last, "Close")
    open_price = get_value(last, "Open")
    high = get_value(last, "High")
    ema5 = get_value(last, "EMA5")
    ema20 = get_value(last, "EMA20")
    volume = get_value(last, "Volume")
    vol20 = get_value(last, "VOL20")
    high20 = get_value(last, "HIGH20")
    low10 = get_value(last, "LOW10")

    if vol20 == 0:
        return None

    trend = ema5 > ema20
    breakout = close > high20
    first_break = breakout and get_value(prev, "Close") <= get_value(prev, "HIGH20")
    volume_strong = volume > vol20 * 1.8
    strong_candle = close > open_price and close >= high * 0.97
    not_too_far = close <= ema20 * 1.05

    score = 0
    if trend:
        score += 30
    if breakout:
        score += 25
    if volume_strong:
        score += 25
    if strong_candle:
        score += 10
    if not_too_far:
        score += 10

    is_signal = trend and breakout and first_break and volume_strong and strong_candle and not_too_far

    stop_loss = min(ema20, low10)
    risk = close - stop_loss
    target = close + risk * 2

    return {
        "symbol": symbol,
        "close": round(close, 2),
        "score": score,
        "volume_rate": round(volume / vol20, 2),
        "high20": round(high20, 2),
        "ema5": round(ema5, 2),
        "ema20": round(ema20, 2),
        "stop_loss": round(stop_loss, 2),
        "target": round(target, 2),
        "is_signal": is_signal
    }


def plot_chart(symbol, info):
    df = yf.download(
        symbol,
        period="2d",
        interval="5m",
        progress=False,
        auto_adjust=False
    )

    df = fix_df(df)

    if df.empty or len(df) < 30:
        return None

    df["EMA5"] = df["Close"].ewm(span=5).mean()
    df["EMA20"] = df["Close"].ewm(span=20).mean()
    df["HIGH20"] = df["High"].shift(1).rolling(20).max()

    buy_marker = [float("nan")] * len(df)
    buy_marker[-1] = float(df["Low"].iloc[-1]) * 0.98

    add_plots = [
        mpf.make_addplot(df["EMA5"], color="blue", width=1),
        mpf.make_addplot(df["EMA20"], color="purple", width=1),
        mpf.make_addplot(df["HIGH20"], color="red", width=1),
        mpf.make_addplot(
            buy_marker,
            type="scatter",
            marker="^",
            markersize=180,
            color="green"
        )
    ]

    file_name = f"{symbol.replace('.', '_')}_chart.png"

    mpf.plot(
        df,
        type="candle",
        style="yahoo",
        volume=True,
        addplot=add_plots,
        hlines=dict(
            hlines=[info["high20"], info["stop_loss"], info["target"]],
            colors=["red", "black", "green"],
            linestyle="--",
            linewidths=1
        ),
        title=f"{symbol} 5分K 買點圖",
        savefig=file_name
    )

    return file_name


def format_intraday_signal(r):
    return (
        f"🚀 盤中新買點\n"
        f"📌 {r['symbol']}\n"
        f"現價：{r['close']}\n"
        f"強度：{r['score']}/100\n"
        f"量能：{r['volume_rate']}倍\n"
        f"突破價：{r['high20']}\n"
        f"EMA5 / EMA20：{r['ema5']} / {r['ema20']}\n"
        f"參考停損：{r['stop_loss']}\n"
        f"參考目標：{r['target']}\n\n"
        f"綠箭頭＝買點\n"
        f"紅線＝突破前高\n"
        f"黑線＝停損\n"
        f"綠線＝目標"
    )

def buy_advice(r):
    close = r["close"]
    score = r["score"]
    high20 = r["high20"]
    ema20 = r.get("ema20", None)
    stop_loss = r["stop_loss"]

    if score >= 80 and close > high20:
        advice = "可以小量試單，屬於突破買點🔥"
        buy_zone = f"現價附近或回測突破價 {high20}"
    elif score >= 60:
        advice = "可以觀察，不建議急追"
        buy_zone = f"等拉回 EMA20 附近 {ema20}，或突破 {high20} 再買"
    elif score >= 40:
        advice = "暫時不建議買，等型態轉強"
        buy_zone = f"等站上突破價 {high20}"
    else:
        advice = "不建議買，趨勢偏弱"
        buy_zone = "先不要進場"

    return (
        f"建議：{advice}\n"
        f"買點：{buy_zone}\n"
        f"停損：跌破 {stop_loss} 要小心"
    )
def format_daily_analysis(r):
    return (
        f"📊 股票分析：{r['symbol']}\n\n"
        f"現價：{r['close']}\n"
        f"強度：{r['score']}/100\n"
        f"量能：{r['volume_rate']}倍\n"
        f"突破前高：{r['high20']}\n"
        f"EMA5/20/60：{r['ema5']} / {r['ema20']} / {r['ema60']}\n"
        f"停損：{r['stop_loss']}\n"
        f"目標：{r['target']}\n\n"
        f"判斷：{r['status']}"
    )


def is_market_time():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    return MARKET_START <= now.time() <= MARKET_END


def scan_market(all_stocks):
    today = datetime.now().strftime("%Y-%m-%d")
    notified = load_notified()

    if today not in notified:
        notified = {today: []}

    found = 0

    for stock in all_stocks:
        try:
            r = analyze_intraday(stock)

            if r and r["is_signal"] and stock not in notified[today]:
                chart = plot_chart(stock, r)
                message = format_intraday_signal(r)

                if chart:
                    send_photo(message, chart)
                else:
                    send_message(message)

                notified[today].append(stock)
                found += 1

        except Exception as e:
            print(stock, e)

    save_notified(notified)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"{now} 掃描完成，新訊號：{found}")


def send_my_stocks_report():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    msg = f"💰 我的股票監控\n時間：{now}\n\n"

    for stock in my_stocks:
        try:
            r = analyze_daily(stock)
            if r:
                msg += format_daily_analysis(r) + "\n\n"
        except Exception as e:
            msg += f"{stock} 分析失敗：{e}\n\n"

    send_message(msg)


def main():
    send_message(
        "🔥 整合最終版已啟動\n\n"
        "你可以傳股票代號，例如：2330、3023、3105\n"
        "系統也會在盤中自動掃描買點。"
    )

    if SCAN_ALL_MARKET:
        all_stocks = get_all_tw_stocks()
    else:
        all_stocks = my_stocks

    offset = None
    last_scan_time = 0
    last_report_date = ""

    while True:
        try:
            # ===== 接收你傳的股票代號 =====
            updates = get_updates(offset)

            for update in updates.get("result", []):
                offset = update["update_id"] + 1

                message = update.get("message", {})
                text = message.get("text", "")

                if not text:
                    continue

                if text == "/my":
                    send_my_stocks_report()
                    continue

                if text == "/scan":
                    send_message("開始手動掃描市場...")
                    scan_market(all_stocks)
                    continue

                symbol = convert_symbol(text)
                result = analyze_daily(symbol)

                if result:
                    send_message(format_daily_analysis(result))
                else:
                    send_message(f"找不到資料：{symbol}")

            # ===== 每天自動回報你的股票 =====
            today = datetime.now().strftime("%Y-%m-%d")
            now_time = datetime.now().time()

            if today != last_report_date and now_time >= dtime(8, 30):
                send_my_stocks_report()
                last_report_date = today

            # ===== 盤中每5分鐘自動掃描 =====
            if is_market_time():
                now_ts = time.time()

                if now_ts - last_scan_time >= SCAN_INTERVAL_SECONDS:
                    scan_market(all_stocks)
                    last_scan_time = now_ts

        except Exception as e:
            print("系統錯誤：", e)

        time.sleep(2)


if __name__ == "__main__":
    main()