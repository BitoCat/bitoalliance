# -*- coding: utf-8 -*-
"""
比特聯盟 BitoAlliance｜整點市場快報 → Discord

台灣時間 00/04/08/12/16/20 點推「H4 完整版」，其他整點推「簡短版」。
資料來源：
  - OKX 公開 API（日內資料，5 分鐘～4 小時）：價格、K 棒、OI、資金費率、
    多空比、合約/現貨主動買賣、爆倉
  - Coinbase 公開 API：計算 Coinbase 溢價
  - CoinGecko：總市值、BTC 市佔、板塊輪動（只在完整版抓）

環境變數：
  DISCORD_WEBHOOK_URL  Discord Webhook 網址（必填，DRY_RUN 時可不填）
  COINGECKO_API_KEY    CoinGecko Demo API Key（選填）
  MODE                 auto / full / short（預設 auto，依台灣時間判斷）
  DRY_RUN              true 時只印出內容，不發送
  WAIT                 1 時會等到整點再抓資料（排程用）
"""
import os
import time
import json
import datetime as dt

import requests

OKX = "https://www.okx.com/api/v5"
CG = "https://api.coingecko.com/api/v3"
TW = dt.timezone(dt.timedelta(hours=8))

S = requests.Session()
S.headers["User-Agent"] = "BitoAlliance-MarketReport/1.0"

COINS = [
    {"name": "BTC", "icon": "₿", "swap": "BTC-USDT-SWAP", "uly": "BTC-USDT", "ctval": 0.01},
    {"name": "ETH", "icon": "Ξ", "swap": "ETH-USDT-SWAP", "uly": "ETH-USDT", "ctval": 0.1},
]

# 只在這些「有交易意義」的板塊裡排名（CoinGecko 類別名稱 → 中文）
SECTORS = {
    "Artificial Intelligence (AI)": "AI",
    "AI Agents": "AI 代理",
    "Meme": "Meme",
    "Solana Meme": "Solana Meme",
    "Layer 1 (L1)": "Layer 1",
    "Layer 2 (L2)": "Layer 2",
    "Rollup": "Rollup",
    "Decentralized Finance (DeFi)": "DeFi",
    "Decentralized Exchange (DEX)": "DEX",
    "Lending/Borrowing Protocols": "借貸",
    "Perpetuals": "永續合約",
    "Real World Assets (RWA)": "RWA",
    "Gaming (GameFi)": "GameFi",
    "DePIN": "DePIN",
    "Liquid Staking": "流動性質押",
    "Zero Knowledge (ZK)": "ZK",
    "Privacy Coins": "隱私幣",
    "Oracle": "預言機",
    "Prediction Markets": "預測市場",
    "Data Availability": "資料可用性",
    "Infrastructure": "基礎設施",
    "NFT": "NFT",
}

COLOR_BULL = 0x2ECC71
COLOR_BEAR = 0xE74C3C
COLOR_NEUTRAL = 0xF5A623  # 比特聯盟琥珀色


# ───────────────────────── 基本工具 ─────────────────────────

def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def get(url, params=None, headers=None, tries=3):
    for i in range(tries):
        try:
            r = S.get(url, params=params, headers=headers, timeout=15)
            if r.status_code == 200:
                return r.json()
            print(f"[HTTP {r.status_code}] {url}")
        except Exception as e:  # noqa
            print(f"[ERR] {url} {e}")
        time.sleep(2 * (i + 1))
    return None


def okx(path, **params):
    j = get(OKX + path, params)
    if j and j.get("code") == "0":
        return j.get("data")
    print(f"[OKX] {path} 失敗：{j and j.get('msg')}")
    return None


def rows_desc(rows):
    """OKX rubik 資料依時間新到舊排序"""
    return sorted(rows, key=lambda r: int(r[0]), reverse=True)


def arrow_pct(x, d=2):
    if x is None:
        return "—"
    a = "▲" if x > 0 else "▼" if x < 0 else "─"
    return f"{a}{abs(x):.{d}f}%"


