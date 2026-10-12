const assert=require('node:assert/strict');
const {compare}=require('../cli/research_entry_readiness');
const start=1700000000;
const candles=Array.from({length:1000},(_,i)=>({time:start+i*3600,open:100+i*.03,high:103+i*.03,low:97+i*.03,close:101+i*.03+Math.sin(i/15)*2,volume:100}));
const input={candles,interval:'1h',params:{regimeMode:'noRegime',rsiMin:0,rsiMax:100,rsThreshold:-.01,volumeFilter:false},
  benchmarkCandles:Array.from({length:300},(_,i)=>({time:start-200*14400+i*14400,open:100,high:102,low:99,close:100,volume:100})),
  executionCandles:Array.from({length:12000},(_,i)=>({time:start+i*300,open:100+i*.0025,high:103+i*.0025,low:97+i*.0025,close:101+i*.0025,volume:10}))};
input.regimeCandles=input.benchmarkCandles;
const report=compare(input);
assert.equal(report.training.length,10);assert.equal(report.finalHoldout.evaluated,false);
assert.equal(report.paperOnly,true);assert.equal(report.modeledCosts.maxPositionPct,100);
const changed=compare({...input,candles:input.candles.map((row,i)=>i<800?row:{...row,close:row.close*10})});
assert.deepEqual(report.training,changed.training);
assert.deepEqual(report.chosen,changed.chosen);
assert.deepEqual(report.baseline,changed.baseline);
console.log('Research: frozen training selection, chronological validation, costs, 1x cap and untouched holdout passed');
