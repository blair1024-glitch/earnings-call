"""解析器的離線測試 —— 完全不連網，用合成的回應驗證 parser 邏輯。

真正的端點是否還活著要靠 workflow 的 --probe 模式確認；
這支測試守的是「拿到資料之後，有沒有正確解析出來」。

    python scripts/test_parsers.py
"""

from __future__ import annotations

import sys

from fetch_financials import (
    F_GROSS, F_NET_PARENT, F_OP, F_REVENUE, F_SEASON, F_YEAR,
    _num, _period, _prev_periods, _rates, evaluate,
)
from fetch_financials import _migrate
from fetch_financials import _pick as _pick_fin
from fetch_financials import _pick_prefix as _prefix
from fetch_mops import _parse_tables, _roc_to_iso, deck_filename, deck_url
from fetch_news import _clean_title, _norm, _parse_rss, attach_to_calls, query_name
from fetch_stocks import _pick
from summarize import (
    fingerprint, needs_summary, normalize, numbers_in, to_browser, verify_outlook,
)

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

print("\n[MOPS 表格：href=# 不該變成有效連結]")
# 實測 log 裡出現過 deck 被解析成 'https://mopsov.twse.com.tw'（只有網域沒有路徑），
# 原因是那格的連結是 href="#" 靠 javascript 送出表單。
HASH_HTML = """
<table>
  <tr><th>公司代號</th><th>公司名稱</th><th>日期</th><th>中文簡報</th></tr>
  <tr><td>1432</td><td>大魯閣</td><td>115/07/02</td><td><a href="#">中文</a></td></tr>
</table>
"""
hash_rows = list(_parse_tables(HASH_HTML, "https://mopsov.twse.com.tw"))
check("href=# 視為沒有簡報", hash_rows[0]["deck"], None)

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
        {"title": "法說會前瞻", "date": "2026-07-14", "url": "u1"},   # 相關，會前 2 天 → 掛 7/16
        {"title": "法說會解讀", "date": "2026-07-20", "url": "u2"},   # 相關，會後 4 天 → 掛 7/16
        {"title": "上季法說會", "date": "2026-04-18", "url": "u3"},   # 相關 → 掛 4/17
        {"title": "去年的法說會", "date": "2025-01-01", "url": "u4"},  # 相關但不在視窗 → leftover
        {"title": "沒有日期的法說會", "date": "", "url": "u5"},        # 相關但沒日期 → leftover
        {"title": "月營收快報", "date": "2025-03-01", "url": "u6"},    # 無關且不在視窗 → 丟棄
        {"title": "股價創新高", "date": "2026-07-17", "url": "u7"},    # 無關但在視窗 → 留著但排後面
    ]
}
leftovers = attach_to_calls(calls, news)
check("7/16 場掛到 3 則（含 1 則視窗內的補充）", len(calls["2330"][0]["news"]), 3)
check_true("相關的排在無關的前面",
           [x["url"] for x in calls["2330"][0]["news"]][:2] == ["u1", "u2"],
           str([x["url"] for x in calls["2330"][0]["news"]]))
check("4/17 場掛到 1 則", len(calls["2330"][1]["news"]), 1)
check("落單 2 則", len(leftovers.get("2330", [])), 2)
check_true("落單的是 u4/u5（相關但掛不上場次）",
           {x["url"] for x in leftovers["2330"]} == {"u4", "u5"},
           str(leftovers.get("2330")))
check_true("與法說會無關又不在視窗內的被丟棄",
           "u6" not in {x["url"] for v in calls["2330"] for x in v["news"]}
           and "u6" not in {x["url"] for x in leftovers.get("2330", [])}, "u6 不該出現")

print("\n[報導掛載：每場數量上限]")
many = {"2330": [{"title": f"法說會報導{i}", "date": "2026-07-17", "url": f"m{i}"}
                 for i in range(20)]}
calls3 = {"2330": [{"date": "2026-07-16", "news": []}]}
attach_to_calls(calls3, many)
check("每場最多留 6 則", len(calls3["2330"][0]["news"]), 6)

