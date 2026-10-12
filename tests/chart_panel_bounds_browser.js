const assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{const browser=await chromium.launch({channel:'msedge',headless:true});try{
 const page=await browser.newPage({viewport:{width:1440,height:900}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:5000/charts');await page.waitForFunction(()=>panes[0]?.historyLoaded&&ChartWatchlists.rows().length===50);
 const controls=await page.evaluate(()=>{const pane=panes[0],chart=pane.chartEl.getBoundingClientRect(),f=pane.node.querySelector('.chart-fullscreen-button').getBoundingClientRect();return {buttonRight:f.right,plotRight:chart.right-pane.chart.priceScale('right').width(),order:pane.node.querySelector('.tactic-source-button').nextElementSibling.classList.contains('tactic-visibility-button')};});
 assert.ok(controls.buttonRight<=controls.plotRight-1);assert.equal(controls.order,true);
 await page.evaluate(()=>{panes[0].strategySummary={status:'ready',strategy:'RelativeStrengthV2 - Custom',symbol:'BTCUSDT',timeframe:'1h',executionTimeframe:'1h',completedTrades:4,winRate:0,totalReturnPct:-3.11,tradingDays:22,checkedAt:Date.now(),historyLabel:'2000 candles',trades:Array.from({length:4},(_,i)=>({entryTime:1787300000+i*86400,exitTime:1787380000+i*86400,returnPct:-.59,priceMovePct:-.76}))};renderWatchlist();});
 const fit=await page.locator('.watch-strategy-trades tbody td').evaluateAll(cells=>cells.every(cell=>{const range=document.createRange();range.selectNodeContents(cell);const content=range.getBoundingClientRect(),box=cell.getBoundingClientRect();return content.right<=box.right+1&&content.left>=box.left-1;}));assert.equal(fit,true);
 const chart=await page.locator('.chart').first().boundingBox();
 for(const [x,y] of [[chart.x+65,chart.y+220],[chart.x+chart.width-125,chart.y+25]]){
  await page.locator('.drawing-rail [data-action="notes"]').click();await page.locator('.drawing-tool-menu [data-tool="text"]').click();await page.mouse.click(x,y);
  const panel=page.locator('.drawing-style:visible');await panel.waitFor();await page.waitForTimeout(200);const box=await panel.boundingBox();assert.ok(box.x>=0&&box.x+box.width<=1440&&box.y>=0&&box.y+box.height<=900,JSON.stringify(box));
  if(y>chart.y+200)assert.ok(box.y+box.height<y);
  await page.locator('.drawing-style input[aria-label="Annotation text"]').fill('A useful chart comment');
  await page.screenshot({path:'reports/chart-panel-bounds-20261006.png'});await page.keyboard.press('Escape');
 }
 assert.deepEqual(errors,[]);console.log('F inside plot, source/eye order, 50 major markets, nonoverlapping trade cells and edge-clamped drawing settings passed');
 }finally{await browser.close();}})().catch(error=>{console.error(error);process.exitCode=1;});
