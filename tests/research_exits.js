const assert = require('node:assert/strict');
const engine = require('../core/backtest/regimeTrend');
const hooks=engine.testHooks;
for(const side of ['long','short']) {
  const sign=side==='long'?1:-1;
  const params={researchExitMode:'staged',takerFeePct:.1,slippagePct:.02,fillModel:'next-open',accountEquity:1000,cooldownBars:0};
  const position={side,entryPrice:100,entryFillPrice:100,entryBasePrice:99.98,entryTime:1700000000,entryFillTime:1700000000,
    entrySignalTime:1700000000,entrySignalPrice:100,entrySignalIndex:0,entryIndex:0,size:10,originalSize:10,notional:1000,
    initialStopDistance:2,initialStop:100-sign*2,trailingStop:100-sign*2,bestClose:100,entryFee:1,entrySlippage:.2,
    researchRealized:{net:0,gross:0,fees:0,slippage:0,weightedExit:0,fills:[]}};
  const state={cash:999,grossPnl:0,totalFees:1,totalSlippage:.2,dailyRealizedPnl:{},trades:[],position};
  const row={time:1700000300,open:100,high:105,low:95,close:100+sign*4,__index:1};
  hooks.researchPartialProfits(state,position,row,params);
  assert.equal(position.size,5);assert.equal(position.researchRealized.fills.length,2);
  assert.deepEqual(position.researchRealized.fills.map(fill=>fill.targetR),[1,2]);
  assert.equal(position.entryFee,.5);assert.ok(Math.abs(position.entrySlippage-.1)<1e-9);
  const cash=state.cash;
  hooks.researchPartialProfits(state,position,row,params);assert.equal(state.cash,cash);
  const stop=position.trailingStop;
  hooks.researchUpdateTrail(position,row,{atr14:2},params);
  assert.ok(sign*(position.trailingStop-stop)>0);
  hooks.closePositionAtFillRow(state,[row],row,100+sign*3,'test',params,{signalIndex:0,signalPrice:100+sign*3,kind:'soft-execution-exit'});
  const trade=state.trades[0];
  assert.equal(trade.size,10);assert.equal(trade.partialExits.length,2);
  assert.ok(Math.abs(trade.netPnl-(state.cash-1000))<.001);
  assert.ok(Math.abs(trade.feePaid-state.totalFees)<.001);
  assert.ok(Math.abs(trade.slippagePaid-state.totalSlippage)<.001);
  assert.ok(Math.abs(Object.values(state.dailyRealizedPnl).reduce((a,b)=>a+b,0)-trade.netPnl)<.001);
  assert.ok(Number.isFinite(trade.givebackR));
  const ambiguousPosition={...position,size:10,originalSize:10,entryFee:1,entrySlippage:.2,bestClose:100,
    trailingStop:100-sign*2,lastExecutionCheckTime:1700000000,executionCursor:0,
    researchTarget1:false,researchTarget2:false,researchRealized:{net:0,gross:0,fees:0,slippage:0,weightedExit:0,fills:[]}};
  const ambiguousState={cash:999,grossPnl:0,totalFees:1,totalSlippage:.2,dailyRealizedPnl:{},trades:[],position:ambiguousPosition};
  const exec={interval:'5m',signalInterval:'15m',candles:[row],diagnostics:{exitsOnExecutionCandles:0,softExitsOnExecutionCandles:0}};
  assert.equal(hooks.closeOnExecutionCandle(ambiguousState,[{...row,atr14:2,ema20:100}],0,params,exec,row.time),true);
  assert.equal(ambiguousState.trades[0].partialExits.length,0,'Existing stop must precede targets on ambiguous bars');
}
assert.throws(()=>engine.run({strategy:'SimpleAtrTrendV2',params:{researchExitMode:'staged'},candles:[]}),/real 5m/);
console.log('Research exits: long/short partial accounting, fees, slippage, daily PnL, target idempotence and trail activation passed');
const start=1700000100;
const candles=Array.from({length:450},(_,i)=>{const price=100+i*.03+Math.sin(i/6)*2;return {time:start+i*900,open:price,high:price+.8,low:price-.8,close:price+.1,volume:100};});
const lower=Array.from({length:1350},(_,i)=>{const price=100+i*.01+Math.sin(i/18)*2;return {time:start+i*300,open:price,high:price+.4,low:price-.4,close:price+.1,volume:10};});
const options={strategy:'SimpleAtrTrendV2',interval:'15m',executionInterval:'5m',candles,executionCandles:lower,
  params:{regimeMode:'noRegime',volumeFilter:false,researchExitMode:'staged',takerFeePct:.055,slippageBps:2,maxPositionPct:100,maxNotional:10000},
  customEntry:(_frame,index)=>({passed:index>=200,side:'long',blockers:[]}),incremental:true};
const all=engine.run(options);
const first=engine.run({...options,candles:candles.slice(0,350),executionCandles:lower.slice(0,1050)});
const resumed=engine.run({...options,resumeState:first.checkpoint});
assert.deepEqual(resumed.checkpoint.state,all.checkpoint.state,'Partial exit state must resume without duplicating cash or fills');
assert.equal(all.tradeList.some(trade=>trade.partialExits.length),true);
console.log('Research partial-exit checkpoint parity passed');
