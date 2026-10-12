const assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{const browser=await chromium.launch({channel:'msedge',headless:true});try{
 const page=await browser.newPage({viewport:{width:1600,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:5000/charts');await page.waitForFunction(()=>panes[0]?.historyLoaded);
 assert.equal(await page.locator('.timeframe-shortcuts [data-timeframe="1s"]').count(),1);
 await page.locator('.timeframe-shortcuts [data-timeframe="1s"]').click();
 await page.waitForFunction(()=>panes[0]?.historyLoaded&&panes[0].timeframeSelect.value==='1s'&&panes[0].candleDiagnostics.liveOnly,{},{timeout:60000});
 assert.equal(await page.evaluate(()=>panes[0].chart.timeScale().options().secondsVisible),true);
 assert.equal(await page.evaluate(()=>panes[0].olderHistoryExhausted),true);
 const units=await page.evaluate(()=>[timeframeMinutes('1s'),timeframeSeconds('1s')]);assert.deepEqual(units,[1/60,1]);
 const merged=await page.evaluate(()=>{const pane={candles:[]},seen=new Set();for(const t of [{i:'a',T:1100,p:'100',v:'1'},{i:'b',T:1200,p:'103',v:'2'},{i:'b',T:1200,p:'103',v:'2'},{i:'c',T:3100,p:'99',v:'4'}])ChartSecondCandles.applyTrade(pane,t,seen);return pane.candles;});
 assert.deepEqual(merged.map(row=>row.time),[1,3]);assert.equal(merged[0].volume,3);
 await page.waitForTimeout(2500);assert.ok(await page.evaluate(()=>panes[0].candles.length>0));
 await page.screenshot({path:'reports/one-second-chart-20261008.png'});
 await page.evaluate(()=>openBacktestControls(panes[0]));assert.match(await page.locator('#backtest-content').innerText(),/limited trade-built history/);
 assert.deepEqual(errors,[]);console.log('1s header, live Bybit trade bars, deduplication, no fabricated empty seconds, seconds axis and backtest boundary passed');
}finally{await browser.close();}})().catch(error=>{console.error(error);process.exitCode=1;});
