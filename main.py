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

# =========================
# 基本設定
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

NOTIFIED_FILE = "notified_final.json"

SCAN_INTERVAL_SECONDS = 300
MARKET_START = dtime(9, 0)
MARKET_END = dtime(13, 35)

SCAN_ALL_MARKET = True
MAX_SCAN_STOCKS = 300
MAX_PUSH_SIGNALS = 3

# 你的持股 / 關注股
MY_STOCKS = [
    "2330.TW",     # 台積電
    "3023.TW",     # 信邦
    "3105.TWO",    # 穩懋，上櫃用 .TWO
    "2885.TW",     # 元大金
    "0050.TW",
    "00878.TW",
    "00981A.TW"
]

# 上櫃特殊代號，可自行增加
OTC_CODES = [
    "3105"
]


# =========================
# Telegram
# =========================
def send_message(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": message
        }
    )


def send_photo(message, image_path):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"

    with open(image_path, "rb") as img:
        requests.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "caption": message
            },
            files={
                "photo": img
            }
        )


def get_updates(offset=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"

    response = requests.get(
        url,
        params={
            "timeout": 10,
            "offset": offset
        }
    )

    return response.json()


# =========================
# 工具函數
# =========================
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

    if text in OTC_CODES:
        return text + ".TWO"

    return text + ".TW"


def is_market_time():
    now = datetime.now()

    if now.weekday() >= 5:
        return False

    return MARKET_START <= now.time() <= MARKET_END


# =========================
# 全市場清單
# =========================
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

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

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


# =========================
# 日K分析
# =========================
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
    not_too_high = close <= ema20 * 1.12

    score = 0

    if trend:
        score += 35
    if breakout:
        score += 35
    if volume_strong:
        score += 20
    if not_too_high:
        score += 10

    stop_loss = min(ema20, low10)
    risk = close - stop_loss

    if risk <= 0:
        target = close * 1.08
    else:
        target = close + risk * 2

    if score >= 85:
        status = "強勢趨勢股，接近買點或已發動🔥"
    elif score >= 70:
        status = "趨勢偏強，等待突破或拉回"
    elif score >= 50:
        status = "整理中，還沒有明確買點"
    else:
        status = "偏弱，不建議急著買"

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
        "status": status,
        "trend": trend,
        "breakout": breakout,
        "volume_strong": volume_strong
    }


# =========================
# 盤中 5 分K 分析
# =========================
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
    volume_strong = volume > vol20 * 2.2
    strong_candle = close > open_price and close >= high * 0.98
    not_too_high = close <= ema20 * 1.03

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

    is_signal = (
        trend and
        breakout and
        first_break and
        volume_strong and
        strong_candle and
        not_too_high and
        score >= 90
    )

    stop_loss = min(ema20, low10)
    risk = close - stop_loss

    if risk <= 0:
        target = close * 1.06
    else:
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


# =========================
# 買賣建議
# =========================
def buy_advice(r):
    close = r["close"]
    score = r["score"]
    high20 = r["high20"]
    stop_loss = r["stop_loss"]
    target = r["target"]
    ema20 = r.get("ema20", "N/A")

    if score >= 85 and close > high20:
        return (
            f"✅ 該不該買：可以小量試單\n"
            f"📌 什麼時候買：現價附近，或回測突破價 {high20} 不破再買\n"
            f"➕ 什麼時候加碼：站穩突破價且量能續強\n"
            f"🛑 停損：跌破 {stop_loss}\n"
            f"🎯 出場：到 {target} 可分批賣，或跌破 EMA20 {ema20} 出場"
        )

    if score >= 70:
        return (
            f"🟡 該不該買：先觀察，不急追\n"
            f"📌 什麼時候買：突破 {high20}，或拉回 EMA20 {ema20} 附近轉強再買\n"
            f"🛑 停損：跌破 {stop_loss}\n"
            f"🎯 出場：到 {target} 可分批賣，或跌破 EMA20 {ema20} 出場"
        )

    return (
        f"❌ 該不該買：目前不建議買\n"
        f"📌 什麼時候買：等重新站上 {high20} 且放量再考慮\n"
        f"🛑 停損：若已持有，跌破 {stop_loss} 要小心\n"
        f"🎯 出場：弱勢股不要攤平，等轉強再看"
    )


