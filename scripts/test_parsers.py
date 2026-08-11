"""解析器的離線測試 —— 完全不連網，用合成的回應驗證 parser 邏輯。

真正的端點是否還活著要靠 workflow 的 --probe 模式確認；
這支測試守的是「拿到資料之後，有沒有正確解析出來」。

    python scripts/test_parsers.py
"""

from __future__ import annotations

import sys

from fetch_mops import _parse_tables, _roc_to_iso
from fetch_news import _clean_title, _norm, _parse_rss, attach_to_calls
from fetch_stocks import _pick

FAILED: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f"  ✓ {name}")
    else:
        FAILED.append(name)
        print(f"  ✗ {name}\n      got  = {got!r}\n      want = {want!r}")


def check_true(name: str, cond, extra: str = "") -> None:
    if cond:
        print(f"  ✓ {name}")
    else:
        FAILED.append(name)
        print(f"  ✗ {name}  {extra}")


# ---------------------------------------------------------------- 民國日期
print("\n[民國日期轉換]")
check("115/07/16", _roc_to_iso("115/07/16"), "2026-07-16")
check("115年7月6日", _roc_to_iso("115年7月6日"), "2026-07-06")
check("已經是西元", _roc_to_iso("2026-07-16"), "2026-07-16")
check("認不出來回空字串", _roc_to_iso("尚未確定"), "")
check("月份不合法", _roc_to_iso("115/13/01"), "")


# ---------------------------------------------------------------- MOPS 表格
MOPS_HTML = """
<html><body>
<table>
  <tr><th>公司代號</th><th>公司名稱</th><th>召開法人說明會日期</th><th>時間</th>
      <th>地點</th><th>法人說明會擇要訊息</th><th>中文簡報</th><th>英文簡報</th><th>影音連結</th></tr>
  <tr>
    <td>2330</td><td>台積電</td><td>115/07/16</td><td>14:00</td>
    <td>線上會議</td><td>說明本季營運概況</td>
    <td><a href="/nas/STR/2330_20260716.pdf">中文</a></td>
    <td><a href="/nas/STR/2330_20260716E.pdf">英文</a></td>
    <td><a href="javascript:openVideo()">影音</a></td>
  </tr>
  <tr>
    <td>2454</td><td>聯發科</td><td>115/07/30</td><td>15:00</td>
    <td>公司會議室</td><td></td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td>
  </tr>
</table>
</body></html>
"""

print("\n[MOPS 表格解析]")
rows = list(_parse_tables(MOPS_HTML, "https://mopsov.twse.com.tw"))
check("解析出 2 列", len(rows), 2)
if len(rows) == 2:
    r0, r1 = rows
    check("代號", r0["code"], "2330")
    check("日期原文", r0["date"], "115/07/16")
    check("地點", r0["place"], "線上會議")
    check("擇要訊息", r0["summary"], "說明本季營運概況")
    check("中文簡報轉成絕對網址", r0["deck"], "https://mopsov.twse.com.tw/nas/STR/2330_20260716.pdf")
    check("英文簡報沒被當成中文簡報", r0["deck_en"],
          "https://mopsov.twse.com.tw/nas/STR/2330_20260716E.pdf")
    check("javascript: 連結視為沒有", r0["video"], None)
    check("第二列代號", r1["code"], "2454")
    check("沒有簡報時為 None", r1["deck"], None)

print("\n[MOPS 表格：不相干的表格要被忽略]")
noise = "<table><tr><th>項目</th><th>數值</th></tr><tr><td>a</td><td>1</td></tr></table>"
check("沒有『代號』表頭 → 0 列", len(list(_parse_tables(noise, "https://x"))), 0)


# ---------------------------------------------------------------- RSS
RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>台積電法說會釋出樂觀展望 - 經濟日報</title>
    <link>https://news.google.com/rss/articles/ABC123</link>
    <pubDate>Thu, 16 Jul 2026 06:30:00 GMT</pubDate>
    <source url="https://money.udn.com">經濟日報</source>
  </item>
  <item>
    <title>台積電法說會前分析</title>
    <link>https://money.udn.com/money/story/5612/999888</link>
    <pubDate>Mon, 13 Jul 2026 01:00:00 GMT</pubDate>
  </item>
  <item>
    <title>沒有連結的項目</title>
    <link></link>
  </item>
</channel></rss>
"""

print("\n[RSS 解析]")
items = _parse_rss(RSS)
check("略過沒有連結的項目 → 2 則", len(items), 2)
if len(items) == 2:
    check("標題有把『 - 來源』拆掉", items[0]["title"], "台積電法說會釋出樂觀展望")
    check("來源取自 <source>", items[0]["source"], "經濟日報")
    check("日期轉成 ISO", items[0]["date"], "2026-07-16")
    check("google 轉址不算 direct", items[0]["direct"], False)
    check("money.udn 直連算 direct", items[1]["direct"], True)

check("壞掉的 XML 不會炸", _parse_rss("<rss><broken>"), [])
check("標題拆來源", _clean_title("某某公司法說會 - 工商時報"), ("某某公司法說會", "工商時報"))
check("沒有來源後綴就原樣回", _clean_title("純標題"), ("純標題", ""))
check("正規化去標點", _norm("台積電，法說會！"), "台積電法說會")


# ---------------------------------------------------------------- 報導掛載
print("\n[報導掛到場次]")
calls = {
    "2330": [
        {"date": "2026-07-16", "news": []},
        {"date": "2026-04-17", "news": []},
    ]
}
news = {
    "2330": [
        {"title": "會前預告", "date": "2026-07-14", "url": "u1"},   # 會前 2 天 → 掛 7/16
        {"title": "會後解讀", "date": "2026-07-20", "url": "u2"},   # 會後 4 天 → 掛 7/16
        {"title": "上一季",   "date": "2026-04-18", "url": "u3"},   # → 掛 4/17
        {"title": "太久以前", "date": "2025-01-01", "url": "u4"},   # 不在任何視窗 → leftover
        {"title": "沒有日期", "date": "",           "url": "u5"},   # → leftover
    ]
}
leftovers = attach_to_calls(calls, news)
check("7/16 場掛到 2 則", len(calls["2330"][0]["news"]), 2)
check("4/17 場掛到 1 則", len(calls["2330"][1]["news"]), 1)
check("落單 2 則", len(leftovers.get("2330", [])), 2)
check_true("落單的是 u4/u5",
           {x["url"] for x in leftovers["2330"]} == {"u4", "u5"},
           str(leftovers.get("2330")))

print("\n[報導掛載：這家還沒開過法說會]")
calls2: dict = {}
left2 = attach_to_calls(calls2, {"9999": [{"title": "t", "date": "2026-07-01", "url": "u"}]})
check("全部歸入 leftover，不會憑空生出場次", len(left2["9999"]), 1)
check("不會新增 calls 條目", "9999" in calls2, False)


# ---------------------------------------------------------------- 欄位挑選
print("\n[OpenAPI 欄位挑選]")
check("優先取公司簡稱", _pick({"公司簡稱": "台積電", "公司名稱": "台灣積體電路製造股份有限公司"},
                        ("公司簡稱", "公司名稱")), "台積電")
check("找不到回空字串", _pick({"x": 1}, ("公司簡稱",)), "")


# ---------------------------------------------------------------- 結果
print()
if FAILED:
    print(f"❌ {len(FAILED)} 項失敗：" + "、".join(FAILED))
    sys.exit(1)
print("✅ 全部通過")