def usd(x):
    if x is None:
        return "—"
    if x >= 1e12:
        return f"${x / 1e12:.2f}T"
    if x >= 1e9:
        return f"${x / 1e9:.2f}B"
    if x >= 1e6:
        return f"${x / 1e6:.1f}M"
    if x >= 1e3:
        return f"${x / 1e3:.0f}K"
    return f"${x:,.0f}"


def price_fmt(p):
    return f"${p:,.0f}" if p >= 1000 else f"${p:,.2f}"


# ───────────────────────── 抓資料（日內版：5 分鐘～4 小時） ─────────────────────────

def liquidations(c, hours=1.0, max_pages=10):
    """OKX 公開爆倉紀錄，加總最近 N 小時多/空爆倉金額（美元，近似值）"""
    cutoff = (time.time() - hours * 3600) * 1000
    long_usd = short_usd = 0.0
    after = None
    for _ in range(max_pages):
        params = dict(instType="SWAP", uly=c["uly"], state="filled", limit="100")
        if after:
            params["after"] = str(after)
        data = okx("/public/liquidation-orders", **params)
        if not data or not data[0].get("details"):
            break
        det = data[0]["details"]
        oldest = None
        for x in det:
            ts = int(x.get("ts") or x.get("time") or 0)
            oldest = ts if oldest is None else min(oldest, ts)
            if ts < cutoff:
                continue
            v = (f(x.get("bkPx")) or 0) * (f(x.get("sz")) or 0) * c["ctval"]
            side = x.get("posSide")
            if side == "net":  # 單向持倉：賣出強平 = 多單爆倉
                side = "long" if x.get("side") == "sell" else "short"
            if side == "long":
                long_usd += v
            elif side == "short":
                short_usd += v
        if oldest is None or oldest < cutoff or len(det) < 100 or oldest == after:
            break
        after = oldest
        time.sleep(0.3)
    return long_usd, short_usd


def taker_ratio(ccy, inst_type, now_ms):
    """主動買賣比（買/賣），回傳 (近 15 分鐘, 近 1 小時)，只用已完成的 5 分鐘資料"""
    tv = okx("/rubik/stat/taker-volume", ccy=ccy, instType=inst_type, period="5m")
    if not tv:
        return None, None
    done = [r for r in rows_desc(tv) if int(r[0]) + 300_000 <= now_ms + 30_000]

    def ratio(n):
        sell = sum(f(r[1]) or 0 for r in done[:n])
        buy = sum(f(r[2]) or 0 for r in done[:n])
        return buy / sell if sell > 0 else None
    return ratio(3), ratio(12)


def ema(vals, n=20):
    k = 2 / (n + 1)
    e, out = vals[0], []
    for v in vals:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def trend_info(rows, last):
    """用已收盤 K 棒判斷趨勢：價格 vs EMA20、EMA20 斜率、最近兩根高低點"""
    if not rows:
        return None
    rows = sorted(rows, key=lambda k: int(k[0]))
    done = [r for r in rows if r[8] == "1"]
    cur = [r for r in rows if r[8] != "1"]
    if len(done) < 22:
        return None
    e = ema([f(r[4]) for r in done], 20)
    slope = (e[-1] / e[-4] - 1) * 100
    score, facts = 0, []
    if last > e[-1]:
        score += 1
        facts.append("價格在均線之上")
    else:
        score -= 1
        facts.append("價格在均線之下")
    if slope > 0.05:
        score += 1
        facts.append("均線向上")
    elif slope < -0.05:
        score -= 1
        facts.append("均線向下")
    else:
        facts.append("均線走平")
    a, b = done[-1], done[-2]
    if f(a[2]) > f(b[2]) and f(a[3]) > f(b[3]):
        score += 1
        facts.append("高低點墊高")
    elif f(a[2]) < f(b[2]) and f(a[3]) < f(b[3]):
        score -= 1
        facts.append("高低點下移")
    state = "偏多" if score >= 2 else "偏空" if score <= -2 else "震盪"
    return {"state": state, "facts": facts, "prev_hi": f(a[2]), "prev_lo": f(a[3]),
            "open": f(cur[-1][1]) if cur else None,
            "chg_bar": (f(a[4]) / f(a[1]) - 1) * 100}


