const fs = require("fs");
const path = require("path");

const data = require("../core/data");
const indicators = require("../core/indicators");

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    const item = argv[i];
    if (!item.startsWith("--")) continue;
    const key = item.slice(2);
    const next = argv[i + 1];
    if (!next || next.startsWith("--")) out[key] = true;
    else {
      out[key] = next;
      i += 1;
    }
  }
  return out;
}

function num(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function round(value, digits = 4) {
  const factor = Math.pow(10, digits);
  return Math.round(num(value) * factor) / factor;
}

function percentile(values, p) {
  const sorted = values.filter(Number.isFinite).slice().sort((a, b) => a - b);
  if (!sorted.length) return null;
  const index = Math.max(0, Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * p)));
  return sorted[index];
}

function maxDrawdown(equity) {
  let peak = 1;
  let dd = 0;
  equity.forEach((value) => {
    peak = Math.max(peak, value);
    if (peak > 0) dd = Math.max(dd, (peak - value) / peak * 100);
  });
  return dd;
}

function timeframeHours(timeframe) {
  const raw = String(timeframe || "4h").toLowerCase();
  if (raw.endsWith("m")) return Math.max(1, Number(raw.slice(0, -1)) || 60) / 60;
  if (raw.endsWith("h")) return Math.max(1, Number(raw.slice(0, -1)) || 1);
  if (raw.endsWith("d")) return Math.max(1, Number(raw.slice(0, -1)) || 1) * 24;
  return 4;
}

function buildAtoms(frame) {
  const returns20 = frame.map((row, i) => i >= 20 ? (row.close / frame[i - 20].close - 1) * 100 : null);
  const atrPct = frame.map((row) => row.close ? row.atr14 / row.close * 100 : null);
  const bbWidthPct = frame.map((row) => row.bbMiddle ? row.bbWidth / row.bbMiddle * 100 : null);
  const atrLow = percentile(atrPct, 0.25);
  const atrHigh = percentile(atrPct, 0.75);
  const widthLow = percentile(bbWidthPct, 0.25);
  const widthHigh = percentile(bbWidthPct, 0.75);

  const atoms = [
    ["trend.close_gt_ema200", (r) => r.close > r.ema200],
    ["trend.close_gt_ema50", (r) => r.close > r.ema50],
    ["trend.ema50_gt_ema200", (r) => r.ema50 > r.ema200],
    ["trend.ema20_gt_ema50", (r) => r.ema20 > r.ema50],
    ["trend.supertrend_bull", (r) => r.supertrendDirection > 0],
    ["trend.adx_gt_18", (r) => r.adx14 > 18],
    ["trend.adx_gt_28", (r) => r.adx14 > 28],
    ["trend.plus_di_gt_minus_di", (r) => r.plusDi14 > r.minusDi14],
    ["momentum.rsi_gt_55", (r) => r.rsi14 > 55],
    ["momentum.rsi_gt_62", (r) => r.rsi14 > 62],
    ["momentum.rsi_45_65", (r) => r.rsi14 >= 45 && r.rsi14 <= 65],
    ["momentum.macd_bull", (r) => r.macdLine > r.macdSignal],
    ["momentum.macd_hist_gt_0", (r) => r.macdHistogram > 0],
    ["breakout.close_gt_donchian20_prev", (r, i) => i > 0 && frame[i - 1].donchianHigh20 && r.close > frame[i - 1].donchianHigh20],
    ["breakout.close_gt_donchian55_prev", (r, i) => i > 0 && frame[i - 1].donchianHigh55 && r.close > frame[i - 1].donchianHigh55],
    ["pullback.low_lte_ema20_close_gt_ema20", (r) => r.low <= r.ema20 && r.close > r.ema20],
    ["pullback.low_lte_ema50_close_gt_ema50", (r) => r.low <= r.ema50 && r.close > r.ema50],
    ["pullback.rsi_reclaim_50", (r, i) => i > 0 && frame[i - 1].rsi14 < 50 && r.rsi14 >= 50],
    ["reversion.close_lt_bb_lower", (r) => r.close < r.bbLower],
    ["reversion.low_lt_bb_lower_close_back_inside", (r) => r.low < r.bbLower && r.close > r.bbLower],
    ["reversion.rsi_lt_35", (r) => r.rsi14 < 35],
    ["reversion.rsi_lt_40", (r) => r.rsi14 < 40],
    ["squeeze.squeeze_on", (r) => r.squeezeOn === true],
    ["squeeze.bb_width_low", (_r, i) => bbWidthPct[i] !== null && widthLow !== null && bbWidthPct[i] <= widthLow],
    ["volatility.atr_low", (_r, i) => atrPct[i] !== null && atrLow !== null && atrPct[i] <= atrLow],
    ["volatility.atr_high", (_r, i) => atrPct[i] !== null && atrHigh !== null && atrPct[i] >= atrHigh],
    ["volume.above_ma20", (r) => r.volumeMa20 && r.volume > r.volumeMa20],
    ["volume.cmf_gt_0", (r) => r.cmf20 > 0],
    ["structure.break_up", (r) => r.structureBreakUp],
    ["structure.higher_low", (r) => r.higherLow],
    ["btc.return20_positive", (_r, i) => returns20[i] !== null && returns20[i] > 0],
    ["btc.return20_strong", (_r, i) => returns20[i] !== null && returns20[i] > 8],
    ["bear.close_lt_ema200", (r) => r.close < r.ema200],
    ["bear.ema50_lt_ema200", (r) => r.ema50 < r.ema200],
    ["bear.minus_di_gt_plus_di", (r) => r.minusDi14 > r.plusDi14],
    ["bear.rsi_lt_45", (r) => r.rsi14 < 45],
    ["bear.macd_bear", (r) => r.macdLine < r.macdSignal],
    ["breakdown.close_lt_donchian20_prev", (r, i) => i > 0 && frame[i - 1].donchianLow20 && r.close < frame[i - 1].donchianLow20],
    ["breakdown.close_lt_donchian55_prev", (r, i) => i > 0 && frame[i - 1].donchianLow55 && r.close < frame[i - 1].donchianLow55],
  ];
  return atoms.map(([name, fn]) => ({ name, fn }));
}

