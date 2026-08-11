"""抓公開資訊觀測站（MOPS）的「法人說明會一覽表」。

拿到的是**官方場次資料**：日期、時間、地點、擇要訊息，以及公司自己上傳的
法說會簡報 PDF 與影音連結。這裡不抓、也不轉載任何新聞內文。

MOPS 這幾年改版過，端點與欄位都可能再變，所以：
  * 候選端點由上往下試
  * 表格用「關鍵字對欄位」的方式解析，不寫死欄位順序
  * 任何一個月失敗都只是略過，不中斷整體
"""

from __future__ import annotations

import re
import sys
from typing import Iterator
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from common import get, log, probe, recent_months, roc_year, session, warn

# 順序有意義：實測 mops.twse.com.tw 對 GitHub runner 會回一頁
# 「因為安全性考量，您所執行的頁面無法呈現」的擋人頁（HTTP 仍是 200），
# 只有 mopsov 這個海外站台拿得到真資料，所以 mopsov 要排第一。
HOSTS = [
    "https://mopsov.twse.com.tw",
    "https://mops.twse.com.tw",
]
PATH = "/mops/web/ajax_t100sb02_1"

# 上面那個擋人頁的特徵字串 —— 它是 200，不看內容會誤判成「這個月沒場次」
BLOCKED_MARKERS = ("因為安全性考量", "FOR SECURITY REASONS")

MARKETS = ["sii", "otc"]   # 上市、上櫃（市場別已經記在 data/stocks.js，場次不再重複存）

# 表頭關鍵字 → 內部欄位名（依序比對，先中者為準）
HEADER_MAP: list[tuple[tuple[str, ...], str]] = [
    (("公司代號", "代號"), "code"),
    (("公司名稱", "公司簡稱"), "name"),
    (("日期",), "date"),
    (("時間",), "time"),
    (("地點",), "place"),
    (("擇要", "說明內容", "重點"), "summary"),
    (("英文",), "deck_en"),          # 要排在「簡報」前面，否則「英文簡報」會先被當成中文簡報
    (("中文", "簡報"), "deck"),
    (("影音", "視訊", "連結"), "video"),
]


def _roc_to_iso(text: str) -> str:
    """民國日期（115/07/16、115年07月16日）→ 2026-07-16。認不出來就回空字串。"""
    # (?<!\d) 是必要的：少了它，"2026-07-16" 會從第二個字元開始比對成 "026-07-16"，
    # 被當成民國 26 年而算出 1937。
    m = re.search(r"(?<!\d)(\d{2,4})\s*[/年-]\s*(\d{1,2})\s*[/月-]\s*(\d{1,2})", text or "")
    if not m:
        return ""
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 1911:          # 民國年
        y += 1911
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return ""
    return f"{y:04d}-{mo:02d}-{d:02d}"


def _classify(header: str) -> str | None:
    h = header.replace(" ", "")
    for keys, field in HEADER_MAP:
        if any(k in h for k in keys):
            return field
    return None


def _cell_link(td, base: str) -> str | None:
    a = td.find("a", href=True)
    if not a:
        return None
    href = a["href"].strip()
    if not href or href in ("#", "/") or href.lower().startswith("javascript"):
        # MOPS 有些連結是 javascript 觸發表單送出，抓不到直接網址就放棄這格
        return None
    url = urljoin(base, href)
    # href="#" 這類會被 urljoin 併成「只有網域、沒有路徑」的網址，
    # 實測就出現過 deck 變成 'https://mopsov.twse.com.tw' 這種沒用的值。
    if not urlparse(url).path.strip("/"):
        return None
    return url


