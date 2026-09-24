# -*- coding: utf-8 -*-
"""
比特聯盟 BitoAlliance｜整點市場快報 → Discord

台灣時間 00/04/08/12/16/20 點推「H4 完整版」，其他整點推「簡短版」。
資料來源：
  - OKX 公開 API：價格、K 棒、OI、資金費率、多空比、主動買賣、爆倉
  - Coinbase 公開 API：計算 Coinbase 溢價
  - CoinGecko：總市值、BTC 市佔、板塊輪動（只在完整版抓）
  - alternative.me：恐懼貪婪指數（只在完整版抓）

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

FNG_ZH = {
    "Extreme Fear": "極度恐懼", "Fear": "恐懼", "Neutral": "中性",
    "Greed": "貪婪", "Extreme Greed": "極度貪婪",
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


def kfmt(p):
    if p >= 10000:
        return f"{p / 1000:.2f}K"
    if p >= 1000:
        return f"{p / 1000:.3f}K"
    return f"{p:,.2f}"


def range_bar(pos, n=12):
    i = max(0, min(n - 1, int(pos / 100 * n)))
    return "─" * i + "●" + "─" * (n - 1 - i)


def pos_text(pos):
    if pos < 25:
        return "貼近下緣"
    if pos > 75:
        return "貼近上緣"
    return "區間中段"


def quad(p, o, th):
    """價格 × OI 四象限解讀"""
    if p is None or o is None:
        return None, 0
    if p > 0 and o > th:
        return "價漲 + OI 增：多方新開倉推動", 1
    if p > 0 and o < -th:
        return "價漲 + OI 減：空方回補推動，力道存疑", 0
    if p < 0 and o > th:
        return "價跌 + OI 增：空方加倉，下跌有延續風險", -1
    if p < 0 and o < -th:
        return "價跌 + OI 減：多方停損、槓桿清洗中", 0
    return "OI 變化不大：觀望為主", 0


# ───────────────────────── 抓資料 ─────────────────────────

def liquidations(c, hours=4, max_pages=10):
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
            if side == "net":  # 單向持倉模式：賣出強平 = 多單爆倉
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


def coin_data(c, full):
    d = {}
    t = okx("/market/ticker", instId=c["swap"])
    if not t:
        return None
    t = t[0]
    last = f(t["last"])
    d["last"] = last
    d["chg24"] = (last / f(t["open24h"]) - 1) * 100
    d["hi24"], d["lo24"] = f(t["high24h"]), f(t["low24h"])
    rng = d["hi24"] - d["lo24"]
    d["pos"] = (last - d["lo24"]) / rng * 100 if rng > 0 else 50.0

    # 1H K 棒：上一小時漲跌、量能倍數、各時間點價格
    open_at = {}
    k1 = okx("/market/candles", instId=c["swap"], bar="1H", limit="60")
    if k1:
        open_at = {int(k[0]): f(k[1]) for k in k1}
        done = [k for k in k1 if k[8] == "1"]
        if done:
            d["p1bar"] = (f(done[0][4]) / f(done[0][1]) - 1) * 100
            vols = [f(k[7]) or 0 for k in done[:25]]
            if len(vols) > 5 and sum(vols[1:]) > 0:
                d["volx"] = vols[0] / (sum(vols[1:]) / len(vols[1:]))

    # 4H K 棒：上一根 4H 漲跌、7 日高低
    k4 = okx("/market/candles", instId=c["swap"], bar="4H", limit="43")
    if k4:
        done4 = [k for k in k4 if k[8] == "1"]
        if done4:
            d["p4bar"] = (f(done4[0][4]) / f(done4[0][1]) - 1) * 100
            d["hi7"] = max(f(k[2]) for k in done4[:42])
            d["lo7"] = min(f(k[3]) for k in done4[:42])

    # OI（OKX 該幣所有合約加總）
    oi = okx("/rubik/stat/contracts/open-interest-volume", ccy=c["name"], period="1H")
    if oi:
        oi = rows_desc(oi)
        d["oi"] = f(oi[0][1])

        def coin_oi(i):
            px = open_at.get(int(oi[i][0]))
            return f(oi[i][1]) / px if px else None

        for h in (1, 4, 24):
            if len(oi) > h and f(oi[h][1]):
                d[f"oi{h}_usd"] = (f(oi[0][1]) / f(oi[h][1]) - 1) * 100
                a, b = coin_oi(0), coin_oi(h)
                d[f"oi{h}_coin"] = (a / b - 1) * 100 if a and b else None
        for h in (1, 4):
            if len(oi) > h:
                p0, ph = open_at.get(int(oi[0][0])), open_at.get(int(oi[h][0]))
                if p0 and ph:
                    d[f"px{h}"] = (p0 / ph - 1) * 100
        vals = [f(r[1]) for r in oi if f(r[1])]
        if vals:
            d["oi_pctl"] = sum(v <= vals[0] for v in vals) / len(vals) * 100
            d["oi_days"] = len(vals) / 24

    # 資金費率（OKX 回傳小數，×100 變百分比）
    fr = okx("/public/funding-rate", instId=c["swap"])
    if fr:
        d["funding"] = f(fr[0]["fundingRate"]) * 100

    # 散戶（全體帳戶）多空比
    ra = okx("/rubik/stat/contracts/long-short-account-ratio", ccy=c["name"], period="1H")
    if ra:
        ra = rows_desc(ra)
        d["retail"] = f(ra[0][1])
        if len(ra) > 4:
            d["retail_chg"] = f(ra[0][1]) - f(ra[4][1])

    # 大戶持倉多空比
    tp = okx("/rubik/stat/contracts/long-short-position-ratio-contract-top-trader",
             instId=c["swap"], period="1H")
    if tp:
        d["top_pos"] = f(rows_desc(tp)[0][1])

    # 主動買賣量（只取已完成的小時）
    tv = okx("/rubik/stat/taker-volume", ccy=c["name"], instType="CONTRACTS", period="1H")
    if tv:
        now_ms = time.time() * 1000
        done = [r for r in rows_desc(tv) if int(r[0]) + 3_600_000 <= now_ms + 60_000]
        sell4 = sum(f(r[1]) or 0 for r in done[:4])
        buy4 = sum(f(r[2]) or 0 for r in done[:4])
        if sell4 > 0:
            d["taker4"] = buy4 / sell4
        if done and f(done[0][1]):
            d["taker1"] = f(done[0][2]) / f(done[0][1])

    if full:
        d["liq_long"], d["liq_short"] = liquidations(c, hours=4)
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
        m["eth_dom"] = g["market_cap_percentage"]["eth"]
    cats = get(CG + "/coins/categories", headers=headers)
    if cats:
        rows = [(SECTORS[x["name"]], x["market_cap_change_24h"]) for x in cats
                if x.get("name") in SECTORS and x.get("market_cap_change_24h") is not None]
        rows.sort(key=lambda r: r[1], reverse=True)
        m["strong"], m["weak"] = rows[:3], rows[-3:][::-1]
    fng = get("https://api.alternative.me/fng/?limit=1")
    if fng and fng.get("data"):
        x = fng["data"][0]
        m["fng"] = (x["value"], FNG_ZH.get(x["value_classification"], x["value_classification"]))
    m["premium"] = coinbase_premium()
    return m


# ───────────────────────── 多空判讀 ─────────────────────────

def judge(d, premium=None):
    score, reasons = 0, []

    o4 = d.get("oi4_coin") if d.get("oi4_coin") is not None else d.get("oi4_usd")
    text, s = quad(d.get("px4"), o4, 0.5)
    if text:
        score += s
        reasons.append(f"近 4h 價 {arrow_pct(d.get('px4'), 1)}、OI {arrow_pct(o4, 1)} → {text}")

    fr = d.get("funding")
    if fr is not None:
        if fr >= 0.03:
            score -= 1
            reasons.append(f"資金費率 {fr:.4f}% 偏高：多方擁擠")
        elif fr <= -0.01:
            score += 1
            reasons.append(f"資金費率 {fr:.4f}% 為負：空方擁擠")

    rc, tp = d.get("retail_chg"), d.get("top_pos")
    if rc is not None and tp is not None:
        if rc > 0.03 and tp < 1:
            score -= 1
            reasons.append("散戶加多、大戶持倉偏空：背離")
        elif rc < -0.03 and tp > 1:
            score += 1
            reasons.append("散戶減多、大戶持倉偏多：背離")

    tr = d.get("taker4")
    if tr is not None:
        if tr >= 1.1:
            score += 1
            reasons.append(f"4h 主動買盤主導（買賣比 {tr:.2f}）")
        elif tr <= 0.9:
            score -= 1
            reasons.append(f"4h 主動賣壓主導（買賣比 {tr:.2f}）")

    if premium is not None:
        if premium >= 0.05:
            score += 1
            reasons.append("Coinbase 溢價為正：美國現貨買盤強")
        elif premium <= -0.05:
            score -= 1
            reasons.append("Coinbase 溢價為負：美國現貨買盤弱")

    ll, ls = d.get("liq_long"), d.get("liq_short")
    if ll and ls is not None and ll > max(ls * 3, 1e6):
        reasons.append("多單爆倉明顯多於空單：多方槓桿被清洗")
    elif ls and ll is not None and ls > max(ll * 3, 1e6):
        reasons.append("空單爆倉明顯多於多單：空方被軋")

    if score >= 2:
        return score, "偏多 🟢", COLOR_BULL, reasons
    if score <= -2:
        return score, "偏空 🔴", COLOR_BEAR, reasons
    return score, "中性 ⚪", COLOR_NEUTRAL, reasons


# ───────────────────────── 組訊息 ─────────────────────────

def full_coin_embed(c, d, premium, stamp):
    if not d:
        return {"title": f"{c['icon']} {c['name']}", "description": "資料暫缺", "color": COLOR_NEUTRAL}
    score, label, color, reasons = judge(d, premium if c["name"] == "BTC" else None)
    L = []
    L.append(f"**{price_fmt(d['last'])}**　24h {arrow_pct(d['chg24'])}｜上一根 4H {arrow_pct(d.get('p4bar'))}")
    L.append(f"24h 區間 {kfmt(d['lo24'])} `{range_bar(d['pos'])}` {kfmt(d['hi24'])}")
    L.append(f"位置 {d['pos']:.0f}%（{pos_text(d['pos'])}）")
    if d.get("hi7"):
        L.append(f"7 日區間 {kfmt(d['lo7'])} ～ {kfmt(d['hi7'])}")
    L.append("")
    L.append("**📊 籌碼面（OKX）**")
    if d.get("oi") is not None:
        L.append(f"OI {usd(d['oi'])}｜1h {arrow_pct(d.get('oi1_coin'), 1)}｜4h {arrow_pct(d.get('oi4_coin'), 1)}"
                 f"｜24h {arrow_pct(d.get('oi24_coin'), 1)}（幣本位）")
        if d.get("oi_pctl") is not None:
            L.append(f"OI 近 {d['oi_days']:.0f} 天位置 {d['oi_pctl']:.0f}%"
                     + ("（偏高，槓桿擁擠）" if d["oi_pctl"] >= 80 else "（偏低）" if d["oi_pctl"] <= 20 else ""))
    if d.get("funding") is not None:
        L.append(f"資金費率 {d['funding']:.4f}%")
    if d.get("retail") is not None:
        rc = d.get("retail_chg")
        rc_txt = f"（4h {'▲' if rc and rc > 0 else '▼' if rc and rc < 0 else '─'}{abs(rc or 0):.2f}）"
        tp = d.get("top_pos")
        L.append(f"散戶多空比 {d['retail']:.2f}{rc_txt}｜大戶持倉比 {tp:.2f}" if tp else
                 f"散戶多空比 {d['retail']:.2f}{rc_txt}")
    if d.get("taker4") is not None:
        L.append(f"主動買賣比 4h {d['taker4']:.2f}｜1h {d.get('taker1', 0):.2f}")
    if d.get("volx") is not None:
        L.append(f"上一小時量能 {d['volx']:.1f} 倍（對比 24h 均量）")
    if d.get("liq_long") is not None:
        L.append(f"4h 爆倉：多單 {usd(d['liq_long'])}｜空單 {usd(d['liq_short'])}")
    L.append("")
    L.append(f"**🧭 判讀：{label}（{score:+d}）**")
    for r in reasons[:5]:
        L.append(f"• {r}")
    L.append(f"偏多確認：站上 {kfmt(d['hi24'])}｜轉弱訊號：跌破 {kfmt(d['lo24'])}")
    return {"title": f"{c['icon']} {c['name']}", "description": "\n".join(L),
            "color": color, "timestamp": stamp}


def market_embed(m, stamp):
    L = []
    if m.get("mcap"):
        L.append(f"總市值 {usd(m['mcap'])}（24h {arrow_pct(m.get('mcap_chg'))}）")
        L.append(f"BTC 市佔 {m['btc_dom']:.1f}%｜ETH 市佔 {m['eth_dom']:.1f}%")
        if m["btc_dom"] >= 55:
            L.append("📌 BTC 市佔高，資金偏向比特幣，山寨相對弱勢")
    if m.get("strong"):
        L.append("🔥 強勢板塊：" + "｜".join(f"{n} {arrow_pct(v, 1)}" for n, v in m["strong"]))
        L.append("🧊 弱勢板塊：" + "｜".join(f"{n} {arrow_pct(v, 1)}" for n, v in m["weak"]))
    if m.get("premium") is not None:
        L.append(f"Coinbase 溢價 {m['premium']:+.3f}%")
    if m.get("fng"):
        L.append(f"恐懼貪婪 {m['fng'][0]}（{m['fng'][1]}，每日 08:00 更新）")
    L.append("\n市場資料由 [CoinGecko](https://www.coingecko.com/en/api) 提供")
    return {"title": "🌐 大盤", "description": "\n".join(L), "color": COLOR_NEUTRAL,
            "timestamp": stamp}


def short_embed(datas, title, stamp):
    L = []
    worst = 0
    for c, d in datas:
        if not d:
            L.append(f"{c['icon']} {c['name']} 資料暫缺")
            continue
        o1 = d.get("oi1_coin") if d.get("oi1_coin") is not None else d.get("oi1_usd")
        L.append(f"{c['icon']} **{c['name']} {price_fmt(d['last'])}**　1h {arrow_pct(d.get('p1bar'))}"
                 f"｜24h {arrow_pct(d['chg24'])}")
        L.append(f"區間位置 {d['pos']:.0f}%（{pos_text(d['pos'])}）｜費率 {d.get('funding', 0):.4f}%"
                 f"｜OI 1h {arrow_pct(o1, 1)}")
        text, s = quad(d.get("px1"), o1, 0.3)
        if text:
            L.append(f"➡️ 近 1h 價 {arrow_pct(d.get('px1'), 1)}、OI {arrow_pct(o1, 1)} → {text}")
            worst = s if abs(s) > abs(worst) else worst
        L.append("")
    color = COLOR_BULL if worst > 0 else COLOR_BEAR if worst < 0 else COLOR_NEUTRAL
    return {"title": title, "description": "\n".join(L).strip(), "color": color, "timestamp": stamp}


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
    footer = {"text": "比特聯盟 BitoAlliance｜籌碼：OKX｜僅供參考，非投資建議"}

    if full:
        m = market_data()
        embeds = [full_coin_embed(c, d, m.get("premium"), stamp) for c, d in datas]
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
