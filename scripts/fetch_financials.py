"""綜合損益表 → 三率（毛利率／營業利益率／稅後淨利率）與「三率三升」判斷。

資料來源是 TWSE / TPEx 的綜合損益表 OpenAPI。這些端點通常**只吐最新一季**，
但回應裡帶了「年度」與「季別」，所以每次執行都把該季數字存進
data/financial_history.json 累積起來，之後就能拿同一季跟去年同季比。

判斷規則
--------
    毛利率   = 營業毛利 ÷ 營業收入
    營業利益率 = 營業利益 ÷ 營業收入
    稅後淨利率 = 母公司業主淨利 ÷ 營業收入      （沒有這欄就退回本期淨利）

    比較基期優先用「去年同季」(YoY) —— 這是法人講三率三升時的標準比法，
    可以避開淡旺季干擾。歷史不足時退回「上一季」(QoQ)，並且**明確標示是 QoQ**。

刻意不做的事
------------
  * 金融、保險、證券業的損益表沒有「營業毛利」這一欄，公式套下去只會算出垃圾。
    偵測到缺這一欄就標成「不適用」，不會硬算一個數字出來。
  * 不輸出任何「買賣建議」。三率是描述過去的財報品質，不是預測，
    而且一次性業外收益會讓淨利率虛升，所以三個數字一律攤開顯示。
"""

from __future__ import annotations

import json
import os
import sys

from common import DATA_DIR, get, log, probe, read_json, session, warn

# 綜合損益表候選端點（不同產業別是不同檔）
IS_CANDIDATES = [
    # 上市
    "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci",     # 一般業
    "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_basi",   # 金融業
    "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_bd",     # 票券業
    "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ins",    # 保險業
    "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_mim",    # 證券業
    # 上櫃
    "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_ci",
    "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_basi",
]

HISTORY_FILE = "financial_history.json"

# 欄位名候選（MOPS 的全形括號與半形括號都出現過，一律列進來）
F_CODE = ("公司代號", "SecuritiesCompanyCode")
F_YEAR = ("年度", "Year")
F_SEASON = ("季別", "Season", "季度")
F_REVENUE = ("營業收入", "收入", "營業收入合計")
F_GROSS = ("營業毛利(毛損)", "營業毛利（毛損）", "營業毛利(毛損)淨額", "營業毛利")
F_OP = ("營業利益(損失)", "營業利益（損失）", "營業利益")
F_NET_PARENT = ("母公司業主(淨利/損)", "母公司業主（淨利／損）", "歸屬於母公司業主(淨利/損)")
F_NET = ("本期淨利(淨損)", "本期淨利（淨損）", "本期淨利")


def _pick(row: dict, keys: tuple[str, ...]) -> str:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return str(row[k]).strip()
    return ""


def _num(text: str) -> float | None:
    """把 "1,234,567" 這種字串轉成數字；轉不動回 None（不要當成 0）。"""
    if not text:
        return None
    cleaned = text.replace(",", "").replace(" ", "")
    neg = cleaned.startswith("(") and cleaned.endswith(")")   # 括號代表負數
    if neg:
        cleaned = cleaned[1:-1]
    try:
        val = float(cleaned)
    except ValueError:
        return None
    return -val if neg else val


def _period(year: str, season: str) -> str:
    """(115, 2) 或 (2026, 2) → "2026Q2"。認不出來回空字串。"""
    y = _num(year)
    s = _num(season)
    if y is None or s is None:
        return ""
    y = int(y)
    s = int(s)
    if y < 1911:                 # 民國年
        y += 1911
    if not 1 <= s <= 4:
        return ""
    return f"{y}Q{s}"


def fetch_snapshots(s) -> dict[str, dict]:
    """打所有候選端點，回傳 {代號: {period, revenue, gross, op, net}}。"""
    out: dict[str, dict] = {}
    for url in IS_CANDIDATES:
        r = get(s, url, retries=2)
        if r is None:
            continue
        try:
            rows = r.json()
        except ValueError:
            warn(f"{url} 回應不是 JSON")
            continue
        if not isinstance(rows, list) or not rows:
            continue

        got = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = _pick(row, F_CODE)
            period = _period(_pick(row, F_YEAR), _pick(row, F_SEASON))
            revenue = _num(_pick(row, F_REVENUE))
            if not code or not period or not revenue:
                continue
            out[code] = {
                "period": period,
                "revenue": revenue,
                # 金融／保險業沒有營業毛利 → 這裡會是 None，後面判成「不適用」
                "gross": _num(_pick(row, F_GROSS)),
                "op": _num(_pick(row, F_OP)),
                "net": _num(_pick(row, F_NET_PARENT)) or _num(_pick(row, F_NET)),
            }
            got += 1
        log(f"  {url.rsplit('/', 1)[-1]} → {got} 筆")

    log(f"📦 損益表快照：{len(out)} 家")
    return out


