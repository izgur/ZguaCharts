const assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{const browser=await chromium.launch({channel:'msedge',headless:true});try{
 const page=await browser.newPage({viewport:{width:1600,height:1000}});
 await page.goto('http://127.0.0.1:5000/charts');await page.waitForFunction(()=>panes[0]?.historyLoaded);
 await page.evaluate(()=>{const p=panes[0];p.strategySummary={status:'ready',strategy:'ZGUA',symbol:'BTCUSDT',timeframe:'1d',executionTimeframe:'1d',completedTrades:2,winRate:50,totalReturnPct:1,tradingDays:20,checkedAt:Date.now(),trades:[{entryTime:1700000000,exitTime:1700100000,returnPct:2,priceMovePct:4,entryPrice:100,exitPrice:104},{entryTime:1700200000,exitTime:1700300000,returnPct:-1,priceMovePct:-2,entryPrice:104,exitPrice:102}]};renderWatchlist();});
 const styles=await page.evaluate(()=>{const css=s=>getComputedStyle(document.querySelector(s));return {watch:css('.watchlist-table td'),trade:css('td[data-trade-column="entryTime"]'),date:css('.trade-date'),gap:css('.watch-symbol-cell').paddingRight,gain:[...document.querySelectorAll('.trade-gain td')].map(n=>getComputedStyle(n).color),loss:[...document.querySelectorAll('.trade-loss td')].map(n=>getComputedStyle(n).color)};});
 // Read explicit properties because computed-style objects are not serializable.
 const dimensions=await page.evaluate(()=>{const s=x=>getComputedStyle(document.querySelector(x));return {watchFont:s('.watchlist-table td').fontSize,tradeFont:s('td[data-trade-column="entryTime"]').fontSize,dateFont:s('.trade-date').fontSize,timeWidth:document.querySelector('td[data-trade-column="entryTime"]').getBoundingClientRect().width};});
 assert.equal(dimensions.watchFont,dimensions.tradeFont);assert.equal(dimensions.tradeFont,dimensions.dateFont);assert.ok(dimensions.timeWidth<65);assert.equal(new Set(styles.gain).size,1);assert.equal(new Set(styles.loss).size,1);assert.notEqual(styles.gain[0],styles.loss[0]);
 await page.screenshot({path:'reports/sidebar-trade-style-20261011.png'});
 console.log('Matching fonts, compact dates and all-column gain/loss colours passed');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
