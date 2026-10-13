const fs=require('node:fs');
const engine=require('../core/backtest/regimeTrend');
async function candles(symbol,tf,limit){const response=await fetch('http://127.0.0.1:5000/api/candles?'+new URLSearchParams({source:'bybit',symbol,timeframe:tf,limit:String(limit)}),{signal:AbortSignal.timeout(180000)});const data=await response.json();if(!response.ok)throw new Error(data.error);return data.candles;}
(async()=>{
 const regime=await candles('BTCUSDT','4h',1500),rows=[];
 for(const symbol of ['BTCUSDT','ETHUSDT','SOLUSDT']){
  const signal=(await candles(symbol,'1h',1000)).filter(bar=>bar.time+3600<=Date.now()/1000),execution=await candles(symbol,'5m',12500);
  const variants=[['V2',{strategy:'RelativeStrengthV2'}],['Fresh-original',{strategy:'RelativeStrengthFreshV3',params:{freshnessMaxBars:0,freshnessMaxDriftAtr:.5}}],['Fresh-2bars',{strategy:'RelativeStrengthFreshV3',params:{freshnessMaxBars:2,freshnessMaxDriftAtr:.75}}]];
  for(const [name,variant]of variants){const options={...variant,candles:signal,regimeCandles:regime,executionCandles:execution,interval:'1h',executionInterval:'5m',params:{takerFeePct:.055,slippageBps:2,maxPositionPct:100,...variant.params}};const result=engine.run(options);rows.push({symbol,variant:name,trades:result.numberOfTrades??result.tradeList?.length,netReturn:result.netReturn,maxDrawdown:result.maxDrawdown,diagnostics:result.diagnostics});}
  process.stdout.write('Analyzed '+symbol+'\n');
 }
 const report={createdAt:new Date().toISOString(),note:'Diagnostic comparison only; not strategy promotion or an untouched holdout.',rows};fs.writeFileSync('reports/research-campaigns/fresh-v3-audit-20261012.json',JSON.stringify(report,null,2));console.log(JSON.stringify(rows.map(({diagnostics,...row})=>row),null,2));
})().catch(error=>{console.error(error);process.exitCode=1;});