def merge_history(snapshots: dict[str, dict]) -> dict[str, dict]:
    """把這次抓到的季度併進歷史檔並存回去。回傳合併後的完整歷史。

    這是整套三率判斷的關鍵：OpenAPI 只給最新一季，靠這個檔案累積出可比的基期。
    """
    history: dict[str, dict] = read_json(HISTORY_FILE, {})
    if not isinstance(history, dict):
        history = {}
    history.pop("_說明", None)

    added = 0
    for code, snap in snapshots.items():
        period = snap["period"]
        bucket = history.setdefault(code, {})
        if period not in bucket:
            added += 1
        bucket[period] = {
            "revenue": snap["revenue"],
            "gross": snap["gross"],
            "op": snap["op"],
            "net": snap["net"],
        }

    path = os.path.join(DATA_DIR, HISTORY_FILE)
    payload = {
        "_說明": "由 scripts/fetch_financials.py 自動累積，請勿手動編輯。"
                 "OpenAPI 只提供最新一季，這個檔案是用來累積歷史基期的。",
        **{k: history[k] for k in sorted(history)},
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1, sort_keys=False)
    log(f"🗄️  歷史季度：新增 {added} 筆，累計 {sum(len(v) for v in history.values())} 筆"
        f"（{len(history)} 家）")
    return history


def _rates(fig: dict) -> dict | None:
    """算三率（%）。缺營業毛利就回 None，代表這個產業不適用。"""
    rev = fig.get("revenue")
    if not rev:
        return None
    gross, op, net = fig.get("gross"), fig.get("op"), fig.get("net")
    if gross is None or op is None or net is None:
        return None
    return {
        "gm": round(gross / rev * 100, 2),
        "opm": round(op / rev * 100, 2),
        "npm": round(net / rev * 100, 2),
    }


def _prev_periods(period: str) -> tuple[str, str]:
    """回傳 (去年同季, 上一季)。"""
    year, q = int(period[:4]), int(period[-1])
    yoy = f"{year - 1}Q{q}"
    qoq = f"{year}Q{q - 1}" if q > 1 else f"{year - 1}Q4"
    return yoy, qoq


VERDICTS = {3: "三率三升", 2: "三率二升", 1: "三率一升", 0: "三率皆降"}


def evaluate(history: dict[str, dict]) -> dict[str, dict]:
    """對每家公司算出最新一季的三率與升降判斷。"""
    out: dict[str, dict] = {}
    stats = {"ok": 0, "na": 0, "no_base": 0}

    for code, periods in history.items():
        if not periods:
            continue
        latest_p = max(periods)
        latest = _rates(periods[latest_p])
        if latest is None:
            out[code] = {"period": latest_p, "applicable": False,
                         "reason": "這個產業的損益表沒有營業毛利，三率公式不適用"}
            stats["na"] += 1
            continue

        yoy_p, qoq_p = _prev_periods(latest_p)
        base_p, basis = None, ""
        if yoy_p in periods and _rates(periods[yoy_p]):
            base_p, basis = yoy_p, "YoY"
        elif qoq_p in periods and _rates(periods[qoq_p]):
            base_p, basis = qoq_p, "QoQ"

        if base_p is None:
            out[code] = {"period": latest_p, "applicable": True, "rates": latest,
                         "basis": "", "reason": "還沒有可比較的基期（歷史正在累積中）"}
            stats["no_base"] += 1
            continue

        base = _rates(periods[base_p])
        delta = {k: round(latest[k] - base[k], 2) for k in ("gm", "opm", "npm")}
        rising = sum(1 for v in delta.values() if v > 0)
        out[code] = {
            "period": latest_p,
            "applicable": True,
            "rates": latest,
            "basePeriod": base_p,
            "baseRates": base,
            "basis": basis,
            "delta": delta,
            "rising": rising,
            "verdict": VERDICTS[rising],
        }
        stats["ok"] += 1

    log(f"📊 三率判斷：{stats['ok']} 家算得出升降、"
        f"{stats['no_base']} 家基期不足、{stats['na']} 家不適用")
    return out


def fetch_financials(s) -> dict[str, dict]:
    snapshots = fetch_snapshots(s)
    if not snapshots:
        return {}
    history = merge_history(snapshots)
    return evaluate(history)


if __name__ == "__main__":
    s = session()
    if "--probe" in sys.argv:
        probe(s, IS_CANDIDATES, label="綜合損益表（三率來源）")
    else:
        result = fetch_financials(s)
        log(f"2330 → {result.get('2330')}")
