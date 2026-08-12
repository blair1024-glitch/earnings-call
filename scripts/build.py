"""把各路來源合成網站要用的資料檔。

  data/stocks.js     ← TWSE / TPEx 公司清單
  data/earnings.js   ← MOPS 法說會場次 ＋ 新聞 RSS 相關報導 ＋ data/notes.json 人工重點
  data/financials.js ← 綜合損益表算出的三率與升降判斷
  data/summaries.js  ← Claude 依報導標題整理的「未來方向」（快取在 summaries.json）

設計原則：**單一來源失敗，就不要覆蓋那個檔案**，寧可讓網站繼續用上一版資料，
也不要寫出一個空檔把好資料洗掉。
"""

from __future__ import annotations

import sys

from common import log, now_taipei, read_json, session, warn, write_js, write_json
from fetch_financials import fetch_financials
from fetch_mops import fetch_mops
from fetch_news import attach_to_calls, fetch_news
from fetch_stocks import fetch_stocks
from summarize import summarize, to_browser

DEFAULT_CONFIG = {
    "featured": ["2330", "2454", "2317", "2382", "3231", "6669",
                 "2308", "3711", "2303", "3034", "2412", "2881"],
    "months": 24,
    "fetchNews": True,
    "fetchFinancials": True,
    "fetchSummaries": True,
    "maxSummaryCalls": 200,
    "searchUrl": "https://money.udn.com/search/result/1001/{q}",
    "mopsUrl": "https://mops.twse.com.tw/mops/web/t100sb02_1",
}

FINANCIALS_HEADER = """/**
 * 財報體質（三率）  —  window.FINANCIALS
 * ⚠️ 這個檔案由 scripts/build.py 自動產生，請不要手動編輯。
 *
 * 三率 = 毛利率、營業利益率、稅後淨利率，數字單位是 %。
 * basis 是比較基期：YoY = 去年同季（法人的標準比法），QoQ = 上一季。
 * applicable=false 代表這個產業的損益表沒有營業毛利（金融／保險／證券），
 * 公式不適用，不會硬算一個數字出來。
 *
 * 這是描述過去財報的落後指標，不是預測，也不是任何投資建議。
 */"""

SUMMARIES_HEADER = """/**
 * 法說會「未來方向」摘要  —  window.SUMMARIES
 * ⚠️ 這個檔案由 scripts/build.py 自動產生，請不要手動編輯。
 *
 * 這些條目是 Claude 依據該場次的**新聞標題與 MOPS 擇要訊息**整理出來的，
 * 不是公司原文，也不是任何投資建議。每一條都帶 src，指回它引用的那一則報導。
 *
 * 產生後有經過程式驗證：條目裡出現的數字，一定要在輸入原文中找得到，
 * 否則整條丟掉（見 scripts/summarize.py 的 verify_outlook）。
 */"""

STOCKS_HEADER = """/**
 * 股票代號對照表  —  window.STOCKS
 * ⚠️ 這個檔案由 scripts/build.py 自動產生，請不要手動編輯（會被下次執行覆蓋）。
 * 格式："代號": ["簡稱", "市場"]，市場 sii = 上市、otc = 上櫃。
 */"""

EARNINGS_HEADER = """/**
 * 法說會資料  —  window.EARNINGS
 * ⚠️ 這個檔案由 scripts/build.py 自動產生，請不要手動編輯（會被下次執行覆蓋）。
 *
 * 想自己補充某一場的重點，請改 data/notes.json，格式：
 *   { "2330": { "2026-07-16": ["重點一", "重點二"] } }
 * build 時會自動併進對應場次的 notes 欄位，不會被覆蓋掉。
 *
 * 精選清單與抓取範圍請改 data/config.json。
 */"""

# 場次裡值為空就不輸出，讓檔案小一點
OPTIONAL_KEYS = ("place", "summary", "deck", "video", "market", "notes", "news")


def _slim(call: dict) -> dict:
    out = {}
    for k, v in call.items():
        if k in OPTIONAL_KEYS and not v:
            continue
        out[k] = v
    return out


