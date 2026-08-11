/* 台股法說會重點 — renderer + picker
   讀 data/stocks.js (window.STOCKS) 與 data/earnings.js (window.EARNINGS)。
   純 ES5、無相依套件、無任何網路請求。 */
(function () {
  "use strict";

  var STOCKS = window.STOCKS;
  var EARNINGS = window.EARNINGS;
  var FINANCIALS = window.FINANCIALS || { items: {} };

  // ---- helpers ------------------------------------------------------------
  function el(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function html(node, markup) { if (node) node.innerHTML = markup; }
  function store(key, val) {
    try {
      if (val === undefined) return localStorage.getItem(key);
      localStorage.setItem(key, val);
    } catch (e) { /* private mode / file:// */ }
    return null;
  }

  // ---- theme --------------------------------------------------------------
  var root = document.documentElement;
  var savedTheme = store("ec-theme");
  if (savedTheme === "light" || savedTheme === "dark") root.setAttribute("data-theme", savedTheme);

  el("theme-btn").addEventListener("click", function () {
    var next = root.getAttribute("data-theme") === "light" ? "dark" : "light";
    root.setAttribute("data-theme", next);
    store("ec-theme", next);
  });

  // ---- bail out early if the data files are missing ------------------------
  if (!STOCKS || !STOCKS.items || !EARNINGS) {
    html(el("call-body"),
      '<div class="empty"><div class="empty-title">⚠️ 找不到資料檔</div>' +
      '<div class="empty-note">請確認 data/stocks.js 與 data/earnings.js 都存在。</div></div>');
    return;
  }

  var META = EARNINGS.meta || {};
  var CALLS = EARNINGS.calls || {};
  var RECENT = EARNINGS.recent || {};   // 掛不到任何場次的近期報導
  var ITEMS = STOCKS.items;

  var MARKET_LABEL = { sii: "上市", otc: "上櫃", rotc: "興櫃" };

  // ---- 建索引 --------------------------------------------------------------
  // list: [{ code, name, market }]，供輸入框做代號／名稱雙向查詢
  var LIST = [];
  (function buildIndex() {
    for (var code in ITEMS) {
      if (!Object.prototype.hasOwnProperty.call(ITEMS, code)) continue;
      var row = ITEMS[code];
      LIST.push({
        code: code,
        name: (row && row[0]) || "",
        market: (row && row[1]) || ""
      });
    }
    LIST.sort(function (a, b) { return a.code < b.code ? -1 : a.code > b.code ? 1 : 0; });
  })();

  function nameOf(code) { return ITEMS[code] ? ITEMS[code][0] : ""; }
  function marketOf(code) { return ITEMS[code] ? ITEMS[code][1] : ""; }

  /* 把使用者輸入解析成代號。
     支援：純代號（2330）、完整名稱（台積電）、名稱片段唯一命中（台積）。
     解析不出來就回傳 null。 */
  function resolveCode(raw) {
    var q = String(raw || "").trim();
    if (!q) return null;
    if (ITEMS[q]) return q;                       // 直接就是代號

    var exact = null, partial = [];
    for (var i = 0; i < LIST.length; i++) {
      var it = LIST[i];
      if (it.name === q) { exact = it.code; break; }
      if (it.name.indexOf(q) !== -1) partial.push(it.code);
    }
    if (exact) return exact;
    if (partial.length === 1) return partial[0];  // 片段唯一命中才算數
    return null;
  }

  // ---- datalist：輸入時才動態填，最多 50 筆（避免一次塞上千個 option） -------
  var datalist = el("stock-codes");
  function fillDatalist(q) {
    q = String(q || "").trim();
    if (!q) { html(datalist, ""); return; }
    var out = [], n = 0;
    for (var i = 0; i < LIST.length && n < 50; i++) {
      var it = LIST[i];
      if (it.code.indexOf(q) === 0 || it.name.indexOf(q) !== -1) {
        out.push('<option value="' + esc(it.code) + '">' + esc(it.name) + "</option>");
        n++;
      }
    }
    html(datalist, out.join(""));
  }

  // ---- 精選清單下拉 --------------------------------------------------------
  var CUSTOM = "__custom__";
  var stockSelect = el("stock-select");
  var codeInput = el("stock-code");
  var callSelect = el("call-select");

  (function fillStockSelect() {
    var featured = EARNINGS.featured || [];
    var out = [];
    for (var i = 0; i < featured.length; i++) {
      var code = featured[i];
      var name = nameOf(code);
      out.push('<option value="' + esc(code) + '">' +
               esc(code) + "　" + esc(name || "（清單中查無此代號）") + "</option>");
    }
    out.push('<option value="' + CUSTOM + '">✏️ 自行輸入…</option>');
    html(stockSelect, out.join(""));
  })();

  // ---- 外連 ---------------------------------------------------------------
  function newsSearchUrl(code) {
    var tpl = META.searchUrl || "https://money.udn.com/search/result/1001/{q}";
    var kw = (nameOf(code) || code) + " 法說會";
    return tpl.replace("{q}", encodeURIComponent(kw));
  }
  function mopsUrl() {
    return META.mopsUrl || "https://mops.twse.com.tw/mops/web/t100sb02_1";
  }
  function outLinks(code) {
    return '<div class="linkrow">' +
      '<a class="linkbtn" href="' + esc(newsSearchUrl(code)) + '" target="_blank" rel="noopener noreferrer">📰 在經濟日報搜尋更多</a>' +
      '<a class="linkbtn" href="' + esc(mopsUrl()) + '" target="_blank" rel="noopener noreferrer">🏛️ 到公開資訊觀測站查詢</a>' +
      "</div>";
  }

  // ---- 場次資料 -----------------------------------------------------------
  /* 資料檔為了縮小體積，不存 id / label / kind（id 跟 date 一樣、label 可推導、
     kind 幾乎都是「法人說明會」），所以這裡補回來。
     舊資料如果有帶這些欄位，就沿用舊的。 */
  function callId(call) { return call.id || call.date || ""; }
  function callKind(call) { return call.kind || "法人說明會"; }
  function callLabel(call, i) {
    if (call.label) return call.label;
    if (call.date) return call.date.replace(/-/g, "/") + " " + callKind(call);
    return "第 " + (i + 1) + " 場";
  }

  function callsFor(code) {
    var list = CALLS[code];
    if (!list || !list.length) return [];
    // 資料應該已經是新到舊；日期齊全時再保險排一次序
    var allDated = true;
    for (var i = 0; i < list.length; i++) { if (!list[i].date) { allDated = false; break; } }
    if (!allDated) return list.slice();
    return list.slice().sort(function (a, b) {
      return a.date < b.date ? 1 : a.date > b.date ? -1 : 0;
    });
  }

  // ---- 渲染：同步顯示股票名稱 ----------------------------------------------
  function renderReadout(rawInput, code) {
    var box = el("stock-readout");
    if (!code) {
      var typed = String(rawInput || "").trim();
      if (!typed) { html(box, '<span class="readout-hint">請選擇或輸入股票代號</span>'); return; }
      html(box,
        '<span class="readout-code">' + esc(typed) + "</span>" +
        '<span class="readout-miss">查無此代號／名稱</span>' +
        '<span class="readout-hint">清單以 data/stocks.js 為準</span>');
      return;
    }
    var market = MARKET_LABEL[marketOf(code)] || "";
    html(box,
      '<span class="readout-code">' + esc(code) + "</span>" +
      '<span class="readout-name">' + esc(nameOf(code)) + "</span>" +
      (market ? '<span class="readout-market">' + esc(market) + "</span>" : "") +
      '<span class="readout-hint">' + esc(callsFor(code).length) + " 場法說會紀錄</span>");
  }

  // ---- 渲染：財報體質（三率） ----------------------------------------------
  var RATE_LABEL = { gm: "毛利率", opm: "營業利益率", npm: "稅後淨利率" };
  var RATE_ORDER = ["gm", "opm", "npm"];

  function fmtPct(v) { return (v == null ? "—" : Number(v).toFixed(2) + "%"); }
  function fmtDelta(v) {
    if (v == null) return "";
    return (v > 0 ? "▲ +" : v < 0 ? "▼ " : "— ") + Number(v).toFixed(2) + " pp";
  }
  function deltaClass(v) { return v > 0 ? "up" : v < 0 ? "down" : "flat"; }

  function renderFinancials(code) {
    var block = el("fin-block");
    var body = el("fin-body");
    var fin = (FINANCIALS.items || {})[code];

    if (!fin) { block.hidden = true; return; }
    block.hidden = false;

    // 金融／保險／證券業：損益表沒有營業毛利，公式不適用
    if (fin.applicable === false) {
      html(body, '<div class="card"><p class="note">' +
        esc(fin.reason || "這個產業不適用三率公式") +
        "（" + esc(fin.period || "") + "）</p></div>");
      return;
    }

    var rates = fin.rates || {};
    var parts = ['<div class="card">'];

    // 判斷徽章 + 比較基期
    parts.push('<div class="fin-head">');
    if (FINANCIALS.fixture) {
      parts.push('<span class="verdict-badge s-y">⚠️ 範例數字</span>');
    }
    if (fin.verdict) {
      var sig = fin.rising === 3 ? "s-g" : fin.rising === 2 ? "s-y" : "s-r";
      parts.push('<span class="verdict-badge ' + sig + '">' + esc(fin.verdict) + "</span>");
    }
    if (fin.basePeriod) {
      parts.push('<span class="fin-basis"><code>' + esc(fin.period) + "</code> vs <code>" +
        esc(fin.basePeriod) + "</code>（" +
        (fin.basis === "YoY" ? "去年同季" : "上一季") + "）</span>");
    } else {
      parts.push('<span class="fin-basis">' +
        esc(fin.reason || "尚無可比較的基期") +
        "（本季 <code>" + esc(fin.period) + "</code>）</span>");
    }
    parts.push("</div>");

    // 三個率各自一塊，數字與升降都攤開
    parts.push('<div class="stats">');
    for (var i = 0; i < RATE_ORDER.length; i++) {
      var k = RATE_ORDER[i];
      var d = fin.delta ? fin.delta[k] : null;
      var cls = d == null ? "s-y" : d > 0 ? "s-g" : d < 0 ? "s-r" : "s-y";
      parts.push('<div class="stat ' + cls + '">' +
        '<div class="stat-label">' + esc(RATE_LABEL[k]) + "</div>" +
        '<div class="stat-value">' + esc(fmtPct(rates[k])) + "</div>" +
        (d == null ? "" :
          '<div class="stat-delta ' + deltaClass(d) + '">' + esc(fmtDelta(d)) +
          (fin.baseRates ? "　(前期 " + esc(fmtPct(fin.baseRates[k])) + ")" : "") +
          "</div>") +
        "</div>");
    }
    parts.push("</div>");

    parts.push('<p class="fin-foot">三率是<strong>描述已公布財報</strong>的落後指標，' +
      '法說會談的則是下一季展望，兩者時間軸不同。' +
      '另外，處分資產或匯兌等一次性業外收益會讓稅後淨利率虛升，' +
      '出現「三率三升但獲利品質不佳」的情況，所以三個數字都攤開來看比單一結論可靠。' +
      '本區僅為公開財報的計算結果，不構成投資建議。</p>');
    parts.push("</div>");

    html(body, parts.join(""));
  }

  // ---- 渲染：報導清單（場次內與空狀態共用） --------------------------------
  function isUdn(item) {
    return /經濟日報|money\.udn/.test((item.source || "") + (item.url || ""));
  }
  function newsListMarkup(news) {
    var sorted = (news || []).slice().sort(function (a, b) {
      var au = isUdn(a) ? 0 : 1, bu = isUdn(b) ? 0 : 1;   // 經濟日報排前面
      if (au !== bu) return au - bu;
      return (b.date || "") < (a.date || "") ? -1 : 1;
    });
    var out = ['<ul class="newslist">'];
    for (var i = 0; i < sorted.length; i++) {
      var it = sorted[i];
      out.push("<li>" +
        '<a class="news-title" href="' + esc(it.url) + '" target="_blank" rel="noopener noreferrer">' +
          esc(it.title) + "</a>" +
        '<div class="news-meta">' +
          (it.date ? '<span class="news-date">' + esc(it.date) + "</span>" : "") +
          (it.source ? '<span class="news-source' + (isUdn(it) ? " udn" : "") + '">' +
            esc(it.source) + "</span>" : "") +
        "</div></li>");
    }
    out.push("</ul>");
    return out.join("");
  }

  // ---- 渲染：單一場次 ------------------------------------------------------
  function renderCall(code, call) {
    var body = el("call-body");
    var parts = [];

    parts.push('<div class="card">');
    parts.push('<div class="call-title">' + esc(nameOf(code)) + "　" + esc(callLabel(call, 0)) + "</div>");
    if (call.date) parts.push('<div class="call-sub">' + esc(call.date) + "</div>");

    // 場次基本資訊
    var stats = [];
    if (call.date)  stats.push(['日期', call.date, 's-g']);
    stats.push(['性質', callKind(call), 's-y']);
    if (call.place) stats.push(['地點', call.place, 's-y']);
    if (stats.length) {
      parts.push('<div class="stats" style="margin-top:14px">');
      for (var i = 0; i < stats.length; i++) {
        parts.push('<div class="stat ' + stats[i][2] + '">' +
                   '<div class="stat-label">' + esc(stats[i][0]) + "</div>" +
                   '<div class="stat-value">' + esc(stats[i][1]) + "</div></div>");
      }
      parts.push("</div>");
    }

    // 官方簡報 / 影音
    if (call.deck || call.video) {
      parts.push('<div class="linkrow" style="margin-top:14px">');
      if (call.deck) {
        parts.push('<a class="linkbtn primary" href="' + esc(call.deck) +
                   '" target="_blank" rel="noopener noreferrer">📄 官方法說會簡報</a>');
      }
      if (call.video) {
        parts.push('<a class="linkbtn" href="' + esc(call.video) +
                   '" target="_blank" rel="noopener noreferrer">🎬 法說會影音</a>');
      }
      parts.push("</div>");
    }

    // MOPS 的「法人說明會擇要訊息」
    if (call.summary) {
      parts.push('<p class="note" style="margin-top:14px">' + esc(call.summary) + "</p>");
    }
    parts.push("</div>");

    // 人工補充的重點
    var notes = call.notes || [];
    if (notes.length) {
      parts.push('<div class="card"><div class="section-title">📌 重點整理</div><ul class="checklist">');
      for (var n = 0; n < notes.length; n++) {
        parts.push('<li><span class="idx">' + (n + 1) + "</span><span>" + esc(notes[n]) + "</span></li>");
      }
      parts.push("</ul></div>");
    }

    // 相關報導（經濟日報優先）
    var news = call.news || [];
    parts.push('<div class="card">');
    parts.push('<div class="section-title">📰 相關報導 <span class="count">' +
               esc(news.length) + " 則</span></div>");
    if (news.length) {
      parts.push(newsListMarkup(news));
    } else {
      parts.push('<p class="note">這一場還沒有抓到相關報導。</p>');
    }
    parts.push('<div style="margin-top:14px">' + outLinks(code) + "</div>");
    parts.push("</div>");

    html(body, parts.join(""));
  }

  // ---- 渲染：查無場次的空狀態 ----------------------------------------------
  function renderEmpty(code) {
    var known = !!ITEMS[code];
    var out = ['<div class="empty">' +
      '<div class="empty-title">尚無 ' + esc(known ? nameOf(code) : code) + ' 的法說會紀錄</div>' +
      '<div class="empty-note">資料檔裡還沒有這檔的場次。可以直接到下面兩個地方查：</div>' +
      outLinks(code) +
      "</div>"];

    // 有抓到報導、但對不上任何場次的，還是列出來給使用者
    var loose = RECENT[code] || [];
    if (loose.length) {
      out.push('<div class="card" style="margin-top:14px">' +
        '<div class="section-title">📰 近期相關報導 <span class="count">' +
        esc(loose.length) + " 則</span></div>" +
        newsListMarkup(loose) + "</div>");
    }
    html(el("call-body"), out.join(""));
  }

  // ---- 場次下拉 -----------------------------------------------------------
  function fillCallSelect(code, wantId) {
    var list = callsFor(code);
    if (!list.length) {
      html(callSelect, '<option value="">（無資料）</option>');
      callSelect.disabled = true;
      return null;
    }
    callSelect.disabled = false;
    var out = [], picked = 0;
    for (var i = 0; i < list.length; i++) {
      var c = list[i];
      var label = callLabel(c, i);
      if (wantId && callId(c) === wantId) picked = i;
      out.push('<option value="' + esc(String(i)) + '">' + esc(label) + "</option>");
    }
    html(callSelect, out.join(""));
    callSelect.selectedIndex = picked;   // ★ 預設就是最新的一場（list[0]）
    return list[picked];
  }

  // ---- 網址參數 -----------------------------------------------------------
  function readParams() {
    var out = {};
    var qs = window.location.search.replace(/^\?/, "");
    if (!qs) return out;
    var pairs = qs.split("&");
    for (var i = 0; i < pairs.length; i++) {
      var kv = pairs[i].split("=");
      if (!kv[0]) continue;
      out[decodeURIComponent(kv[0])] = decodeURIComponent((kv[1] || "").replace(/\+/g, " "));
    }
    return out;
  }
  function writeParams(code, callId) {
    if (!window.history || !window.history.replaceState) return;
    var q = "?stock=" + encodeURIComponent(code);
    if (callId) q += "&call=" + encodeURIComponent(callId);
    try { window.history.replaceState(null, "", window.location.pathname + q); } catch (e) { /* file:// */ }
  }

  // ---- 主流程 -------------------------------------------------------------
  var current = null;   // 目前選定的代號

  /* 切換股票。
     opts.fromInput  true 時不要覆寫使用者正在打的字
     opts.callId     想選中的場次 id（沒有就用最新的） */
  function selectStock(code, opts) {
    opts = opts || {};
    current = code;

    // 下拉與輸入框同步
    var inFeatured = false;
    for (var i = 0; i < stockSelect.options.length; i++) {
      if (stockSelect.options[i].value === code) { stockSelect.selectedIndex = i; inFeatured = true; break; }
    }
    if (!inFeatured) stockSelect.value = CUSTOM;
    if (!opts.fromInput) codeInput.value = code;

    renderReadout(code, code);
    renderFinancials(code);

    var call = fillCallSelect(code, opts.callId);
    if (call) { renderCall(code, call); } else { renderEmpty(code); }

    store("ec-stock", code);
    writeParams(code, call ? callId(call) : null);
  }

  // 下拉選股
  stockSelect.addEventListener("change", function () {
    if (stockSelect.value === CUSTOM) {
      codeInput.value = "";
      codeInput.focus();
      renderReadout("", null);
      return;
    }
    selectStock(stockSelect.value, {});
  });

  // 自行輸入：每打一個字就更新名稱顯示；解析得出代號才換內容
  codeInput.addEventListener("input", function () {
    var raw = codeInput.value;
    fillDatalist(raw);
    var code = resolveCode(raw);
    if (code && code !== current) {
      selectStock(code, { fromInput: true });
    } else {
      renderReadout(raw, code);
    }
  });

  // 按 Enter／離開輸入框時，把輸入正規化成代號
  function commitInput() {
    var code = resolveCode(codeInput.value);
    if (code) { selectStock(code, {}); }
  }
  codeInput.addEventListener("change", commitInput);
  codeInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter" || e.keyCode === 13) { e.preventDefault(); commitInput(); }
  });

  // 場次下拉
  callSelect.addEventListener("change", function () {
    if (!current) return;
    var list = callsFor(current);
    var call = list[Number(callSelect.value) || 0];
    if (call) { renderCall(current, call); writeParams(current, callId(call)); }
  });

  // ---- 啟動 ---------------------------------------------------------------
  (function boot() {
    // 範例資料警告橫幅
    if (META.fixture) el("fixture-banner").hidden = false;
    el("stamp").textContent = META.updated ? "資料更新：" + META.updated : "";

    var params = readParams();
    var featured = EARNINGS.featured || [];
    var initial = resolveCode(params.stock) ||
                  resolveCode(store("ec-stock")) ||
                  featured[0] ||
                  LIST[0].code;

    fillDatalist("");
    selectStock(initial, { callId: params.call });
  })();
})();
