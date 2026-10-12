// Closed-bar, display-only assessment. Direction and entry quality are not probabilities.
const indicators = require('../indicators');
const clamp = (value, low = -1, high = 1) => Math.max(low, Math.min(high, value));
const mean = rows => rows.reduce((sum, value) => sum + value, 0) / Math.max(1, rows.length);

function assess(frame, execution, params, options = {}) {
  const row = frame.at(-1), previous = frame.at(-6);
  const unavailable = reason => ({version:'market-assessment-v1', score:null, direction:'UNAVAILABLE', status:'UNAVAILABLE', blockers:[reason], scoreMeaning:'Rule-based directional bias, not win probability'});
  if (frame.length < 200 || !row || !(row.atr14 > 0) || !previous ||
      [row.close,row.ema20,row.ema50,row.ema200,row.rsi14,row.macdHistogram,row.cmf20,row.adx14].some(value => !Number.isFinite(value))) return unavailable('Indicator warmup/data unavailable');
  const step = options.signalSeconds || 900;
  if (frame.slice(-50).some((bar,index,rows) => index && bar.time - rows[index-1].time !== step)) return unavailable('Recent signal candle gap');
  const atr = row.atr14;
  const trend = mean([clamp((row.ema20-row.ema50)/atr),clamp((row.close-row.ema200)/(2*atr)),clamp((row.ema20-previous.ema20)/(.5*atr))]);
  const momentum = mean([clamp((row.rsi14-50)/25),clamp(row.macdHistogram/(.15*atr))]);
  const prior = frame.slice(-21,-1), high = Math.max(...prior.map(bar=>bar.high)), low = Math.min(...prior.map(bar=>bar.low));
  const structure = high > low ? clamp(2*(row.close-low)/(high-low)-1) : 0;
  const flow = clamp(row.cmf20/.2);
  // Correlated EMA/price measures share one capped trend block, not separate votes.
  const components = {trend:trend*45,momentum:momentum*25,structure:structure*20,volumeFlow:flow*10};
  const score = Math.round(clamp(50 + Object.values(components).reduce((sum,value)=>sum+value,0)/2,0,100));
  const direction = score >= 60 ? 'BULLISH' : score <= 40 ? 'BEARISH' : 'NEUTRAL';
  const sign = score >= 60 ? 1 : score <= 40 ? -1 : 0;
  const side = sign === 1 ? 'long' : sign === -1 ? 'short' : null;
  const closedAt = row.time + step, asOf = options.asOf || closedAt;
  const lower = (execution.candles || []).filter(bar=>bar.time+300<=asOf && bar.time>=asOf-18000);
  const last = lower.at(-1), before = lower.at(-2);
  const coverage = execution.interval === '5m' && lower.length >= 40 &&
    lower.slice(-40).every((bar,index,rows)=>!index||bar.time-rows[index-1].time===300) && asOf-(last?.time+300)<=330;
  const executionFrame = indicators.buildIndicatorFrame(lower, {}), timing = executionFrame.at(-1);
  const price = last?.close || row.close;
  const extended = sign && (sign*(price-row.ema20)/atr > 1.5 || (sign===1?row.rsi14>75:row.rsi14<25));
  const swing = sign===1 ? low : high;
  const stopDistance = sign ? Math.max(.75*atr,sign*(price-swing)+.15*atr) : atr;
  const opposite = sign===1 ? high : low;
  const room = sign ? sign*(opposite-price) : 0;
  const rewardRisk = room > 0 ? room/stopDistance : null;
  const slippage = params.slippageBps == null ? Number(params.slippagePct || 0) : Number(params.slippageBps)/100;
  const costPct = 2*(Number(params.takerFeePct || 0)+slippage);
  const riskPct = stopDistance/price*100;
  const recentLevel = before ? (sign===1?before.high:before.low) : null;
  const trigger = Boolean(sign && coverage && timing && before && sign*(price-recentLevel)>0 && sign*(price-timing.ema20)>0);
  const blockers = [];
  if (!side) blockers.push('No clear directional edge');
  if (!coverage) blockers.push('Fresh contiguous closed 5m confirmation unavailable');
  if (row.adx14 < 18) blockers.push('Weak trend / range risk');
  if (!(costPct>0) || riskPct<3*costPct) blockers.push('Risk distance does not cover modeled costs');
  if (stopDistance>3*atr) blockers.push('Invalidation too far away');
  if (rewardRisk!==null && rewardRisk<1.5) blockers.push('Nearby opposing structure leaves less than 1.5R');
  if (rewardRisk===null) blockers.push('Breakout: no nearby observed profit objective; wait for retest');
  if (!trigger) blockers.push('No fresh closed 5m continuation trigger');
  if (extended) blockers.push('Price/RSI overextended; avoid chasing');
  const entryScore = side ? Math.round(clamp(Math.abs(score-50)/50*35 + (extended?0:25) + (trigger?25:0) + (rewardRisk===null?0:clamp(rewardRisk/2,0,1)*15),0,100)) : 0;
  const status = !coverage ? 'UNAVAILABLE' : extended ? 'LATE' : !blockers.length && entryScore>=75 ? 'ENTER' : 'WAIT';
  return {version:'market-assessment-v1',score,direction,status,side,entryScore,closedAt,timingClosedAt:last?last.time+300:null,
    scoreMeaning:'Rule-based directional bias, not win probability',components,blockers,executionCovered:coverage,
    price,atr,stopPrice:side?price-sign*stopDistance:null,riskPct,roundTripCostPct:costPct,rewardRisk,
    objective:rewardRisk===null?null:opposite,extended,trigger,
    tacticSetup:options.tacticSetup || null,
    checks:{close:row.close,ema20:row.ema20,ema50:row.ema50,ema200:row.ema200,rsi:row.rsi14,adx:row.adx14,cmf:row.cmf20},
    positionContext:'Fresh-entry review; independent of simulated/account positions',
    limitations:['Experimental rubric, not calibrated odds','Funding/spread excluded; fee and slippage modeled','Short bias is informational; long-only tactics are not converted to short tactics']};
}

function align(rows) {
  return rows.map(row=>{
    const blockers = [...(row.blockers || [])];
    if(row.timeframe==='15m' && row.side) {
      for(const tf of ['4h','1h']) {
        const higher = rows.find(item=>item.timeframe===tf);
        if(!higher || higher.score===null || higher.error) blockers.push(tf+' directional data unavailable');
        else if(row.side==='long'?higher.score<60:higher.score>40) blockers.push(tf+' does not support '+row.side);
      }
    }
    return {...row,blockers,status:row.status==='ENTER'&&blockers.length?'WAIT':row.status};
  });
}
module.exports = {assess,align};