def coin_data(c, full=False):
    d = {}
    now_ms = time.time() * 1000
    t = okx("/market/ticker", instId=c["swap"])
    if not t:
        return None
    t = t[0]
    last = f(t["last"])
    d["last"] = last
    d["chg24"] = (last / f(t["open24h"]) - 1) * 100

    # 5 分鐘 K：近 1h / 4h 高低點、15 分鐘與 1 小時漲跌
    open_at = {}
    k5 = okx("/market/candles", instId=c["swap"], bar="5m", limit="49")
    if k5:
        k5 = sorted(k5, key=lambda k: int(k[0]), reverse=True)
        open_at = {int(k[0]): f(k[1]) for k in k5}
        h1, h4 = k5[:12], k5[:48]
        d["hi1"], d["lo1"] = max(f(k[2]) for k in h1), min(f(k[3]) for k in h1)
        d["hi4"], d["lo4"] = max(f(k[2]) for k in h4), min(f(k[3]) for k in h4)
        if len(k5) > 3:
            d["p15"] = (last / f(k5[2][1]) - 1) * 100
        if len(k5) > 12:
            d["p1"] = (last / f(k5[11][1]) - 1) * 100
        rng = d["hi1"] - d["lo1"]
        d["range1"] = rng
        d["pos1"] = (last - d["lo1"]) / rng * 100 if rng > 0 else 50.0

    # 波動度：近 1 小時振幅 ÷ 過去 24 小時平均每小時振幅
    k1 = okx("/market/candles", instId=c["swap"], bar="1H", limit="25")
    if k1 and d.get("range1") is not None:
        done = [k for k in k1 if k[8] == "1"]
        rs = [f(k[2]) - f(k[3]) for k in done[:24]]
        if rs and sum(rs) > 0:
            d["volx"] = d["range1"] / (sum(rs) / len(rs))

    # 日內大方向：H4 與日線（日線以 UTC 0 點 = 台灣 08:00 換日，與 TradingView 一致）
    if full:
        d["h4"] = trend_info(okx("/market/candles", instId=c["swap"], bar="4H", limit="40"), last)
        d["day"] = trend_info(okx("/market/candles", instId=c["swap"], bar="1Dutc", limit="40"), last)
        if d["h4"]:
            d["p4bar"] = d["h4"]["chg_bar"]

    # 合約持倉（OKX 該幣所有合約加總，5 分鐘資料）
    oi = okx("/rubik/stat/contracts/open-interest-volume", ccy=c["name"], period="5m")
    if oi:
        oi = rows_desc(oi)
        d["oi"] = f(oi[0][1])

        def coin_oi(i):
            px = open_at.get(int(oi[i][0]))
            return f(oi[i][1]) / px if px else None

        for key, i in (("15", 3), ("1h", 12)):
            if len(oi) > i and f(oi[i][1]):
                a, b = coin_oi(0), coin_oi(i)
                d[f"oi{key}"] = (a / b - 1) * 100 if a and b else (f(oi[0][1]) / f(oi[i][1]) - 1) * 100
                p0, pi = open_at.get(int(oi[0][0])), open_at.get(int(oi[i][0]))
                if p0 and pi:
                    d[f"px{key}"] = (p0 / pi - 1) * 100

    # 資金費率 + 距離下次結算
    fr = okx("/public/funding-rate", instId=c["swap"])
    if fr:
        d["funding"] = f(fr[0]["fundingRate"]) * 100
        ft = f(fr[0].get("fundingTime"))
        if ft and ft > now_ms:
            d["fund_min"] = (ft - now_ms) / 60000

    # 散戶多空比（1 小時變化）、大戶持倉比
    ra = okx("/rubik/stat/contracts/long-short-account-ratio", ccy=c["name"], period="5m")
    if ra:
        ra = rows_desc(ra)
        d["retail"] = f(ra[0][1])
        if len(ra) > 12:
            d["retail_chg"] = f(ra[0][1]) - f(ra[12][1])
    tp = okx("/rubik/stat/contracts/long-short-position-ratio-contract-top-trader",
             instId=c["swap"], period="5m")
    if tp:
        d["top_pos"] = f(rows_desc(tp)[0][1])

    # 主動買賣：合約、現貨
    d["tk15"], d["tk1h"] = taker_ratio(c["name"], "CONTRACTS", now_ms)
    d["sp15"], d["sp1h"] = taker_ratio(c["name"], "SPOT", now_ms)

    # 近 1 小時爆倉
    d["liq_long"], d["liq_short"] = liquidations(c, hours=1)
    return d


