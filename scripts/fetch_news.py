"""抓法說會相關報導的「標題／日期／連結」，以經濟日報為主。

刻意只走 RSS，不爬新聞網站的 HTML，也**只保留標題、日期與原文連結，
不抓取也不轉載任何內文**。使用者點進去看的是原始報導頁。

兩路來源：
  1. Google News RSS —— 可以針對個股下關鍵字，並用 site: 限定經濟日報
  2. 經濟日報自家分類 RSS —— 連結是直接指向 money.udn.com 的原始網址

同一篇文章兩邊都出現時，優先保留直連 money.udn.com 的那一份。
"""

from __future__ import annotations

import re
import sys
import time
from email.utils import parsedate_to_datetime
from urllib.parse import quote
from xml.etree import ElementTree as ET

from common import get, log, probe, session, warn

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"

# 經濟日報分類 RSS（候選，解析得出來的就用）
UDN_FEEDS = [
    "https://money.udn.com/rssfeed/news/1001/5591?ch=money",   # 股市
    "https://money.udn.com/rssfeed/news/1001/5590?ch=money",   # 要聞
    "https://money.udn.com/rssfeed/news/1001/5612?ch=money",   # 產業
]

UDN_RE = re.compile(r"money\.udn\.com")


def _clean_title(title: str) -> tuple[str, str]:
    """Google News 的標題會是「標題 - 來源」，把來源拆出來。"""
    title = (title or "").strip()
    m = re.match(r"^(.*)\s+-\s+([^-]{2,20})$", title)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return title, ""


def _parse_rss(xml_text: str) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        warn(f"RSS 解析失敗：{e}")
        return []

    out: list[dict] = []
    for item in root.iter("item"):
        title_raw = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title_raw or not link:
            continue

        title, src_from_title = _clean_title(title_raw)
        src_el = item.find("source")
        source = (src_el.text or "").strip() if src_el is not None and src_el.text else src_from_title

        date = ""
        pub = item.findtext("pubDate")
        if pub:
            try:
                date = parsedate_to_datetime(pub).date().isoformat()
            except (TypeError, ValueError):
                date = ""

        out.append({
            "title": title,
            "url": link,
            "date": date,
            "source": source or "新聞",
            "direct": bool(UDN_RE.search(link)),
        })
    return out


def query_name(name: str) -> str:
    """把交易所簡稱整理成適合下關鍵字的樣子。

    「國巨*」的星號、「中租-KY」的 -KY 都是交易所的註記，新聞標題不會這樣寫。
    -KY 只在後面還有字時才留（避免把「世芯-KY」砍成空字串）。
    """
    n = (name or "").strip().rstrip("*＊ ")
    n = re.sub(r"[-－]KY$", "", n).strip()
    return n or (name or "").strip()


def title_matches(name: str, code: str, title: str) -> bool:
    """標題有沒有真的提到這家公司。

    Google News 用「公司名 法說會」查回來的結果，有一大半只是**同一天也開法說會**
    的別家公司。實測廣達 2026-08-13 那一場掛到的六則裡，沒有一則跟廣達有關
    （長興、台積電＋SONY、Lumentum…），25 檔裡有 11 檔是這種情況。
    只靠「標題有沒有法說／財報字眼」擋不住，還要求標題點名這家公司。
    """
    t = title or ""
    if code and code in t:          # 標題常寫成「瑞昱(2379)」
        return True
    n = query_name(name)
    if not n:
        return False
    if n in t:
        return True
    # 交易所簡稱常常比新聞寫法長：簡稱是「日月光投控」，標題寫的是「日月光法說」
    return len(n) >= 4 and n[:3] in t


def _norm(title: str) -> str:
    return re.sub(r"[\s　,，。、!！?？「」【】\[\]()（）-]+", "", title)


def _merge(bucket: dict[str, dict], items: list[dict]) -> None:
    """依標題去重；直連 money.udn.com 的版本優先。"""
    for it in items:
        key = _norm(it["title"])
        if not key:
            continue
        old = bucket.get(key)
        if old is None or (it["direct"] and not old["direct"]):
            bucket[key] = it


