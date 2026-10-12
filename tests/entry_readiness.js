const assert = require('node:assert/strict');
const engine = require('../core/backtest/regimeTrend');
const options = {strategy:'SimpleAtrTrendV2', interval:'1h', executionInterval:'5m',
  params:{regimeMode:'noRegime', volumeFilter:false, useRsiFilter:false, takerFeePct:0.055,slippageBps:2,maxPositionPct:100,maxNotional:10000},
  candles:Array.from({length:260},(_,i)=>({time:1700000000+i*3600,open:100+i*.3,high:102+i*.3,low:98+i*.3,close:101+i*.3,volume:100})),
  executionCandles:Array.from({length:260*12},(_,i)=>({time:1700000000+i*300,open:100+i*.025,high:102+i*.025,low:98+i*.025,close:101+i*.025,volume:10}))};
const normal = engine.run(options), observed = engine.run({...options,collectReadiness:true});
assert.deepEqual(normal.tradeList,observed.tradeList);
assert.equal(normal.netReturn,observed.netReturn);
const prefix = engine.run({...options,candles:options.candles.slice(0,240),executionCandles:options.executionCandles.slice(0,240*12),collectReadiness:true});
assert.deepEqual(prefix.entryReadiness.rows,observed.entryReadiness.rows.filter(row=>row.time<options.candles[240].time));
const hook=engine.testHooks.entryReadiness;
const frame=Array.from({length:201},(_,i)=>({...options.candles[i],atr14:2,ema200:100}));
const execution={active:true,interval:'5m',signalInterval:'1h',candles:options.executionCandles};
const params={...engine.defaultParams('SimpleAtrTrendV2'),...options.params};
const flat={position:null,pendingEntry:null,pendingExit:null,cooldown:0,dailyRealizedPnl:{}};
const entry={passed:true,side:'long',blockers:[]};
const trigger={index:200,time:frame[200].time,price:frame[200].close,side:'long'};
const go=hook(frame,200,flat,params,execution,entry,entry,trigger);
assert.equal(go.status,'LONG');assert.ok(go.score>=90&&go.score<=100);
assert.equal(hook(frame,200,flat,params,{...execution,candles:[]},entry,entry,trigger).status,'UNAVAILABLE');
assert.equal(hook(frame,200,flat,params,execution,{...entry,passed:false,blockers:['relativeStrengthFailed']},entry,trigger).status,'WAIT');
assert.equal(hook(frame,200,flat,params,execution,entry,entry,{...trigger,index:199}).status,'LATE');
assert.equal(hook(frame,200,{...flat,position:{}},params,execution,{...entry,passed:false,blockers:['alreadyInPosition']},entry,trigger).status,'HOLD');
assert.equal(hook(frame,200,{...flat,position:{},pendingExit:{}},params,execution,entry,entry,trigger).status,'EXIT');
assert.equal(hook(frame,200,flat,{...params,takerFeePct:20},execution,entry,entry,trigger).status,'WAIT');
assert.equal(hook(frame,10,flat,params,execution,entry,entry,trigger).status,'UNAVAILABLE');
assert.equal(hook(frame,200,flat,params,execution,{...entry,side:'short'},{...entry,side:'short'},trigger).status,'SHORT');
console.log('Readiness: native equivalence, prefix invariance, costs, freshness, coverage, warmup and position states passed');
