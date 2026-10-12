const assert = require('node:assert/strict');
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({headless: true, channel: 'msedge'});
  try {
    const page = await browser.newPage({viewport: {width: 1400, height: 900}}), errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto('http://127.0.0.1:5000/charts');
    await page.waitForFunction(() => window.ChartCustomIndicators && typeof config !== 'undefined' && config);
    const rows = Array.from({length:100}, (_, i) => ({time:i+1,open:100+i,high:102+i,low:99+i,close:101+i,volume:100}));
    const result = await page.evaluate(async rows => {
      const source = await (await fetch('/api/indicator-source/rsi')).json();
      return ChartCustomIndicators.execute(source.files[0].code, rows);
    }, rows);
    assert.ok(result.payload.panes.length);
    await page.evaluate(rows => ChartCustomIndicators.configure({candles:()=>rows}), rows);
    await page.locator('.indicator-button:visible').first().click();
    const picker = page.locator('.indicator-menu:visible').first();
    await picker.locator('.indicator-search').fill('rsi');
    await picker.locator('.indicator-picker-row:visible .indicator-source-button').first().click();
    const dialog = page.locator('.indicator-editor');
    await dialog.locator('.source-copy:not([disabled])').waitFor();
    assert.equal(await dialog.locator('.source-files').count(),0);
    const sourceCode = await dialog.locator('textarea').inputValue();
    await dialog.locator('textarea').fill(sourceCode.replace("'length': 14", "'length': 12"));
    await dialog.locator('input').fill('My RSI Test');
    await dialog.locator('.source-save-copy').click();
    assert.equal(await dialog.locator('.source-status').innerText(),'Saving...');
    await dialog.waitFor({state:'detached',timeout:90000});
    const saved = await page.evaluate(()=>ChartCustomIndicators.list());
    assert.equal(saved.length,1); assert.equal(saved[0].label,'My RSI Test');
    assert.equal(saved[0].schema.find(field => field.name === 'length').default,12);
    await page.reload();
    await page.locator('.indicator-button:visible').first().click();
    await picker.locator('[data-category="my"]').click();
    assert.equal(await picker.locator('.indicator-picker-row:visible').count(),1);
    await picker.locator('.indicator-picker-row:visible .indicator-favourite').click();
    await picker.locator('.indicator-picker-row:visible input').check();
    await page.locator('.indicator-pane[data-indicator-id^="custom_"]').first().waitFor({timeout:90000});
    await picker.locator('.indicator-picker-row:visible .indicator-source-button').click();
    await dialog.locator('.source-copy:not([disabled])').waitFor();
    assert.equal(await dialog.locator('input').inputValue(),'My RSI Test');
    await page.screenshot({path:'reports/custom-indicator-editor-20261006.png'});
    await dialog.locator('.source-close').click();
    if (!await picker.isVisible()) await page.locator('.indicator-button:visible').first().click();
    await picker.getByRole('button', {name:'New indicator'}).click();
    await dialog.locator('.source-copy:not([disabled])').waitFor();
    assert.equal(await dialog.locator('input').inputValue(),'My SMA');
    await dialog.locator('.source-close').click();
    const blocked = await page.evaluate(async rows => {
      try { await ChartCustomIndicators.execute("import js\nx = js.XMLHttpRequest.new()\nx.open('GET', '/api/account/session', False)\nx.send()\nDEFAULTS={}\ndef indicator(candles,params=None): return {'overlays':[],'panes':[]}", rows); return false; }
      catch (error) { return /NetworkError|Content Security Policy|Failed to execute.*send/.test(error.message); }
    }, rows);
    assert.equal(blocked,true);
    assert.deepEqual(errors,[]);
    console.log('Custom Python execution, single source editor, save, persistence, My indicators and favourites passed');
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
