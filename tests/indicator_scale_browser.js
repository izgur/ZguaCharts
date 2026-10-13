const assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{const browser=await chromium.launch({channel:'msedge',headless:true});try{
 const page=await browser.newPage({viewport:{width:1500,height:950}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:5000/charts');await page.waitForFunction(()=>panes[0]?.historyLoaded);
 let extreme=false;
 await page.route('**/api/indicators?**',route=>{const candles=route.request().postDataJSON().candles;const data=candles.map((c,i)=>({time:c.time,value:extreme&&i===candles.length-1?100:8+Math.sin(i/4)*2}));return route.fulfill({json:{overlays:[],panes:[{id:'cipherb',title:'Cipher B-style Oscillator',series:[{type:'line',name:'Upper',guide:true,data:candles.map(c=>({time:c.time,value:60}))},{type:'line',name:'Lower',guide:true,data:candles.map(c=>({time:c.time,value:-60}))},{type:'line',name:'WaveTrend',color:'#4dabf7',data}]}],diagnostics:{}}});});
 await page.evaluate(async()=>{const p=panes[0];restoreIndicators(p,['cipherb']);await renderIndicators(p,p.requestId);});await page.waitForTimeout(300);
 const initial=await page.evaluate(()=>panes[0].indicatorCharts[0].chart.priceScale('right').getVisibleRange());assert.ok(initial.to-initial.from<20,JSON.stringify(initial));
 const before=await page.evaluate(()=>{const p=panes[0],last=p.candles.at(-1);p.chart.priceScale('right').setVisibleRange({from:last.close*.9,to:last.close*1.1});p.indicatorCharts[0].chart.priceScale('right').setVisibleRange({from:0,to:20});return {main:p.chart.priceScale('right').getVisibleRange(),lower:p.indicatorCharts[0].chart.priceScale('right').getVisibleRange(),logical:p.chart.timeScale().getVisibleLogicalRange()};});
 extreme=true;
 await page.evaluate(async()=>{const p=panes[0],last=p.candles.at(-1),bar={...last,time:last.time+3600,high:last.close*2,close:last.close*1.8};freezeLivePriceScale(p);mergeLiveCandle(p,bar);p.series.update(bar);await renderIndicators(p,p.requestId);});await page.waitForTimeout(300);
 const after=await page.evaluate(()=>{const p=panes[0];return {main:p.chart.priceScale('right').getVisibleRange(),lower:p.indicatorCharts[0].chart.priceScale('right').getVisibleRange(),logical:p.chart.timeScale().getVisibleLogicalRange()};});assert.deepEqual(after,before);
 await page.getByRole('button',{name:'Fit visible indicator values vertically'}).click();await page.waitForTimeout(200);assert.ok(await page.evaluate(()=>panes[0].indicatorCharts[0].chart.priceScale('right').getVisibleRange().to)>=100);
 await page.screenshot({path:'reports/indicator-scale-20261012.png'});assert.deepEqual(errors,[]);console.log('Visible-data fit ignores guides; main/lower vertical and time ranges survive new candles; explicit fit works.');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
