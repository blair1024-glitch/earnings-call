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
from urllib.parse import urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

import requests

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

# ---- 官方簡報的下載連結 ----------------------------------------------------
# 簡報那一格長這樣（--probe-deck 從真實回應挖出來的）：
#
#   <a href="#" onclick='document.fm_fileDownload.fileName.value="121620260810M001.pdf";
#                        document.fm_fileDownload.submit();'>121620260810M001.pdf</a>
#
# 而頁面上的 fm_fileDownload 表單是：
#
#   action='/server-java/FileDownLoad' method='post'
#   inputs=[('step','9'), ('filePath','/home/html/nas/STR/'),
#           ('fileName',''), ('functionName','t100sb02_1')]
#
# 也就是說檔名就寫在 onclick 裡（代號＋日期＋序號），其餘參數全是固定值。
# 之前 deck 之所以 0/9740 都抓不到，是因為只看 href（那是 "#"）。
DECK_PATH = "/server-java/FileDownLoad"
DECK_PARAMS = {"step": "9", "filePath": "/home/html/nas/STR/", "functionName": "t100sb02_1"}
_DECK_FILE_RE = re.compile(r"fileName\.value\s*=\s*[\"']([^\"']+)[\"']")

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


def deck_filename(td) -> str | None:
    """從 onclick 裡挖出簡報檔名。抓不到就回 None。"""
    a = td.find("a")
    if not a:
        return None
    m = _DECK_FILE_RE.search(a.get("onclick") or "")
    if not m:
        return None
    name = m.group(1).strip()
    # 只收看起來像檔名的東西，免得端點改版後塞一堆垃圾進資料檔
    return name if re.fullmatch(r"[\w.\-]+\.(pdf|pptx?|docx?)", name, re.I) else None


def deck_url(host: str, filename: str) -> str:
    """把檔名組成可以直接點的下載網址。"""
    params = dict(DECK_PARAMS)
    params["fileName"] = filename
    return host + DECK_PATH + "?" + urlencode(params)


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

            # 簡報檔名不靠表頭關鍵字去找。實測那一欄的表頭對不到
            # 「中文」「簡報」任何一個，結果 deck 整批是空的。
            # 改成整列掃：哪一格的 onclick 有 fm_fileDownload.fileName，那格就是簡報。
            # 這個特徵夠獨特，不會誤判成別的欄位。
            if not rec.get("deck"):
                for td in tds:
                    fn = deck_filename(td)
                    if fn:
                        rec["deck"] = fn
                        break

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


def _is_file_response(r) -> bool:
    """回應到底是檔案還是一頁 HTML？"""
    if r.status_code != 200:
        return False
    ctype = (r.headers.get("content-type") or "").lower()
    disp = (r.headers.get("content-disposition") or "").lower()
    head = next(r.iter_content(1024), b"") or b""
    return ("attachment" in disp) or head[:4] == b"%PDF" or ("html" not in ctype and bool(head))


def verify_deck(s, host: str, filename: str) -> str | None:
    """確認簡報檔到底怎麼拿得到。回傳 "get" / "post"，都不行就 None。

    頁面上的 fm_fileDownload 表單是 POST，但這種下載端點通常 GET 也吃。
    GET 能用的話前端就是一個單純的 <a>，簡單得多，所以先試 GET。
    兩種都不行就整批不輸出 —— 寧可沒有這個按鈕，也不要放一堆點下去是錯誤頁的
    「官方簡報」。
    """
    params = dict(DECK_PARAMS, fileName=filename)
    url = host + DECK_PATH

    for method in ("get", "post"):
        try:
            if method == "get":
                r = s.get(url + "?" + urlencode(params), timeout=30, stream=True)
            else:
                r = s.post(url, data=params, timeout=30, stream=True)
        except requests.RequestException as e:
            warn("簡報連結 %s 驗證失敗（%s: %s）" % (method.upper(), type(e).__name__, e))
            continue
        try:
            if _is_file_response(r):
                log("📄 簡報連結驗證通過：%s（content-type=%s）"
                    % (method.upper(), r.headers.get("content-type")))
                return method
            warn("簡報連結 %s 回的不是檔案（HTTP %d, content-type=%s）"
                 % (method.upper(), r.status_code, r.headers.get("content-type")))
        finally:
            r.close()
    return None


