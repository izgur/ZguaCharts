const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const {PNG} = require('pngjs');
(async () => {
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1600, height: 1000}}), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://127.0.0.1:5000/charts');
    await page.waitForFunction(() => panes[0]?.historyLoaded);
    assert.ok(await page.evaluate(() => config.indicators.length >= 114 && config.indicators.some(item=>item.id==='zgua')));
    await page.evaluate(async () => {
      const pane = panes[0];
      if (pane.ws) pane.ws.close();
      clearInterval(pane.pollTimer); clearTimeout(pane.strategyPollTimer);
      pane.requestId++; pane.strategyRequestId++;
      pane.candles = Array.from({length: 600}, (_, i) => {
        const close = 100 + i * 0.01 + 8 * Math.sin(i / 22);
        return {time: 1700000000 + i * 3600, open: close - 0.3, high: close + 1, low: close - 1, close, volume: 100+i%23};
      });
      pane.series.setData(pane.candles);
      restoreIndicators(pane, ['ichimoku', 'rsi', 'macd']);
      await renderIndicators(pane, pane.requestId);
      pane.chart.timeScale().setVisibleLogicalRange({from: 420, to: 630});
      syncIndicatorTimeRanges(pane);
    });
    await page.waitForTimeout(600);
    const result = await page.evaluate(() => {
      const pane = panes[0], a = pane.overlaySeries[2].data(), b = pane.overlaySeries[3].data(), lag = pane.overlaySeries[4].data();
      const panels = [{chart: pane.chart, host: pane.chartEl}, ...pane.indicatorCharts.map(item => ({chart: item.chart, host: item.element.querySelector('.indicator-chart')}))];
      const alignment = [450, 480, 520, 570].map(index => panels.map(({chart, host}) => host.getBoundingClientRect().left + chart.timeScale().timeToCoordinate(pane.candles[index].time)));
      return {length: pane.indicatorTimeline.length, futureA: a.at(-1), futureB: b.at(-1), last: pane.candles.at(-1).time,
        lag: lag.find(point => point.time === pane.candles[574].time).value, lastClose: pane.candles.at(-1).close, alignment,
        help: ChartIndicatorHelp.guides.ichimoku};
    });
    assert.equal(result.length, 625);
    assert.ok(Number.isFinite(result.futureA.value) && Number.isFinite(result.futureB.value));
    assert.equal(result.futureA.time, result.last + 25*3600);
    assert.equal(result.lag, result.lastClose);
    assert.match(result.help, /not a forecast/);
    for (const points of result.alignment) assert.ok(Math.max(...points) - Math.min(...points) < 2, JSON.stringify(points));
    const canvas = page.locator('.main-chart-wrap .chart canvas').first();
    const withCloud = PNG.sync.read(await canvas.screenshot());
    await page.evaluate(() => {const pane = panes[0]; pane.overlaySeries[2].applyOptions({visible:false}); pane.overlaySeries[3].applyOptions({visible:false});});
    await page.waitForTimeout(100);
    const withoutCloud = PNG.sync.read(await canvas.screenshot());
    let changed = 0;
    for (let i=0; i<withCloud.data.length; i+=4) {
      if (withCloud.data[i] !== withoutCloud.data[i] || withCloud.data[i+1] !== withoutCloud.data[i+1] || withCloud.data[i+2] !== withoutCloud.data[i+2]) changed++;
    }
    assert.ok(changed > 1000, 'Cloud must visibly render, not just expose metadata: '+changed);
    await page.evaluate(() => {panes[0].overlaySeries[2].applyOptions({visible:true}); panes[0].overlaySeries[3].applyOptions({visible:true});});
    await page.screenshot({path:'reports/extended-indicators-desktop.png'});
    await page.setViewportSize({width: 430, height: 932});
    await page.evaluate(() => {const pane = panes[0]; pane.chart.timeScale().setVisibleLogicalRange({from:540,to:615}); syncIndicatorTimeRanges(pane);});
    await page.waitForTimeout(500);
    await page.screenshot({path:'reports/extended-indicators-mobile.png'});
    await page.evaluate(async () => {
      const pane = panes[0], full = pane.candles.slice();
      pane.replayActive = true;
      pane.replayTimeline = full.map(({time}) => ({time}));
      pane.candles = full.slice(0, 500);
      await renderIndicators(pane, pane.requestId);
    });
    const replay = await page.evaluate(() => {
      const pane = panes[0], a = pane.overlaySeries[2].data(), lag = pane.overlaySeries[4].data();
      const at = (data, index) => data.find(point => point.time === pane.replayTimeline[index].time)?.value;
      return {lastKnown: at(a,524), unseen: at(a,525), lagKnown: at(lag,474),
        lagUnseen: at(lag,475), actual: pane.candles.at(-1).close};
    });
    assert.ok(Number.isFinite(replay.lastKnown));
    assert.equal(replay.unseen, undefined);
    assert.equal(replay.lagKnown, replay.actual);
    assert.equal(replay.lagUnseen, undefined);
    await page.evaluate(() => editIndicatorParameters(panes[0], config.indicators.find(item => item.id === 'ichimoku')));
    await page.locator('.indicator-parameter-popover input[name="displacement"]').fill('10');
    await page.locator('.indicator-parameter-popover button[type="submit"]').click();
    await page.waitForFunction(() => !document.querySelector('.indicator-parameter-popover'));
    assert.equal(await page.evaluate(() => panes[0].overlaySeries[2].data().at(-1).time), 1700000000 + (499+9)*3600);
    assert.deepEqual(errors, []);
    console.log('113-study registration, displaced Ichimoku, visible cloud, study alignment, replay and mobile rendering passed');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode=1;});