def _parse_tables(html: str, base: str) -> Iterator[dict]:
    """從回應 HTML 找出含「公司代號」表頭的表格，逐列吐出 dict。"""
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        # 找表頭列
        cols: dict[int, str] = {}
        header_idx = -1
        for i, tr in enumerate(rows[:5]):
            cells = tr.find_all(["th", "td"])
            texts = [c.get_text(strip=True) for c in cells]
            if not any("代號" in t for t in texts):
                continue
            for j, t in enumerate(texts):
                field = _classify(t)
                if field and field not in cols.values():
                    cols[j] = field
            header_idx = i
            break

        if header_idx < 0 or "code" not in cols.values():
            continue

        for tr in rows[header_idx + 1:]:
            tds = tr.find_all("td")
            if len(tds) < 3:
                continue
            rec: dict = {}
            for j, td in enumerate(tds):
                field = cols.get(j)
                if not field:
                    continue
                if field in ("deck", "deck_en", "video"):
                    rec[field] = _cell_link(td, base)
                else:
                    rec[field] = td.get_text(strip=True)
            code = (rec.get("code") or "").strip()
            if re.fullmatch(r"\d{4,6}[A-Z]?", code):
                yield rec


def _fetch_month(s, market: str, year: int, month: int) -> list[dict]:
    payload = {
        "encodeURIComponent": "1",
        "step": "1",
        "firstin": "1",
        "off": "1",
        "TYPEK": market,
        "year": str(roc_year(year)),
        "month": f"{month:02d}",
    }
    for host in HOSTS:
        url = host + PATH
        r = get(s, url, method="POST", data=payload, retries=2)
        if r is None:
            continue
        r.encoding = r.apparent_encoding or "utf-8"
        if any(mark in r.text for mark in BLOCKED_MARKERS):
            warn(f"{host} 回了擋人頁（HTTP 200 但內容是安全性拒絕），換下一個 host")
            continue
        rows = list(_parse_tables(r.text, host))
        if rows:
            return rows
        # 該月沒場次是正常的；但如果連表格都找不到，可能是端點又改了
        if "代號" not in r.text:
            warn(f"{url}（{market} {year}/{month:02d}）回應裡找不到表格，端點可能已變更")
    return []


def fetch_mops(s, months: int = 24) -> dict[str, list[dict]]:
    """回傳 {代號: [場次, ...]}，場次已依日期新到舊排序。"""
    by_code: dict[str, dict[str, dict]] = {}
    total = 0

    for year, month in recent_months(months):
        for market in MARKETS:
            rows = _fetch_month(s, market, year, month)
            for rec in rows:
                code = rec["code"]
                iso = _roc_to_iso(rec.get("date", ""))
                if not iso:
                    continue
                place = (rec.get("place") or "").strip()
                summary = (rec.get("summary") or "").strip()
                call = {
                    "id": iso,
                    "label": f"{iso.replace('-', '/')} 法人說明會",
                    "date": iso,
                    "kind": "法人說明會",
                    "place": place,
                    "summary": summary,
                    "deck": rec.get("deck") or rec.get("deck_en"),
                    "video": rec.get("video"),
                    "notes": [],
                    "news": [],
                }
                # 同一天可能重覆出現在不同月份查詢裡，用 id 去重
                by_code.setdefault(code, {})[iso] = call
                total += 1
        log(f"  … {year}/{month:02d} 累計 {total} 筆場次")

    out: dict[str, list[dict]] = {}
    for code, calls in by_code.items():
        out[code] = sorted(calls.values(), key=lambda c: c["date"], reverse=True)

    log(f"📦 MOPS 法說會：{len(out)} 家公司、{sum(len(v) for v in out.values())} 場")
    return out


if __name__ == "__main__":
    s = session()
    if "--probe" in sys.argv:
        probe(s, [h + PATH for h in HOSTS], label="MOPS 法人說明會端點（GET 探測）")
        log("\n=== PROBE: MOPS POST 實際查詢 ===")
        for year, month in recent_months(2):
            rows = _fetch_month(s, "sii", year, month)
            log(f"  {year}/{month:02d} sii → {len(rows)} 列；首列 = {rows[0] if rows else None}")
    else:
        result = fetch_mops(s, months=3)
        log(f"取得 {len(result)} 家；2330 = {result.get('2330')}")
