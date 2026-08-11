/**
 * 法說會資料  —  window.EARNINGS
 * ------------------------------------------------------------------
 * ⚠️⚠️ 這份目前是「範例資料（fixture）」，裡面的場次、日期、連結全部都是假的，
 *      只是用來讓介面可以先跑起來、方便驗收互動行為。
 *      meta.fixture = true 時，網頁上方會顯示一條明顯的警告橫幅。
 *
 *      GitHub Action（scripts/build.py）第一次成功抓到真實資料後，
 *      會整份覆蓋這個檔案，並把 meta.fixture 設為 false。
 *
 * 資料結構
 * ------------------------------------------------------------------
 *   calls["2330"] = [ 場次, 場次, ... ]   ★ 一律「新到舊」排序，UI 取第 0 筆當預設
 *
 *   場次欄位：
 *     id     場次唯一 id（用日期）
 *     label  下拉選單顯示文字
 *     date   YYYY-MM-DD
 *     kind   性質（法人說明會 / 業績發表會 …）
 *     place  地點
 *     deck   官方簡報 PDF 連結，沒有就 null
 *     video  影音連結，沒有就 null
 *     notes  ★ 人工補充的重點條列 —— Action 重跑時會「保留」這個欄位，不會被洗掉
 *     news   相關報導（Action 由 RSS 產生），經濟日報的會被排在最前面
 */
window.EARNINGS = {
  meta: {
    updated: "fixture",
    fixture: true,
    // 查無資料時導出去的連結樣板，{q} 會被代換成「公司名稱 法說會」
    searchUrl: "https://money.udn.com/search/result/1001/{q}",
    mopsUrl: "https://mops.twse.com.tw/mops/web/t100sb02_1"
  },

  // 下拉選單的精選清單（可自由增減，代號要存在於 data/stocks.js）
  featured: [
    "2330", "2454", "2317", "2382", "3231", "6669",
    "2308", "3711", "2303", "3034", "2412", "2881"
  ],

  calls: {
    "2330": [
      {
        id: "fixture-a1",
        label: "（範例）第一場",
        date: "",
        kind: "範例資料",
        place: "",
        deck: null,
        video: null,
        notes: [
          "這是範例文字，不是真實的法說會內容。",
          "接上真實來源後，這一區會變成你自己補充的重點。"
        ],
        news: []
      },
      {
        id: "fixture-a2",
        label: "（範例）第二場",
        date: "",
        kind: "範例資料",
        place: "",
        deck: null,
        video: null,
        notes: ["這是第二筆範例場次，用來測試場次下拉切換。"],
        news: []
      }
    ],

    "2454": [
      {
        id: "fixture-b1",
        label: "（範例）第一場",
        date: "",
        kind: "範例資料",
        place: "",
        deck: null,
        video: null,
        notes: ["範例：用來測試切換股票時，場次下拉會重建並自動選回最新一場。"],
        news: []
      }
    ]

    // 2317、2382 等其餘精選股在 fixture 階段刻意「沒有資料」，
    // 用來驗收「查無場次」的空狀態畫面與外連按鈕。
  }
};
