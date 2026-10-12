const assert = require('node:assert/strict');
const {chromium} = require('playwright');
(async () => {
 const browser = await chromium.launch({channel:'msedge',headless:true});
 try {
  const page=await browser.newPage({viewport:{width:1600,height:1000}}), errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:5000/charts');
  await page.waitForFunction(()=>panes[0]?.historyLoaded);
  assert.equal(await page.locator('#watchlist-section > summary .watchlist-settings').count(),1);
  await page.locator('.watchlist-settings > summary').click();
  assert.equal(await page.locator('.watchlist-settings').getAttribute('open'),'');
  await page.locator('#watchlist-column-settings .watch-new-list').click();
  await page.locator('.watch-new-list-form input[name="name"]').fill('Blue');
  assert.equal(await page.locator('.watch-new-list-form input[type="color"]').evaluate(node=>node.closest('label')===null),true);
  const colourClicks=await page.locator('.watch-colour-control').evaluate(node=>{let clicks=0;node.querySelector('input').addEventListener('click',()=>clicks++);node.querySelector('span').click();return clicks;});assert.equal(colourClicks,0);
  const fonts=await page.locator('#watchlist-column-settings label').evaluateAll(rows=>rows.map(row=>getComputedStyle(row).fontSize));
  assert.equal(new Set(fonts).size,1);
  await page.locator('#header-logo').count().then(async count=>count ? page.locator('#header-logo').click() : page.mouse.click(500,550));
  assert.equal(await page.locator('.watchlist-settings').getAttribute('open'),null);
  await page.evaluate(()=>{
   const pane=panes[0]; pane.strategySummary={status:'ready',strategy:'Test',symbol:'BTCUSDT',timeframe:'1h',executionTimeframe:'5m',completedTrades:1,winRate:100,totalReturnPct:2,tradingDays:20,historyLabel:'Test',checkedAt:Date.now(),trades:[{entryTime:1700000000,exitTime:1700003600,returnPct:2,priceMovePct:4,entryPrice:100,exitPrice:104}]};renderWatchlist();
  });
  assert.match(await page.locator('[data-trade-column="money"]').last().innerText(),/1,?020\.00€/);
  assert.equal(await page.locator('td[data-trade-column="entryPrice"]').innerText(),'100');
  await page.locator('.table-settings > summary').click();
  await page.locator('.table-settings input[value="exitPrice"]').uncheck();
  await page.waitForFunction(()=>document.querySelector('td[data-trade-column="exitPrice"]').hidden);
  await page.mouse.click(500,550);
  assert.equal(await page.locator('.table-settings').getAttribute('open'),null);
  await page.locator('.chart-style-control > summary').first().click();
  await page.mouse.click(500,550);
  assert.equal(await page.locator('.chart-style-control').first().getAttribute('open'),null);
  await page.evaluate(async()=>{const pane=panes[0];restoreIndicators(pane,['rsi','macd','volume','atr']);await renderIndicators(pane,pane.requestId);pane.indicatorPanesEl.style.height='160px';pane.chart.timeScale().setVisibleLogicalRange({from:pane.candles.length-150,to:pane.candles.length+5});syncIndicatorTimeRanges(pane);});
  await page.waitForTimeout(500);
  const alignment=await page.evaluate(()=>{const pane=panes[0], points=[pane.candles.at(-120),pane.candles.at(-60),pane.candles.at(-5)], panels=[{chart:pane.chart,host:pane.chartEl},...pane.indicatorCharts.map(item=>({chart:item.chart,host:item.element.querySelector('.indicator-chart')}))];return {scroll:getComputedStyle(pane.indicatorPanesEl).scrollbarWidth,points:points.map(row=>panels.map(({chart,host})=>host.getBoundingClientRect().left+chart.timeScale().timeToCoordinate(row.time)))};});
  assert.equal(alignment.scroll,'none');
  for(const points of alignment.points) assert.ok(Math.max(...points)-Math.min(...points)<2,JSON.stringify(points));
  await page.locator('.watchlist-settings > summary').click();
  await page.screenshot({path:'reports/sidebar-settings-20261007.png'});
  assert.deepEqual(errors,[]);
  console.log('Header settings, consistent controls, outside dismissal, trade money/prices, saved columns and overflowing-study alignment passed');
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
