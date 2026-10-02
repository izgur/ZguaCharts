const fs = require("fs");
const path = require("path");
const { fetchCompleteRange } = require("./research_short_timeframe");

function arg(name, fallback) {
  const index = process.argv.indexOf(`--${name}`);
  return index < 0 ? fallback : process.argv[index + 1];
}

function product(family, dimensions) {
  let rows = [{ family }];
  for (const [name, values] of Object.entries(dimensions)) {
    rows = rows.flatMap((row) => values.map((value) => ({ ...row, [name]: value })));
  }
  return rows;
}

const variants = [
  ...product("volumeImpulse", {
    volumeRatio: [1.5, 2, 3], bodyRatio: [0.5, 0.7], impulseAtr: [0.5, 0.9], trend: [true, false], stopAtr: [1.4, 2.1], rewardRisk: [1.5, 2.5]
  }),
  ...product("volatilityBreakout", {
    lookback: [20, 40], atrRegimeRatio: [1, 1.25], volumeRatio: [1, 1.5], trend: [true, false], stopAtr: [1.4, 2.1], rewardRisk: [1.5, 2.5]
  }),
  ...product("sessionRangeBreakout", {
    rangeMinutes: [30, 60], bufferAtr: [0.1, 0.3], trend: [true, false], stopAtr: [1.4, 2.1], rewardRisk: [1.5, 2.5]
  })
];

function ema(values, period) {
  const out = [];
  const alpha = 2 / (period + 1);
  let value = null;
  for (let i = 0; i < values.length; i++) {
    value = value === null ? values[i] : values[i] * alpha + value * (1 - alpha);
    out.push(value);
  }
  return out;
}

function prepare(candles) {
  const close = candles.map((c) => c.close);
  const volume = candles.map((c) => c.volume);
  const tr = candles.map((c, i) => i === 0 ? c.high - c.low : Math.max(c.high - c.low, Math.abs(c.high - candles[i - 1].close), Math.abs(c.low - candles[i - 1].close)));
  const atr = ema(tr, 14);
  return { atr, atrSlow: ema(atr, 80), fast: ema(close, 20), slow: ema(close, 80), volumeMean: ema(volume, 30) };
}

function direction(candles, frame, index, variant, intervalMinutes) {
  const candle = candles[index];
  const previous = candles[index - 1];
  const atr = frame.atr[index];
  const trendLong = frame.fast[index] > frame.slow[index];
  const trendShort = frame.fast[index] < frame.slow[index];
  let side = 0;
  if (variant.family === "volumeImpulse") {
    const range = Math.max(candle.high - candle.low, 1e-9);
    const bodyRatio = Math.abs(candle.close - candle.open) / range;
    const impulse = Math.abs(candle.close - previous.close) / Math.max(atr, 1e-9);
    if (candle.volume / Math.max(frame.volumeMean[index], 1e-9) >= variant.volumeRatio && bodyRatio >= variant.bodyRatio && impulse >= variant.impulseAtr) {
      side = candle.close > candle.open ? 1 : -1;
    }
  } else if (variant.family === "volatilityBreakout") {
    const start = Math.max(0, index - variant.lookback);
    const prior = candles.slice(start, index);
    const high = Math.max(...prior.map((c) => c.high));
    const low = Math.min(...prior.map((c) => c.low));
    const active = atr / Math.max(frame.atrSlow[index], 1e-9) >= variant.atrRegimeRatio;
    const enoughVolume = candle.volume / Math.max(frame.volumeMean[index], 1e-9) >= variant.volumeRatio;
    if (active && enoughVolume && candle.close > high) side = 1;
    if (active && enoughVolume && candle.close < low) side = -1;
  } else {
    const date = new Date(candle.time * 1000);
    const minute = date.getUTCHours() * 60 + date.getUTCMinutes();
    const sessionStart = [0, 480, 780].filter((value) => value <= minute).pop();
    if (sessionStart !== undefined && minute >= sessionStart + variant.rangeMinutes && minute < sessionStart + variant.rangeMinutes + 240) {
      const bars = Math.ceil(variant.rangeMinutes / intervalMinutes);
      const sessionIndex = index - Math.floor((minute - sessionStart) / intervalMinutes);
      const opening = candles.slice(Math.max(0, sessionIndex), Math.max(0, sessionIndex) + bars);
      if (opening.length === bars) {
        const high = Math.max(...opening.map((c) => c.high)) + atr * variant.bufferAtr;
        const low = Math.min(...opening.map((c) => c.low)) - atr * variant.bufferAtr;
        if (previous.close <= high && candle.close > high) side = 1;
        if (previous.close >= low && candle.close < low) side = -1;
      }
    }
  }
  if (variant.trend && ((side === 1 && !trendLong) || (side === -1 && !trendShort))) return 0;
  return side;
}

