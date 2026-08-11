"""抓上市＋上櫃公司清單（代號 → 簡稱），供介面「同步顯示股票名稱」使用。

主要來源是 TWSE / TPEx 的 OpenAPI；兩邊都失敗時退回 TWSE 的 ISIN 對照表（HTML）。
每個來源都獨立 try，一邊掛掉不影響另一邊。
"""

from __future__ import annotations

import re
import sys

from bs4 import BeautifulSoup

from common import get, log, probe, session, warn

# 候選端點：由上往下試，第一個解析成功的就採用。
TWSE_CANDIDATES = [
    "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
]
TPEX_CANDIDATES = [
    "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
    "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
]
# 退路：ISIN 對照表（Big5 HTML）。strMode=2 上市、strMode=4 上櫃。
ISIN_URL = "https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}"

CODE_KEYS = ("公司代號", "SecuritiesCompanyCode", "Code", "股票代號")
NAME_KEYS = ("公司簡稱", "CompanyAbbreviation", "Name", "公司名稱", "SecuritiesCompanyName")


def _pick(row: dict, keys: tuple[str, ...]) -> str:
    for k in keys:
        v = row.get(k)
        if v:
            return str(v).strip()
    return ""


def _from_openapi(s, urls: list[str], market: str) -> dict[str, list[str]]:
    """打 OpenAPI，取 代號/簡稱。回傳 {code: [name, market]}。"""
    for url in urls:
        r = get(s, url)
        if r is None:
            continue
        try:
            rows = r.json()
        except ValueError:
            warn(f"{url} 回應不是 JSON，跳過")
            continue
        if not isinstance(rows, list) or not rows:
            warn(f"{url} 回應不是非空陣列，跳過")
            continue

        out: dict[str, list[str]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = _pick(row, CODE_KEYS)
            name = _pick(row, NAME_KEYS)
            if re.fullmatch(r"\d{4,6}[A-Z]?", code) and name:
                out[code] = [name, market]
        if out:
            log(f"✅ {market}：{url} → {len(out)} 檔")
            return out
        warn(f"{url} 解析後 0 筆（欄位名可能改了：{list(rows[0].keys())[:8]}）")
    return {}


def _from_isin(s, mode: int, market: str) -> dict[str, list[str]]:
    """退路：解析 ISIN 對照表 HTML。第一欄是「代號　名稱」。

    這個站很慢（實測上市那份 7.7MB 下載了 5 分 41 秒，不是逾時，就是純粹慢），
    所以只在 OpenAPI 掛掉時才走這裡，而且不重試，免得把整個 job 拖垮。
    """
    r = get(s, ISIN_URL.format(mode=mode), timeout=420, retries=1)
    if r is None:
        return {}
    r.encoding = "big5-hkscs"
    soup = BeautifulSoup(r.text, "html.parser")
    out: dict[str, list[str]] = {}
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        cell = tds[0].get_text(strip=True)
        parts = re.split(r"[\s　]+", cell, maxsplit=1)
        if len(parts) != 2:
            continue
        code, name = parts[0].strip(), parts[1].strip()
        if re.fullmatch(r"\d{4,6}[A-Z]?", code) and name:
            out[code] = [name, market]
    if out:
        log(f"✅ {market}（ISIN 退路）→ {len(out)} 檔")
    return out


def fetch_stocks(s) -> dict[str, list[str]]:
    items: dict[str, list[str]] = {}

    sii = _from_openapi(s, TWSE_CANDIDATES, "sii") or _from_isin(s, 2, "sii")
    if not sii:
        warn("上市清單全部來源都失敗")
    items.update(sii)

    otc = _from_openapi(s, TPEX_CANDIDATES, "otc") or _from_isin(s, 4, "otc")
    if not otc:
        warn("上櫃清單全部來源都失敗")
    # 上市優先，不讓上櫃來源覆蓋掉已有的上市資料
    for code, row in otc.items():
        items.setdefault(code, row)

    log(f"📦 股票清單合計 {len(items)} 檔")
    return items


if __name__ == "__main__":
    s = session()
    if "--probe" in sys.argv:
        probe(s, TWSE_CANDIDATES, label="TWSE 上市公司基本資料")
        probe(s, TPEX_CANDIDATES, label="TPEx 上櫃公司基本資料")
        # ISIN 退路刻意不放進探測：它慢到會讓整個 probe 多花 8 分鐘，
        # 而且只有在上面兩個 OpenAPI 都掛掉時才會用到。
        log("\n（ISIN 退路太慢，不列入例行探測；需要時單獨測）")
    else:
        result = fetch_stocks(s)
        log(f"取得 {len(result)} 檔，前 5 筆：{list(result.items())[:5]}")
