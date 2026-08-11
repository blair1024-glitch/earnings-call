const { chromium } = require('playwright');

const BASE = 'http://127.0.0.1:8899/';
let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log('  ✓ ' + name); }
  else { fail++; console.log('  ✗ ' + name + (extra ? '  → ' + extra : '')); }
}

(async () => {
  const browser = await chromium.launch({
    executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome'
  });
  const errors = [];
  const requests = [];
  const page = await browser.newPage();
  page.on('pageerror', e => errors.push(String(e)));
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
  page.on('request', r => requests.push(r.url()));

  // ---------- 1. 預設載入 ----------
  console.log('\n[1] 預設載入');
  await page.goto(BASE, { waitUntil: 'networkidle' });
  check('無 JS 錯誤', errors.length === 0, errors.join(' | '));
  check('範例資料橫幅有顯示', await page.locator('#fixture-banner').isVisible());
  const code0 = await page.locator('.readout-code').textContent();
  const name0 = await page.locator('.readout-name').textContent();
  check('預設選到 featured[0] = 2330', code0 === '2330', code0);
  check('同步顯示名稱 = 台積電', name0 === '台積電', name0);
  const callOpts = await page.locator('#call-select option').allTextContents();
  check('場次下拉有 2 場', callOpts.length === 2, JSON.stringify(callOpts));
  check('場次預設在第一場(最新)', await page.locator('#call-select').inputValue() === '0');
  check('內容區顯示第一場', (await page.locator('.call-title').textContent()).includes('第一場'));

  // ---------- 2. 切換場次 ----------
  console.log('\n[2] 切換場次');
  await page.selectOption('#call-select', '1');
  check('切到第二場', (await page.locator('.call-title').textContent()).includes('第二場'));
  check('網址帶上 call 參數', page.url().includes('call=fixture-a2'), page.url());

  // ---------- 3. 輸入代號 ----------
  console.log('\n[3] 自行輸入代號');
  await page.fill('#stock-code', '2454');
  check('名稱即時變成聯發科', (await page.locator('.readout-name').textContent()) === '聯發科');
  check('場次下拉重建為 1 場', (await page.locator('#call-select option').count()) === 1);
  check('場次自動回到最新', await page.locator('#call-select').inputValue() === '0');

  // ---------- 4. 輸入名稱反查 ----------
  console.log('\n[4] 輸入名稱反查代號');
  await page.fill('#stock-code', '緯穎');
  check('反查出代號 6669', (await page.locator('.readout-code').textContent()) === '6669');
  check('6669 無場次 → 空狀態', await page.locator('.empty-title').isVisible());
  check('空狀態有經濟日報連結',
    (await page.locator('.empty a.linkbtn').first().getAttribute('href')).includes('money.udn.com'));

  // ---------- 5. 部分名稱 ----------
  console.log('\n[5] 名稱片段唯一命中');
  await page.fill('#stock-code', '台積');
  check('「台積」→ 2330', (await page.locator('.readout-code').textContent()) === '2330');

  // ---------- 6. 查無代號 ----------
  console.log('\n[6] 查無代號');
  await page.fill('#stock-code', '9999');
  check('顯示查無此代號', await page.locator('.readout-miss').isVisible());
  check('內容區維持上一檔不崩', errors.length === 0, errors.join(' | '));

  // ---------- 7. datalist 上限 ----------
  console.log('\n[7] datalist 動態填充');
  await page.fill('#stock-code', '2');
  const dlCount = await page.locator('#stock-codes option').count();
  check('datalist 有結果且 <= 50', dlCount > 0 && dlCount <= 50, 'count=' + dlCount);

  // ---------- 8. 網址參數 ----------
  console.log('\n[8] 網址參數直接開啟');
  await page.goto(BASE + '?stock=2454', { waitUntil: 'networkidle' });
  check('?stock=2454 直接選中聯發科',
    (await page.locator('.readout-name').textContent()) === '聯發科');

  // ---------- 9. localStorage 記憶 ----------
  console.log('\n[9] localStorage 記憶上次選擇');
  await page.goto(BASE, { waitUntil: 'networkidle' });
  check('重開記得 2454', (await page.locator('.readout-code').textContent()) === '2454',
    await page.locator('.readout-code').textContent());

  // ---------- 10. 主題切換 ----------
  console.log('\n[10] 深淺色主題');
  const before = await page.getAttribute('html', 'data-theme');
  await page.click('#theme-btn');
  const after = await page.getAttribute('html', 'data-theme');
  check('主題有切換', before !== after, before + ' → ' + after);
  await page.reload({ waitUntil: 'networkidle' });
  check('主題有記住', (await page.getAttribute('html', 'data-theme')) === after);

  // ---------- 11. 手機寬度不橫捲 ----------
  console.log('\n[11] 響應式');
  await page.setViewportSize({ width: 360, height: 740 });
  await page.goto(BASE, { waitUntil: 'networkidle' });
  const overflow = await page.evaluate(() =>
    document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check('360px 寬不橫向捲動', overflow <= 0, 'overflow=' + overflow);

  // ---------- 12. 零外部請求 ----------
  console.log('\n[12] 零外部請求');
  const external = requests.filter(u => !u.startsWith(BASE) && !u.startsWith('data:'));
  check('沒有任何外部請求', external.length === 0, external.join(', '));

  console.log('\n' + (fail === 0 ? '✅ 全部通過' : '❌ 有失敗') + `  (pass=${pass}, fail=${fail})`);
  await browser.close();
  process.exit(fail === 0 ? 0 : 1);
})();
