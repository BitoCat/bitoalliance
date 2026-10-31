#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
貓咪隧道 × 量比 × 持倉(OI) 回測（幣圈合約版）—— 一次性、只讀、不推播

股票版（trading-bot/tunnel_backtest.py）的幣圈對照：
  股票「週轉率」在永續合約沒有分母 → 改用「量比」（本根量 ÷ 前20根均量）＋「持倉 OI 變化」
  量增+OI增 = 新倉進場；量增+OI減 = 平倉/爆倉（投降型）

回答（H1 / H4 / Day 三個週期，主流幣永續合約）：
  A 收在上軌之上：之後續漲，還是回隧道？
  B 收在下軌之下：之後續跌，還是回漲？
  C 隧道中間(40~60%)：之後先碰上軌還是下軌？（從哪來 × 中線方向）
  D 多方回軌K（觸下軌後首根整根站回下軌且收漲）：先碰中線(成功) 還是先回下軌(失敗)？
  E 空方回軌K（觸上軌後首根整根跌回上軌且收跌）：先碰中線(成功) 還是先回上軌(失敗)？
  D/E 就是股票幣「外→內反轉」與回漲雷達「脫離K」的幣圈版，依回軌K的量能/OI 分組。

隧道：高斯核回歸，BW=14、DEV=1.5（同 Pine 五貓咪 / day_bands）。
資料：交易所公開 K 線與持倉歷史（GitHub Actions 可連）。OI 歷史較短，抓不到的事件歸「OI無資料」。

用法：
  python tools/tunnel_backtest_crypto.py                       # 預設 10 檔主流幣 × 15m/1H/4H/1D ＋ 多週期嵌套
  python tools/tunnel_backtest_crypto.py --tf nest             # 只跑「多週期嵌套」
  python tools/tunnel_backtest_crypto.py --tf 1H --coins BTC,ETH
結果印在 log；明細存 backtest_crypto_{tf}.csv（workflow 上傳成 Artifact）。

F 多週期嵌套（毛哥的震盪框架）：
  第一層：H1 上軌 ↔ 15m 下軌（小震盪）
  第二層：H1 上軌 ↔ H1 下軌（整個 H1 隧道的來回）
  第三層：觸及 H4 軌道（大波動）
  事件＝15m K 線「剛碰到」H1 上軌（或下軌）。往後 12 小時（48 根 15m）記錄最深回到哪一層：
    碰 H1 上軌 → 15m 下軌？H1 中線？H1 下軌？H4 下軌？以及「先續創高再回」的比例。
  H1/H4 隧道一律用「已收盤」那根的值，避免偷看未來。
