const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const {runtime,source}=require('../cli/tactic_source');
let output;
const context={self:{postMessage:value=>output=value},importScripts:()=>{}};vm.createContext(context);vm.runInContext(runtime(),context);vm.runInContext(fs.readFileSync('static/custom-tactic-worker.js','utf8'),context);
const candles=Array.from({length:600},(_,i)=>({time:1700000000+i*3600,open:100+i*.02,close:101+i*.02+Math.sin(i/6),high:103+i*.02,low:98+i*.02,volume:100+i%23}));
const asOf=candles.at(-1).time+3600;
context.self.onmessage({data:{code:source('ZGUA').code,options:{candles,interval:'1h',assessmentOnly:true,assessmentAsOf:asOf,executionCandles:[],divergenceEvents:[]}}});
assert.ok(!output.error,output.error);const result=JSON.parse(output.result).result;assert.equal(result.marketAssessment.version,'market-assessment-v1');assert.ok(Number.isFinite(result.marketAssessment.score));assert.equal(result.marketAssessment.tacticSetup.passed,false);assert.deepEqual(result.tradeList,[]);
console.log('ZGUA display-only bias assessment works without simulating trades; missing divergence is not a buy');
