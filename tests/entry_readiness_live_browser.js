const assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{const browser=await chromium.launch({channel:'msedge',headless:true});try{
  const page=await browser.newPage({viewport:{width:1600,height:1000}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto('http://127.0.0.1:5000/charts');
  await page.waitForFunction(()=>panes[0]?.historyLoaded);
  await page.evaluate(async()=>{const pane=panes[0];pane.presetSelect.value=config.chart_signal_strategies.find(row=>row.strategy==='RelativeStrengthV2').id;pane.presetSelect.dispatchEvent(new Event('change'));await refreshHeaderSignalStrip();});
  await page.waitForFunction(()=>panes[0].entryReadiness?.rows.length===3,{},{timeout:60000});
  const rows=await page.evaluate(()=>panes[0].entryReadiness.rows);
  for(const row of rows){assert.ok(!row.closedAt||row.closedAt<=Date.now()/1000);assert.ok(row.score===null||(row.score>=0&&row.score<=100));if(row.status==='ENTER'){assert.ok(row.entryScore>=75);assert.equal(row.blockers.length,0);assert.equal(row.executionCovered,true);}}
  await page.screenshot({path:'reports/entry-readiness-live-20261008.png'});
  await page.setViewportSize({width:390,height:844});
  await page.locator('.active-chart > .pane-toolbar').evaluate(node=>{node.scrollLeft=node.scrollWidth;});
  await page.screenshot({path:'reports/entry-readiness-live-mobile-20261008.png'});
  assert.deepEqual(errors,[]);console.log(JSON.stringify({liveClosedBarReadiness:rows.map(({timeframe,status,score,blockers})=>({timeframe,status,score,blockers}))}));
}finally{await browser.close();}})().catch(error=>{console.error(error);process.exitCode=1;});
