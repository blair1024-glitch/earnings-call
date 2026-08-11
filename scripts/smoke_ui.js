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

  // ---------- 11. 響應式 ----------
  console.log('\n[11] 響應式');
  await page.setViewportSize({ width: 360, height: 740 });
  await page.goto(BASE, { waitUntil: 'networkidle' });
  const overflow = await page.evaluate(() =>
    document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check('360px 寬不橫向捲動', overflow <= 0, 'overflow=' + overflow);

  // ---------- 12. 零外部請求 ----------
  console.log('\n[12] 零外部請求');
  const external = requests.filter(u => !u.startsWith(BASE) && !u.startsWith('data:'));
  check('沒有任何外部請求', external.length === 0, external.slice(0, 3).join(', '));

  console.log('\n' + (fail === 0 ? '✅ 全部通過' : '❌ 有失敗') + `  (pass=${pass}, fail=${fail})`);
  await browser.close();
  process.exit(fail === 0 ? 0 : 1);
})();