# =========================
# 畫圖：日K清楚版
# =========================
def plot_daily_chart(symbol, info):
    df = yf.download(
        symbol,
        period="3mo",
        interval="1d",
        progress=False,
        auto_adjust=False
    )

    df = fix_df(df)

    if df.empty or len(df) < 30:
        return None

    df = df.tail(30)

    df["EMA5"] = df["Close"].ewm(span=5).mean()
    df["EMA20"] = df["Close"].ewm(span=20).mean()

    buy_marker = [float("nan")] * len(df)
    buy_marker[-1] = float(df["Low"].iloc[-1]) * 0.97

    add_plots = [
        mpf.make_addplot(df["EMA5"], color="blue", width=1.5),
        mpf.make_addplot(df["EMA20"], color="purple", width=1.5),
        mpf.make_addplot(
            buy_marker,
            type="scatter",
            marker="^",
            markersize=300,
            color="green"
        )
    ]

    file_name = f"{symbol.replace('.', '_')}_daily_chart.png"

    mpf.plot(
        df,
        type="candle",
        style="yahoo",
        volume=True,
        addplot=add_plots,
        hlines=dict(
            hlines=[
                info["high20"],
                info["stop_loss"],
                info["target"]
            ],
            colors=[
                "red",
                "black",
                "green"
            ],
            linestyle="--",
            linewidths=2
        ),
        title=f"{symbol}\n紅=突破｜黑=停損｜綠=目標｜箭頭=觀察買點",
        figsize=(14, 9),
        savefig=dict(
            fname=file_name,
            dpi=160,
            bbox_inches="tight"
        )
    )

    return file_name


# =========================
# 畫圖：盤中 5分K
# =========================
def plot_intraday_chart(symbol, info):
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

    df = df.tail(60)

    df["EMA5"] = df["Close"].ewm(span=5).mean()
    df["EMA20"] = df["Close"].ewm(span=20).mean()

    buy_marker = [float("nan")] * len(df)
    buy_marker[-1] = float(df["Low"].iloc[-1]) * 0.98

    add_plots = [
        mpf.make_addplot(df["EMA5"], color="blue", width=1.3),
        mpf.make_addplot(df["EMA20"], color="purple", width=1.3),
        mpf.make_addplot(
            buy_marker,
            type="scatter",
            marker="^",
            markersize=280,
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
            hlines=[
                info["high20"],
                info["stop_loss"],
                info["target"]
            ],
            colors=[
                "red",
                "black",
                "green"
            ],
            linestyle="--",
            linewidths=2
        ),
        title=f"{symbol} 5分K\n紅=突破｜黑=停損｜綠=目標｜箭頭=買點",
        figsize=(14, 9),
        savefig=dict(
            fname=file_name,
            dpi=160,
            bbox_inches="tight"
        )
    )

    return file_name


# =========================
# 格式化訊息
# =========================
def format_daily_analysis(r):
    return (
        f"📊 股票分析：{r['symbol']}\n\n"
        f"現價：{r['close']}\n"
        f"強度分數：{r['score']}/100\n"
        f"量能倍數：{r['volume_rate']}倍\n"
        f"突破前高：{r['high20']}\n"
        f"EMA5 / EMA20 / EMA60：{r['ema5']} / {r['ema20']} / {r['ema60']}\n"
        f"停損價：{r['stop_loss']}\n"
        f"目標價：{r['target']}\n\n"
        f"判斷：{r['status']}\n\n"
        f"📌 進出場建議\n"
        f"{buy_advice(r)}\n\n"
        f"圖表說明：\n"
        f"綠箭頭＝觀察買點\n"
        f"紅線＝突破前高\n"
        f"黑線＝停損\n"
        f"綠線＝目標價"
    )


