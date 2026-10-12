// Offline, paper-only experiment. Input is a frozen JSON candle dataset, never an exchange order API.
const fs = require('node:fs');
const engine = require('../core/backtest/regimeTrend');
const data = require('../core/data');
function compare(input) {
  const candles = input.candles || [];
  if (candles.length < 1000 || candles.length > 50000) throw new Error('Use 1,000-50,000 chronological candles.');
  if (candles.some((row,i)=>!Number.isFinite(row.time)||i&&row.time<=candles[i-1].time)) throw new Error('Candles must be strictly chronological.');
  const trainEnd=Math.floor(candles.length*.6), validationEnd=Math.floor(candles.length*.8);
  const params={...input.params,accountEquity:10000,maxNotional:10000,maxPositionPct:100,takerFeePct:0.055,makerFeePct:0.02,slippageBps:2,slippagePct:0.02,fillModel:'next-open',shortMode:false};
  delete params.feePct;
  function evaluate(strategy,extra,start,end) {
    const close=candles[end-1].time+data.intervalToMs(input.interval||'1h')/1000;
    const result=engine.run({strategy,params:{...params,...extra},candles:candles.slice(0,end),
      regimeCandles:input.regimeCandles,benchmarkCandles:input.benchmarkCandles,benchmarkInterval:'4h',
      interval:input.interval||'1h',executionInterval:'5m',executionCandles:(input.executionCandles||[]).filter(row=>row.time+300<=close),
      entryFilter:({index})=>index>=start,collectReadiness:true});
    const trades=result.tradeList.filter(trade=>trade.exitReason!=='End of data');
    const decisions=result.entryReadiness.rows.filter(row=>row.time>=candles[start].time);
    const unavailable=decisions.filter(row=>row.status==='UNAVAILABLE').length;
    return {netReturn:result.netReturn,drawdown:result.maxDrawdown,trades:trades.length,fees:result.totalFees,
      slippage:result.totalSlippageCost,profitFactor:result.profitFactor,
      diagnosticCoveragePct:100*(1-unavailable/Math.max(1,decisions.length)),
      blockers:decisions.reduce((sum,row)=>{for(const reason of row.blockers||[])sum[reason]=(sum[reason]||0)+1;return sum;},{}),
      decisions:decisions.reduce((sum,row)=>{sum[row.status]=(sum[row.status]||0)+1;return sum;},{})};
  }
  const variants=[{strategy:'RelativeStrengthV2',params:{}}];
  for(const freshnessMaxBars of [0,1,2]) for(const freshnessMaxDriftAtr of [.25,.5,1]) variants.push({strategy:'RelativeStrengthFreshV3',params:{freshnessMaxBars,freshnessMaxDriftAtr}});
  const training=variants.map(variant=>({...variant,training:evaluate(variant.strategy,variant.params,200,trainEnd)}));
  const chosen=training.filter(row=>row.strategy==='RelativeStrengthFreshV3').sort((a,b)=>
    Number(b.training.trades>=150)-Number(a.training.trades>=150)||b.training.netReturn-a.training.netReturn)[0];
  // Freeze the training choice before looking at validation; final 20% remains untouched.
  const baseline=training[0];
  baseline.validation=evaluate(baseline.strategy,baseline.params,trainEnd,validationEnd);
  chosen.validation=evaluate(chosen.strategy,chosen.params,trainEnd,validationEnd);
  return {schemaVersion:1,createdAt:new Date().toISOString(),paperOnly:true,source:input.source||'frozen dataset',symbol:input.symbol||'unspecified',interval:input.interval||'1h',modeledCosts:params,
    training,baseline,chosen,selectionRule:'Training sample gate then net return; experimental, not a promoted winner',
    finalHoldout:{start: candles[validationEnd].time,rows:candles.length-validationEnd,evaluated:false,
      eligible:chosen.training.trades>=150&&chosen.validation.trades>=50&&chosen.training.diagnosticCoveragePct>=95&&chosen.validation.diagnosticCoveragePct>=95&&chosen.training.netReturn>0&&chosen.validation.netReturn>0},
    limitations:['Funding excluded; not a calibrated probability','Boundary positions liquidated conservatively; results are exploratory','Overlapping trades are not independent evidence; no automatic candidate updates']};
}
if(require.main===module) {
  if(!process.argv[2]) throw new Error('Usage: node cli/research_entry_readiness.js frozen-input.json [report.json]');
  const report=JSON.stringify(compare(JSON.parse(fs.readFileSync(process.argv[2],'utf8'))),null,2);
  if(process.argv[3]) fs.writeFileSync(process.argv[3],report+'\n');else process.stdout.write(report+'\n');
}
module.exports={compare};