def fetch_mops(s, months: int = 24) -> tuple[dict[str, list[dict]], dict | None]:
    """回傳 ({代號: [場次, ...]}, 簡報下載設定)。場次已依日期新到舊排序。

    場次裡的 `deck` 是**簡報檔名**（例如 121620260810M001.pdf），不是網址；
    完整網址由前端用第二個回傳值組出來。
    """
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
                # 刻意不輸出 id / label / kind：
                #   id 跟 date 完全一樣、label 可以由 date 推導、
                #   kind 幾乎清一色是「法人說明會」。
                # 三者加起來原本佔了 earnings.js 的 19%，改由前端組出來。
                call = {
                    "date": iso,
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

    # deck 存的是**檔名**，不是完整網址 —— 網址由前端依 meta 組出來，
    # 這樣 9000 多場省下大量重複的固定參數，也能同時支援 GET 與 POST 兩種取法。
    decks = [c["deck"] for v in out.values() for c in v if c.get("deck")]
    info: dict | None = None
    if decks:
        method = verify_deck(s, HOSTS[0], decks[0])
        if method:
            info = {"base": HOSTS[0] + DECK_PATH, "method": method, "params": DECK_PARAMS}
        else:
            warn(f"簡報連結兩種方法都沒通過驗證 → 這次不輸出 deck（本來有 {len(decks)} 場）")
            for v in out.values():
                for c in v:
                    c["deck"] = None
            decks = []

    log(f"📦 MOPS 法說會：{len(out)} 家公司、"
        f"{sum(len(v) for v in out.values())} 場、{len(decks)} 場有官方簡報")
    return out, info


def probe_deck(s, *, rows: int = 6) -> None:
    """把「簡報」那一格的原始 HTML 印出來，看看官方 PDF 到底構不構得出直連。

    背景：實測 9740 場的 deck 全是 None —— MOPS 的簡報連結是 `href="#"` 加 JS 送出
    表單，`_cell_link()` 正確地把它擋掉了。但檔案本身通常還是存在於某個可預測的
    路徑上，而答案就在我們已經抓下來的 HTML 裡（`onclick` 的內容、表單的 hidden
    欄位），不需要用猜的。

    這個模式只印東西、不改任何解析邏輯，也不寫檔。
    """
    log("\n=== PROBE: 法說會簡報連結的實際結構 ===")
    for year, month in recent_months(2):
        for host in HOSTS:
            payload = {
                "encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1",
                "TYPEK": "sii", "year": str(roc_year(year)), "month": f"{month:02d}",
            }
            r = get(s, host + PATH, method="POST", data=payload, retries=1)
            if r is None:
                continue
            r.encoding = r.apparent_encoding or "utf-8"
            if any(mark in r.text for mark in BLOCKED_MARKERS):
                warn(f"{host} 回擋人頁，換下一個")
                continue

            soup = BeautifulSoup(r.text, "html.parser")

            # 頁面上的表單有哪些欄位 —— JS 送出時就是改這些值
            for i, form in enumerate(soup.find_all("form")):
                names = [(inp.get("name"), inp.get("value")) for inp in form.find_all("input")]
                log(f"  form[{i}] action={form.get('action')!r} method={form.get('method')!r}")
                log(f"           inputs={names[:20]}")

            shown = 0
            for table in soup.find_all("table"):
                trs = table.find_all("tr")
                if len(trs) < 2:
                    continue
                # 表頭與它被分類成什麼 —— 簡報那欄第一版之所以整批空掉，
                # 就是因為它的表頭對不到任何關鍵字，而當時 log 看不出來。
                heads = [c.get_text(strip=True) for c in trs[0].find_all(["th", "td"])]
                if any("代號" in h for h in heads):
                    log("  表頭 = %s" % heads)
                    log("  分類 = %s" % [(h, _classify(h)) for h in heads])
                for tr in trs[1:]:
                    tds = tr.find_all("td")
                    if len(tds) < 3:
                        continue
                    for td in tds:
                        a = td.find("a")
                        if not a:
                            continue
                        # 只看「不是普通超連結」的那種格子，普通的已經抓得到了
                        if a.get("href") not in (None, "", "#") and not str(
                                a.get("href")).lower().startswith("javascript"):
                            continue
                        log(f"  ↳ 格子原始 HTML：{str(td)[:400]}")
                        log(f"     a.attrs = {dict(a.attrs)}")
                        shown += 1
                        break
                    if shown >= rows:
                        break
                if shown >= rows:
                    break

            if shown == 0:
                log(f"  {host} {year}/{month:02d}：沒有找到 JS 型態的連結格")
            else:
                log(f"  {host} {year}/{month:02d}：印了 {shown} 格")
            return   # 拿到一個能用的 host 就夠了
    warn("probe-deck：所有 host 都拿不到資料")


if __name__ == "__main__":
    s = session()
    if "--probe-deck" in sys.argv:
        probe_deck(s)
    elif "--probe" in sys.argv:
        probe(s, [h + PATH for h in HOSTS], label="MOPS 法人說明會端點（GET 探測）")
        log("\n=== PROBE: MOPS POST 實際查詢 ===")
        for year, month in recent_months(2):
            rows = _fetch_month(s, "sii", year, month)
            log(f"  {year}/{month:02d} sii → {len(rows)} 列；首列 = {rows[0] if rows else None}")
    else:
        result, deck_info = fetch_mops(s, months=3)
        log(f"取得 {len(result)} 家；簡報設定 = {deck_info}；2330 = {result.get('2330')}")
