const assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{const browser=await chromium.launch({channel:'msedge',headless:true});try{
 const page=await browser.newPage({viewport:{width:1500,height:950}}),errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto('http://127.0.0.1:5000/charts');await page.waitForFunction(()=>panes[0]?.historyLoaded);
 await page.evaluate(async()=>{const pane=panes[0];pane.studyViewportHeight=384;pane.indicatorHeights={rsi:128,macd:128};restoreIndicators(pane,['rsi','macd']);await renderIndicators(pane,pane.requestId);});
 await page.waitForTimeout(200);
 const before=await page.evaluate(()=>({lower:panes[0].indicatorPanesEl.clientHeight,main:panes[0].chartEl.clientHeight}));assert.ok(Math.abs(before.lower-256)<3);
 await page.evaluate(async()=>{const pane=panes[0];restoreIndicators(pane,['rsi']);await renderIndicators(pane,pane.requestId);});await page.waitForTimeout(200);
 const after=await page.evaluate(()=>{const pane=panes[0];return {lower:pane.indicatorPanesEl.clientHeight,study:pane.indicatorPanesEl.firstElementChild.clientHeight,main:pane.chartEl.clientHeight,saved:pane.studyViewportHeight};});
 assert.ok(Math.abs(after.lower-128)<3);assert.ok(Math.abs(after.lower-after.study)<3);assert.ok(after.main>=before.main+120);assert.equal(after.saved,384);
 await page.evaluate(()=>{const pane=panes[0];pane.indicatorHeights.rsi=200;pane.studyViewportHeight=200;applyIndicatorHeights(pane);});await page.waitForTimeout(200);assert.ok(Math.abs(await page.evaluate(()=>panes[0].indicatorPanesEl.clientHeight)-200)<3);
 await page.screenshot({path:'reports/single-indicator-viewport-20261009.png'});
 assert.deepEqual(errors,[]);console.log('Lower viewport follows visible study heights: two-to-one removal reclaims chart space, saved heights and manual resizing preserved.');
}finally{await browser.close();}})().catch(error=>{console.error(error);process.exitCode=1;});
