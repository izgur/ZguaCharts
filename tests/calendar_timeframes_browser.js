const assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{const browser=await chromium.launch({channel:'msedge',headless:true});try{
 const page=await browser.newPage({viewport:{width:1500,height:950}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:5000/charts');await page.waitForFunction(()=>panes[0]?.historyLoaded);
 await page.locator('#account-button').click();await page.getByRole('menuitem',{name:'Appearance',exact:true}).click();await page.getByRole('button',{name:'Header timeframes',exact:true}).click();
 for(const value of ['1M','1y']){assert.equal(await page.locator(`.appearance-timeframes input[value="${value}"]`).isChecked(),false);await page.locator(`.appearance-timeframes input[value="${value}"]`).check();}
 await page.getByRole('button',{name:'Close',exact:true}).click();
 for(const value of ['1M','1y']){
  await page.locator(`.timeframe-shortcuts button[data-timeframe="${value}"]`).click();await page.waitForFunction(tf=>panes[0]?.historyLoaded&&panes[0].timeframeSelect.value===tf,value,{timeout:60000});
  const result=await page.evaluate(()=>{const p=panes[0];return {count:p.candles.length,calendar:p.candleDiagnostics.calendarAggregation,dates:p.candles.map(c=>new Date(c.time*1000).toISOString()),last:p.candles.at(-1).time};});assert.ok(result.count>3,JSON.stringify(result));assert.equal(result.calendar,true);assert.ok(result.dates.every(date=>value==='1M'?date.includes('-01T00:00:00'):date.includes('-01-01T00:00:00')));const now=new Date();assert.equal(new Date(result.last*1000).getUTCFullYear(),now.getUTCFullYear());
  if(value==='1M')assert.equal(new Date(result.last*1000).getUTCMonth(),now.getUTCMonth());
  await page.waitForTimeout(300);await page.screenshot({path:`reports/calendar-${value}-20261012.png`});
 }
 assert.deepEqual(errors,[]);console.log('M/Y Appearance toggles, real calendar OHLC and current forming periods render successfully.');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
