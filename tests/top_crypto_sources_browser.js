const assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{const browser=await chromium.launch({channel:'msedge',headless:true});try{
 const page=await browser.newPage({viewport:{width:1600,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:5000/charts');await page.waitForFunction(()=>panes[0]?.historyLoaded);
 const exchanges=await page.evaluate(()=>[...new Set(Object.entries(config.sources).filter(([id])=>id!=='yfinance').map(([,source])=>source.exchange))]);assert.equal(exchanges.length,10);
 for(const source of ['bitget_spot','bitget_perpetual','mexc_spot']){
  const response=await page.request.get('http://127.0.0.1:5000/api/market/instruments?source='+source,{timeout:90000});assert.equal(response.status(),200);assert.ok((await response.json()).instruments.length>100);
  await page.evaluate(source=>{const p=panes[0];p.presetSelect.value='none';p.sourceSelect.value=source;populateSymbolAndTimeframe(p,{symbol:'BTCUSDT',timeframe:'1h'});startPane(p);},source);
  await page.waitForFunction(source=>panes[0].sourceSelect.value===source&&panes[0].historyLoaded&&panes[0].candles.length>20,source,{timeout:90000});
  assert.ok(await page.evaluate(()=>panes[0].series.data().length>20));
  assert.ok(await page.evaluate(()=>Date.now()/1000-panes[0].candles.at(-1).time<7200),'Newest candle must be fresh, not merely nonempty');
 }
 await page.screenshot({path:'reports/top-crypto-sources-20261011.png'});assert.deepEqual(errors,[]);console.log('Ten exchanges registered; Bitget spot/perpetual and MEXC catalogs and chart candles passed');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