function combos(atoms) {
  const out = atoms.map((atom) => ({ name: atom.name, atoms: [atom] }));
  for (let i = 0; i < atoms.length; i += 1) {
    for (let j = i + 1; j < atoms.length; j += 1) {
      const left = atoms[i].name.split(".")[0];
      const right = atoms[j].name.split(".")[0];
      if (left === right) continue;
      out.push({ name: `${atoms[i].name} AND ${atoms[j].name}`, atoms: [atoms[i], atoms[j]] });
    }
  }
  return out;
}

function signalPasses(combo, frame, index) {
  const row = frame[index];
  return combo.atoms.every((atom) => {
    try {
      return atom.fn(row, index) === true;
    } catch (_error) {
      return false;
    }
  });
}

function evaluateCombo(combo, frame, holdBars, side, feePct) {
  const returns = [];
  const equity = [1];
  let cursor = 250;
  let exposureBars = 0;
  while (cursor < frame.length - holdBars - 1) {
    if (!signalPasses(combo, frame, cursor)) {
      cursor += 1;
      continue;
    }
    const entry = frame[cursor + 1].open || frame[cursor].close;
    const exit = frame[cursor + holdBars + 1].open || frame[cursor + holdBars].close;
    const raw = side === "long" ? (exit / entry - 1) * 100 : (entry / exit - 1) * 100;
    const net = raw - feePct * 2;
    returns.push(net);
    equity.push(equity[equity.length - 1] * (1 + net / 100));
    exposureBars += holdBars;
    cursor += holdBars + 1;
  }
  const wins = returns.filter((value) => value > 0);
  const losses = returns.filter((value) => value < 0);
  const totalReturnPct = (equity[equity.length - 1] - 1) * 100;
  const grossWins = wins.reduce((sum, value) => sum + value, 0);
  const grossLosses = Math.abs(losses.reduce((sum, value) => sum + value, 0));
  const avg = returns.reduce((sum, value) => sum + value, 0) / Math.max(1, returns.length);
  return {
    side,
    holdBars,
    trades: returns.length,
    totalReturnPct: round(totalReturnPct),
    avgReturnPct: round(avg),
    medianReturnPct: round(percentile(returns, 0.5) || 0),
    winRate: round(wins.length / Math.max(1, returns.length) * 100, 2),
    profitFactor: round(grossLosses ? grossWins / grossLosses : grossWins ? 99 : 0),
    maxDrawdownPct: round(maxDrawdown(equity)),
    exposurePct: round(exposureBars / Math.max(1, frame.length) * 100, 2),
  };
}