print("\n[報導掛載：這家還沒開過法說會]")
calls2: dict = {}
left2 = attach_to_calls(calls2, {"9999": [{"title": "首場法說會將登場", "date": "2026-07-01", "url": "u"}]})
check("全部歸入 leftover，不會憑空生出場次", len(left2["9999"]), 1)
check("不會新增 calls 條目", "9999" in calls2, False)


# ---------------------------------------------------------------- 欄位挑選
print("\n[OpenAPI 欄位挑選]")
check("優先取公司簡稱", _pick({"公司簡稱": "台積電", "公司名稱": "台灣積體電路製造股份有限公司"},
                        ("公司簡稱", "公司名稱")), "台積電")
check("找不到回空字串", _pick({"x": 1}, ("公司簡稱",)), "")


# ---------------------------------------------------------------- 三率
print("\n[數字與期別解析]")
check("千分位逗號", _num("1,234,567"), 1234567.0)
check("括號代表負數", _num("(1,234)"), -1234.0)
check("空字串回 None（不能當成 0）", _num(""), None)
check("非數字回 None", _num("不適用"), None)
check("民國年季別", _period("115", "2"), "2026Q2")
check("西元年季別", _period("2026", "2"), "2026Q2")
check("季別不合法", _period("2026", "5"), "")
check("上一季跨年", _prev_periods("2026Q1"), ("2025Q1", "2025Q4"))
check("上一季同年", _prev_periods("2026Q3"), ("2025Q3", "2026Q2"))

print("\n[損益表欄位名 —— 用 probe 抓回來的真實欄位鎖住]")
# 這一筆的欄位名是從 openapi.twse.com.tw/v1/opendata/t187ap06_L_ci 的實際回應複製來的，
# 注意括號是全形（），而且母公司淨利的語序是「淨利（損）歸屬於母公司業主」。
REAL_ROW = {
    "出表日期": "1150811", "年度": "115", "季別": "2",
    "公司代號": "1213", "公司名稱": "大飲",
    "營業收入": "150587.00", "營業成本": "153262.00",
    "營業毛利（毛損）": "-2675.00", "營業毛利（毛損）淨額": "-2675.00",
    "營業費用": "20313.00", "營業利益（損失）": "-22988.00",
    "本期淨利（淨損）": "-31570.00",
    "淨利（損）歸屬於母公司業主": "-30000.00",
}
check("抓得到營收", _num(_pick_fin(REAL_ROW, F_REVENUE)), 150587.0)
check("抓得到全形括號的營業毛利", _num(_pick_fin(REAL_ROW, F_GROSS)), -2675.0)
check("抓得到全形括號的營業利益", _num(_pick_fin(REAL_ROW, F_OP)), -22988.0)
check("優先抓歸屬母公司的淨利", _num(_pick_fin(REAL_ROW, F_NET_PARENT)), -30000.0)
check("期別解析自年度與季別",
      _period(_pick_fin(REAL_ROW, F_YEAR), _pick_fin(REAL_ROW, F_SEASON)), "2026Q2")

# TPEx 用英文欄位名
TPEX_ROW = {"Year": "115", "Season": "2", "SecuritiesCompanyCode": "1259",
            "營業收入": "3091195.00", "營業毛利（毛損）": "717423.00",
            "營業利益（損失）": "6249.00"}
check("TPEx 的英文年度季別也認得",
      _period(_pick_fin(TPEX_ROW, F_YEAR), _pick_fin(TPEX_ROW, F_SEASON)), "2026Q2")

# 金融業那幾支端點實測回的是「每個欄位都空字串」的一列
EMPTY_ROW = {"出表日期": "1150811", "年度": "", "季別": "", "公司代號": "", "營業收入": ""}
check("空白列不會被誤收", _num(_pick_fin(EMPTY_ROW, F_REVENUE)), None)

print("\n[營益分析彙總表欄位 —— 用 probe 抓回來的真實欄位鎖住]")
# 上市：欄位名把算式也寫進去了
TWSE_RATIO = {
    "出表日期": "1150811", "年度": "115", "季別": "2",
    "公司代號": "1213", "公司名稱": "大飲",
    "營業收入(百萬元)": "150.59",
    "毛利率(%)(營業毛利)/(營業收入)": "-1.78",
    "營業利益率(%)(營業利益)/(營業收入)": "-15.27",
    "稅前純益率(%)(稅前純益)/(營業收入)": "-20.34",
    "稅後純益率(%)(稅後純益)/(營業收入)": "-20.96",
}
check("上市毛利率", _num(_prefix(TWSE_RATIO, "毛利率")), -1.78)
check("上市營業利益率", _num(_prefix(TWSE_RATIO, "營業利益率")), -15.27)
check("上市稅後純益率（不是稅前）", _num(_prefix(TWSE_RATIO, "稅後純益率")), -20.96)