def coinbase_premium():
    cb = get("https://api.exchange.coinbase.com/products/BTC-USD/ticker")
    ok = okx("/market/ticker", instId="BTC-USDT")
    if not cb or not ok:
        return None
    a, b = f(cb.get("price")), f(ok[0].get("last"))
    return (a / b - 1) * 100 if a and b else None


def market_data():
    key = os.getenv("COINGECKO_API_KEY", "").strip()
    headers = {"x-cg-demo-api-key": key} if key else None
    m = {}
    g = get(CG + "/global", headers=headers)
    if g and g.get("data"):
        g = g["data"]
        m["mcap"] = g["total_market_cap"]["usd"]
        m["mcap_chg"] = g.get("market_cap_change_percentage_24h_usd")
        m["btc_dom"] = g["market_cap_percentage"]["btc"]
    cats = get(CG + "/coins/categories", headers=headers)
    if cats:
        rows = [(SECTORS[x["name"]], x["market_cap_change_24h"]) for x in cats
                if x.get("name") in SECTORS and x.get("market_cap_change_24h") is not None]
        rows.sort(key=lambda r: r[1], reverse=True)
        m["strong"], m["weak"] = rows[:3], rows[-3:][::-1]
    m["premium"] = coinbase_premium()
    return m


# ───────────────────────── 局勢判讀（白話、日內） ─────────────────────────

def pnum(p):
    return f"{p:,.0f}" if p >= 1000 else f"{p:,.2f}"


def hm(minutes):
    h, m_ = divmod(int(minutes), 60)
    return f"{h} 小時 {m_} 分" if h else f"{m_} 分鐘"


def funding_note(fr, fm):
    """資金費率結算倒數 + 對短線的影響（倒數本身不分多空，要看費率正負）"""
    if fm is None:
        return None
    t = f"距離資金費率結算 {hm(fm)}"
    if fr is None or fm > 60:
        return t
    if fr >= 0.01:
        return t + "：費率偏正，結算前做多者常先平倉避費，短線偏賣壓"
    if fr <= -0.01:
        return t + "：費率偏負，結算前做空者常先回補，短線偏買盤"
    return t + "：費率接近零，結算影響不大"


