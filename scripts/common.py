"""共用工具：HTTP、日誌、資料檔輸出。"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import time
from typing import Any, Iterable

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 earnings-call-bot/1.0 "
    "(+https://github.com/blair1024-glitch/earnings-call)"
)

TZ_TAIPEI = _dt.timezone(_dt.timedelta(hours=8))


def log(msg: str) -> None:
    print(msg, flush=True)


def warn(msg: str) -> None:
    print("⚠️  " + msg, file=sys.stderr, flush=True)


def now_taipei() -> _dt.datetime:
    return _dt.datetime.now(TZ_TAIPEI)


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9"})
    return s


def get(
    s: requests.Session,
    url: str,
    *,
    method: str = "GET",
    data: dict | None = None,
    timeout: int = 30,
    retries: int = 3,
) -> requests.Response | None:
    """帶重試的請求。失敗回 None，不丟例外 —— 單一來源失敗不該拖垮整個 build。"""
    delay = 2
    for attempt in range(1, retries + 1):
        try:
            if method == "POST":
                r = s.post(url, data=data, timeout=timeout)
            else:
                r = s.get(url, timeout=timeout)
            if r.status_code == 200:
                return r
            warn(f"{method} {url} → HTTP {r.status_code}（第 {attempt}/{retries} 次）")
        except requests.RequestException as e:
            warn(f"{method} {url} → {type(e).__name__}: {e}（第 {attempt}/{retries} 次）")
        if attempt < retries:
            time.sleep(delay)
            delay *= 2
    return None


def probe(s: requests.Session, urls: Iterable[str], *, label: str) -> None:
    """--probe 模式：逐一打候選端點，把狀態碼與回應開頭印到 log，
    好在 GitHub Actions 的執行紀錄裡看出哪個端點還活著、格式長怎樣。"""
    log(f"\n=== PROBE: {label} ===")
    for url in urls:
        try:
            r = s.get(url, timeout=30)
            head = r.text[:400].replace("\n", " ")
            log(f"  [{r.status_code}] {url}")
            log(f"        content-type: {r.headers.get('content-type', '?')}")
            log(f"        len={len(r.content)}  head={head!r}")
        except requests.RequestException as e:
            log(f"  [ERR] {url} → {type(e).__name__}: {e}")


def write_js(filename: str, varname: str, payload: Any, header: str) -> None:
    """把 payload 寫成 `window.<varname> = {...};` 的 .js 檔。
    刻意用 .js 而不是 .json，這樣直接用瀏覽器開本機 index.html 也不會被 CORS 擋。"""
    path = os.path.join(DATA_DIR, filename)
    os.makedirs(DATA_DIR, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)
    with open(path, "w", encoding="utf-8") as f:
        f.write(header.rstrip() + "\n")
        f.write(f"window.{varname} = {body};\n")
    log(f"✍️  已寫出 {path}（{os.path.getsize(path):,} bytes）")


def read_json(filename: str, default: Any) -> Any:
    """讀 data/ 底下的手動維護 JSON（缺檔或格式壞掉都回 default，不中斷 build）。"""
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        warn(f"找不到 {path}，使用預設值")
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        warn(f"讀取 {path} 失敗（{e}），使用預設值")
        return default


def roc_year(year: int) -> int:
    """西元年 → 民國年。"""
    return year - 1911


def recent_months(count: int) -> list[tuple[int, int]]:
    """回傳最近 count 個月的 (西元年, 月)，由新到舊。"""
    today = now_taipei().date()
    out: list[tuple[int, int]] = []
    y, m = today.year, today.month
    for _ in range(count):
        out.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out
