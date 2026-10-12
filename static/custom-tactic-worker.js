"use strict";
importScripts('/api/tactic-source/runtime.js');
self.onmessage = ({data}) => {
  try {
    const module = {exports:{}};
    new Function('module', 'indicators', 'regime', data.code)(module, require('../indicators'), require('../regime'));
    const tactic = module.exports;
    if (typeof tactic.entry !== 'function' || !['ZGUA','RelativeStrengthV2','RelativeStrengthFreshV3','EmaBounceV2','SimpleAtrTrendV2','PullbackReclaimV2','BreakoutRetestV2'].includes(tactic.baseStrategy)) throw new Error('Export a supported baseStrategy and entry function.');
    if (tactic.baseStrategy === 'ZGUA' && typeof tactic.exit !== 'function') throw new Error('ZGUA requires an exit function.');
    const params = {...tactic.params, accountEquity:10000, maxNotional:10000, maxPositionPct:100, takerFeePct:0.055, makerFeePct:0.02, slippagePct:0.02, slippageBps:2};
    delete params.feePct;
    delete params.customEntry;
    if (tactic.baseStrategy === 'ZGUA') params.feePct=0.055;
    let result;
    if (tactic.baseStrategy === 'ZGUA' && data.options.assessmentOnly) {
      const options=data.options, rows=options.candles||[], interval=options.interval;
      const step=interval==='4h'?14400:interval==='1h'?3600:900;
      const asOf=options.assessmentAsOf;
      const recent=(options.divergenceEvents||[]).filter(event=>event.confirmedAt<=asOf&&event.confirmedAt>asOf-step);
      const passed=Boolean(tactic.entry(recent,params));
      const marketAssessment=require('./marketAssessment').assess(require('../indicators').buildIndicatorFrame(rows,{}),{candles:options.executionCandles||[],interval:'5m'},params,{signalSeconds:step,asOf,tacticSetup:{passed,side:passed?'long':null,blockers:passed?[]:['No fresh bullish-only Zgua divergence; directional bias is not a ZGUA entry']}});
      result={marketAssessment,netReturn:0,maxDrawdown:0,numberOfTrades:0,totalFees:0,totalSlippageCost:0,tradeList:[],markers:[],equityCurve:[]};
    } else result = tactic.baseStrategy === 'ZGUA'
      ? require('./zgua').run({...data.options,params,customEntry:tactic.entry,customExit:tactic.exit},require('./metrics'))
      : TacticEngine.run({...data.options, strategy:tactic.baseStrategy, params, customEntry:tactic.entry});
    const encoded = JSON.stringify({result, params, baseStrategy:tactic.baseStrategy});
    if (encoded.length > 4000000) throw new Error('Result exceeds 4 MB');
    self.postMessage({result:encoded});
  } catch (error) { self.postMessage({error:String(error.message || error).slice(0,3000)}); }
};
