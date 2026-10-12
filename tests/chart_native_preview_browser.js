const assert = require('node:assert/strict');
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
    await page.goto('http://127.0.0.1:5000/charts');
    await page.waitForFunction(() => panes[0]?.historyLoaded);
    const payload = await page.evaluate(async () => {
      const response = await fetch('/api/backtest/run-custom', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source: 'bybit', symbol: 'BTCUSDT', timeframe: '15m',
          executionTimeframe: '5m', period: '21d', limit: 2001,
          strategy: 'SimpleAtrTrendV2', params: {}, feePct: 0, slippagePct: 0,
          saveHistory: false, includeFullTrades: true })
      });
      if (!response.ok) throw new Error(await response.text());
      const payload = await response.json();
      const pane = panes[0];
      pane.strategyPayload = payload;
      pane.presetSelect.value = config.chart_signal_strategies.find(item => item.strategy === 'SimpleAtrTrendV2').id;
      openSignalDetails(pane);
      return payload;
    });
    for (const field of ['total_return_pct', 'number_of_trades', 'win_rate', 'average_win',
      'average_loss', 'max_drawdown', 'profit_factor', 'average_bars_held']) {
      assert.equal(typeof payload[field], 'number', field);
    }
    assert.equal(payload.number_of_trades, payload.tradeCount);
    assert.ok(!await page.locator('#backtest-content').innerText().then(text => text.includes('undefined')));
    await page.evaluate(() => {
      const pane = panes[0];
      restoreIndicators(pane, ['rsi', 'volume']);
      startPane(pane);
    });
    await page.waitForFunction(() => panes[0].indicatorCharts.length === 2);
    await page.waitForTimeout(500);
    await page.evaluate(() => {
      const pane = panes[0];
      pane.chart.priceScale('right').applyOptions({ minimumWidth: 150 });
      pane.chart.timeScale().setVisibleLogicalRange({ from: pane.candles.length - 180, to: pane.candles.length + 10 });
      syncIndicatorTimeRanges(pane);
    });
    await page.waitForTimeout(500);
    const alignment = await page.evaluate(() => {
      const pane = panes[0], n = pane.candles.length;
      const panels = [{ chart: pane.chart, host: pane.chartEl },
        ...pane.indicatorCharts.map(item => ({ chart: item.chart, host: item.element.querySelector('.indicator-chart') }))];
      return pane.candles.slice(n - 150, n - 10).filter((_, index) => index % 20 === 0)
        .map(row => panels.map(({ chart, host }) => host.getBoundingClientRect().left + chart.timeScale().timeToCoordinate(row.time)));
    });
    for (const points of alignment) assert.ok(Math.max(...points) - Math.min(...points) < 2, JSON.stringify(points));
    console.log('Native FLAT preview metrics and widened-axis study alignment passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