function evaluateTrainTest(combo, frame, holdBars, side, feePct) {
  const split = Math.floor(frame.length * 0.7);
  const train = evaluateCombo(combo, frame.slice(0, split), holdBars, side, feePct);
  const test = evaluateCombo(combo, frame.slice(Math.max(0, split - 250)), holdBars, side, feePct);
  return { train, test };
}

function scoreRow(row) {
  if (row.trades < 25) return -9999;
  if (row.profitFactor < 1) return -9999;
  return row.totalReturnPct
    + row.avgReturnPct * 18
    + row.profitFactor * 8
    - row.maxDrawdownPct * 1.2
    + Math.min(row.trades, 180) * 0.08;
}

function summarizeRegime(frame) {
  const out = {};
  frame.slice(250).forEach((row) => {
    const key = row.close > row.ema200
      ? (row.adx14 > 22 ? "bull_trend" : "bull_chop")
      : (row.adx14 > 22 ? "bear_trend" : "bear_chop");
    out[key] = (out[key] || 0) + 1;
  });
  return out;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const symbol = args.symbol || "BTCUSDT";
  const timeframe = args.timeframe || "4h";
  const days = Number(args.days || 1460);
  const limit = Math.ceil(days * 24 / timeframeHours(timeframe));
  const top = Number(args.top || 40);
  const feePct = Number(args.feePct || 0.06);
  const holdBars = String(args.holdBars || "3,6,12,24")
    .split(",")
    .map((value) => Number(value.trim()))
    .filter((value) => Number.isFinite(value) && value > 0);
  const sides = String(args.sides || "long,short").split(",").map((value) => value.trim()).filter(Boolean);

  const candles = await data.fetchCandles({ source: "bybit", symbol, interval: timeframe, limit });
  const frame = indicators.buildIndicatorFrame(candles, {});
  const atoms = buildAtoms(frame);
  const candidates = combos(atoms);
  const rows = [];
  candidates.forEach((combo) => {
    holdBars.forEach((hold) => {
      sides.forEach((side) => {
        const metrics = evaluateCombo(combo, frame, hold, side, feePct);
        const split = evaluateTrainTest(combo, frame, hold, side, feePct);
        const score = scoreRow(metrics);
        if (score <= -9999) return;
        if (split.train.trades < 15 || split.test.trades < 8) return;
        if (split.train.profitFactor < 1 || split.test.profitFactor < 1) return;
        rows.push({
          pattern: combo.name,
          side,
          holdBars: hold,
          score: round(score, 4),
          ...metrics,
          train: split.train,
          test: split.test,
        });
      });
    });
  });
  rows.sort((a, b) => b.score - a.score || b.totalReturnPct - a.totalReturnPct || b.profitFactor - a.profitFactor);
  const report = {
    ok: true,
    generatedAt: new Date().toISOString(),
    symbol,
    timeframe,
    days,
    candles: candles.length,
    feePct,
    holdBars,
    sides,
    atomCount: atoms.length,
    patternCount: candidates.length,
    regimeBars: summarizeRegime(frame),
    top: rows.slice(0, top),
  };
  const outDir = args.output || "reports/research-discovery";
  fs.mkdirSync(outDir, { recursive: true });
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\..+/, "").replace("T", "-");
  const outPath = path.join(outDir, `candle-pattern-discovery-${symbol}-${timeframe}-${stamp}.json`);
  fs.writeFileSync(outPath, JSON.stringify(report, null, 2));
  console.log(JSON.stringify({
    ok: true,
    savedPath: outPath,
    symbol,
    timeframe,
    candles: candles.length,
    patternCount: candidates.length,
    viableRows: rows.length,
    top: rows.slice(0, Math.min(10, top)),
  }, null, 2));
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});
