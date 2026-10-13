const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
function load(name, context) {
  const start = new RegExp('^(?:async )?function '+name+'\\(', 'm').exec(source).index;
  const rest = source.slice(start), next = /\n(?:async )?function /.exec(rest);
  vm.runInContext(next ? rest.slice(0,next.index) : rest, context);
}
(async()=>{
  for (const timeframe of ['2m','6m','12m']) {
    let timer, updates=0, studies=0, options, restored;
    const first={time:100,open:10,high:12,low:9,close:11,volume:2};
    let next={...first,close:12,volume:3};
    const context=vm.createContext({freezeLivePriceScale:()=>{},setTimeout:fn=>{timer=fn;return 1;},Date,loadCandles:async(_,opts)=>{options=opts;return {candles:[next]};},renderIndicators:async()=>studies++,updateTicker:()=>{},updatePresentationSeries:()=>{}});
    load('startBinancePolling',context);
    const priceRange={from:8,to:15};let restoredPrice;
    const range={from:3,to:90}, pane={requestId:1,candles:[first],timeframeSelect:{value:timeframe},chart:{priceScale:()=>({getVisibleRange:()=>priceRange,setVisibleRange:value=>restoredPrice=value}),timeScale:()=>({getVisibleLogicalRange:()=>range,setVisibleLogicalRange:value=>restored=value})},series:{setData:()=>updates++}};
    context.startBinancePolling(pane);await timer();
    assert.equal(options.limit,50);assert.equal(options.liveTail,true);assert.equal(updates,1);assert.equal(studies,1);assert.deepEqual(restored,range);
    assert.deepEqual(restoredPrice,priceRange,'Live refresh must preserve vertical range');
    await timer();assert.equal(updates,1);assert.equal(studies,1,'Unchanged data must not reload studies');
    pane.replayActive=true;next={...next,close:13};await timer();assert.equal(updates,1,'Replay stays frozen');
  }
  console.log('2m/6m/12m polling: fresh tails, lower-study refresh, fixed viewport, unchanged-data and replay guards passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