# 上櫃：欄位名精簡很多
TPEX_RATIO = {
    "Date": "1150811", "Year": "115", "季別": "2",
    "SecuritiesCompanyCode": "1259", "CompanyName": "安心",
    "營業收入百萬元": "3091.20", "毛利率": "23.21",
    "營業利益率": "0.20", "稅前純益率": "2.18", "稅後純益率": "2.13",
}
check("上櫃毛利率", _num(_prefix(TPEX_RATIO, "毛利率")), 23.21)
check("上櫃稅後純益率", _num(_prefix(TPEX_RATIO, "稅後純益率")), 2.13)
check("上櫃期別（季別是中文鍵、Year 是英文鍵）",
      _period(_pick_fin(TPEX_RATIO, F_YEAR), _pick_fin(TPEX_RATIO, F_SEASON)), "2026Q2")
check_true("「毛利率」的前綴比對不會誤抓到「營業利益率」",
           _prefix(TWSE_RATIO, "毛利率").startswith("-1.78"), _prefix(TWSE_RATIO, "毛利率"))

print("\n[歷史檔舊格式遷移]")
check("舊格式（原始金額）會換算成三率",
      _migrate({"revenue": 1000.0, "gross": 500.0, "op": 400.0, "net": 360.0}),
      {"gm": 50.0, "opm": 40.0, "npm": 36.0})
check("新格式原樣保留",
      _migrate({"gm": 1.0, "opm": 2.0, "npm": 3.0}), {"gm": 1.0, "opm": 2.0, "npm": 3.0})
check("舊格式缺營業毛利 → None", _migrate({"revenue": 1.0, "gross": None, "op": 1.0, "net": 1.0}), None)

print("\n[三率計算]")
check("三率百分比", _rates({"revenue": 1000.0, "gross": 560.0, "op": 450.0, "net": 410.0}),
      {"gm": 56.0, "opm": 45.0, "npm": 41.0})
check("缺營業毛利 → None（金融保險業）",
      _rates({"revenue": 1000.0, "gross": None, "op": 450.0, "net": 410.0}), None)
check("營收為 0 → None", _rates({"revenue": 0, "gross": 1, "op": 1, "net": 1}), None)

print("\n[三率三升判斷]")
hist = {
    # 三個都比去年同季高 → 三率三升
    "2330": {
        "2025Q2": {"revenue": 1000.0, "gross": 500.0, "op": 400.0, "net": 360.0},
        "2026Q2": {"revenue": 1200.0, "gross": 660.0, "op": 540.0, "net": 480.0},
    },
    # 毛利率升、營益率升、淨利率降 → 三率二升
    "2454": {
        "2025Q2": {"revenue": 1000.0, "gross": 480.0, "op": 300.0, "net": 280.0},
        "2026Q2": {"revenue": 1000.0, "gross": 500.0, "op": 320.0, "net": 260.0},
    },
    # 只有上一季可比 → 應標成 QoQ
    "3231": {
        "2026Q1": {"revenue": 1000.0, "gross": 100.0, "op": 60.0, "net": 50.0},
        "2026Q2": {"revenue": 1000.0, "gross": 120.0, "op": 70.0, "net": 55.0},
    },
    # 金融業，沒有營業毛利 → 不適用
    "2881": {
        "2026Q2": {"revenue": 1000.0, "gross": None, "op": 400.0, "net": 380.0},
    },
    # 只有一季，沒有基期
    "6669": {
        "2026Q2": {"revenue": 1000.0, "gross": 200.0, "op": 100.0, "net": 90.0},
    },
}
ev = evaluate(hist)
check("2330 判為三率三升", ev["2330"]["verdict"], "三率三升")
check("2330 基期是去年同季", (ev["2330"]["basis"], ev["2330"]["basePeriod"]), ("YoY", "2025Q2"))
check("2454 判為三率二升", ev["2454"]["verdict"], "三率二升")
check("2454 淨利率是下降的", ev["2454"]["delta"]["npm"] < 0, True)
check("3231 退回 QoQ 並標示", (ev["3231"]["basis"], ev["3231"]["basePeriod"]), ("QoQ", "2026Q1"))
check("2881 標為不適用", ev["2881"]["applicable"], False)
check_true("2881 有講原因", bool(ev["2881"].get("reason")), str(ev["2881"]))
check("6669 沒有基期就不給 verdict", "verdict" in ev["6669"], False)
check_true("6669 仍給得出當季三率", ev["6669"]["rates"]["gm"] == 20.0, str(ev["6669"]))


