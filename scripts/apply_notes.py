"""把 data/notes.json 併進現有的 data/earnings.js，不重新抓任何東西。

改完 notes 想馬上在網站上看到效果的時候用這支，不用等 Action 跑完整流程
（完整流程要抓 MOPS 24 個月、54 檔的新聞、損益表，要好幾分鐘）。

    python3 scripts/apply_notes.py

結果跟 build.py 併出來的完全一樣 —— 用的是同一份 notes.json 和同一套合併規則，
所以下次 Action 執行不會把這裡做的事覆蓋掉，也不會產生額外的 diff。
"""

from __future__ import annotations

import json
import os
import re
import sys

from build import EARNINGS_HEADER
from common import DATA_DIR, log, read_json, warn, write_js


def load_earnings() -> dict | None:
    path = os.path.join(DATA_DIR, "earnings.js")
    if not os.path.exists(path):
        warn(f"找不到 {path} —— 先跑一次 scripts/build.py")
        return None
    src = open(path, encoding="utf-8").read()
    m = re.search(r"window\.EARNINGS\s*=\s*", src)
    if not m:
        warn("earnings.js 裡找不到 window.EARNINGS")
        return None
    body = src[m.end():].rstrip().rstrip(";")
    try:
        return json.loads(body[:body.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError) as e:
        warn(f"earnings.js 解析失敗：{e}")
        return None


def main() -> int:
    data = load_earnings()
    if data is None:
        return 1

    notes_map = read_json("notes.json", {})
    calls = data.get("calls") or {}

    merged = cleared = 0
    for code, by_date in (notes_map or {}).items():
        # notes.json 最上面那個 _說明 是一個 list，不是場次對照表
        if not isinstance(by_date, dict):
            continue
        for call in calls.get(code, []):
            extra = by_date.get(call.get("date"))
            if extra:
                call["notes"] = list(extra)
                merged += 1

    # 從 notes.json 拿掉的重點，也要跟著從資料檔消失，否則會留下孤兒
    for code, lst in calls.items():
        by_date = notes_map.get(code) if isinstance(notes_map.get(code), dict) else {}
        for call in lst:
            if call.get("notes") and not by_date.get(call.get("date")):
                call.pop("notes", None)
                cleared += 1

    missing = []
    for code, by_date in (notes_map or {}).items():
        if not isinstance(by_date, dict):
            continue
        dates = {c.get("date") for c in calls.get(code, [])}
        for d in by_date:
            if d not in dates:
                missing.append(f"{code} {d}")

    write_js("earnings.js", "EARNINGS", data, EARNINGS_HEADER)
    log(f"📝 併入 {merged} 場的重點" + (f"、清掉 {cleared} 場舊的" if cleared else ""))
    if missing:
        warn("下列 notes 對不到任何場次，不會顯示：" + "、".join(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
