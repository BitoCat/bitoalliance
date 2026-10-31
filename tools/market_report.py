# -*- coding: utf-8 -*-
"""
比特聯盟 BitoAlliance｜整點市場快報 → Discord

台灣時間 00/04/08/12/16/20 點推「H4 完整版」，其他整點推「簡短版」。
資料來源：
  - OKX 公開 API（日內資料，5 分鐘～4 小時）：價格、K 棒、OI、資金費率、
    多空比、合約/現貨主動買賣、爆倉、成交量分布（1h／4h／24h）
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
GUIDE_URL = os.getenv("GUIDE_URL", "https://bitocat.github.io/bitoalliance/report-guide.html")
GUIDE_LINK = f"-# 📖 看不懂名詞？[快報讀法]({GUIDE_URL})"

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
        if len(k5) >= 48:
            d["open4h"] = f(k5[47][1])
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

    # 成交量分布：每整點算 1h（1 分 K）；完整版再算 4h、24h（5 分 K）
    k1m = okx("/market/candles", instId=c["swap"], bar="1m", limit="60")
    if k1m:
        d["vp1"] = vol_profile(k1m, bins=40, min_gap_pct=0.1)
    if full:
        k5d = okx("/market/candles", instId=c["swap"], bar="5m", limit="288")
        if k5d:
            k5d = sorted(k5d, key=lambda k: int(k[0]), reverse=True)
            d["vp4"] = vol_profile(k5d[:48], bins=50, min_gap_pct=0.2)
            d["vp24"] = vol_profile(k5d, bins=80, min_gap_pct=0.2)

    # 日內大方向：H4 與日線（日線以 UTC 0 點 = 台灣 08:00 換日，與 TradingView 一致）
    if full:
        d["h4"] = trend_info(okx("/market/candles", instId=c["swap"], bar="4H", limit="40"), last)
        d["day"] = trend_info(okx("/market/candles", instId=c["swap"], bar="1Dutc", limit="40"), last)
        if d["h4"]:
            d["p4bar"] = d["h4"]["chg_bar"]
        d["liq_map"] = liq_clusters(c, last)

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

    # 貓咪隧道（15m／H1／H4，完整版加日線）＋ 三層波動
    try:
        d["tun"] = tunnel_data(c, d["last"], full)
    except Exception as e:  # noqa  隧道失敗不影響其他內容
        print(f"[tunnel] {c['name']} 失敗：{e}")
    return d


# ───────────────────────── 貓咪隧道 × 量能（依 2026/10 回測） ─────────────────────────
# 隧道：高斯核回歸 BW=14、DEV=1.5（與 TradingView 貓咪隧道相同參數），只用「已收盤」K 計算。
# 回測（tools/tunnel_backtest_crypto.py，10 檔主流幣）結論，只採各週期方向一致者：
#   - 15m／1H 出軌本身沒有方向性（平均報酬≈0）→ 只描述，不下方向
#   - H4／日線收在上軌外：偏續強，不是回落訊號
#   - 下軌外：偏小幅反彈但過半先再跌一段；爆量（＋持倉下降）時反彈最明顯；H4 下軌外第 4 根起反彈較明顯
#   - 中段「從上軌回落、中線仍上彎」：較常繼續回測下軌（三週期一致）
#   - 回軌K：放量站回下軌成功率最高、量縮最弱；跌回上軌時持倉下降＝多單撤退，回落成功率高
#   - 三層波動（15m 碰 H1 軌後 12 小時）：機率表同 Pine 指標 貓咪隧道crypto Premium
import math

_TBW, _TDEV = 14, 1.5
_TW = [math.exp(-(j / _TBW) ** 2 / 2) / math.sqrt(2 * math.pi) for j in range(150)]
_TSUM = sum(math.exp(-(j / _TBW) ** 2 / 2) / math.sqrt(2 * math.pi) for j in range(500))
_TF_MS = {"15m": 900_000, "1H": 3_600_000, "4H": 14_400_000, "1D": 86_400_000}

# 三層波動機率（%）：0~2 碰上軌時 H4【上軌外/中線上/中線下】；3~5 碰下軌時 H4【下軌外/中線上/中線下】
NEST_P = [
    [77, 87, 83, 73, 76, 79],   # 到 15m 對側軌
    [57, 77, 70, 54, 71, 72],   # 到 H1 中線
    [21, 39, 33, 12, 38, 36],   # 到 H1 對側軌
    [3, 11, 15, 1, 18, 9],      # 到 H4 對側軌
]
NEST_X = [61, 48, 48, 66, 50, 56]   # 先續創高/續破低 0.5% 才回頭
NEST_H = 12                         # 追蹤小時數（同回測）


def _closed_rows(rows):
    """OKX K 線 → 已收盤、舊到新的 (ts, o, h, l, c, 成交額USDT)"""
    out = []
    for r in rows or []:
        if len(r) > 8 and r[8] != "1":
            continue
        q = f(r[7]) if len(r) > 7 and r[7] else (f(r[5]) or 0) * (f(r[4]) or 0)
        out.append((int(r[0]), f(r[1]), f(r[2]), f(r[3]), f(r[4]), q or 0))
    return sorted(out, key=lambda x: x[0])


def _band(closes, i):
    seg = closes[max(0, i - 149):i + 1][::-1]
    s1 = s2 = 0.0
    for j, v in enumerate(seg):
        w = _TW[j]
        s1 += v * w
        s2 += v * v * w
    m = s1 / _TSUM
    sd = math.sqrt(max(s2 / _TSUM - m * m, 0.0))
    return m - _TDEV * sd, m, m + _TDEV * sd


def _bands(k, start):
    c = [x[4] for x in k]
    return {i: _band(c, i) for i in range(max(60, start), len(k))}


def tunnel_info(k, last):
    """單一週期：最後一根已收盤 K 的隧道與位置（同回測，以收盤判斷）、連續出軌根數、量比、來源/中線方向、回軌K"""
    n = len(k)
    if n < 90:
        return None
    B = _bands(k, n - 30)
    L, M, U = B[n - 1]
    if U <= L:
        return None
    pos = lambda p, b: (p - b[0]) / (b[2] - b[0]) * 100
    pc = pos(k[-1][4], B[n - 1])                       # 最後收盤在隧道的位置
    side = "up" if pc > 100 else "dn" if pc < 0 else None
    run, run_v = 0, []
    for i in range(n - 1, n - 30, -1):
        pi = pos(k[i][4], B[i])
        if (side == "up" and pi > 100) or (side == "dn" and pi < 0):
            run += 1
            run_v.append(k[i][5])
        else:
            break
    vols = [x[5] for x in k[n - 21:n - 1] if x[5]]
    ratio = k[-1][5] / (sum(vols) / len(vols)) if vols and k[-1][5] else None
    tl = any(k[i][3] <= B[i][0] for i in range(n - 16, n - 1))
    tu = any(k[i][2] >= B[i][2] for i in range(n - 16, n - 1))
    M5 = B[n - 6][1]
    slope = "上彎" if M > M5 * 1.002 else "下彎" if M < M5 * 0.998 else "走平"
    # 回軌K（最後一根已收盤）
    Lp, Mp, Up = B[n - 2]
    rev = None
    if k[-2][3] <= Lp and k[-1][3] > L and k[-1][4] > k[-2][4]:
        rev = "long"
    elif k[-2][2] >= Up and k[-1][2] < U and k[-1][4] < k[-2][4]:
        rev = "short"
    return {"L": L, "M": M, "U": U, "pos": pc, "pos_now": pos(last, B[n - 1]), "side": side, "run": run,
            "maxvol": run > 1 and k[-1][5] >= max(run_v), "ratio": ratio,
            "from": "下軌" if tl and not tu else "上軌" if tu and not tl else None,
            "slope": slope, "rev": rev, "ts": k[-1][0]}


def _oi_change(c, k1h, hours):
    """近 N 小時持倉（以幣數計，排除價格漲跌影響）變化 %"""
    oi = okx("/rubik/stat/contracts/open-interest-volume", ccy=c["name"], period="1H")
    if not oi or not k1h:
        return None
    px = {x[0]: x[1] for x in k1h}                      # 每小時開盤價
    rows = sorted(oi, key=lambda r: int(r[0]), reverse=True)
    if len(rows) <= hours:
        return None
    a, b = rows[0], rows[hours]
    pa, pb = px.get(int(a[0])), px.get(int(b[0]))
    if not (pa and pb and f(a[1]) and f(b[1])):
        return None
    return (f(a[1]) / pa) / (f(b[1]) / pb) * 100 - 100


def nest_event(k15, k1, k4, last):
    """找最近 12 小時內「15m 剛碰到 H1 上/下軌」的事件，並追蹤四站到了沒"""
    if len(k15) < 60 or len(k1) < 90 or len(k4) < 90:
        return None
    B15 = _bands(k15, len(k15) - NEST_H * 4 - 2)
    B1 = _bands(k1, 60)
    B4 = _bands(k4, 60)
    ends1 = [x[0] + _TF_MS["1H"] for x in k1]
    ends4 = [x[0] + _TF_MS["4H"] for x in k4]
    import bisect

    def hb(ends, B, t):
        j = bisect.bisect_right(ends, t) - 1
        return B.get(j)
    start = len(k15) - NEST_H * 4
    ev = None
    for i in range(max(start, 1), len(k15)):
        t = k15[i][0]
        b1, b1p, b4 = hb(ends1, B1, t), hb(ends1, B1, k15[i - 1][0]), hb(ends4, B4, t)
        if not (b1 and b1p and b4):
            continue
        up = k15[i][2] >= b1[2] and k15[i - 1][2] < b1p[2]
        dn = k15[i][3] <= b1[0] and k15[i - 1][3] > b1p[0]
        if up or dn:
            p4 = (k15[i][4] - b4[0]) / (b4[2] - b4[0]) * 100 if b4[2] > b4[0] else 50
            side = 1 if up and (not dn or k15[i][4] >= b1[1]) else -1
            ctx = (0 if p4 > 100 else 1 if p4 >= 50 else 2) if side > 0 else (3 if p4 < 0 else 4 if p4 > 50 else 5)
            ev = {"i": i, "side": side, "ctx": ctx, "ts": t, "ref": k15[i][2] if side > 0 else k15[i][3]}
    if not ev:
        return None
    hit = [False] * 4
    ext = False
    for j in list(range(ev["i"] + 1, len(k15))) + [None]:
        if j is None:                                   # 最後用現價補一次
            hi = lo = last
            t = k15[-1][0] + _TF_MS["15m"]
            b15 = B15.get(len(k15) - 1)
        else:
            hi, lo, t = k15[j][2], k15[j][3], k15[j][0]
            b15 = B15.get(j)
        b1, b4 = hb(ends1, B1, t), hb(ends4, B4, t)
        if not (b15 and b1 and b4):
            continue
        if ev["side"] > 0:
            if not hit[0] and hi >= ev["ref"] * 1.005:
                ext = True
            lv = [b15[0], b1[1], b1[0], b4[0]]
            for x in range(4):
                hit[x] = hit[x] or lo <= lv[x]
        else:
            if not hit[0] and lo <= ev["ref"] * 0.995:
                ext = True
            lv = [b15[2], b1[1], b1[2], b4[2]]
            for x in range(4):
                hit[x] = hit[x] or hi >= lv[x]
    b15, b1, b4 = B15.get(len(k15) - 1), B1.get(len(k1) - 1), B4.get(len(k4) - 1)
    if ev["side"] > 0:
        lv = [b15[0], b1[1], b1[0], b4[0]]
    else:
        lv = [b15[2], b1[1], b1[2], b4[2]]
    ev.update(hit=hit, ext=ext, lv=lv, hours=(time.time() * 1000 - ev["ts"]) / 3_600_000)
    return ev


def tunnel_data(c, last, full=False):
    k15 = _closed_rows(okx("/market/candles", instId=c["swap"], bar="15m", limit="300"))
    k1 = _closed_rows(okx("/market/candles", instId=c["swap"], bar="1H", limit="300"))
    k4 = _closed_rows(okx("/market/candles", instId=c["swap"], bar="4H", limit="300"))
    t = {"15m": tunnel_info(k15, last), "1H": tunnel_info(k1, last), "4H": tunnel_info(k4, last)}
    if full:
        t["1D"] = tunnel_info(_closed_rows(okx("/market/candles", instId=c["swap"], bar="1Dutc", limit="300")), last)
    t["oi4h"] = _oi_change(c, k1, 4)
    t["nest"] = nest_event(k15, k1, k4, last)
    return t


def _zone(ti):
    if not ti:
        return "—"
    p = ti["pos"]
    if p > 100:
        return "上軌外"
    if p < 0:
        return "下軌外"
    if p >= 70:
        return "靠近上軌"
    if p <= 30:
        return "靠近下軌"
    return "中段"


def _ratio_txt(r):
    if r is None:
        return ""
    tag = "爆量" if r >= 2.5 else "放量" if r >= 1.5 else "量縮" if r < 0.8 else "量平"
    return f"{tag} {r:.1f}×"


def tunnel_read(ti, name, oi=None):
    """H4／日線：位置 ＋ 回測的歷史傾向（描述盤勢，不下指令）"""
    if not ti:
        return None
    z = _zone(ti)
    head = f"{name} {z}"
    if ti["side"]:
        head += f" 第 {ti['run']} 根"
    rt = _ratio_txt(ti["ratio"]) if (ti["side"] or ti["rev"]) else ""
    if rt:
        head += f"｜量 {rt}"
    if oi is not None and name == "H4":
        head += f"｜持倉 4h {arrow_pct(oi, 1)}"
    note = ""
    r = ti["ratio"] or 0
    if ti["side"] == "up":
        note = "歷史上收在上軌外之後多半延續強勢，並不是回落訊號"
    elif ti["side"] == "dn":
        if r >= 2.5 and oi is not None and oi <= -1:
            note = "爆量＋持倉下降（多單被清）：歷史上這類情況之後反彈最明顯"
        elif r >= 2.5:
            note = "出現爆量：歷史上下軌外爆量之後反彈較明顯"
        elif name == "H4" and ti["run"] >= 4:
            note = "已在下軌外多根：歷史上這時之後的反彈反而較明顯，但過程常有震盪"
        else:
            note = "歷史上偏小幅反彈，但過半會先再跌一段"
    elif z == "中段" or 30 < ti["pos"] < 70:
        if ti["from"] == "上軌" and ti["slope"] == "上彎":
            note = "從上軌回落、中線仍上彎：歷史上較常繼續回測下軌"
        elif ti["from"] == "下軌" and ti["slope"] == "下彎":
            note = "從下軌反彈、中線仍下彎：歷史上略偏先碰上軌"
    if ti["rev"] == "long":
        rr = ti["ratio"] or 0
        note = ("剛站回下軌，" + ("而且放量：歷史上這類站回成功率較高" if rr >= 1.5 else
                                  "但量縮：歷史上這類站回較常失敗" if rr < 1 else "量能普通"))
    elif ti["rev"] == "short":
        note = "剛跌回上軌內" + ("，持倉下降（多單撤退）：歷史上這類回落成功率較高" if oi is not None and oi <= -1 and name == "H4" else "")
    return head, note


NEST_NAMES = {1: ["15m 下軌", "H1 中線", "H1 下軌", "H4 下軌"], -1: ["15m 上軌", "H1 中線", "H1 上軌", "H4 上軌"]}
NEST_CTX = {0: "大方向強勢", 1: "大方向偏多", 2: "大方向偏空", 3: "大方向弱勢", 4: "大方向偏多", 5: "大方向偏空"}


def nest_lines(t):
    ev = (t or {}).get("nest")
    if not ev:
        return []
    up = ev["side"] > 0
    names = NEST_NAMES[ev["side"]]
    far = max([x for x in range(4) if ev["hit"][x]], default=-1)   # 最遠到過哪一站
    nxt = far + 1 if far < 3 else None
    L = [f"🪜 {ev['hours']:.1f} 小時前碰到 H1 {'上軌' if up else '下軌'}（{NEST_CTX[ev['ctx']]}）"
         f"→ 歷史上 12 小時內{'回落' if up else '反彈'}到："
         + "、".join(f"{names[x]} {NEST_P[x][ev['ctx']]}%" for x in range(4))]
    status = f"最遠已到 {names[far]}" if far >= 0 else "還沒到第一站"
    if nxt is not None:
        status += f"｜下一站 {names[nxt]} {pnum(ev['lv'][nxt])}"
        if far < 0:
            status += f"（有 {NEST_X[ev['ctx']]}% 會先再{'漲' if up else '跌'} 0.5% 才{'回頭' if up else '彈'}）"
    else:
        status += "（四站都到了，這次是大波動）"
    L.append("-# " + status)
    return L


def tunnel_short_line(t):
    """簡短版：一行位置 ＋ 三層波動"""
    if not t or not t.get("4H"):
        return []
    parts = [f"H4 {_zone(t['4H'])}", f"H1 {_zone(t.get('1H'))}", f"15m {_zone(t.get('15m'))}"]
    L = ["🐱 隧道：" + "｜".join(parts)]
    return L + nest_lines(t)


def tunnel_full_lines(t):
    if not t or not t.get("4H"):
        return []
    L = ["**🐱 貓咪隧道（位置 × 歷史傾向）**"]
    for name in ("1D", "4H"):
        r = tunnel_read(t.get(name), "日線" if name == "1D" else "H4", t.get("oi4h"))
        if r:
            L.append(f"• {r[0]}" + (f"：{r[1]}" if r[1] else ""))
    L.append(f"• H1 {_zone(t.get('1H'))}｜15m {_zone(t.get('15m'))}")
    L += nest_lines(t)
    L.append("-# 15m／H1 碰軌或出軌本身不代表方向，短線看上面的三層波動；歷史傾向來自回測，不代表這次一定如此")
    return L


# ───────────────────────── 成交量分布（Volume Profile） ─────────────────────────

def vol_profile(candles, bins=60, min_gap_pct=0.2):
    """
    成交量分布：把每根 K 的成交額（USDT）平均攤到它的高低價之間，累加成各價位成交量。
    回傳 {poc 成交最密集價, vah/val 主要成交區（七成成交量）上下緣, gaps 成交真空區 [(低, 高)]}
    """
    rows = []
    for k in candles or []:
        h, l = f(k[2]), f(k[3])
        v = f(k[7]) if len(k) > 7 else None
        if v is None:
            v = f(k[6]) if len(k) > 6 else None
        if h and l and v and h >= l:
            rows.append((h, l, v))
    if len(rows) < 10:
        return None
    lo = min(r[1] for r in rows)
    hi = max(r[0] for r in rows)
    if hi <= lo:
        return None
    step = (hi - lo) / bins
    vol = [0.0] * bins
    for h, l, v in rows:
        a = min(int((l - lo) / step), bins - 1)
        b = min(int((h - lo) / step), bins - 1)
        share = v / (b - a + 1)
        for i in range(a, b + 1):
            vol[i] += share
    total = sum(vol)
    if total <= 0:
        return None
    mid = lambda i: lo + (i + 0.5) * step

    # 成交最密集價
    pi = max(range(bins), key=lambda i: vol[i])

    # 主要成交區：從最密集價往成交量較大的一側擴張，直到涵蓋七成
    i = j = pi
    acc = vol[pi]
    while acc < total * 0.7 and (i > 0 or j < bins - 1):
        up = vol[j + 1] if j < bins - 1 else -1
        dn = vol[i - 1] if i > 0 else -1
        if up >= dn:
            j += 1; acc += up
        else:
            i -= 1; acc += dn

    # 成交真空區：去掉頭尾各 5% 的邊緣後，連續偏低（< 平均 25%）的價位
    cum, s0, s1 = 0.0, 0, bins - 1
    for k in range(bins):
        cum += vol[k]
        if cum >= total * 0.05:
            s0 = k; break
    cum = 0.0
    for k in range(bins - 1, -1, -1):
        cum += vol[k]
        if cum >= total * 0.05:
            s1 = k; break
    inner = vol[s0:s1 + 1]
    gaps = []
    if len(inner) > 4:
        th = sum(inner) / len(inner) * 0.25
        min_run = max(2, bins // 30)
        k = s0
        while k <= s1:
            if vol[k] < th:
                e = k
                while e + 1 <= s1 and vol[e + 1] < th:
                    e += 1
                g0, g1 = lo + k * step, lo + (e + 1) * step
                if e - k + 1 >= min_run and (g1 - g0) / g0 * 100 >= min_gap_pct:   # 太窄的空檔是雜訊，不列
                    gaps.append((g0, g1))
                k = e + 1
            else:
                k += 1
    return {"poc": mid(pi), "val": lo + i * step, "vah": lo + (j + 1) * step, "gaps": gaps}


def near_gaps(vp, last, max_pct):
    """回傳（上方最近真空區, 下方最近真空區, 價格所在真空區），只取距現價 max_pct% 以內"""
    up = dn = inside = None
    for a, b in (vp or {}).get("gaps", []):
        if a <= last <= b:
            inside = (a, b)
        elif a > last and (a / last - 1) * 100 <= max_pct and (up is None or a < up[0]):
            up = (a, b)
        elif b < last and (1 - b / last) * 100 <= max_pct and (dn is None or b > dn[1]):
            dn = (a, b)
    return up, dn, inside


def rng_txt(g):
    return f"{pnum(g[0])}～{pnum(g[1])}"


def vp_short_line(d):
    """簡短版：1 小時成交密集價 + 貼近現價的真空區"""
    vp = d.get("vp1")
    if not vp:
        return None
    last = d["last"]
    pct = (vp["poc"] / last - 1) * 100
    side = "現價上方" if pct > 0 else "現價下方"
    s = f"📊 1h 成交密集 {pnum(vp['poc'])}（{side} {pct:+.2f}%）"
    up, dn, inside = near_gaps(vp, last, 1.0)
    if inside:
        s += f"｜價格正在成交稀少區 {rng_txt(inside)}，容易快速移動"
    elif up or dn:
        parts = ([f"上方 {rng_txt(up)}"] if up else []) + ([f"下方 {rng_txt(dn)}"] if dn else [])
        s += "｜成交稀少：" + "、".join(parts) + "，易快速穿越"
    return s


def vp_full_lines(d):
    """完整版：24h 主要成交區位置判讀、4h/24h 共振、成交真空區"""
    v24, v4 = d.get("vp24"), d.get("vp4")
    if not v24:
        return []
    last = d["last"]
    L = ["**📊 成交分布（4h／24h）**"]
    poc, vah, val = v24["poc"], v24["vah"], v24["val"]
    if last > vah:
        L.append(f"價格在 24h 主要成交區上方，已脫離密集區往上；回測上緣 {pnum(vah)} 是第一道支撐")
    elif last < val:
        L.append(f"價格在 24h 主要成交區下方，已跌出密集區；反彈到下緣 {pnum(val)} 是第一道壓力")
    elif last >= poc:
        L.append(f"價格在 24h 主要成交區內（{pnum(val)}～{pnum(vah)}），高於成交最密集價 {pnum(poc)}；"
                 f"回落到 {pnum(poc)} 附近容易有買盤承接")
    else:
        L.append(f"價格在 24h 主要成交區內（{pnum(val)}～{pnum(vah)}），低於成交最密集價 {pnum(poc)}；"
                 f"反彈到 {pnum(poc)} 附近容易遇到賣壓")
    # 共振：相差 0.3% 以內，且不超過 24h 主要成交區寬度的 15%（波動小的日子自動收緊）
    if v4 and abs(v4["poc"] - poc) <= min(last * 0.003, (vah - val) * 0.15):
        L.append(f"🎯 4h 與 24h 成交最密集價接近（{pnum(v4['poc'])}／{pnum(poc)}）"
                 f"→ 這一帶是日內重要的多空平衡區")
    up, dn, inside = near_gaps(v24, last, 5.0)
    if inside:
        L.append(f"🕳 價格正處於成交真空區 {rng_txt(inside)}，容易快速移動")
    if up or dn:
        parts = ([f"上方 {rng_txt(up)}"] if up else []) + ([f"下方 {rng_txt(dn)}"] if dn else [])
        L.append("🕳 成交真空：" + "、".join(parts) + " → 價格進入後容易快速穿越")
    L.append("-# 成交密集＝該時段成交最多的價位，常成為支撐或壓力；主要成交區＝七成成交量所在區間；"
             "成交真空＝成交稀少的價位，價格通常快速通過")
    return L


# 常見槓桿分布（估算清算價用）
LEVERAGE_MIX = [(10, 0.30), (25, 0.30), (50, 0.25), (100, 0.15)]


def liq_clusters(c, last, hours=48, max_dist=8.0):
    """
    估算清算密集區：用近 48 小時每小時新增的合約部位 × 當時價格，
    依主動買賣量分多空，套用常見槓桿算出清算價；之後已被價格掃過的剔除。
    回傳 (上方空單清算區, 下方多單清算區)，各為 [(價格, 強度 0~1)]
    """
    oi = okx("/rubik/stat/contracts/open-interest-volume", ccy=c["name"], period="1H")
    k1 = okx("/market/candles", instId=c["swap"], bar="1H", limit=str(hours + 1))
    tv = okx("/rubik/stat/taker-volume", ccy=c["name"], instType="CONTRACTS", period="1H")
    if not oi or not k1:
        return None
    candles = {int(k[0]): k for k in k1}
    ts_all = sorted(candles)
    taker = {int(r[0]): (f(r[1]) or 0, f(r[2]) or 0) for r in (tv or [])}
    step = last * 0.004  # 每 0.4% 一格
    longs, shorts = {}, {}
    prev = None
    for r in sorted(oi, key=lambda r: int(r[0])):
        ts = int(r[0])
        k = candles.get(ts)
        if not k or not f(r[1]):
            prev = None
            continue
        coin = f(r[1]) / f(k[1])
        if prev is not None and coin > prev:
            px = (f(k[2]) + f(k[3]) + f(k[4])) / 3
            notional = (coin - prev) * px
            sell, buy = taker.get(ts, (1, 1))
            long_share = buy / (sell + buy) if sell + buy > 0 else 0.5
            later = [candles[t] for t in ts_all if t >= ts]
            hi_after = max(f(x[2]) for x in later)
            lo_after = min(f(x[3]) for x in later)
            for lev, w in LEVERAGE_MIX:
                lp = px * (1 - 1 / lev + 0.005)   # 多單清算價（含維持保證金的近似）
                sp = px * (1 + 1 / lev - 0.005)   # 空單清算價
                if lo_after > lp:
                    key = round(lp / step)
                    longs[key] = longs.get(key, 0) + notional * long_share * w
                if hi_after < sp:
                    key = round(sp / step)
                    shorts[key] = shorts.get(key, 0) + notional * (1 - long_share) * w
        prev = coin

    def pick(book, above):
        rows = [(k * step, v) for k, v in book.items()
                if (k * step > last if above else k * step < last)
                and abs(k * step / last - 1) * 100 <= max_dist]
        return rows
    up, down = pick(shorts, True), pick(longs, False)
    peak = max([v for _, v in up + down], default=0)
    if peak <= 0:
        return None

    def top(rows):
        rows = [(p, v / peak) for p, v in rows if v / peak >= 0.25]
        rows = sorted(rows, key=lambda x: x[1], reverse=True)[:2]
        return sorted(rows, key=lambda x: abs(x[0] - last))
    return top(up), top(down)


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


def regime(d):
    """
    行情型態：波動放大 / 正常 / 低波動（箱型整理、緩慢盤漲、緩慢盤跌）
    低波動不是壞事：盤整期區間上下緣常出現反轉，適合抓反轉的做法
    """
    vx = d.get("volx")
    if vx is None:
        return None
    if vx >= 1.8:
        return {"type": "急動", "light": ("🟡", "波動度", f"波動明顯放大，是平常的 {vx:.1f} 倍，行情正在急動")}
    if vx >= 1.2:
        return {"type": "偏高", "light": ("⚪", "波動度", f"波動略高於平常（{vx:.1f} 倍）")}
    if vx >= 0.8 or d.get("open4h") is None or "hi4" not in d:
        return {"type": "正常", "light": ("⚪", "波動度", f"波動正常（{vx:.1f} 倍）")}
    last, hi, lo = d["last"], d["hi4"], d["lo4"]
    rng = hi - lo
    if rng <= 0:
        return {"type": "正常", "light": ("⚪", "波動度", f"波動正常（{vx:.1f} 倍）")}
    net = (last - d["open4h"]) / rng
    width = rng / last * 100
    if abs(net) <= 0.35:
        return {"type": "箱型", "hi": hi, "lo": lo,
                "light": ("🔵", "波動度", f"低波動箱型整理，區間約 {pnum(lo)}～{pnum(hi)}（寬 {width:.1f}%），上下緣容易出現反轉")}
    if net > 0:
        return {"type": "緩漲",
                "light": ("🔵", "波動度", "低波動緩慢盤漲，方向偏多但節奏慢，進場點不易掌握")}
    return {"type": "緩跌",
            "light": ("🔵", "波動度", "低波動緩慢盤跌，方向偏空但節奏慢，進場點不易掌握")}


def lights(d):
    """燈號：(燈, 名稱, 白話)。🟢 利多 🔴 利空 🟡 波動放大 🔵 盤整型態 ⚪ 中性"""
    out = []

    rg = regime(d)
    if rg:
        out.append(rg["light"])

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
    for key, lab in (("vp1", "1h 成交密集"), ("vp4", "4h 成交密集"), ("vp24", "24h 成交密集")):
        if d.get(key):
            cand.append((d[key]["poc"], lab))
    if d.get("vp24"):
        cand += [(d["vp24"]["vah"], "24h 主要成交區上緣"), (d["vp24"]["val"], "24h 主要成交區下緣")]
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


def round_price(p):
    if p >= 10000:
        return round(p / 100) * 100
    if p >= 1000:
        return round(p / 10) * 10
    return round(p, 2)


def liq_lines(d):
    lm = d.get("liq_map")
    if not lm or not (lm[0] or lm[1]):
        return []
    last = d["last"]

    def fmt(rows):
        return "、".join(f"{pnum(round_price(p))}（{(p / last - 1) * 100:+.1f}%）"
                        f"{'■' * max(1, round(v * 3))}" for p, v in rows)
    L = ["**💥 清算密集區（估算）**"]
    if lm[0]:
        L.append(f"⬆️ 上方空單清算：{fmt(lm[0])}")
    if lm[1]:
        L.append(f"⬇️ 下方多單清算：{fmt(lm[1])}")
    L.append("-# 價格靠近時容易觸發連環強制平倉、行情被加速；■ 越多代表越密集。"
             "依近 48 小時新增合約部位與常見槓桿估算，非實際清算單")
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
    tn = tunnel_full_lines(d.get("tun"))
    if tn:
        L += tn + [""]
    rg = regime(d)
    box = f"目前是箱型整理，區間 {pnum(rg['lo'])}～{pnum(rg['hi'])}。" if rg and rg["type"] == "箱型" and st in ("整理", "膠著") else ""
    L += [f"**🧭 短線局勢（15m／1h）：{icon} {st}**", desc + pos_sentence(d) + box, "", "**🚦 短線燈號**"]
    for light, name, text in lt:
        L.append(f"{light} **{name}**：{text}")
    fn = funding_note(d.get("funding"), d.get("fund_min"))
    if fn and d.get("fund_min", 999) <= 60:
        L.append(f"⏳ {fn}")
    L += ["", "**📍 關鍵價位**"] + (multi_levels(d) if (d.get("h4") or d.get("day")) else level_lines(d))
    vl = vp_full_lines(d)
    if vl:
        L += [""] + vl
    ll = liq_lines(d)
    if ll:
        L += [""] + ll
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
    L.append(GUIDE_LINK)
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
        L += tunnel_short_line(d.get("tun"))
        vs = vp_short_line(d)
        if vs:
            L.append(vs)
        L.append("")
    L.append(GUIDE_LINK)
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
