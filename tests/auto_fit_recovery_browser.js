const assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{const browser=await chromium.launch({channel:'msedge',headless:true});try{
 const page=await browser.newPage({viewport:{width:1500,height:950}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:5000/charts');await page.waitForFunction(()=>panes[0]?.historyLoaded);
 await page.evaluate(async()=>{const p=panes[0];p.symbolSelect.value='ETHUSDT';p.timeframeSelect.value='1m';restoreIndicators(p,['rsi','zgua']);await startPane(p);p.ws?.close();if(p.pollTimer)clearInterval(p.pollTimer);});await page.waitForTimeout(300);
 const data=await page.evaluate(()=>{const p=panes[0];return {count:p.candles.length,maxGap:Math.max(...p.candles.slice(1).map((c,i)=>c.time-p.candles[i].time))};});assert.ok(data.count>=1900,JSON.stringify(data));assert.equal(data.maxGap,60);
 for(let i=0;i<4;i++){
  await page.evaluate(()=>{const p=panes[0];p.chart.priceScale('right').setVisibleRange({from:1,to:2});freezeLivePriceScale(p);p.node.querySelector('.chart-auto-button').click();const last=p.candles.at(-1),bar={...last,time:last.time+60,high:last.high+.1};mergeLiveCandle(p,bar);freezeLivePriceScale(p);p.series.update(bar);});
  await page.waitForTimeout(150);await page.evaluate(()=>renderIndicators(panes[0],panes[0].requestId));await page.waitForTimeout(250);
  const view=await page.evaluate(()=>{const p=panes[0],last=p.candles.at(-1);return {range:p.chart.priceScale('right').getVisibleRange(),y:p.series.priceToCoordinate(last.close),height:p.chartEl.clientHeight,logical:p.chart.timeScale().getVisibleLogicalRange()};});assert.ok(view.range.from>1000&&view.range.to<10000,JSON.stringify(view));assert.ok(view.y>=0&&view.y<view.height,JSON.stringify(view));
 }
 await page.screenshot({path:'reports/eth-1m-auto-fit-20261012.png'});assert.deepEqual(errors,[]);console.log('ETH 1m history is continuous; four Auto Fit/live-update races recover visible candles with RSI and Zgua.');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