def lights(d):
    """燈號：(燈, 名稱, 白話)。🟢 利多 🔴 利空 🟡 留意（不分方向） ⚪ 中性"""
    out = []

    vx = d.get("volx")
    if vx is not None:
        if vx >= 1.8:
            out.append(("🟡", "波動度", f"波動明顯放大，是平常的 {vx:.1f} 倍，行情正在動"))
        elif vx >= 1.2:
            out.append(("⚪", "波動度", f"波動略高於平常（{vx:.1f} 倍）"))
        elif vx < 0.6:
            out.append(("⚪", "波動度", f"波動收斂，只有平常的 {vx:.1f} 倍，行情清淡"))
        else:
            out.append(("⚪", "波動度", f"波動正常（{vx:.1f} 倍）"))

    t15, t1 = d.get("tk15"), d.get("tk1h")
    if t15 is not None and t1 is not None:
        if t15 >= 1.1 and t1 >= 1.05:
            out.append(("🟢", "買賣力道", "近 15 分鐘和 1 小時都是買方比較積極"))
        elif t15 <= 0.9 and t1 <= 0.95:
            out.append(("🔴", "買賣力道", "近 15 分鐘和 1 小時都是賣方比較積極"))
        elif t15 >= 1.15:
            out.append(("🟢", "買賣力道", "近 15 分鐘買方轉強"))
        elif t15 <= 0.85:
            out.append(("🔴", "買賣力道", "近 15 分鐘賣方轉強"))
        else:
            out.append(("⚪", "買賣力道", "買賣雙方力道差不多"))

    p1, sp, ct = d.get("p1"), d.get("sp1h"), d.get("tk1h")
    if p1 is not None and sp is not None and ct is not None:
        if p1 > 0.1 and sp >= 1.05:
            out.append(("🟢", "現貨 vs 合約", "上漲有現貨買盤支撐，走勢較扎實"))
        elif p1 > 0.1 and ct >= 1.05:
            out.append(("🟡", "現貨 vs 合約", "上漲主要由合約推動，容易急漲後拉回"))
        elif p1 < -0.1 and sp <= 0.95:
            out.append(("🔴", "現貨 vs 合約", "下跌有現貨賣壓，走勢較扎實"))
        elif p1 < -0.1 and ct <= 0.95:
            out.append(("🟡", "現貨 vs 合約", "下跌主要由合約推動，容易急跌後反彈"))
        elif p1 < -0.1 and sp >= 1.05:
            out.append(("🟢", "現貨 vs 合約", "下跌時現貨有人在買，下方有承接"))
        elif p1 > 0.1 and sp <= 0.95:
            out.append(("🔴", "現貨 vs 合約", "上漲時現貨在出貨，漲勢可能不持久"))
        else:
            out.append(("⚪", "現貨 vs 合約", "現貨和合約都沒有明顯帶動"))

    px, o1, o15 = d.get("px1h"), d.get("oi1h"), d.get("oi15")
    if px is not None and o1 is not None:
        if px > 0 and o1 > 0.5:
            item = ("🟢", "合約持倉", "上漲時合約交易者在加倉做多，有新資金推動")
        elif px > 0 and o1 < -0.5:
            item = ("⚪", "合約持倉", "上漲主要來自空單回補，力道還待確認")
        elif px < 0 and o1 > 0.5:
            item = ("🔴", "合約持倉", "下跌時合約交易者在加倉做空，賣壓可能延續")
        elif px < 0 and o1 < -0.5:
            item = ("⚪", "合約持倉", "下跌時多單在停損出場，賣壓正在消化")
        else:
            item = ("⚪", "合約持倉", "近 1 小時合約部位變化不大")
        if o15 is not None and abs(o15) >= 1.0:
            item = (item[0], item[1], item[2] + f"；近 15 分鐘合約部位急{'增' if o15 > 0 else '減'} {abs(o15):.1f}%")
        out.append(item)

    rc, tp = d.get("retail_chg"), d.get("top_pos")
    if tp is not None:
        if rc is not None and rc > 0.02 and tp < 1:
            out.append(("🔴", "散戶 vs 大戶", "散戶在加碼做多，大戶反而偏空（通常大戶較準）"))
        elif rc is not None and rc < -0.02 and tp > 1:
            out.append(("🟢", "散戶 vs 大戶", "散戶在減碼做多，大戶反而偏多（通常大戶較準）"))
        elif tp >= 1.05:
            out.append(("🟢", "散戶 vs 大戶", "大戶持倉偏多"))
        elif tp <= 0.95:
            out.append(("🔴", "散戶 vs 大戶", "大戶持倉偏空"))
        else:
            out.append(("⚪", "散戶 vs 大戶", "散戶與大戶看法沒有明顯分歧"))

    fr = d.get("funding")
    if fr is not None:
        fm = d.get("fund_min")
        tail = f"（距離結算 {hm(fm)}）" if fm and fm > 60 else ""
        if fr >= 0.03:
            out.append(("🔴", "多空費用", "做多的合約交易者太擁擠，容易被洗盤" + tail))
        elif fr <= -0.01:
            out.append(("🟢", "多空費用", "做空的合約交易者太擁擠，容易出現軋空" + tail))
        else:
            out.append(("⚪", "多空費用", "做多做空都沒有過熱" + tail))

    ll, ls = d.get("liq_long"), d.get("liq_short")
    if ll is not None and ls is not None:
        # 門檻跟合約部位大小掛鉤：爆倉金額要超過 OI 的 0.05% 才算明顯
        th = (d.get("oi") or 0) * 0.0005 or 5e5
        if ls > ll * 2 and ls >= th:
            out.append(("🟢", "強制平倉", f"近 1 小時空單被強制平倉較多（{usd(ls)}），空方吃虧"))
        elif ll > ls * 2 and ll >= th:
            out.append(("🔴", "強制平倉", f"近 1 小時多單被強制平倉較多（{usd(ll)}），多方吃虧"))
        else:
            out.append(("⚪", "強制平倉", "近 1 小時沒有明顯的強制平倉潮"))
    return out


