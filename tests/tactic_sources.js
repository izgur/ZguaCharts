const assert=require('node:assert/strict');
const vm=require('node:vm');
const {source,runtime}=require('../cli/tactic_source');
const engine=require('../core/backtest/regimeTrend');
const rows=Array.from({length:600},(_,i)=>({time:1700000000+i*3600,open:100+i*.05,high:103+i*.05,low:98+i*.05,close:101+i*.05+Math.sin(i/6),volume:100+i%23}));
const context={self:{}};vm.createContext(context);vm.runInContext(runtime(),context);
for(const name of ['RelativeStrengthV2','RelativeStrengthFreshV3','EmaBounceV2','SimpleAtrTrendV2','PullbackReclaimV2','BreakoutRetestV2']) {
  const exported=source(name), scope={module:{exports:{}}};
  vm.createContext(scope);vm.runInContext(exported.code,scope);
  const options={strategy:name,params:exported.params,candles:rows,regimeCandles:rows,benchmarkCandles:rows,interval:'1h'};
  if(name==='RelativeStrengthFreshV3'){options.executionInterval='5m';options.executionCandles=rows.flatMap(row=>Array.from({length:12},(_,i)=>({...row,time:row.time+i*300})));}
  const expected=engine.run(options), custom=context.self.TacticEngine.run({...options,customEntry:scope.module.exports.entry});
  assert.deepEqual(JSON.parse(JSON.stringify(custom.tradeList)),expected.tradeList);
  assert.equal(custom.netReturn,expected.netReturn);
  assert.ok(!exported.code.includes('function entrySignal('));
}
console.log('Six single-tactic exports and browser engine match native backtesting');