"""
import bisect
import argparse
import csv
import math
import time
from collections import defaultdict

import requests

API = "https://www.okx.com/api/v5"
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0"

DEFAULT_COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "ADA", "LINK", "AVAX", "SUI"]
# 每個週期：抓幾根、往後看幾根、「再延伸」門檻%、OI 變化分界%
TF = {
    "15m": {"bars": 17520, "H": 16, "ext": 0.8, "oi": 0.3, "okx": "15m"},  # 約半年；往後看 4 小時
    "1H": {"bars": 8760, "H": 12, "ext": 1.5, "oi": 0.5, "okx": "1H"},     # 約一年
    "4H": {"bars": 4380, "H": 12, "ext": 3.0, "oi": 1.0, "okx": "4H"},     # 約兩年
    "1D": {"bars": 1100, "H": 10, "ext": 5.0, "oi": 2.0, "okx": "1Dutc"},  # 約三年
}
WARMUP = 60
BW, DEV = 14, 1.5
_W = [math.exp(-(j / BW) ** 2 / 2) / math.sqrt(2 * math.pi) for j in range(150)]
_SUMW = sum(math.exp(-(j / BW) ** 2 / 2) / math.sqrt(2 * math.pi) for j in range(500))


# ---------------- 資料 ----------------
def api(path, params, tries=4):
    for i in range(tries):
        try:
            r = S.get(API + path, params=params, timeout=20)
            j = r.json()
            if j.get("code") == "0":
                return j.get("data") or []
            print(f"[api] {path} {j.get('msg')}")
        except Exception as e:
            print(f"[api] {path} {e}")
        time.sleep(1.5 * (i + 1))
    return []


_CACHE = {}


def candles(coin, tf, need):
    """回 [(ts,o,h,l,c,volUSDT)] 舊→新，只取已收盤K。同一幣種週期抓過就用快取（嵌套分析會重用）。"""
    got = _CACHE.get((coin, tf))
    if got and len(got) >= need:
        return got[-need:]
    out = _fetch_candles(coin, tf, need)
    _CACHE[(coin, tf)] = out
    return out


def _fetch_candles(coin, tf, need):
    inst = f"{coin}-USDT-SWAP"
    out, after = [], None
    while len(out) < need:
        p = {"instId": inst, "bar": TF[tf]["okx"], "limit": "100"}
        if after:
            p["after"] = after
        rows = api("/market/history-candles", p)
        if not rows:
            break
        for r in rows:
            if len(r) > 8 and r[8] == "0":
                continue                          # 未收盤
            vq = float(r[7]) if len(r) > 7 and r[7] else float(r[5]) * float(r[4])
            out.append((int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), vq))
        after = rows[-1][0]
        time.sleep(0.12)
    out.sort(key=lambda x: x[0])
    return out


def oi_history(coin, tf):
    """回 {ts: oi}。先試 open-interest-history（可分頁），不行退 open-interest-volume（較短）。"""
    inst = f"{coin}-USDT-SWAP"
    period = {"15m": "15m", "1H": "1H", "4H": "4H", "1D": "1D"}[tf]
    m, end = {}, None
    for _ in range(60):
        p = {"instId": inst, "period": period, "limit": "100"}
        if end:
            p["end"] = end
        rows = api("/rubik/stat/contracts/open-interest-history", p, tries=2)
        if not rows:
            break
        for r in rows:
            try:
                m[int(r[0])] = float(r[3] if len(r) > 3 and r[3] else r[1])
            except (ValueError, IndexError):
                pass
        end = rows[-1][0]
        time.sleep(0.12)
    if not m:
        rows = api("/rubik/stat/contracts/open-interest-volume", {"ccy": coin, "period": period}, tries=2)
        for r in rows:
            try:
                m[int(r[0])] = float(r[1])
            except (ValueError, IndexError):
                pass
    return m


# ---------------- 指標 ----------------
def bands(closes, i):
    seg = closes[max(0, i - 149):i + 1][::-1]
    s1 = s2 = 0.0
    for j, v in enumerate(seg):
        w = _W[j]
        s1 += v * w
        s2 += v * v * w
    mean = s1 / _SUMW
    sd = math.sqrt(max(s2 / _SUMW - mean * mean, 0.0))
    lo, up = mean - DEV * sd, mean + DEV * sd
    return lo, (lo + up) / 2, up


def ratio_b(r):
    if r is None:
        return "無量資料"
    if r < 0.8:
        return "1 量縮(<0.8)"
    if r < 1.5:
        return "2 正常(0.8~1.5)"
    if r < 2.5:
        return "3 放量(1.5~2.5)"
    return "4 爆量(≥2.5)"


def oi_b(chg, thr):
    if chg is None:
        return "OI無資料"
    if chg >= thr:
        return "OI增(新倉進場)"
    if chg <= -thr:
        return "OI減(平倉/爆倉)"
    return "OI持平"


# ---------------- 事件 ----------------
def scan(coin, tf, k, oi):
    cfg = TF[tf]
    H, EXT, OIT = cfg["H"], cfg["ext"], cfg["oi"]
    n = len(k)
    if n < WARMUP + H + 30:
        return []
    ts = [x[0] for x in k]
    o = [x[1] for x in k]
    h = [x[2] for x in k]
    l = [x[3] for x in k]
    c = [x[4] for x in k]
    v = [x[5] for x in k]
    B = [None] * n
    for i in range(WARMUP, n):
        B[i] = bands(c, i)

    def vr(i):
        prev = [x for x in v[max(0, i - 20):i] if x]
        return v[i] / (sum(prev) / len(prev)) if len(prev) >= 10 and v[i] else None

    def oichg(i):
        a, b = oi.get(ts[i]), oi.get(ts[i - 1])
        return (a / b - 1) * 100 if a and b else None

    out = []
    run_side, run_n, run_max = None, 0, 0
    for i in range(WARMUP + 20, n - H):
        L, M, U = B[i]
        if U <= L:
            continue
        pos = (c[i] - L) / (U - L) * 100
        rng = h[i] - l[i]
        up_sh = (h[i] - max(o[i], c[i])) / rng if rng > 0 else 0
        dn_sh = (min(o[i], c[i]) - l[i]) / rng if rng > 0 else 0
        r = vr(i)
        oc = oichg(i)
        retH2 = (c[i + H // 2] - c[i]) / c[i] * 100
        retH = (c[i + H] - c[i]) / c[i] * 100
        base = {"coin": coin, "tf": tf, "ts": ts[i], "pos": round(pos, 1),
                "ratio": round(r, 2) if r else None, "ratio_b": ratio_b(r),
                "oi_chg": round(oc, 2) if oc is not None else None, "oi_b": oi_b(oc, OIT),
                "retH2": round(retH2, 3), "retH": round(retH, 3)}

        side = "up" if pos > 100 else ("dn" if pos < 0 else None)
        if side and side == run_side:
            run_n += 1
        else:
            run_n, run_max = (1 if side else 0), 0
        run_side = side
        is_max = bool(side and run_n > 1 and v[i] >= run_max)
        if side:
            run_max = max(run_max, v[i])

        # ---- D / E 回軌K ----
        Lp, Mp, Up = B[i - 1]
        if l[i - 1] <= Lp and l[i] > L and c[i] > c[i - 1] and h[i] < M:
            out.append(revert_event(base, "D 多方回軌K", i, B, h, l, v, H, up_sh, "long"))
        if h[i - 1] >= Up and h[i] < U and c[i] < c[i - 1] and l[i] > M:
            out.append(revert_event(base, "E 空方回軌K", i, B, h, l, v, H, dn_sh, "short"))

        # ---- A / B 隧道外 ----
        if side:
            back = None
            for kk in range(1, H + 1):
                Lk, Mk, Uk = B[i + kk]
                if (side == "up" and c[i + kk] < Uk) or (side == "dn" and c[i + kk] > Lk):
                    back = kk
                    break
            if side == "up":
                further = (max(h[i + 1:i + H + 1]) - c[i]) / c[i] * 100
                shadow = up_sh >= 0.5
            else:
                further = (c[i] - min(l[i + 1:i + H + 1])) / c[i] * 100
                shadow = dn_sh >= 0.5
            out.append({**base, "family": "A 上軌外" if side == "up" else "B 下軌外",
                        "nth": "第1根" if run_n == 1 else ("第2~3根" if run_n <= 3 else "第4根以上"),
                        "shadow": "長影線" if shadow else "無長影",
                        "maxvol": "軌外最大量" if is_max and r and r >= 1.0 else "-",
                        "back_half": back is not None and back <= H // 2,
                        "back": back is not None, "further": further >= EXT})
        elif 40 <= pos <= 60:
            first = "未碰軌"
            for kk in range(1, H + 1):
                Lk, Mk, Uk = B[i + kk]
                hu, hl = h[i + kk] >= Uk, l[i + kk] <= Lk
                if hu and hl:
                    first = "同根兩碰"; break
                if hu:
                    first = "先碰上軌"; break
                if hl:
                    first = "先碰下軌"; break
            tl = any(l[j] <= B[j][0] for j in range(i - 15, i))
            tu = any(h[j] >= B[j][2] for j in range(i - 15, i))
            ctx = ("從下軌上來" if tl and not tu else "從上軌下來" if tu and not tl else
                   "兩軌都碰過" if tl else "區間內遊走")
            M5 = B[i - 5][1]
            slope = "上彎" if M > M5 * 1.002 else ("下彎" if M < M5 * 0.998 else "走平")
            out.append({**base, "family": "C 隧道中間", "ctx": ctx, "slope": slope, "first": first})
    return out


def revert_event(base, fam, i, B, h, l, v, H, shadow, side):
    t = i - 1
    while t > i - 15 and B[t - 1] and ((side == "long" and l[t - 1] <= B[t - 1][0]) or
                                       (side == "short" and h[t - 1] >= B[t - 1][2])):
        t -= 1
    basev = [x for x in v[max(0, t - 20):t] if x]
    av = sum(basev) / len(basev) if basev else None
    bk = v[i] / av if av else None
    tr = max(v[t:i]) / av if av and v[t:i] else 0
    if bk is None:
        vt = "無量資料"
    elif bk >= 4 and shadow >= 0.5:
        vt = "4 爆量長影"
    elif bk >= 1.5:
        vt = "1 放量回軌"
    elif bk < 1 and tr >= 1.5:
        vt = "2 洗盤完成"
    elif bk < 1:
        vt = "3 量縮回軌"
    else:
        vt = "5 量平回軌"
    res = "未決"
    for kk in range(1, H + 1):
        Lk, Mk, Uk = B[i + kk]
        if side == "long":
            if h[i + kk] >= Mk:
                res = "成功"; break
            if l[i + kk] <= Lk:
                res = "失敗"; break
        else:
            if l[i + kk] <= Mk:
                res = "成功"; break
            if h[i + kk] >= Uk:
                res = "失敗"; break
    return {**base, "family": fam, "vtype": vt, "first": res,
            "touch_b": "觸軌期放量" if tr >= 1.5 else "觸軌期無放量"}


# ---------------- F 多週期嵌套 ----------------
NEST_H = 48            # 往後看幾根 15m（12 小時）
NEST_EXT = 0.5         # 「先續創高」門檻 %
TF_MS = {"15m": 900000, "1H": 3600000, "4H": 14400000}


def band_series(k):
    c = [x[4] for x in k]
    return [bands(c, i) if i >= WARMUP else None for i in range(len(k))]


def closed_lookup(k_hi, B_hi, tf_hi):
    """回函式 f(t)：15m K 開盤時間 t 當下，最近一根「已收盤」高週期K的隧道值。"""
    ends = [x[0] + TF_MS[tf_hi] for x in k_hi]       # 高週期每根的收盤時間

    def f(t):
        j = bisect.bisect_right(ends, t) - 1
        return B_hi[j] if j >= 0 else None
    return f


def nest_scan(coin, k15, k1, k4, oi15):
    n = len(k15)
    if n < WARMUP + NEST_H + 30:
        return []
    ts = [x[0] for x in k15]
    h = [x[2] for x in k15]
    l = [x[3] for x in k15]
    c = [x[4] for x in k15]
    v = [x[5] for x in k15]
    B15 = band_series(k15)
    f1 = closed_lookup(k1, band_series(k1), "1H")
    f4 = closed_lookup(k4, band_series(k4), "4H")
    b1 = [f1(t) for t in ts]
    b4 = [f4(t) for t in ts]

    def vr(i):
        prev = [x for x in v[max(0, i - 20):i] if x]
        return v[i] / (sum(prev) / len(prev)) if len(prev) >= 10 and v[i] else None

    # 15m ATR(14)：用來把「再延伸多少」換成波動單位（BTC 與山寨幣才能比較）
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, n)]
    atr = [None] * n
    for i in range(14, n):
        atr[i] = sum(tr[i - 13:i + 1]) / 14

    out = []
    for i in range(WARMUP + 20, n - NEST_H):
        if not (B15[i] and b1[i] and b4[i] and b1[i - 1]):
            continue
        L1, M1, U1 = b1[i]
        L4, M4, U4 = b4[i]
        for side in ("up", "dn"):
            if side == "up":
                hit = h[i] >= U1 and h[i - 1] < b1[i - 1][2]      # 剛碰 H1 上軌
            else:
                hit = l[i] <= L1 and l[i - 1] > b1[i - 1][0]      # 剛碰 H1 下軌
            if not hit:
                continue
            ref = h[i] if side == "up" else l[i]
            first = {"15m": None, "H1中": None, "H1對軌": None, "H4": None, "ext": None}
            for kk in range(1, NEST_H + 1):
                j = i + kk
                if not (B15[j] and b1[j] and b4[j]):
                    continue
                L15j, _, U15j = B15[j]
                L1j, M1j, U1j = b1[j]
                L4j, _, U4j = b4[j]
                if side == "up":
                    checks = [("15m", l[j] <= L15j), ("H1中", l[j] <= M1j), ("H1對軌", l[j] <= L1j),
                              ("H4", l[j] <= L4j), ("ext", h[j] >= ref * (1 + NEST_EXT / 100))]
                else:
                    checks = [("15m", h[j] >= U15j), ("H1中", h[j] >= M1j), ("H1對軌", h[j] >= U1j),
                              ("H4", h[j] >= U4j), ("ext", l[j] <= ref * (1 - NEST_EXT / 100))]
                for key, ok in checks:
                    if ok and first[key] is None:
                        first[key] = kk
            # 最深回到哪一層（12 小時內）
            if first["H4"]:
                tier = "4 到H4對側軌(大波動)"
            elif first["H1對軌"]:
                tier = "3 到H1對側軌"
            elif first["H1中"]:
                tier = "2 到H1中線"
            elif first["15m"]:
                tier = "1 只到15m對側軌"
            else:
                tier = "0 沒回到15m對側軌"
            ext_first = first["ext"] is not None and (first["15m"] is None or first["ext"] < first["15m"])
            # 碰軌後、到第一站（15m 對側軌）之前，往反方向最多再走多少（沒到第一站就看整個 12 小時）
            end = i + (first["15m"] or NEST_H)
            if side == "up":
                mae = max(max(h[i + 1:end + 1]) - ref, 0.0)
            else:
                mae = max(ref - min(l[i + 1:end + 1]), 0.0)
            mae_atr = mae / atr[i] if atr[i] else None
            mae_pct = mae / ref * 100
            r = vr(i)
            a, b = oi15.get(ts[i]), oi15.get(ts[i - 1])
            oc = (a / b - 1) * 100 if a and b else None
            h4pos = (c[i] - L4) / (U4 - L4) * 100 if U4 > L4 else None
            if side == "up":
                h4ctx = ("H4上軌外" if h4pos > 100 else "H4中線上" if h4pos >= 50 else "H4中線下")
            else:
                h4ctx = ("H4下軌外" if h4pos < 0 else "H4中線下" if h4pos <= 50 else "H4中線上")
            out.append({"coin": coin, "ts": ts[i], "side": side, "tier": tier,
                        "t15": first["15m"], "tmid": first["H1中"], "topp": first["H1對軌"],
                        "r15_4h": first["15m"] is not None and first["15m"] <= 16,
                        "r15": first["15m"] is not None, "rmid": first["H1中"] is not None,
                        "ropp": first["H1對軌"] is not None, "rh4": first["H4"] is not None,
                        "ext_first": ext_first, "ratio": round(r, 2) if r else None, "ratio_b": ratio_b(r),
                        "oi_chg": round(oc, 2) if oc is not None else None, "oi_b": oi_b(oc, 0.3),
                        "h4ctx": h4ctx, "mae_atr": round(mae_atr, 3) if mae_atr is not None else None,
                        "mae_pct": round(mae_pct, 3),
                        "grp": "BTC" if coin == "BTC" else "ETH" if coin == "ETH" else "山寨"})
    return out


def _q(xs, q):
    xs = sorted(x for x in xs if x is not None)
    return xs[min(len(xs) - 1, int(len(xs) * q))] if xs else None


def report_mae(ev):
    print("\n==================== F3 碰軌後先往反方向多走多少（到第一站之前）====================")
    print("欄位：N｜中位數(ATR)｜75分位(ATR)｜90分位(ATR)｜中位數%｜≥0.5ATR｜≥1ATR｜≥0.5%｜到15m對側軌｜到H1中線｜到H1對側軌｜到H4對側軌")

    def line(name, rows):
        n = len(rows)
        if n < 30:
            return
        a = [r["mae_atr"] for r in rows]
        print(f"  {name:<22}{n:6d}  {_q(a, .5):5.2f}  {_q(a, .75):5.2f}  {_q(a, .9):5.2f}"
              f"  {_q([r['mae_pct'] for r in rows], .5):5.2f}%"
              f"  {pct(sum(1 for x in a if x is not None and x >= .5), n)}  {pct(sum(1 for x in a if x is not None and x >= 1), n)}"
              f"  {pct(sum(1 for r in rows if r['mae_pct'] >= .5), n)}"
              f"  {pct(sum(r['r15'] for r in rows), n)}  {pct(sum(r['rmid'] for r in rows), n)}"
              f"  {pct(sum(r['ropp'] for r in rows), n)}  {pct(sum(r['rh4'] for r in rows), n)}")

    for side, nm in (("up", "碰H1上軌"), ("dn", "碰H1下軌")):
        es = [e for e in ev if e["side"] == side]
        print(f" ◆ {nm}")
        line("全部", es)
        for g in ("BTC", "ETH", "山寨"):
            line(g, [e for e in es if e["grp"] == g])
        for ctx in sorted({e["h4ctx"] for e in es}):
            line(ctx, [e for e in es if e["h4ctx"] == ctx])
            for g in ("BTC", "ETH", "山寨"):
                line(f"  {ctx}/{g}", [e for e in es if e["h4ctx"] == ctx and e["grp"] == g])


def report_nest(ev, side):
    up = side == "up"
    title = "F1 碰 H1 上軌之後（往下回到哪一層？）" if up else "F2 碰 H1 下軌之後（往上彈到哪一層？）"
    print(f"\n==================== {title}（事件 {len(ev)}）====================")
    print("欄位：N｜4h內到15m對側軌｜12h內到15m對側軌｜到H1中線｜到H1對側軌｜到H4對側軌｜"
          f"先續{'創高' if up else '破低'}≥{NEST_EXT}%｜到15m對側軌中位數(根15m)")

    def med(xs):
        xs = sorted(x for x in xs if x is not None)
        return xs[len(xs) // 2] if xs else None

    def line(name, rows):
        n = len(rows)
        if n < 30:
            return
        m = med([r["t15"] for r in rows])
        print(f"  {name:<20}{n:6d}  {pct(sum(r['r15_4h'] for r in rows), n)}  {pct(sum(r['r15'] for r in rows), n)}"
              f"  {pct(sum(r['rmid'] for r in rows), n)}  {pct(sum(r['ropp'] for r in rows), n)}"
              f"  {pct(sum(r['rh4'] for r in rows), n)}  {pct(sum(r['ext_first'] for r in rows), n)}"
              f"   {m if m is not None else '-'}")

    line("全部", ev)
    group_lines(ev, [("h4ctx", "H4 大方向"), ("ratio_b", "碰軌那根15m量比"), ("oi_b", "碰軌那根持倉變化")], line)
    print(" ◆ 12 小時內最深到哪一層（波動層級分布）")
    g = defaultdict(int)
    for r in ev:
        g[r["tier"]] += 1
    for k in sorted(g):
        print(f"  {k:<20}{g[k]:6d}  {pct(g[k], len(ev))}")


# ---------------- 報表 ----------------
def pct(x, n):
    return f"{x / n * 100:5.1f}%" if n else "   - "


def group_lines(ev, keys, line):
    for key, label in keys:
        print(f" ◆ {label}")
        g = defaultdict(list)
        for r in ev:
            g[r.get(key)].append(r)
        for k in sorted(g, key=str):
            line(str(k), g[k])


def report_outside(ev, title, up, ext):
    print(f"\n==================== {title}（事件 {len(ev)}）====================")
    print(f"欄位：N｜半程內回隧道｜全程內回隧道｜再{'漲' if up else '跌'}≥{ext}%｜平均半程報酬｜平均全程報酬")

    def line(name, rows):
        n = len(rows)
        if n < 20:
            return
        print(f"  {name:<18}{n:6d}  {pct(sum(r['back_half'] for r in rows), n)}  {pct(sum(r['back'] for r in rows), n)}"
              f"  {pct(sum(r['further'] for r in rows), n)}  {sum(r['retH2'] for r in rows) / n:+6.2f}%"
              f"  {sum(r['retH'] for r in rows) / n:+6.2f}%")

    line("全部", ev)
    group_lines(ev, [("nth", "軌外第幾根"), ("ratio_b", "量比"), ("oi_b", "持倉變化"),
                     ("shadow", "影線"), ("maxvol", "軌外最大量(量比≥1)")], line)
    print(" ◆ 量比 × 持倉")
    g = defaultdict(list)
    for r in ev:
        if r["oi_b"] != "OI無資料":
            g[f"{r['ratio_b'][2:4]}/{r['oi_b'][:3]}"].append(r)
    for k in sorted(g):
        line(k, g[k])


def report_mid(ev):
    print(f"\n==================== C 隧道中間（事件 {len(ev)}）====================")
    print("欄位：N｜先碰上軌｜先碰下軌｜未碰軌｜上軌/下軌比｜平均半程報酬｜平均全程報酬")

    def line(name, rows):
        n = len(rows)
        if n < 30:
            return
        u = sum(r["first"] == "先碰上軌" for r in rows)
        d = sum(r["first"] == "先碰下軌" for r in rows)
        z = sum(r["first"] == "未碰軌" for r in rows)
        rr = f"{u / d:4.2f}" if d else "  ∞ "
        print(f"  {name:<18}{n:6d}  {pct(u, n)}  {pct(d, n)}  {pct(z, n)}  {rr}"
              f"  {sum(r['retH2'] for r in rows) / n:+6.2f}%  {sum(r['retH'] for r in rows) / n:+6.2f}%")

    line("全部", ev)
    g = defaultdict(list)
    for r in ev:
        g[f"{r['ctx']}/{r['slope']}"].append(r)
    print(" ◆ 從哪裡來 × 中線方向")
    for k in sorted(g):
        line(k, g[k])
    group_lines(ev, [("ratio_b", "量比"), ("oi_b", "持倉變化")], line)


def report_revert(ev, title):
    print(f"\n==================== {title}（事件 {len(ev)}）====================")
    print("欄位：N｜先碰中線(成功)｜先回原軌(失敗)｜未決｜成功/失敗比｜平均半程報酬｜平均全程報酬"
          "（空方報酬為做空方向，正=對空單有利）")
    short = title.startswith("E")

    def line(name, rows):
        n = len(rows)
        if n < 15:
            return
        s_ = sum(r["first"] == "成功" for r in rows)
        f_ = sum(r["first"] == "失敗" for r in rows)
        sgn = -1 if short else 1
        rr = f"{s_ / f_:4.2f}" if f_ else "  ∞ "
        print(f"  {name:<18}{n:6d}  {pct(s_, n)}  {pct(f_, n)}  {pct(n - s_ - f_, n)}  {rr}"
              f"  {sgn * sum(r['retH2'] for r in rows) / n:+6.2f}%  {sgn * sum(r['retH'] for r in rows) / n:+6.2f}%")

    line("全部", ev)
    group_lines(ev, [("vtype", "回軌K量能型態"), ("oi_b", "回軌K持倉變化"), ("touch_b", "觸軌期間")], line)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="15m,1H,4H,1D,nest")
    ap.add_argument("--coins", default=",".join(DEFAULT_COINS))
    args = ap.parse_args()
    coins = [x.strip().upper() for x in args.coins.split(",") if x.strip()]

    tfs = [x.strip() for x in args.tf.split(",") if x.strip()]
    for tf in [t for t in tfs if t != "nest"]:
        events = []
        print(f"\n################ 週期 {tf}（往後看 {TF[tf]['H']} 根）################")
        for coin in coins:
            k = candles(coin, tf, TF[tf]["bars"])
            oi = oi_history(coin, tf)
            cov = sum(1 for x in k if x[0] in oi)
            print(f"[data] {coin} {tf}：K線 {len(k)} 根、OI 對得上 {cov} 根")
            events += scan(coin, tf, k, oi)
        print(f"[info] {tf} 共 {len(events)} 個事件")
        ext = TF[tf]["ext"]
        report_outside([e for e in events if e["family"] == "A 上軌外"], f"A 上軌外 {tf}（續漲 or 回隧道？）", True, ext)
        report_outside([e for e in events if e["family"] == "B 下軌外"], f"B 下軌外 {tf}（續跌 or 回漲？）", False, ext)
        report_mid([e for e in events if e["family"] == "C 隧道中間"])
        report_revert([e for e in events if e["family"] == "D 多方回軌K"], f"D 多方回軌K {tf}")
        report_revert([e for e in events if e["family"] == "E 空方回軌K"], f"E 空方回軌K {tf}")
        fn = f"backtest_crypto_{tf}.csv"
        keys = sorted({kk for e in events for kk in e})
        with open(fn, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=keys)
            w.writeheader()
            w.writerows(events)
        print(f"[info] 明細已存 {fn}")

    if "nest" in tfs:
        print("\n################ F 多週期嵌套（15m × H1 × H4）################")
        ev = []
        for coin in coins:
            k15 = candles(coin, "15m", TF["15m"]["bars"])
            if not k15:
                continue
            span = k15[-1][0] - k15[0][0]
            k1 = candles(coin, "1H", int(span / 3600000) + 200)
            k4 = candles(coin, "4H", int(span / 14400000) + 200)
            oi15 = oi_history(coin, "15m")
            print(f"[data] {coin}：15m {len(k15)} 根、1H {len(k1)} 根、4H {len(k4)} 根、15m OI {len(oi15)} 筆")
            ev += nest_scan(coin, k15, k1, k4, oi15)
        report_nest([e for e in ev if e["side"] == "up"], "up")
        report_nest([e for e in ev if e["side"] == "dn"], "dn")
        report_mae(ev)
        if ev:
            keys = sorted({kk for e in ev for kk in e})
            with open("backtest_crypto_nest.csv", "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.DictWriter(fh, fieldnames=keys)
                w.writeheader()
                w.writerows(ev)
            print("[info] 明細已存 backtest_crypto_nest.csv")


if __name__ == "__main__":
    main()