# ---------------------------------------------------------------- 官方簡報連結
# 這些 HTML 是 2026-08-12 的 --probe-deck 從 MOPS 真實回應抓下來的，
# 一個字都沒改。之前 deck 之所以 0/9740 全空，就是因為只看 href（那是 "#"）。
print("\n[官方簡報連結]")
from bs4 import BeautifulSoup as _BS

def _td(html):
    return _BS(html, "html.parser").find("td")

DECK_TD = _td('<td style="text-align:left !important;"><a href="#" onclick=\''
              'document.fm_fileDownload.fileName.value="121620260810M001.pdf";'
              'document.fm_fileDownload.submit();\'><font color="blue"><u>'
              '121620260810M001.pdf</u></font></a></td>')
PPTX_TD = _td('<td><a href="#" onclick=\'document.fm_fileDownload.fileName.value='
              '"132120260811M001.pptx";document.fm_fileDownload.submit();\'>x</a></td>')

check("從 onclick 挖出 PDF 檔名", deck_filename(DECK_TD), "121620260810M001.pdf")
check("pptx 也收", deck_filename(PPTX_TD), "132120260811M001.pptx")
check("沒有 onclick 就回 None", deck_filename(_td('<td><a href="#">x</a></td>')), None)
check("沒有連結就回 None", deck_filename(_td("<td>無</td>")), None)
check("不像檔名的值會被擋掉",
      deck_filename(_td('<td><a onclick=\'document.fm_fileDownload.fileName.value='
                        '"../../etc/passwd";\'>x</a></td>')), None)
check("組出來的下載網址",
      deck_url("https://mopsov.twse.com.tw", "121620260810M001.pdf"),
      "https://mopsov.twse.com.tw/server-java/FileDownLoad"
      "?step=9&filePath=%2Fhome%2Fhtml%2Fnas%2FSTR%2F&functionName=t100sb02_1"
      "&fileName=121620260810M001.pdf")

# 第一版只在「表頭對到簡報欄」時才去挖 onclick，結果實測 deck 整批是空的 ——
# 那一欄的表頭根本對不到「中文」或「簡報」。現在改成整列掃，不依賴表頭。
DECK_TABLE = """
<table>
 <tr><th>公司代號</th><th>公司名稱</th><th>召開日期</th><th>擇要訊息</th><th>電子檔案</th></tr>
 <tr><td>1216</td><td>統一</td><td>115/08/10</td><td>法說</td>
     <td><a href="#" onclick='document.fm_fileDownload.fileName.value="121620260810M001.pdf";
         document.fm_fileDownload.submit();'>121620260810M001.pdf</a></td></tr>
 <tr><td>1321</td><td>大洋</td><td>115/08/11</td><td>法說</td><td>&nbsp;</td></tr>
</table>
"""
rows = list(_parse_tables(DECK_TABLE, "https://mopsov.twse.com.tw"))
check("表頭叫「電子檔案」也抓得到簡報", [r.get("deck") for r in rows],
      ["121620260810M001.pdf", None])
check("其他欄位照樣解析", [r["code"] for r in rows], ["1216", "1321"])
check("deck 存的是檔名不是網址", rows[0]["deck"].startswith("http"), False)


# ---------------------------------------------------------------- 查詢用公司名
print("\n[查詢用公司名]")
check("星號註記會拿掉", query_name("國巨*"), "國巨")
check("-KY 會拿掉", query_name("世芯-KY"), "世芯")
check("中租-KY", query_name("中租-KY"), "中租")
check("一般名稱不動", query_name("台積電"), "台積電")
check("不會把名字砍成空字串", query_name("-KY"), "-KY")