def main() -> int:
    cfg = {**DEFAULT_CONFIG, **read_json("config.json", {})}
    notes_map = read_json("notes.json", {})
    s = session()

    stamp = now_taipei().strftime("%Y-%m-%d %H:%M") + " +08:00"
    failures: list[str] = []

    # ---------- 1. 股票清單 ----------
    log("\n── 1/5 股票清單 ─────────────────────────────")
    items = fetch_stocks(s)
    if items:
        write_js("stocks.js", "STOCKS",
                 {"updated": stamp, "fixture": False, "items": items},
                 STOCKS_HEADER)
    else:
        failures.append("股票清單")
        warn("股票清單抓取失敗 → 保留現有的 data/stocks.js 不覆蓋")

    # ---------- 2. 法說會場次 ----------
    log("\n── 2/5 MOPS 法說會場次 ──────────────────────")
    calls = fetch_mops(s, months=int(cfg["months"]))
    if not calls:
        failures.append("法說會場次")
        warn("MOPS 抓取失敗 → 保留現有的 data/earnings.js 不覆蓋")
        _report(failures)
        return 1

    # ---------- 3. 相關報導 ----------
    log("\n── 3/5 相關報導 RSS ─────────────────────────")
    recent: dict[str, list[dict]] = {}
    if cfg.get("fetchNews"):
        name_of = {c: (items.get(c) or ["", ""])[0] for c in cfg["featured"]}
        targets = {c: n for c, n in name_of.items() if n}
        if not targets:
            warn("精選清單對不到公司名稱（股票清單可能失敗），跳過新聞抓取")
        else:
            news = fetch_news(s, targets)
            if news:
                recent = attach_to_calls(calls, news)
            else:
                failures.append("相關報導")
                warn("新聞 RSS 沒抓到任何東西，場次仍會照常輸出")
    else:
        log("  config.fetchNews = false，跳過")

    # ---------- 4. 財報體質（三率） ----------
    log("\n── 4/5 財報體質（三率） ─────────────────────")
    if cfg.get("fetchFinancials"):
        fin = fetch_financials(s)
        if fin:
            write_js("financials.js", "FINANCIALS",
                     {"updated": stamp, "items": fin}, FINANCIALS_HEADER)
        else:
            failures.append("財報三率")
            warn("損益表抓取失敗 → 保留現有的 data/financials.js 不覆蓋")
    else:
        log("  config.fetchFinancials = false，跳過")

    # ---------- 5. 未來方向摘要 ----------
    # 放在新聞之後，因為它的原料就是掛好的 call["news"]。
    log("\n── 5/5 未來方向摘要 ─────────────────────────")
    if cfg.get("fetchSummaries"):
        cache = read_json("summaries.json", {})
        if not isinstance(cache, dict):
            warn("summaries.json 格式不對，這次當成空的重新累積")
            cache = {}
        names = {c: (v or ["", ""])[0] for c, v in items.items()} if items else {}
        cache = summarize(calls, names, cache,
                          max_calls=int(cfg.get("maxSummaryCalls") or 200))
        # 一律回寫。內容沒變的話 git 也不會產生 diff，不用自己判斷。
        write_json("summaries.json", {
            "_說明": "由 scripts/summarize.py 自動維護的摘要快取，請勿手動編輯。"
                     "key 是「代號|日期」，fp 是輸入指紋 —— 指紋沒變就不會重打 API。"
                     "outlook 為空代表那場材料不足或沒通過數字驗證。",
            **{k: v for k, v in cache.items() if k != "_說明"},
        })
        write_js("summaries.js", "SUMMARIES",
                 {"updated": stamp, "items": to_browser(calls, cache)},
                 SUMMARIES_HEADER)
    else:
        log("  config.fetchSummaries = false，跳過")

    # ---------- 6. 併入人工重點 ----------
    merged = 0
    for code, by_date in (notes_map or {}).items():
        for call in calls.get(code, []):
            extra = by_date.get(call["date"])
            if extra:
                call["notes"] = list(extra)
                merged += 1
    log(f"\n📝 併入 data/notes.json 的人工重點：{merged} 場")

    # ---------- 5. 寫檔 ----------
    payload = {
        "meta": {
            "updated": stamp,
            "fixture": False,
            "months": int(cfg["months"]),
            "searchUrl": cfg["searchUrl"],
            "mopsUrl": cfg["mopsUrl"],
            "sources": [
                "公開資訊觀測站（法人說明會一覽表）",
                "TWSE / TPEx OpenAPI（公司清單）",
                "Google News RSS、經濟日報 RSS（相關報導標題與連結）",
            ],
        },
        "featured": cfg["featured"],
        "calls": {code: [_slim(c) for c in v] for code, v in sorted(calls.items())},
        "recent": {code: v for code, v in sorted(recent.items())},
    }
    write_js("earnings.js", "EARNINGS", payload, EARNINGS_HEADER)

    _report(failures)
    return 0


def _report(failures: list[str]) -> None:
    log("\n────────────────────────────────────────────")
    if failures:
        log("⚠️  下列來源這次失敗（相關檔案已保留舊版）：" + "、".join(failures))
    else:
        log("✅ 所有來源都成功")


if __name__ == "__main__":
    if "--probe" in sys.argv:
        log("probe 模式請直接跑個別腳本，例如：python scripts/fetch_mops.py --probe")
        sys.exit(2)
    sys.exit(main())