def format_intraday_signal(r):
    return (
        f"🚀 盤中新買點\n"
        f"📌 {r['symbol']}\n\n"
        f"現價：{r['close']}\n"
        f"強度分數：{r['score']}/100\n"
        f"量能倍數：{r['volume_rate']}倍\n"
        f"突破價：{r['high20']}\n"
        f"EMA5 / EMA20：{r['ema5']} / {r['ema20']}\n"
        f"停損價：{r['stop_loss']}\n"
        f"目標價：{r['target']}\n\n"
        f"📌 進出場建議\n"
        f"{buy_advice(r)}\n\n"
        f"圖表說明：\n"
        f"綠箭頭＝買點\n"
        f"紅線＝突破前高\n"
        f"黑線＝停損\n"
        f"綠線＝目標價"
    )


# =========================
# 自動掃描
# =========================
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

    candidates = sorted(
        candidates,
        key=lambda x: x["score"],
        reverse=True
    )[:MAX_PUSH_SIGNALS]

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


# =========================
# 我的股票報告
# =========================
def send_my_stocks_report():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    msg = f"📊 每日 8:30 股票報告\n時間：{now}\n\n"

    for stock in MY_STOCKS:
        try:
            r = analyze_daily(stock)

            if r:
                msg += (
                    f"📌 {r['symbol']}\n"
                    f"現價：{r['close']}\n"
                    f"分數：{r['score']}/100\n"
                    f"判斷：{r['status']}\n"
                    f"買點：突破 {r['high20']} 或拉回 EMA20 {r['ema20']}\n"
                    f"停損：{r['stop_loss']}\n"
                    f"目標：{r['target']}\n\n"
                )

        except Exception as e:
            msg += f"{stock} 分析失敗：{e}\n\n"

    send_message(msg)


# =========================
# TOP10
# =========================
def send_top10(all_stocks):
    results = []

    for stock in all_stocks:
        try:
            r = analyze_daily(stock)

            if r:
                results.append(r)

        except Exception as e:
            print(stock, e)

    results = sorted(
        results,
        key=lambda x: x["score"],
        reverse=True
    )[:10]

    msg = "🏆 今日強勢股票 TOP10\n\n"

    for i, r in enumerate(results, start=1):
        msg += (
            f"{i}. {r['symbol']}｜{r['score']}分｜現價 {r['close']}\n"
            f"判斷：{r['status']}\n\n"
        )

    send_message(msg)


# =========================
# 趨勢股 TOP10
# =========================
def send_trend10(all_stocks):
    results = []

    for stock in all_stocks:
        try:
            r = analyze_daily(stock)

            if r and r["trend"]:
                results.append(r)

        except Exception as e:
            print(stock, e)

    results = sorted(
        results,
        key=lambda x: x["score"],
        reverse=True
    )[:10]

    msg = "📈 趨勢股 TOP10\n\n"

    if not results:
        msg += "目前沒有明顯趨勢股"

    for i, r in enumerate(results, start=1):
        msg += (
            f"{i}. {r['symbol']}｜{r['score']}分｜現價 {r['close']}\n"
            f"突破前高：{r['high20']}\n"
            f"判斷：{r['status']}\n\n"
        )

    send_message(msg)


