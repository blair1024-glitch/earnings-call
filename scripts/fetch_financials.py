"""［評估中］綜合損益表 → 三率（毛利率／營業利益率／淨利率）。

目前**只實作 --probe 探測模式**，用來確認 TWSE / TPEx 的財報 OpenAPI 端點
還活著、欄位長什麼樣、以及最關鍵的一件事：**它到底給不給歷史季度**。

為什麼這件事是關鍵：
    「三率三升」要比較的是「同一季 vs 去年同季」。
    如果 OpenAPI 只吐「最新一季」，那就得自己把每季快照存下來累積歷史，
    或改從 MOPS 逐檔抓歷史財報（請求量大很多）。
    先探測、看清楚，再決定怎麼做。

注意：金融、保險、證券業的損益表結構不同（沒有營業毛利這一欄），
      不能直接套同一套三率公式，之後實作時要分開處理。
"""

from __future__ import annotations

import sys

from common import probe, session

# 綜合損益表候選端點（不同產業別是不同檔）
TWSE_IS_CANDIDATES = [
    "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci",     # 一般業
    "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_basi",   # 金融業
    "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_bd",     # 票券業
    "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ins",    # 保險業
    "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_mim",    # 證券業
]
TPEX_IS_CANDIDATES = [
    "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_ci",
    "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_basi",
]
# 月營收（另一條可能有用的線：營收動能）
REVENUE_CANDIDATES = [
    "https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
    "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O",
]


if __name__ == "__main__":
    if "--probe" not in sys.argv:
        print(__doc__)
        print("目前只支援 --probe，尚未接進 build.py。")
        sys.exit(2)
    s = session()
    probe(s, TWSE_IS_CANDIDATES, label="TWSE 綜合損益表（三率來源候選）")
    probe(s, TPEX_IS_CANDIDATES, label="TPEx 綜合損益表（三率來源候選）")
    probe(s, REVENUE_CANDIDATES, label="每月營業收入")
