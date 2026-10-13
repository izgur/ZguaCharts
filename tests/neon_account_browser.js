const assert = require('node:assert/strict');
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const first = await browser.newContext({ viewport: { width: 1500, height: 950 } });
    const page = await first.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    async function login(target) {
      await target.goto('http://127.0.0.1:5011/charts');
      await target.waitForFunction(() => window.ChartAccounts?.ready);
      await target.evaluate(() => ChartAccounts.ready);
      await target.locator('#account-button').click();
      await target.getByRole('menuitem', { name: 'Sign in', exact: true }).click();
      await target.locator('.account-form [name="username"]').fill('browsercheck');
      await target.locator('.account-form [name="password"]').fill(process.env.ZGUA_BROWSER_TEST_PASSWORD);
      await Promise.all([
        target.waitForNavigation(),
        target.locator('.account-form .account-primary').click(),
      ]);
      await target.evaluate(() => ChartAccounts.ready);
      assert.equal(await target.evaluate(() => ChartAccounts.isSignedIn()), true);
      await target.waitForFunction(() => typeof panes !== 'undefined' && panes[0]?.historyLoaded, null, { timeout: 90000 });
    }
    await login(page);
    await page.locator('#account-button').click();
    await page.getByRole('menuitem', { name: 'Admin dashboard', exact: true }).waitFor();
    await page.getByRole('menuitem', { name: 'Appearance', exact: true }).click();
    const saved = page.waitForResponse(response => response.url().endsWith('/api/account/profile') && response.request().method() === 'PUT' && response.status() === 200);
    await page.getByLabel('Header font size', { exact: true }).fill('13');
    await saved;
    await page.getByRole('button', { name: 'Close', exact: true }).click();
    const second = await browser.newContext({ viewport: { width: 1500, height: 950 } });
    const another = await second.newPage();
    another.on('pageerror', error => errors.push(error.message));
    await login(another);
    assert.equal(await another.evaluate(() => ChartTypography.get('headerFontSize')), 13);
    await another.screenshot({ path: 'reports/neon-account-production-check-20261013.png' });
    await another.locator('#account-button').click();
    await another.getByRole('menuitem', { name: 'Appearance', exact: true }).click();
    const changed = another.waitForResponse(response => response.url().endsWith('/api/account/profile') && response.request().method() === 'PUT' && response.status() === 200);
    await another.getByLabel('Header font size', { exact: true }).fill('14');
    await changed;
    await another.getByRole('button', { name: 'Close', exact: true }).click();
    await page.locator('#account-button').click();
    await page.getByRole('menuitem', { name: 'Sign out', exact: true }).click();
    await page.waitForFunction(() => window.ChartAccounts && !ChartAccounts.isSignedIn());
    assert.equal(await another.evaluate(() => ChartAccounts.isSignedIn()), true);
    await another.locator('#account-button').click();
    await another.getByRole('menuitem', { name: 'Sign out', exact: true }).click();
    await another.waitForFunction(() => window.ChartAccounts && !ChartAccounts.isSignedIn());
    assert.deepEqual(errors, []);
    console.log('Browser login and persisted preferences passed in two independent contexts.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