# =========================
# 回測
# =========================
def backtest_stock(symbol):
    df = yf.download(
        symbol,
        period="2y",
        interval="1d",
        progress=False,
        auto_adjust=False
    )

    df = fix_df(df)

    if df.empty or len(df) < 120:
        return f"{symbol} 資料不足，無法回測"

    close = df["Close"]
    open_price = df["Open"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    ema20 = close.ewm(span=20).mean()
    ema60 = close.ewm(span=60).mean()
    high20 = high.shift(1).rolling(20).max()
    vol20 = volume.rolling(20).mean()

    trades = []

    for i in range(60, len(df) - 11):
        trend = close.iloc[i] > ema20.iloc[i] > ema60.iloc[i]
        breakout = close.iloc[i] > high20.iloc[i]
        volume_strong = volume.iloc[i] > vol20.iloc[i] * 2.2
        not_too_high = close.iloc[i] <= ema20.iloc[i] * 1.03

        if trend and breakout and volume_strong and not_too_high:
            buy_price = open_price.iloc[i + 1]
            take_profit = buy_price * 1.08
            stop_loss = buy_price * 0.96

            result = None

            for j in range(i + 1, i + 11):
                if low.iloc[j] <= stop_loss:
                    result = -4
                    break

                if high.iloc[j] >= take_profit:
                    result = 8
                    break

            if result is None:
                sell_price = close.iloc[i + 10]
                result = ((sell_price - buy_price) / buy_price) * 100

            trades.append(float(result))

    if not trades:
        return f"🤖 回測結果：{symbol}\n\n近2年沒有符合策略的買點"

    wins = [x for x in trades if x > 0]

    win_rate = len(wins) / len(trades) * 100
    avg_return = sum(trades) / len(trades)
    max_loss = min(trades)

    if win_rate >= 60 and avg_return > 1:
        rating = "策略表現不錯，可以觀察🔥"
    elif win_rate >= 50:
        rating = "策略普通，建議降低部位"
    else:
        rating = "策略勝率偏低，不建議單靠這招"

    return (
        f"🤖 回測結果：{symbol}\n\n"
        f"回測期間：近2年\n"
        f"交易次數：{len(trades)} 次\n"
        f"勝率：{round(win_rate, 2)}%\n"
        f"平均報酬：{round(avg_return, 2)}%\n"
        f"最大單筆虧損：{round(max_loss, 2)}%\n\n"
        f"策略評價：{rating}"
    )


# =========================
# 主程式
# =========================
def main():
    send_message(
        "🔥 職業交易員版 Bot 已啟動\n\n"
        "可用指令：\n"
        "2330 = 股票分析 + K線圖 + 進出場\n"
        "/my = 我的股票報告\n"
        "/scan = 手動掃市場買點\n"
        "/top = 強勢股 TOP10\n"
        "/trend = 趨勢股 TOP10\n"
        "/backtest 2330 = 回測策略勝率"
    )

    if SCAN_ALL_MARKET:
        all_stocks = get_all_tw_stocks()[:MAX_SCAN_STOCKS]
    else:
        all_stocks = MY_STOCKS

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

                text = text.strip()

                if text == "/my":
                    send_my_stocks_report()
                    continue

                if text == "/scan":
                    send_message("開始手動掃描市場買點...")
                    scan_market(all_stocks)
                    continue

                if text == "/top":
                    send_message("開始計算強勢股 TOP10...")
                    send_top10(all_stocks)
                    continue

                if text == "/trend":
                    send_message("開始計算趨勢股 TOP10...")
                    send_trend10(all_stocks)
                    continue

                if text.startswith("/backtest"):
                    parts = text.split()

                    if len(parts) < 2:
                        send_message("請輸入股票代號，例如：/backtest 2330")
                        continue

                    symbol = convert_symbol(parts[1])
                    result = backtest_stock(symbol)
                    send_message(result)
                    continue

                symbol = convert_symbol(text)
                r = analyze_daily(symbol)

                if r:
                    chart = plot_daily_chart(symbol, r)

                    if chart:
                        send_photo(format_daily_analysis(r), chart)
                    else:
                        send_message(format_daily_analysis(r))
                else:
                    send_message(f"找不到資料：{symbol}")

            today = datetime.now().strftime("%Y-%m-%d")
            now_time = datetime.now().time()

            # 每天 8:30 自動報告
            if today != last_report_date and now_time >= dtime(8, 30):
                send_my_stocks_report()
                last_report_date = today

            # 盤中每 5 分鐘掃描
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