STATES = {
    "偏多": ("📈", COLOR_BULL, "短線多方占優勢，順多較有利。"),
    "短多": ("↗️", COLOR_BULL, "短線多方稍強，偏向短多。"),
    "偏空": ("📉", COLOR_BEAR, "短線空方占優勢，順空較有利。"),
    "短空": ("↘️", COLOR_BEAR, "短線空方稍強，偏向短空。"),
    "膠著": ("⚖️", COLOR_NEUTRAL, "多空訊號互相抵銷，行情膠著。"),
    "整理": ("↔️", COLOR_NEUTRAL, "多空力道都不明顯，行情在整理。"),
}


def situation(d, premium=None):
    lt = lights(d)
    greens = sum(1 for x in lt if x[0] == "🟢")
    reds = sum(1 for x in lt if x[0] == "🔴")
    net = greens - reds
    if premium is not None:
        net += 1 if premium >= 0.05 else -1 if premium <= -0.05 else 0
    if net >= 3:
        st = "偏多"
    elif net == 2:
        st = "短多"
    elif net <= -3:
        st = "偏空"
    elif net == -2:
        st = "短空"
    elif greens >= 1 and reds >= 1:
        st = "膠著"
    else:
        st = "整理"
    return st, lt


def pos_sentence(d):
    p = d.get("pos1")
    if p is None:
        return ""
    if p < 20:
        return "價格貼近 1 小時低點。"
    if p > 80:
        return "價格貼近 1 小時高點。"
    return "價格在 1 小時區間中間。"


def level_lines(d):
    def pair(a, b, la, lb):
        return f"{pnum(a)}（{la}／{lb}）" if abs(a - b) < 1e-9 else f"{pnum(a)}（{la}）／{pnum(b)}（{lb}）"
    return [
        f"⬆️ 壓力 {pair(d['hi1'], d['hi4'], '1h 高', '4h 高')} → 站上轉強",
        f"⬇️ 支撐 {pair(d['lo1'], d['lo4'], '1h 低', '4h 低')} → 跌破轉弱",
    ]


TREND_ICON = {"偏多": "📈", "偏空": "📉", "震盪": "↔️"}


def big_picture(day, h4):
    if not day or not h4:
        return None
    D, H = day["state"], h4["state"]
    if D == H == "偏多":
        return "日線和 H4 同步偏多，日內大方向偏多"
    if D == H == "偏空":
        return "日線和 H4 同步偏空，日內大方向偏空"
    if D == "偏多" and H == "偏空":
        return "日線偏多，但 H4 正在回檔"
    if D == "偏空" and H == "偏多":
        return "日線偏空，但 H4 正在反彈"
    if D == H == "震盪":
        return "日線和 H4 都在震盪，沒有明確大方向"
    if D == "震盪":
        return f"日線震盪，H4 {H}，日內方向以 H4 為主"
    return f"日線{D}，H4 在整理"


def trend_lines(d):
    day, h4 = d.get("day"), d.get("h4")
    if not day and not h4:
        return []
    L = ["**🗺 日內大方向（日線／H4）**"]
    for name, t in (("日線", day), ("H4", h4)):
        if t:
            L.append(f"{TREND_ICON[t['state']]} {name} **{t['state']}**：{'、'.join(t['facts'])}")
    bp = big_picture(day, h4)
    if bp:
        L.append(f"➡️ {bp}")
    return L


