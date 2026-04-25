import os
import time
import json
import warnings
from datetime import datetime, time as dtime

import yfinance as yf
import pandas as pd
import requests
import mplfinance as mpf
import urllib3

warnings.filterwarnings("ignore")
urllib3.disable_warnings()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

NOTIFIED_FILE = "notified_final.json"

SCAN_INTERVAL_SECONDS = 300
MARKET_START = dtime(9, 0)
MARKET_END = dtime(13, 35)

SCAN_ALL_MARKET = True
MAX_SCAN_STOCKS = 300
MAX_PUSH_SIGNALS = 5

my_stocks = [
    "2330.TW",
    "3023.TW",
    "3105.TWO",
    "2885.TW",
    "0050.TW",
    "00878.TW",
    "00981A.TW"
]


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
    return requests.get(url, params={"timeout": 10, "offset": offset}).json()


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
        {"url": "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2", "suffix": ".TW"},
        {"url": "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4", "suffix": ".TWO"}
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
    df = yf.download(symbol, period="6mo", interval="1d", progress=False, auto_adjust=False)
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
        status = "偏強，可以觀察買點🔥"
    elif score >= 60:
        status = "整理偏強，等突破或拉回"
    elif score >= 40:
        status = "普通，先觀察"
    else:
        status = "偏弱，不建議買"

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
    df = yf.download(symbol, period="5d", interval="5m", progress=False, auto_adjust=False)
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
    not_too_high = close <= ema20 * 1.05

    score = 0
    if trend:
        score += 30
    if breakout:
        score += 25
    if volume_strong:
        score += 25
    if strong_candle:
        score += 10
    if not_too_high:
        score += 10

    is_signal = trend and breakout and first_break and volume_strong and strong_candle and not_too_high

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


def buy_advice(r):
    close = r["close"]
    score = r["score"]
    high20 = r["high20"]
    stop_loss = r["stop_loss"]
    target = r["target"]
    ema20 = r.get("ema20")

    if score >= 80 and close > high20:
        return (
            f"✅ 建議：可以小量試單\n"
            f"進場：現價附近，或回測突破價 {high20} 不破再買\n"
            f"加碼：站穩突破價且量能續強\n"
            f"停損：跌破 {stop_loss}\n"
            f"出場：到 {target} 可分批賣，或跌破 EMA20 {ema20} 出場"
        )

    if score >= 60:
        return (
            f"🟡 建議：先觀察，不急追\n"
            f"進場：等突破 {high20}，或拉回 EMA20 {ema20} 附近轉強再買\n"
            f"停損：跌破 {stop_loss}\n"
            f"出場：到 {target} 可分批賣，或跌破 EMA20 {ema20} 出場"
        )

    return (
        f"❌ 建議：目前不建議買\n"
        f"進場：等重新站上 {high20} 且放量再考慮\n"
        f"停損：若已持有，跌破 {stop_loss} 要小心\n"
        f"出場：弱勢股不要攤平，等轉強再看"
    )


def plot_chart(symbol, info):
    df = yf.download(symbol, period="3mo", interval="1d", progress=False, auto_adjust=False)
    df = fix_df(df)

    if df.empty or len(df) < 30:
        return None

    df["EMA5"] = df["Close"].ewm(span=5).mean()
    df["EMA20"] = df["Close"].ewm(span=20).mean()
    df["EMA60"] = df["Close"].ewm(span=60).mean()
    df["HIGH20"] = df["High"].shift(1).rolling(20).max()

    buy_marker = [float("nan")] * len(df)
    buy_marker[-1] = float(df["Low"].iloc[-1]) * 0.98

    add_plots = [
        mpf.make_addplot(df["EMA5"], color="blue", width=1),
        mpf.make_addplot(df["EMA20"], color="purple", width=1),
        mpf.make_addplot(df["EMA60"], color="orange", width=1),
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
        title=f"{symbol} 進出場圖",
        savefig=file_name
    )

    return file_name