# ---------------------------------------------------------------- 未來方向摘要
# 這一段守的是「模型回來之後的防幻覺驗證」。不連網、不需要 API key，
# 直接餵合成的模型輸出進 verify_outlook()。
print("\n[未來方向摘要：數字驗證]")

SUM_SUMMARY = "公司營運簡介、財務業務資訊、未來展望"
SUM_NEWS = [
    {"title": "聯發科法說2》估Q3營收年增7％至12％上看1598億元 第2代AI ASIC預計2028年初量產",
     "date": "2026-07-30", "source": "經濟日報", "url": "https://money.udn.com/news/story/1"},
    {"title": "聯發科法說會：旗艦晶片需求穩健 全年毛利率目標維持48.5%",
     "date": "2026-07-30", "source": "經濟日報", "url": "https://money.udn.com/news/story/2"},
]


def bullets(*items):
    return [{"text": t, "tag": g, "src": s} for t, g, s in items]


check("全形數字與千分位正規化", normalize("１,５９８ 億元"), "1598億元")
check("抓得出標題裡的數字", numbers_in("估Q3營收年增7％至12％"), ["3", "7", "12"])

kept = verify_outlook(
    bullets(("Q3 營收預估年增 7%～12%，上看 1,598 億元", "下季展望", 0),
            ("第 2 代 AI ASIC 預計 2028 年初量產", "新產品", 0)),
    SUM_NEWS, SUM_SUMMARY)
check("有根據的條目全部留下", len(kept), 2)
check("千分位寫法也對得上", kept[0]["text"].startswith("Q3 營收預估"), True)

check("編出來的數字被擋掉",
      verify_outlook(bullets(("Q3 營收上看 1600 億元", "下季展望", 0)), SUM_NEWS, SUM_SUMMARY),
      [])
check("四捨五入寫法可放行",
      len(verify_outlook(bullets(("全年毛利率目標 48.5%", "全年目標", 1)), SUM_NEWS, SUM_SUMMARY)), 1)
check("改成別的百分比就被擋掉",
      verify_outlook(bullets(("全年毛利率目標 49%", "全年目標", 1)), SUM_NEWS, SUM_SUMMARY), [])

check("src 超出報導數量 → 丟掉",
      verify_outlook(bullets(("需求穩健", "營運策略", 5)), SUM_NEWS, SUM_SUMMARY), [])
check("src 是 True（bool 不算 int）→ 丟掉",
      verify_outlook([{"text": "需求穩健", "tag": "營運策略", "src": True}], SUM_NEWS, SUM_SUMMARY), [])
check("src 缺漏 → 丟掉",
      verify_outlook([{"text": "需求穩健", "tag": "營運策略"}], SUM_NEWS, SUM_SUMMARY), [])
check("超長條目 → 丟掉",
      verify_outlook(bullets(("需求穩健" * 20, "營運策略", 0)), SUM_NEWS, SUM_SUMMARY), [])
check("不認得的 tag 會被歸到營運策略",
      verify_outlook(bullets(("需求穩健", "亂寫的標籤", 1)), SUM_NEWS, SUM_SUMMARY)[0]["tag"],
      "營運策略")
check("條目數量有上限",
      len(verify_outlook(bullets(*[("需求穩健", "營運策略", 1)] * 9), SUM_NEWS, SUM_SUMMARY)), 5)
check("全部被擋掉就回空清單（這場不產生摘要）",
      verify_outlook(bullets(("Q4 營收上看 9999 億元", "下季展望", 0)), SUM_NEWS, SUM_SUMMARY), [])


print("\n[未來方向摘要：指紋與呼叫條件]")
import datetime as _dt

TODAY = _dt.date(2026, 8, 12)
CALL = {"date": "2026-07-30", "summary": SUM_SUMMARY, "news": SUM_NEWS}

fp = fingerprint("2454", "2026-07-30", SUM_SUMMARY, SUM_NEWS)
check("同樣輸入 → 同樣指紋",
      fingerprint("2454", "2026-07-30", SUM_SUMMARY, SUM_NEWS), fp)