def multi_levels(d):
    """把 1h、前根 4H、今日開盤、昨日高低合併，依距離現價排成壓力與支撐"""
    last = d["last"]
    cand = [(d["hi1"], "1h 高"), (d["lo1"], "1h 低")]
    h4, day = d.get("h4"), d.get("day")
    if h4:
        cand += [(h4["prev_hi"], "前根 4H 高"), (h4["prev_lo"], "前根 4H 低")]
    if day:
        if day.get("open"):
            cand.append((day["open"], "今日開盤"))
        cand += [(day["prev_hi"], "昨日高"), (day["prev_lo"], "昨日低")]
    cand.sort()
    merged = []
    for p, lab in cand:
        if merged and abs(p - merged[-1][0]) / p < 0.0005:
            merged[-1] = (merged[-1][0], merged[-1][1] + "／" + lab)
        else:
            merged.append((p, lab))
    res = [x for x in merged if x[0] > last][:3]
    sup = [x for x in merged if x[0] < last][::-1][:3]
    L = []
    if res:
        L.append("⬆️ 壓力：" + "、".join(f"{pnum(p)}（{lab}）" for p, lab in res))
    if sup:
        L.append("⬇️ 支撐：" + "、".join(f"{pnum(p)}（{lab}）" for p, lab in sup))
    L.append("-# 由近到遠排列；站上壓力轉強、跌破支撐轉弱")
    return L


def raw_line(d):
    parts = []
    if d.get("oi") is not None:
        parts.append(f"OI {usd(d['oi'])} 15m {arrow_pct(d.get('oi15'), 1)} 1h {arrow_pct(d.get('oi1h'), 1)}")
    if d.get("funding") is not None:
        parts.append(f"費率 {d['funding']:.4f}%")
    if d.get("tk15") is not None:
        parts.append(f"合約買賣比 15m {d['tk15']:.2f}/1h {d.get('tk1h') or 0:.2f}")
    if d.get("sp1h") is not None:
        parts.append(f"現貨買賣比 1h {d['sp1h']:.2f}")
    if d.get("retail") is not None:
        parts.append(f"散戶 {d['retail']:.2f}")
    if d.get("top_pos") is not None:
        parts.append(f"大戶 {d['top_pos']:.2f}")
    if d.get("liq_long") is not None:
        parts.append(f"1h 爆倉 多 {usd(d['liq_long'])}/空 {usd(d['liq_short'])}")
    return "-# 數據：" + "｜".join(parts) if parts else ""


# ───────────────────────── 組訊息 ─────────────────────────

def price_head(d, h4):
    s = f"**{price_fmt(d['last'])}**　15m {arrow_pct(d.get('p15'), 2)}｜1h {arrow_pct(d.get('p1'), 2)}"
    if h4 and d.get("p4bar") is not None:
        s += f"｜上一根 4H {arrow_pct(d['p4bar'], 1)}"
    return s + f"｜24h {arrow_pct(d['chg24'], 1)}"


def full_coin_embed(c, d, premium, stamp, h4):
    if not d or "hi1" not in d:
        return {"title": f"{c['icon']} {c['name']}", "description": "資料暫缺", "color": COLOR_NEUTRAL}
    st, lt = situation(d, premium if c["name"] == "BTC" else None)
    icon, color, desc = STATES[st]
    L = [price_head(d, h4), ""]
    tl = trend_lines(d)
    if tl:
        L += tl + [""]
    L += [f"**🧭 短線局勢（15m／1h）：{icon} {st}**", desc + pos_sentence(d), "", "**🚦 短線燈號**"]
    for light, name, text in lt:
        L.append(f"{light} **{name}**：{text}")
    fn = funding_note(d.get("funding"), d.get("fund_min"))
    if fn and d.get("fund_min", 999) <= 60:
        L.append(f"⏳ {fn}")
    L += ["", "**📍 關鍵價位**"] + (multi_levels(d) if (d.get("h4") or d.get("day")) else level_lines(d))
    rl = raw_line(d)
    if rl:
        L += ["", rl]
    return {"title": f"{c['icon']} {c['name']}", "description": "\n".join(L),
            "color": color, "timestamp": stamp}