def plot_intraday_chart(symbol, info):
    df = yf.download(symbol, period="2d", interval="5m", progress=False, auto_adjust=False)
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

    file_name = f"{symbol.replace('.', '_')}_intraday_chart.png"

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
        title=f"{symbol} 5分K 盤中買點圖",
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
        f"停損：{r['stop_loss']}\n"
        f"目標：{r['target']}\n\n"
        f"{buy_advice(r)}\n\n"
        f"圖表：綠箭頭=買點｜紅線=突破｜黑線=停損｜綠線=目標"
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
        f"判斷：{r['status']}\n\n"
        f"📌 進出場建議\n"
        f"{buy_advice(r)}\n\n"
        f"圖表：綠箭頭=觀察買點｜紅線=突破前高｜黑線=停損｜綠線=目標"
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

    candidates = []

    for stock in all_stocks:
        try:
            r = analyze_intraday(stock)

            if r and r["is_signal"] and stock not in notified[today]:
                candidates.append(r)

        except Exception as e:
            print(stock, e)

    candidates = sorted(candidates, key=lambda x: x["score"], reverse=True)[:MAX_PUSH_SIGNALS]

    for r in candidates:
        try:
            chart = plot_intraday_chart(r["symbol"], r)
            message = format_intraday_signal(r)

            if chart:
                send_photo(message, chart)
            else:
                send_message(message)

            notified[today].append(r["symbol"])

        except Exception as e:
            print("推播失敗：", e)

    save_notified(notified)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"{now} 掃描完成，新訊號：{len(candidates)}")


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


def send_top10(all_stocks):
    results = []

    for stock in all_stocks[:MAX_SCAN_STOCKS]:
        try:
            r = analyze_daily(stock)
            if r:
                results.append(r)
        except Exception as e:
            print(stock, e)

    results = sorted(results, key=lambda x: x["score"], reverse=True)[:10]

    msg = "🏆 今日強勢股票 TOP10\n\n"

    for i, r in enumerate(results, start=1):
        msg += (
            f"{i}. {r['symbol']}｜{r['score']}分｜現價 {r['close']}\n"
            f"判斷：{r['status']}\n\n"
        )

    send_message(msg)


def main():
    send_message(
        "🔥 終極版 Bot 已啟動\n\n"
        "可用指令：\n"
        "2330 = 查股票分析 + 進出場圖\n"
        "/my = 我的股票監控\n"
        "/scan = 手動掃市場買點\n"
        "/top = 今日強勢股 TOP10"
    )

    if SCAN_ALL_MARKET:
        all_stocks = get_all_tw_stocks()[:MAX_SCAN_STOCKS]
    else:
        all_stocks = my_stocks

    offset = None
    last_scan_time = 0
    last_report_date = ""

    while True:
        try:
            updates = get_updates(offset)

            for update in updates.get("result", []):
                offset = update["update_id"] + 1
                text = update.get("message", {}).get("text", "")

                if not text:
                    continue

                if text == "/my":
                    send_my_stocks_report()
                    continue

                if text == "/scan":
                    send_message("開始手動掃描市場買點...")
                    scan_market(all_stocks)
                    continue

                if text == "/top":
                    send_message("開始計算今日強勢股 TOP10...")
                    send_top10(all_stocks)
                    continue

                symbol = convert_symbol(text)
                r = analyze_daily(symbol)

                if r:
                    chart = plot_chart(symbol, r)
                    if chart:
                        send_photo(format_daily_analysis(r), chart)
                    else:
                        send_message(format_daily_analysis(r))
                else:
                    send_message(f"找不到資料：{symbol}")

            today = datetime.now().strftime("%Y-%m-%d")
            now_time = datetime.now().time()

            if today != last_report_date and now_time >= dtime(8, 30):
                send_my_stocks_report()
                last_report_date = today

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