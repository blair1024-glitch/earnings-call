/**
 * 介面行為驗收（Playwright）
 *
 *   1. 先開一個 http server：python3 -m http.server 8899
 *   2. node scripts/smoke_ui.js
 *
 * 這支測試跑的是「repo 裡實際的資料檔」，而資料每天都會被 Action 更新，
 * 所以斷言刻意寫成**結構性的不變條件**（場次下拉一定停在第一項、
 * 輸入代號一定要出現對應名稱…），而不是寫死某一筆資料的內容。
 * 唯一寫死的是 2330 = 台積電 這種不會變的對應。
 */
const { chromium } = require('playwright');

const BASE = process.env.BASE || 'http://127.0.0.1:8899/';
const CHROME = process.env.CHROME_PATH || undefined;

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log('  ✓ ' + name); }
  else { fail++; console.log('  ✗ ' + name + (extra ? '  → ' + extra : '')); }
}

(async () => {
  const browser = await chromium.launch(CHROME ? { executablePath: CHROME } : {});
  const errors = [];
  const requests = [];
  const page = await browser.newPage();
  page.on('pageerror', e => errors.push(String(e)));
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
  page.on('request', r => requests.push(r.url()));

  const text = sel => page.locator(sel).textContent();

  // ---------- 1. 載入 ----------
  console.log('\n[1] 預設載入');
  await page.goto(BASE, { waitUntil: 'networkidle' });
  check('無 JS 錯誤', errors.length === 0, errors.join(' | '));
  const featured = await page.evaluate(() => window.EARNINGS.featured[0]);
  check('預設選到 featured[0]', (await text('.readout-code')) === featured, featured);
  check('有同步顯示股票名稱', ((await text('.readout-name')) || '').length > 0);

  // ---------- 2. 場次下拉 ----------
  console.log('\n[2] 場次下拉');
  const optCount = await page.locator('#call-select option').count();
  check('場次下拉有選項', optCount > 0, 'count=' + optCount);
  check('預設停在第一項（最新一場）', (await page.locator('#call-select').inputValue()) === '0');
  const labels = await page.locator('#call-select option').allTextContents();
  const dates = labels.map(l => (l.match(/\d{4}\/\d{2}\/\d{2}/) || [''])[0]).filter(Boolean);
  check('場次由新到舊排序',
    dates.every((d, i) => i === 0 || dates[i - 1] >= d), JSON.stringify(dates.slice(0, 4)));

  if (optCount > 1) {
    const before = await text('.call-title');
    await page.selectOption('#call-select', '1');
    check('切換場次會換內容', (await text('.call-title')) !== before);
    check('網址帶上 call 參數', page.url().includes('call='), page.url());
  }

  // ---------- 3. 輸入代號 ----------
  // 先切到別檔、並把場次挑到非第一項，才測得到「換股票會把場次拉回最新一場」。
  // （輸入的若是目前已選的同一檔，程式刻意保留使用者選的場次，不重建下拉。）
  console.log('\n[3] 自行輸入代號');
  await page.goto(BASE + '?stock=2454', { waitUntil: 'networkidle' });
  const n2454 = await page.locator('#call-select option').count();
  if (n2454 > 1) await page.selectOption('#call-select', String(n2454 - 1));
  await page.fill('#stock-code', '2330');
  check('2330 → 台積電', (await text('.readout-name')) === '台積電');
  check('換股票後場次拉回最新一場',
    (await page.locator('#call-select').inputValue()) === '0');

  // ---------- 4. 輸入名稱反查 ----------
  console.log('\n[4] 名稱反查代號');
  await page.fill('#stock-code', '聯發科');
  check('聯發科 → 2454', (await text('.readout-code')) === '2454');
  await page.fill('#stock-code', '台積');
  check('片段「台積」→ 2330', (await text('.readout-code')) === '2330');

  // ---------- 5. 查無代號 ----------
  console.log('\n[5] 查無代號');
  await page.fill('#stock-code', '0000');
  check('顯示查無此代號', await page.locator('.readout-miss').isVisible());
  check('查無代號不會產生 JS 錯誤', errors.length === 0, errors.join(' | '));

  // ---------- 6. datalist ----------
  console.log('\n[6] datalist 動態填充');
  await page.fill('#stock-code', '23');
  const dl = await page.locator('#stock-codes option').count();
  check('有結果且不超過 50 筆', dl > 0 && dl <= 50, 'count=' + dl);

  // ---------- 7. 沒有場次的股票 ----------
  console.log('\n[7] 沒有法說會紀錄的股票');
  const noCall = await page.evaluate(() =>
    Object.keys(window.STOCKS.items).find(c => !(window.EARNINGS.calls[c] || []).length));
  if (noCall) {
    await page.goto(BASE + '?stock=' + noCall, { waitUntil: 'networkidle' });
    check('顯示空狀態', await page.locator('.empty-title').isVisible());
    check('空狀態有經濟日報外連',
      (await page.locator('.empty a.linkbtn').first().getAttribute('href')).includes('money.udn.com'));
    check('場次下拉停用', await page.locator('#call-select').isDisabled());
  } else {
    console.log('  （資料裡每檔都有場次，跳過）');
  }

  // ---------- 8. 網址參數 / localStorage ----------
  console.log('\n[8] 網址參數與記憶');
  await page.goto(BASE + '?stock=2454', { waitUntil: 'networkidle' });
  check('?stock=2454 直接選中聯發科', (await text('.readout-name')) === '聯發科');
  await page.goto(BASE, { waitUntil: 'networkidle' });
  check('重開記得上次選的股票', (await text('.readout-code')) === '2454');

  // ---------- 9. 主題 ----------
  console.log('\n[9] 深淺色主題');
  const before = await page.getAttribute('html', 'data-theme');
  await page.click('#theme-btn');
  const after = await page.getAttribute('html', 'data-theme');
  check('主題有切換', before !== after, before + ' → ' + after);
  await page.reload({ waitUntil: 'networkidle' });
  check('主題有記住', (await page.getAttribute('html', 'data-theme')) === after);

  // ---------- 10. 財報體質 ----------
  console.log('\n[10] 財報體質（三率）');
  const finCode = await page.evaluate(() =>
    Object.keys((window.FINANCIALS || { items: {} }).items || {})[0]);
  if (finCode) {
    await page.goto(BASE + '?stock=' + finCode, { waitUntil: 'networkidle' });
    check('三率區塊有顯示', await page.locator('#fin-block').isVisible());
    const fin = await page.evaluate(c => window.FINANCIALS.items[c], finCode);
    if (fin.applicable === false) {
      check('不適用的產業有說明', (await text('#fin-body')).includes('不適用'));
      check('不適用時不顯示三率數字', (await page.locator('#fin-body .stat').count()) === 0);
    } else {
      check('三個率各一塊', (await page.locator('#fin-body .stat').count()) === 3);
      check('數值是百分比', (await page.locator('#fin-body .stat-value').first().textContent()).includes('%'));
      if (fin.basePeriod) {
        check('有標示比較基期',
          /去年同季|上一季/.test(await text('.fin-basis')), await text('.fin-basis'));
        check('升降有標 pp',
          (await page.locator('#fin-body .stat-delta').first().textContent()).includes('pp'));
      } else {
        check('沒有基期時說明原因', (await text('.fin-basis')).includes('基期'));
        check('沒有基期時不給判斷結論', (await page.locator('.verdict-badge').count()) === 0);
      }
    }
  }
  const noFin = await page.evaluate(() =>
    Object.keys(window.STOCKS.items).find(c => !((window.FINANCIALS || { items: {} }).items || {})[c]));
  if (noFin) {
    await page.goto(BASE + '?stock=' + noFin, { waitUntil: 'networkidle' });
    check('沒有財報資料時整個區塊隱藏', await page.locator('#fin-block').isHidden());
  }

  // ---------- 11. 未來方向（AI 摘要） ----------
  // 摘要要花錢才產得出來，repo 裡不放假資料。所以這裡注入一份合成的
  // window.SUMMARIES 再重新渲染，測的是「渲染邏輯」而不是「資料內容」。
  console.log('\n[11] 未來方向（AI 摘要）');
  const target = await page.evaluate(() => {
    const calls = window.EARNINGS.calls || {};
    for (const code of window.EARNINGS.featured || []) {
      const first = (calls[code] || [])[0];
      if (first && (first.news || []).length >= 2) return { code, date: first.date };
    }
    return null;
  });

  if (target) {
    await page.goto(BASE + '?stock=' + target.code, { waitUntil: 'networkidle' });
    const injected = await page.evaluate(t => {
      window.SUMMARIES = {
        updated: 'test',
        items: {
          [t.code]: {
            [t.date]: {
              confidence: 'high',
              outlook: [
                { text: '下一季產能利用率預期回升', tag: '下季展望', src: 0 },
                { text: '新製程明年進入量產', tag: '新產品', src: 1 },
              ],
            },
          },
        },
      };
      document.getElementById('call-select').dispatchEvent(new Event('change'));
      const call = window.EARNINGS.calls[t.code][0];
      return { url0: call.news[0].url, url1: call.news[1].url };
    }, target);

    check('有摘要時卡片出現', await page.locator('#outlook-card').isVisible());
    check('條目數量正確', (await page.locator('#outlook-card .outlook li').count()) === 2);
    check('條目文字有出來',
      (await text('#outlook-card .outlook li:first-child .outlook-text')) === '下一季產能利用率預期回升');
    check('有分類標籤',
      (await text('#outlook-card .outlook li:first-child .outlook-tag')) === '下季展望');
    check('第 1 條連回 news[0]',
      (await page.locator('#outlook-card .outlook-src').first().getAttribute('href')) === injected.url0);
    check('第 2 條連回 news[1]',
      (await page.locator('#outlook-card .outlook-src').nth(1).getAttribute('href')) === injected.url1);
    check('來源連結開新分頁且有 noopener',
      (await page.locator('#outlook-card .outlook-src').first().getAttribute('rel')) === 'noopener noreferrer');
    check('有標明是 AI 整理', (await text('#outlook-card .ai-badge')).includes('AI'));
    check('有免責說明', /不是公司原文/.test(await text('#outlook-card .ai-disclaimer')));
    check('排在相關報導前面', await page.evaluate(() => {
      const card = document.getElementById('outlook-card');
      const news = document.querySelector('.newslist');
      return !!card && !!news &&
        (card.compareDocumentPosition(news) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
    }));

    // 沒有摘要的場次 → 整張卡不該存在
    await page.evaluate(() => {
      window.SUMMARIES = { items: {} };
      document.getElementById('call-select').dispatchEvent(new Event('change'));
    });
    check('沒有摘要時整張卡不顯示', (await page.locator('#outlook-card').count()) === 0);
    check('摘要渲染沒有 JS 錯誤', errors.length === 0, errors.join(' | '));
  } else {
    console.log('  （資料裡找不到有 2 則以上報導的場次，跳過）');
  }

  // ---------- 11.5 官方簡報連結 ----------
  // deck 存的是檔名，網址由前端依 META.deck 組。抓取端還沒驗出 GET/POST
  // 哪個能用之前資料是空的，所以這裡一樣用注入的方式測兩條渲染路徑。
  console.log('\n[11.5] 官方簡報連結');
  if (target) {
    const deckCheck = await page.evaluate(t => {
      const call = window.EARNINGS.calls[t.code][0];
      const orig = { deck: call.deck, meta: window.EARNINGS.meta.deck };
      const render = () => document.getElementById('call-select').dispatchEvent(new Event('change'));

      call.deck = '121620260810M001.pdf';

      window.EARNINGS.meta.deck = {
        base: 'https://mopsov.twse.com.tw/server-java/FileDownLoad',
        method: 'get',
        params: { step: '9', filePath: '/home/html/nas/STR/', functionName: 't100sb02_1' },
      };
      render();
      const a = document.querySelector('.linkrow a.linkbtn.primary');
      const getHref = a ? a.getAttribute('href') : null;

      window.EARNINGS.meta.deck.method = 'post';
      render();
      const form = document.querySelector('form.deckform');
      const post = form ? {
        action: form.getAttribute('action'),
        method: form.getAttribute('method'),
        target: form.getAttribute('target'),
        fileName: (form.querySelector('input[name=fileName]') || {}).value,
        hidden: form.querySelectorAll('input[type=hidden]').length,
        hasButton: !!form.querySelector('button.linkbtn'),
      } : null;

      window.EARNINGS.meta.deck = null;
      render();
      const none = document.querySelectorAll('.linkrow a.linkbtn.primary, form.deckform').length;

      call.deck = orig.deck;
      window.EARNINGS.meta.deck = orig.meta;
      render();
      return { getHref, post, none };
    }, target);

    check('GET 模式畫成一般連結',
      (deckCheck.getHref || '').startsWith(
        'https://mopsov.twse.com.tw/server-java/FileDownLoad?'), deckCheck.getHref);
    check('GET 網址帶了檔名與固定參數',
      /fileName=121620260810M001\.pdf/.test(deckCheck.getHref || '') &&
      /functionName=t100sb02_1/.test(deckCheck.getHref || ''), deckCheck.getHref);
    check('POST 模式畫成表單', !!deckCheck.post);
    check('表單送到正確端點且開新分頁',
      deckCheck.post && deckCheck.post.method === 'post' && deckCheck.post.target === '_blank' &&
      deckCheck.post.action.endsWith('/server-java/FileDownLoad'), JSON.stringify(deckCheck.post));
    check('表單帶齊 4 個欄位（3 固定 + 檔名）',
      deckCheck.post && deckCheck.post.hidden === 4 &&
      deckCheck.post.fileName === '121620260810M001.pdf', JSON.stringify(deckCheck.post));
    check('沒有 meta.deck 時不畫任何簡報按鈕', deckCheck.none === 0, String(deckCheck.none));
    check('簡報渲染沒有 JS 錯誤', errors.length === 0, errors.join(' | '));
  }

  // ---------- 12. 響應式 ----------
  console.log('\n[12] 響應式');
  await page.setViewportSize({ width: 360, height: 740 });
  await page.goto(BASE, { waitUntil: 'networkidle' });
  const overflow = await page.evaluate(() =>
    document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check('360px 寬不橫向捲動', overflow <= 0, 'overflow=' + overflow);

  // ---------- 13. 零外部請求 ----------
  console.log('\n[13] 零外部請求');
  const external = requests.filter(u => !u.startsWith(BASE) && !u.startsWith('data:'));
  check('沒有任何外部請求', external.length === 0, external.slice(0, 3).join(', '));

  console.log('\n' + (fail === 0 ? '✅ 全部通過' : '❌ 有失敗') + `  (pass=${pass}, fail=${fail})`);
  await browser.close();
  process.exit(fail === 0 ? 0 : 1);
})();