def market_embed(m, stamp):
    L = []
    if m.get("mcap"):
        L.append(f"加密市場今天 {arrow_pct(m.get('mcap_chg'), 1)}｜BTC 市佔 {m['btc_dom']:.1f}%"
                 + ("（資金集中在比特幣）" if m["btc_dom"] >= 55 else ""))
    if m.get("strong"):
        L.append("🔥 資金流入：" + "、".join(f"{n} {arrow_pct(v, 1)}" for n, v in m["strong"]))
        L.append("🧊 資金流出：" + "、".join(f"{n} {arrow_pct(v, 1)}" for n, v in m["weak"]))
    pr = m.get("premium")
    if pr is not None:
        if pr >= 0.05:
            L.append(f"🟢 美國現貨買盤積極（Coinbase 溢價 {pr:+.3f}%）")
        elif pr <= -0.05:
            L.append(f"🔴 美國現貨買盤偏弱（Coinbase 溢價 {pr:+.3f}%）")
        else:
            L.append("⚪ 美國現貨買盤正常")
    L.append("\n-# 市場資料由 [CoinGecko](https://www.coingecko.com/en/api) 提供")
    return {"title": "🌐 大方向參考", "description": "\n".join(L), "color": COLOR_NEUTRAL,
            "timestamp": stamp}


def short_embed(datas, title, stamp):
    L = []
    for c, d in datas:
        if not d or "hi1" not in d:
            L += [f"{c['icon']} {c['name']} 資料暫缺", ""]
            continue
        st, lt = situation(d)
        icon = STATES[st][0]
        L.append(f"{c['icon']} **{c['name']} {price_fmt(d['last'])}**　15m {arrow_pct(d.get('p15'), 2)}"
                 f"｜1h {arrow_pct(d.get('p1'), 2)}　局勢：{icon} **{st}**")
        vol = [x for x in lt if x[1] == "波動度"]
        key = [x for x in lt if x[1] != "波動度" and x[0] != "⚪"][:2]
        for light, name, text in vol + key:
            L.append(f"{light} {name}：{text}")
        fm = d.get("fund_min")
        if fm is not None and fm <= 60:
            L.append(f"⏳ {funding_note(d.get('funding'), fm)}")
        L.append(f"📍 壓力 {pnum(d['hi1'])}｜支撐 {pnum(d['lo1'])}（近 1 小時）")
        L.append("")
    return {"title": title, "description": "\n".join(L).strip(), "color": COLOR_NEUTRAL,
            "timestamp": stamp}


# ───────────────────────── 主流程 ─────────────────────────

def wait_top_of_hour():
    now = dt.datetime.now(dt.timezone.utc)
    if now.minute >= 45:
        target = (now + dt.timedelta(hours=1)).replace(minute=0, second=40, microsecond=0)
        secs = (target - now).total_seconds()
        print(f"等待 {secs:.0f} 秒到整點…")
        time.sleep(max(0, secs))


def send(payload):
    if os.getenv("DRY_RUN", "false").lower() == "true":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print("（DRY_RUN：只印出，不發送）")
        return
    url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        raise SystemExit("缺少 DISCORD_WEBHOOK_URL")
    r = requests.post(url, json=payload, timeout=15)
    print("Discord 回應：", r.status_code, r.text[:200])
    r.raise_for_status()


def main():
    if os.getenv("WAIT") == "1":
        wait_top_of_hour()

    now_tw = dt.datetime.now(TW)
    hour = now_tw.hour
    mode = os.getenv("MODE", "auto").lower()
    full = (hour % 4 == 0) if mode == "auto" else (mode == "full")
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    hh = f"{hour:02d}:00"
    print(f"台灣時間 {now_tw:%Y-%m-%d %H:%M}，模式：{'H4 完整版' if full else '整點簡短版'}")

    datas = [(c, coin_data(c, full)) for c in COINS]
    footer = {"text": "比特聯盟 BitoAlliance｜僅供參考，非投資建議"}

    if full:
        m = market_data()
        embeds = [full_coin_embed(c, d, m.get("premium"), stamp, hour % 4 == 0) for c, d in datas]
        embeds.append(market_embed(m, stamp))
        embeds[-1]["footer"] = footer
        payload = {"username": "比特聯盟", "content": f"## ⏰ {hh} " + ("H4 收盤報告" if hour % 4 == 0 else "完整報告"), "embeds": embeds}
    else:
        e = short_embed(datas, f"⏱ {hh} 整點快報", stamp)
        e["footer"] = footer
        payload = {"username": "比特聯盟", "embeds": [e]}

    send(payload)


if __name__ == "__main__":
    main()