check_true("多掛一則報導 → 指紋改變",
           fingerprint("2454", "2026-07-30", SUM_SUMMARY,
                       SUM_NEWS + [{"title": "新的一則", "date": "2026-08-01"}]) != fp)

check("指紋沒變就不重打 API",
      needs_summary(CALL, {"2454|2026-07-30": {"fp": fp}}, "2454", today=TODAY), False)
check("指紋變了就要重打",
      needs_summary(CALL, {"2454|2026-07-30": {"fp": "舊的"}}, "2454", today=TODAY), True)
check("沒摘要過的場次要打", needs_summary(CALL, {}, "2454", today=TODAY), True)
check("只有一則報導 → 不值得打",
      needs_summary({**CALL, "news": SUM_NEWS[:1]}, {}, "2454", today=TODAY), False)
check("太舊的場次 → 不打",
      needs_summary({**CALL, "date": "2023-01-05"}, {}, "2454", today=TODAY), False)
check("日期壞掉 → 不打",
      needs_summary({**CALL, "date": ""}, {}, "2454", today=TODAY), False)


print("\n[未來方向摘要：失敗不可以污染快取]")
# 2026-08-12 第一次正式執行踩到的坑：API key 無效，200 場全部 401，
# 但每一場都被記成「材料不足」寫進快取 —— 指紋一記下去，key 修好之後
# 那 200 場也永遠不會重試了。這裡守的就是這件事。
import summarize as _sum


class _Boom:
    """模擬一個永遠 401 的 client。"""
    class messages:
        @staticmethod
        def create(**kw):
            raise _sum.__dict__.setdefault("_FakeAuthError", type(
                "AuthenticationError", (Exception,), {}))("invalid x-api-key")


CALLS = {"2454": [dict(CALL)]}
poisoned = {}
_orig_client = _sum._client
try:
    _sum._client = lambda: _Boom()
    _sum.summarize(CALLS, {"2454": "聯發科"}, poisoned)
finally:
    _sum._client = _orig_client
check("呼叫失敗的場次不寫進快取", poisoned, {})
check("下次執行會重新嘗試", needs_summary(CALL, poisoned, "2454", today=TODAY), True)


class _Empty:
    """模擬呼叫成功、但材料撐不出東西。"""
    class messages:
        @staticmethod
        def create(**kw):
            class R:
                content = [type("B", (), {"type": "text",
                                          "text": '{"outlook":[],"confidence":"low"}'})()]
            return R()


thin = {}
try:
    _sum._client = lambda: _Empty()
    _sum.summarize({"2454": [dict(CALL)]}, {"2454": "聯發科"}, thin)
finally:
    _sum._client = _orig_client
check("材料不足的場次會記快取（免得天天重打）", list(thin.keys()), ["2454|2026-07-30"])
check("記的是空 outlook", thin["2454|2026-07-30"]["outlook"], [])
check("材料沒變就不再打", needs_summary(CALL, thin, "2454", today=TODAY), False)


print("\n[未來方向摘要：輸出給前端]")
CACHE = {
    "_說明": "這一列不是場次，不可以被當成資料",
    "2454|2026-07-30": {"fp": fp, "outlook": bullets(("需求穩健", "營運策略", 1)),
                        "confidence": "high"},
    "2454|2026-04-30": {"fp": "x", "outlook": [], "confidence": "low"},
    "9999|2026-07-30": {"fp": "x", "outlook": bullets(("不存在的公司", "營運策略", 0)),
                        "confidence": "high"},
}
BROWSER = to_browser({"2454": [{"date": "2026-07-30"}, {"date": "2026-04-30"}]}, CACHE)
check("只輸出有摘要的場次", sorted(BROWSER.get("2454", {}).keys()), ["2026-07-30"])
check("outlook 空的場次不輸出", "2026-04-30" in BROWSER.get("2454", {}), False)
check("cache 裡對不到場次的公司不會外洩", "9999" in BROWSER, False)
check("說明列不會被當成場次", "_說明" in BROWSER, False)


# ---------------------------------------------------------------- 結果
print()
if FAILED:
    print(f"❌ {len(FAILED)} 項失敗：" + "、".join(FAILED))
    sys.exit(1)
print("✅ 全部通過")