function simulate(candles, variant, intervalMinutes, costs, startTime) {
  const frame = prepare(candles);
  let equity = 10000;
  let peak = equity;
  let maxDrawdownPct = 0;
  let grossProfit = 0;
  let grossLoss = 0;
  let wins = 0;
  const trades = [];
  let position = null;
  const maxHold = Math.max(8, Math.round(720 / intervalMinutes));
  for (let i = 90; i < candles.length - 1; i++) {
    const candle = candles[i];
    if (position) {
      const stopHit = position.side === 1 ? candle.low <= position.stop : candle.high >= position.stop;
      const targetHit = position.side === 1 ? candle.high >= position.target : candle.low <= position.target;
      if (stopHit || targetHit || i - position.index >= maxHold) {
        const exit = stopHit ? position.stop : targetHit ? position.target : candle.close;
        const gross = position.side * (exit - position.entry) * position.quantity;
        const cost = (position.entry + exit) * position.quantity * costs.perSide;
        const pnl = gross - cost;
        equity += pnl;
        if (pnl > 0) { grossProfit += pnl; wins++; } else grossLoss += Math.abs(pnl);
        trades.push(pnl);
        position = null;
      }
    }
    peak = Math.max(peak, equity);
    maxDrawdownPct = Math.max(maxDrawdownPct, 100 * (peak - equity) / peak);
    if (position || candle.time < startTime) continue;
    const side = direction(candles, frame, i, variant, intervalMinutes);
    if (!side) continue;
    const entry = candles[i + 1].open;
    const distance = frame.atr[i] * variant.stopAtr;
    const riskQuantity = equity * 0.01 / Math.max(distance, 1e-9);
    const quantity = Math.min(riskQuantity, equity * 2 / entry);
    position = { side, entry, quantity, index: i + 1, stop: entry - side * distance, target: entry + side * distance * variant.rewardRisk };
  }
  return {
    returnPct: 100 * (equity / 10000 - 1), drawdownPct: maxDrawdownPct,
    profitFactor: grossLoss ? grossProfit / grossLoss : grossProfit ? 99 : 0,
    trades: trades.length, winRatePct: trades.length ? 100 * wins / trades.length : 0
  };
}

function score(row) {
  return row.returnPct - row.drawdownPct * 1.5 + Math.min(row.profitFactor, 5) * 3;
}

async function main() {
  const symbol = arg("symbol", "BTCUSDT");
  const timeframe = arg("timeframe", "15m");
  const days = Number(arg("days", "180"));
  const maxVariants = Number(arg("max-variants", String(variants.length)));
  const selectedVariants = variants.slice(0, maxVariants);
  const output = path.resolve(arg("output", `reports/research-campaigns/microstructure-${symbol}-${timeframe}.json`));
  const intervalMinutes = Number(timeframe.slice(0, -1));
  if (![3, 5, 15].includes(intervalMinutes) || days < 14) throw new Error("Use 3m/5m/15m and at least 14 days");
  const now = new Date();
  const from = new Date(now.getTime() - days * 86400000);
  const candles = await fetchCompleteRange(symbol, timeframe, Math.floor(from.getTime() / 1000), Math.floor(now.getTime() / 1000));
  const split1 = Math.floor(candles.length * 0.6);
  const split2 = Math.floor(candles.length * 0.8);
  const starts = [candles[0].time, candles[split1].time, candles[split2].time];
  const warmup = 120;
  const blocks = [candles.slice(0, split1), candles.slice(split1 - warmup, split2), candles.slice(split2 - warmup)];
  const report = {
    generatedAt: now.toISOString(), complete: false, symbol, timeframe, days, candles: candles.length,
    method: "Fixed grid, next-open long/short entries, conservative stop-first bars, 1% risk and 2x notional cap; 60/20/20 train/validation/final.",
    costs: { takerRoundTripPct: 0.15, makerScenarioRoundTripPct: 0.06, makerFillProbabilityModeled: false },
    totalVariants: selectedVariants.length, evaluated: 0, rows: [], selectedForValidation: [], selectedForFinal: []
  };
  const save = () => { fs.mkdirSync(path.dirname(output), { recursive: true }); fs.writeFileSync(output, JSON.stringify(report, null, 2)); };
  const evaluate = (variant, block, costs) => simulate(blocks[block], variant, intervalMinutes, costs, starts[block]);
  for (let id = 0; id < selectedVariants.length; id++) {
    const variant = selectedVariants[id];
    report.rows.push({ id, variant, train: evaluate(variant, 0, { perSide: 0.00075 }), validation: null, validationMaker: null, final: null });
    report.evaluated++;
    save();
    process.stderr.write(`${symbol} ${timeframe} ${id + 1}/${selectedVariants.length}\n`);
  }
  const families = [...new Set(selectedVariants.map((row) => row.family))];
  const leads = report.rows.filter((row) => row.train.trades >= 30 && row.train.returnPct > 0 && row.train.profitFactor > 1);
  const validation = families.flatMap((family) => leads.filter((row) => row.variant.family === family).sort((a, b) => score(b.train) - score(a.train)).slice(0, 3));
  report.selectedForValidation = validation.map((row) => row.id); save();
  for (const row of validation) {
    row.validation = evaluate(row.variant, 1, { perSide: 0.00075 });
    row.validationMaker = evaluate(row.variant, 1, { perSide: 0.0003 });
    save();
  }
  const final = validation.filter((row) => row.validation.trades >= 10 && row.validation.returnPct > 0 && row.validation.profitFactor > 1.1)
    .sort((a, b) => score(b.validation) - score(a.validation)).slice(0, 4);
  report.selectedForFinal = final.map((row) => row.id); save();
  for (const row of final) { row.final = evaluate(row.variant, 2, { perSide: 0.00075 }); save(); }
  report.complete = true; report.finishedAt = new Date().toISOString(); save();
  process.stdout.write(JSON.stringify({ output, totalVariants: report.totalVariants, validation: report.selectedForValidation, final: report.selectedForFinal }, null, 2) + "\n");
}

main().catch((error) => { console.error(error.stack || error.message); process.exitCode = 1; });
