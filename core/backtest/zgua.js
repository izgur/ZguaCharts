function entry(events, params) {
  return events.some(event => event.bullish && event.studies.length >= (params.minStudies || 1)) && !exit(events, params);
}
function exit(events, params) {
  return events.some(event => !event.bullish && event.studies.length >= (params.minStudies || 1));
}
function reason(events, bullish, params) {
  return events.filter(e=>e.bullish===bullish && e.studies.length >= (params.minStudies || 1)).map(e=>e.kind+' '+(bullish?'bullish':'bearish')+' divergence ('+e.studies.join(', ')+')').join('; ');
}
function run(options, formatResult) {
  if (!Array.isArray(options.divergenceEvents)) throw new Error('ZGUA requires confirmed Python divergence events');
  const candles = options.candles || [], params = Object.assign({feePct:0.055, slippagePct:0.02, accountEquity:10000, minStudies:1}, options.params);
  const fee = Number(params.feePct)/100, slip = Number(params.slippagePct)/100;
  const initial = Number(params.accountEquity);
  if (!(initial > 0) || !Number.isFinite(initial) || !Number.isFinite(fee) || fee < 0 || fee >= 1 || !Number.isFinite(slip) || slip < 0 || slip >= 1) throw new Error('Invalid ZGUA costs or equity');
  const grouped = new Map();
  for (const event of options.divergenceEvents) {
    if (!['regular','hidden','exaggerated','weak'].includes(event.kind)) continue;
    if (typeof event.bullish !== 'boolean' || !Array.isArray(event.studies) || !(event.firstTime < event.pivotTime && event.pivotTime < event.confirmedAt)) throw new Error('Invalid confirmed divergence');
    if (!grouped.has(event.confirmedAt)) grouped.set(event.confirmedAt, []);
    grouped.get(event.confirmedAt).push(event);
  }
  let cash = initial, position = null, totalFees = 0, totalSlippageCost = 0;
  const tradeList = [], equityCurve = [];
  for (let i=0; i<candles.length; i++) {
    const row = candles[i], events = grouped.get(row.time) || [];
    if (position && (options.customExit || exit)(events, params)) {
      const price = row.open*(1-slip), gross = position.quantity*price, cost = gross*fee;
      cash = gross-cost; totalFees += cost; totalSlippageCost += position.quantity*(row.open-price);
      tradeList.push({entryTime:position.entryTime, exitTime:row.time, entryPrice:position.entryPrice, exitPrice:price, returnPct:(cash/position.balance-1)*100, barsHeld:i-position.index, entryReason:position.entryReason, exitReason:reason(events,false,params) || 'Personal tactic exit', direction:'long'});
      position = null;
    } else if (!position && (options.customEntry || entry)(events, params)) {
      const price = row.open*(1+slip), quantity = cash/(price*(1+fee)), cost = quantity*price*fee;
      position = {entryTime:row.time, entryPrice:price, quantity, balance:cash, index:i, entryReason:reason(events,true,params) || 'Personal tactic entry'};
      totalFees += cost; totalSlippageCost += quantity*(price-row.open); cash=0;
    }
    equityCurve.push({time:row.time, equity:(position ? position.quantity*row.close : cash)/initial});
  }
  const equity = equityCurve.length ? equityCurve[equityCurve.length-1].equity : 1;
  const result = formatResult({symbol:options.symbol, interval:options.interval, strategy:'ZGUA', params, equity, equityCurve, tradeList, candlesLoaded:candles.length, warmup:0});
  return Object.assign(result, {strategy:'ZGUA', netReturn:result.totalReturn, numberOfTrades:result.trades, totalFees, totalSlippageCost, accountEquity:initial*equity, executionTimeframe:options.interval, openTrade:position, warnings:result.warnings.concat('Exploratory long-only divergence tactic; no validated profitability. Open positions are marked to market, not force-closed.')});
}
module.exports = {run, entry, exit};
