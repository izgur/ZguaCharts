const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('static/app.js','utf8');
const start=source.indexOf('async function postBacktestWhenAvailable(');
const end=source.indexOf('\nfunction ',start+1);
async function run(){
  let calls=0,waiting=0,current=true;
  const busy=Object.assign(new Error('A backtest is already running. Please retry shortly.'),{status:503,retryAfter:0});
  const context=vm.createContext({Date,Error,Promise,setTimeout:fn=>fn(),apiPost:async()=>{if(++calls===1)throw busy;return {ok:true};}});
  vm.runInContext(source.slice(start,end),context);
  assert.equal((await context.postBacktestWhenAvailable({},()=>current,()=>waiting++)).ok,true);
  assert.equal(calls,2);assert.equal(waiting,1);
  context.apiPost=async()=>{calls++;throw busy;};
  await assert.rejects(()=>context.postBacktestWhenAvailable({},()=>current,()=>{current=false;}),/chart changed/);
  context.apiPost=async()=>{throw Object.assign(new Error('Invalid parameters'),{status:400});};
  await assert.rejects(()=>context.postBacktestWhenAvailable({},()=>true,()=>{}),/Invalid parameters/);
  console.log('Busy retries, chart-change cancellation and non-busy errors passed.');
}
run().catch(error=>{console.error(error);process.exitCode=1;});
