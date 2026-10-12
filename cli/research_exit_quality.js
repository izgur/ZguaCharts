// Frozen, paper-only comparison. Does not write candidates, orders, or paper state.
const fs = require('node:fs');
const engine = require('../core/backtest/regimeTrend');
const data = require('../core/data');
const average = values => values.length ? values.reduce((a,b)=>a+b,0)/values.length : null;

function compare(input) {
  const candles=input.candles||[], interval=input.interval||'15m', step=data.intervalToMs(interval)/1000;
  if(candles.length<500||candles.length>50000)throw new Error('Use 500-50,000 signal candles');
  for(const rows of [candles,input.executionCandles||[]]) if(rows.some((row,i)=>!Number.isFinite(row.time)||i&&row.time<=rows[i-1].time))throw new Error('Strictly chronological candles required');
  const trainEnd=Math.floor(candles.length*.6), validationEnd=Math.floor(candles.length*.8);
  const params={...input.params,accountEquity:10000,maxPositionPct:100,maxNotional:10000,
    takerFeePct:.055,makerFeePct:.02,slippageBps:2,fillModel:'next-open',shortMode:false};
  delete params.feePct; delete params.researchExitMode;
  const execution=input.executionCandles||[];
  const times=new Set(execution.map(row=>row.time));
  const coverage=(start,end)=>{
    let present=0,total=0;
    for(let i=start;i<end;i++)for(let time=candles[i].time;time<candles[i].time+step;time+=300){total++;if(times.has(time))present++;}
    return total?present/total*100:0;
  };
  const trainingCoverage=coverage(200,trainEnd),validationCoverage=coverage(trainEnd,validationEnd);
  const report={schemaVersion:1,createdAt:new Date().toISOString(),paperOnly:true,source:input.source,symbol:input.symbol,
    interval,modeledCosts:params,coverage:{trainingPct:trainingCoverage,validationPct:validationCoverage},
    split:{training:trainEnd,validation:validationEnd-trainEnd,holdout:candles.length-validationEnd},
    finalHoldout:{start:candles[validationEnd].time,evaluated:false,eligible:false},
    priorHoldoutOverlap:input.priorHoldoutOverlap || null,
    limitations:['Exploratory, not a proven or promoted winner','Funding/spread excluded','Closed-price MFE proxy, not intrabar peak; capture can exceed 100% for intrabar targets','Stops precede targets on ambiguous 5m candles; tightened stops apply from the following candle','Exits change position occupancy, so entry rules are fixed but realized entry sets can differ']};
  if(trainingCoverage<100||validationCoverage<100) return {...report,status:'BLOCKED_DATA',reason:'Complete real 5m coverage required; missing bars are not filled or ignored',training:[],chosen:null};
  function evaluate(mode,start,end) {
    const cutoff=candles[end-1].time+step;
    const result=engine.run({strategy:input.strategy||'RelativeStrengthV2',params:{...params,...(mode?{researchExitMode:mode}:{})},
      candles:candles.slice(0,end),interval,regimeCandles:input.regimeCandles,benchmarkCandles:input.benchmarkCandles,
      benchmarkInterval:input.benchmarkInterval||'4h',executionInterval:'5m',executionCandles:execution.filter(row=>row.time+300<=cutoff),
      entryFilter:({index})=>index>=start});
    const closed=result.tradeList.filter(trade=>trade.exitReason!=='End of data');
    const metrics=closed.map(trade=>{
      const sign=trade.side==='long'?1:-1;
      const observed=execution.filter(bar=>bar.time>=trade.entryTime&&bar.time<trade.exitTime);
      const peak=Math.max(0,...observed.map(bar=>sign*(bar.close-trade.entryPrice)));
      const captured=sign*((trade.weightedExitPrice||trade.exitPrice)-trade.entryPrice);
      return {capture:peak>0?captured/peak*100:null,giveback:Math.max(0,peak-captured)/trade.initialStopDistance};
    });
    return {netReturn:result.tradeList.reduce((sum,trade)=>sum+trade.netPnl,0)/100,
      drawdown:result.maxDrawdown,trades:closed.length,winRate:result.winRate,fees:result.totalFees,slippage:result.totalSlippageCost,
      averageGivebackR:average(metrics.map(row=>row.giveback)),averageCapturePct:average(metrics.map(row=>row.capture).filter(Number.isFinite)),
      partialFills:closed.reduce((sum,trade)=>sum+(trade.partialExits||[]).length,0),forcedBoundaryClosures:result.tradeList.length-closed.length};
  }
  const training=[null,'staged','adaptive','momentum'].map(mode=>({mode:mode||'baseline',training:evaluate(mode,200,trainEnd)}));
  const chosen=training.slice(1).sort((a,b)=>Number(b.training.trades>=150)-Number(a.training.trades>=150)||b.training.netReturn-a.training.netReturn)[0];
  const baseline=training[0];
  // Training choice is frozen before validation; holdout remains unread by the engine.
  chosen.validation=evaluate(chosen.mode,trainEnd,validationEnd);
  baseline.validation=evaluate(null,trainEnd,validationEnd);
  report.finalHoldout.eligible=!input.priorHoldoutOverlap&&chosen.training.trades>=150&&chosen.validation.trades>=50&&chosen.training.netReturn>0&&chosen.validation.netReturn>0&&chosen.validation.netReturn>baseline.validation.netReturn;
  return {...report,status:report.finalHoldout.eligible?'HOLDOUT_ELIGIBLE':'EXPLORATORY_ONLY',training,baseline,chosen,
    selectionRule:'Training sample gate (150 closed trades) then net return; only frozen choice and baseline see validation (50-trade gate)'};
}

function recentComplete(input) {
  const rows=input.executionCandles||[];
  let start=rows.length-1;
  while(start>0&&rows[start].time-rows[start-1].time===300)start--;
  const lower=rows.slice(start), groups=new Map();
  for(const row of lower){const time=Math.floor(row.time/900)*900;if(!groups.has(time))groups.set(time,[]);groups.get(time).push(row);}
  const candles=[...groups].filter(([time,bars])=>bars.length===3&&bars[0].time===time&&bars[2].time===time+600)
    .map(([time,bars])=>({time,open:bars[0].open,high:Math.max(...bars.map(bar=>bar.high)),low:Math.min(...bars.map(bar=>bar.low)),close:bars[2].close,volume:bars.reduce((sum,bar)=>sum+bar.volume,0)}));
  const reservedStart=input.candles?.[Math.floor(input.candles.length*.8)]?.time;
  const validationEnd=candles[Math.floor(candles.length*.8)-1]?.time;
  const priorHoldoutOverlap=reservedStart && validationEnd>=reservedStart ? {
    start:reservedStart,reason:'Recent subwindow overlaps the earlier snapshot reserved holdout. Exploratory only; fresh forward holdout required.'
  } : null;
  return {...input,candles,executionCandles:lower,interval:'15m',priorHoldoutOverlap};
}
if(require.main===module){
  if(!process.argv[2]||!process.argv[3])throw new Error('Usage: node cli/research_exit_quality.js frozen-input.json report.json [--recent-complete]');
  let input=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
  if(process.argv.includes('--recent-complete'))input=recentComplete(input);
  const report=compare(input);
  fs.writeFileSync(process.argv[3],JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify({status:report.status,coverage:report.coverage,chosen:report.chosen,holdout:report.finalHoldout}));
}
module.exports={compare,recentComplete};
