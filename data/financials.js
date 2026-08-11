/**
 * 財報體質（三率）  —  window.FINANCIALS
 * ------------------------------------------------------------------
 * ⚠️⚠️ 這份目前是「範例資料（fixture）」，裡面的數字全是為了測試介面而編的，
 *      刻意用整數比例，跟真實財報無關。fixture = true 時每一塊都會標「範例」。
 *
 *      GitHub Action（scripts/fetch_financials.py）抓到真實損益表後，
 *      會整份覆蓋這個檔案。
 *
 * 三率 = 毛利率、營業利益率、稅後淨利率，單位 %。
 * basis：YoY = 去年同季（法人的標準比法）、QoQ = 上一季。
 * applicable=false 代表該產業損益表沒有營業毛利（金融／保險／證券），公式不適用。
 */
window.FINANCIALS = {
  updated: "fixture",
  fixture: true,
  items: {
    /* 三率三升（與去年同季比） */
    "2330": {
      period: "2026Q2",
      applicable: true,
      rates: { gm: 55.00, opm: 45.00, npm: 40.00 },
      basePeriod: "2025Q2",
      baseRates: { gm: 50.00, opm: 40.00, npm: 36.00 },
      basis: "YoY",
      delta: { gm: 5.00, opm: 5.00, npm: 4.00 },
      rising: 3,
      verdict: "三率三升"
    },

    /* 三率二升：毛利率、營益率升，淨利率降 */
    "2454": {
      period: "2026Q2",
      applicable: true,
      rates: { gm: 50.00, opm: 32.00, npm: 26.00 },
      basePeriod: "2025Q2",
      baseRates: { gm: 48.00, opm: 30.00, npm: 28.00 },
      basis: "YoY",
      delta: { gm: 2.00, opm: 2.00, npm: -2.00 },
      rising: 2,
      verdict: "三率二升"
    },

    /* 歷史只夠比上一季 → 標成 QoQ */
    "3231": {
      period: "2026Q2",
      applicable: true,
      rates: { gm: 12.00, opm: 7.00, npm: 5.50 },
      basePeriod: "2026Q1",
      baseRates: { gm: 10.00, opm: 6.00, npm: 5.00 },
      basis: "QoQ",
      delta: { gm: 2.00, opm: 1.00, npm: 0.50 },
      rising: 3,
      verdict: "三率三升"
    },

    /* 金融業：損益表沒有營業毛利，公式不適用 */
    "2881": {
      period: "2026Q2",
      applicable: false,
      reason: "這個產業的損益表沒有營業毛利，三率公式不適用"
    },

    /* 只有一季，還沒有可比較的基期 */
    "6669": {
      period: "2026Q2",
      applicable: true,
      rates: { gm: 20.00, opm: 10.00, npm: 9.00 },
      basis: "",
      reason: "還沒有可比較的基期（歷史正在累積中）"
    }
  }
};