def fetch_news(s, targets: dict[str, str], *, pause: float = 1.0) -> dict[str, list[dict]]:
    """targets: {代號: 公司名稱}。回傳 {代號: [報導, ...]}（新到舊）。"""
    result: dict[str, list[dict]] = {}

    # --- 先抓一次經濟日報分類 RSS，之後拿來跟每檔比對 ---
    udn_pool: list[dict] = []
    for url in UDN_FEEDS:
        r = get(s, url, retries=2)
        if r is None:
            continue
        items = _parse_rss(r.text)
        if items:
            for it in items:
                it["source"] = it["source"] or "經濟日報"
            udn_pool.extend(items)
        time.sleep(pause)
    log(f"  經濟日報分類 RSS 共 {len(udn_pool)} 則")

    for code, name in targets.items():
        bucket: dict[str, dict] = {}
        # 交易所簡稱帶的註記符號（國巨* 的星號代表財報有保留意見、-KY 是註冊地）
        # 不會出現在新聞標題裡，拿去查只會查不到東西。
        name = query_name(name)

        # 1) Google News，限定經濟日報
        q1 = quote(f'{name} 法說會 site:money.udn.com')
        r = get(s, GOOGLE_NEWS_RSS.format(q=q1), retries=2)
        if r is not None:
            _merge(bucket, _parse_rss(r.text))
        time.sleep(pause)

        # 2) Google News，不限來源（補漏，仍會標示各自來源）
        q2 = quote(f'{name} 法說會')
        r = get(s, GOOGLE_NEWS_RSS.format(q=q2), retries=2)
        if r is not None:
            _merge(bucket, _parse_rss(r.text))
        time.sleep(pause)

        # 3) 經濟日報分類 RSS 裡標題有提到這家公司且跟法說有關的
        hits = [
            it for it in udn_pool
            if name in it["title"] and ("法說" in it["title"] or "法人說明" in it["title"])
        ]
        _merge(bucket, hits)

        raw = list(bucket.values())
        items = [it for it in raw if title_matches(name, code, it.get("title", ""))]
        items.sort(key=lambda x: x["date"], reverse=True)
        for it in items:
            it.pop("direct", None)
        if items:
            result[code] = items
        dropped = len(raw) - len(items)
        log(f"  {code} {name} → {len(items)} 則"
            + (f"（另有 {dropped} 則標題沒提到這家公司，已濾掉）" if dropped else ""))

    log(f"📦 新聞：{len(result)} 檔有報導、合計 {sum(len(v) for v in result.values())} 則")
    return result


# 標題有這些字，才算真的跟這場法說會有關
RELEVANT_RE = re.compile(r"法說|法人說明|業績發表|財報|營運展望|季報")

MAX_NEWS_PER_CALL = 6      # 每一場最多留幾則
MAX_RECENT_PER_STOCK = 12  # 掛不上場次的近期報導，每檔最多留幾則


def attach_to_calls(
    calls: dict[str, list[dict]],
    news: dict[str, list[dict]],
    *,
    before_days: int = 5,
    after_days: int = 25,
) -> dict[str, list[dict]]:
    """把報導掛到日期最接近的場次上。

    視窗設成「法說會前 5 天到後 25 天」—— 會前有預告、會後有解讀。

    光靠日期視窗會撈進一堆無關新聞（實測中華電一檔就掛了 158 則，
    第一則還是月營收快報），所以再加兩道：標題要真的提到法說／財報之類的
    關鍵字才算相關，且每場只留最相關、日期最接近的前幾則。

    掛不上任何場次的（例如這家還沒開過法說會）會被回傳，由 build.py
    放進 recent 區塊，不會憑空生出一個假的場次。
    """
    import datetime as dt

    leftovers: dict[str, list[dict]] = {}
    buckets: dict[int, list[tuple[int, int, dict]]] = {}   # id(call) → [(相關度, 差幾天, 報導)]
    dropped = 0

    for code, items in news.items():
        stock_calls = calls.get(code) or []
        for it in items:
            relevant = bool(RELEVANT_RE.search(it.get("title", "")))
            try:
                d = dt.date.fromisoformat(it["date"])
            except (ValueError, KeyError, TypeError):
                if relevant:
                    leftovers.setdefault(code, []).append(it)
                else:
                    dropped += 1
                continue

            best, best_gap = None, None
            for call in stock_calls:
                try:
                    cd = dt.date.fromisoformat(call["date"])
                except (ValueError, KeyError):
                    continue
                delta = (d - cd).days
                if -before_days <= delta <= after_days:
                    gap = abs(delta)
                    if best_gap is None or gap < best_gap:
                        best, best_gap = call, gap

            if best is not None:
                # 相關度優先（0 比 1 前面），同相關度時日期越接近越前面
                buckets.setdefault(id(best), []).append((0 if relevant else 1, best_gap, it))
            elif relevant:
                leftovers.setdefault(code, []).append(it)
            else:
                dropped += 1

    # 排序後截斷
    for call in (c for v in calls.values() for c in v):
        picked = buckets.get(id(call))
        if not picked:
            continue
        picked.sort(key=lambda x: (x[0], x[1]))
        call["news"] = [it for _, _, it in picked[:MAX_NEWS_PER_CALL]]

    for code in list(leftovers):
        leftovers[code].sort(key=lambda x: x.get("date", ""), reverse=True)
        leftovers[code] = leftovers[code][:MAX_RECENT_PER_STOCK]

    attached = sum(len(c["news"]) for v in calls.values() for c in v)
    log(f"🔗 報導掛載：{attached} 則掛到場次、"
        f"{sum(len(v) for v in leftovers.values())} 則歸入近期報導、"
        f"{dropped} 則因與法說會無關而略過")
    return leftovers


if __name__ == "__main__":
    s = session()
    if "--probe" in sys.argv:
        probe(s, UDN_FEEDS, label="經濟日報分類 RSS")
        probe(s, [GOOGLE_NEWS_RSS.format(q=quote("台積電 法說會 site:money.udn.com"))],
              label="Google News RSS")
    else:
        out = fetch_news(s, {"2330": "台積電"})
        log(str(out.get("2330", [])[:3]))
