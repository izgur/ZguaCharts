const fs = require("fs");
const path = require("path");

const inputDir = path.resolve(process.argv[2] || "reports/market-flow");
const output = path.resolve(process.argv[3] || "reports/research-campaigns/passive-flow-latest.json");
const files = fs.existsSync(inputDir) ? fs.readdirSync(inputDir)
  .filter((name) => /^bybit-linear-flow-\d{4}-\d{2}-\d{2}\.jsonl$/.test(name)) : [];
const bySymbol = new Map();

for (const file of files) {
  for (const line of fs.readFileSync(path.join(inputDir, file), "utf8").split(/\r?\n/)) {
    if (!line) continue;
    const row = JSON.parse(line);
    if (row.partial || !(row.lastPrice > 0)) continue;
    if (!bySymbol.has(row.symbol)) bySymbol.set(row.symbol, new Map());
    if (!bySymbol.get(row.symbol).has(row.minute)) bySymbol.get(row.symbol).set(row.minute, row);
  }
}

function variants() {
  const rows = [];
  for (const direction of ["follow", "fade"])
    for (const threshold of [0.5, 0.7, 0.9])
      for (const offsetBps of [1, 3, 5])
        for (const fillTimeoutMinutes of [1, 3, 5])
          for (const holdMinutes of [3, 5, 15])
            for (const session of ["all", "europeUs"])
              for (const minVolRatio of [0, 1.25])
                rows.push({ direction, threshold, offsetBps, fillTimeoutMinutes, holdMinutes, session, minVolRatio });
  return rows;
}

function metrics(trades, signals) {
  const net = trades.map((trade) => trade.netBps);
  const grossWins = net.filter((value) => value > 0).reduce((sum, value) => sum + value, 0);
  const grossLosses = Math.abs(net.filter((value) => value < 0).reduce((sum, value) => sum + value, 0));
  let equity = 1;
  let peak = 1;
  let maxDrawdownPct = 0;
  for (const value of net) {
    equity *= 1 + value / 10000;
    peak = Math.max(peak, equity);
    maxDrawdownPct = Math.max(maxDrawdownPct, 100 * (peak - equity) / peak);
  }
  return {
    signals, fills: trades.length, fillRatePct: signals ? 100 * trades.length / signals : 0,
    meanGrossBps: trades.length ? trades.reduce((sum, trade) => sum + trade.grossBps, 0) / trades.length : 0,
    meanNetBps: trades.length ? net.reduce((sum, value) => sum + value, 0) / trades.length : 0,
    compoundedReturnPct: (equity - 1) * 100, profitFactor: grossLosses ? grossWins / grossLosses : grossWins ? 99 : 0,
    winRatePct: trades.length ? 100 * net.filter((value) => value > 0).length / trades.length : 0,
    maxDrawdownPct
  };
}

function test(map, rows, variant, fromIndex, toIndex) {
  const trades = [];
  let signals = 0;
  let nextEligibleMinute = 0;
  for (let index = fromIndex; index < toIndex; index++) {
    if (index === 0) continue;
    const signal = rows[index];
    if (signal.minute < nextEligibleMinute) continue;
    const hour = new Date(signal.minute).getUTCHours();
    if (variant.session === "europeUs" && (hour < 7 || hour >= 21)) continue;
    if (variant.minVolRatio > 0) {
      const recent = [];
      for (let sampleIndex = Math.max(1, index - 30); sampleIndex < index; sampleIndex++) {
        if (rows[sampleIndex].minute - rows[sampleIndex - 1].minute !== 60000) continue;
        recent.push(Math.abs(rows[sampleIndex].lastPrice / rows[sampleIndex - 1].lastPrice - 1));
      }
      const baseline = recent.length ? recent.reduce((sum, value) => sum + value, 0) / recent.length : 0;
      const currentMove = Math.abs(signal.lastPrice / rows[index - 1].lastPrice - 1);
      if (!(baseline > 0) || currentMove / baseline < variant.minVolRatio) continue;
    }
    const buy = Number(signal.buyNotional || 0);
    const sell = Number(signal.sellNotional || 0);
    const imbalance = (buy - sell) / (buy + sell || 1);
    if (Math.abs(imbalance) < variant.threshold) continue;
    signals++;
    const side = Math.sign(imbalance) * (variant.direction === "follow" ? 1 : -1);
    const limit = signal.lastPrice * (1 - side * variant.offsetBps / 10000);
    let fill = null;
    for (let wait = 1; wait <= variant.fillTimeoutMinutes; wait++) {
      const minute = map.get(signal.minute + wait * 60000);
      if (!minute) continue;
      const lowObserved = Number.isFinite(minute.lowPrice) ? minute.lowPrice : Math.min(minute.firstPrice, minute.lastPrice);
      const highObserved = Number.isFinite(minute.highPrice) ? minute.highPrice : Math.max(minute.firstPrice, minute.lastPrice);
      if ((side === 1 && lowObserved <= limit) || (side === -1 && highObserved >= limit)) {
        fill = minute;
        break;
      }
    }
    if (!fill) continue;
    const exit = map.get(fill.minute + variant.holdMinutes * 60000);
    if (!exit) continue;
    const grossBps = side * (exit.lastPrice / limit - 1) * 10000;
    // Maker entry (2 bps) plus taker exit (5.5 bps) and 2 bps exit slippage.
    const netBps = grossBps - 9.5;
    trades.push({ signalMinute: signal.minute, fillMinute: fill.minute, side, grossBps, netBps });
    nextEligibleMinute = exit.minute;
  }
  return metrics(trades, signals);
}

const report = {
  generatedAt: new Date().toISOString(), inputDir, files,
  method: "Chronological 60/20/20 split. Signal at completed minute; passive limit may fill only when a later observed first/last endpoint crosses it. Non-overlapping trades.",
  costs: { makerEntryBps: 2, takerExitBps: 5.5, exitSlippageBps: 2, totalRoundTripBps: 9.5 },
  caveats: ["Older rows use endpoint-cross fills; newer collector rows include actual minute high/low.", "Price crossing does not guarantee queue priority, so maker fills remain optimistic unless later calibrated.", "Short sample; final block remains untouched unless validation qualifies."],
  symbols: [], rows: []
};

for (const [symbol, map] of bySymbol) {
  const rows = [...map.values()].sort((a, b) => a.minute - b.minute);
  const split1 = Math.floor(rows.length * 0.6);
  const split2 = Math.floor(rows.length * 0.8);
  report.symbols.push({ symbol, minutes: rows.length, first: new Date(rows[0].minute).toISOString(), last: new Date(rows.at(-1).minute).toISOString() });
  const trained = variants().map((variant, id) => ({ id, symbol, variant, train: test(map, rows, variant, 0, split1), validation: null, final: null }));
  const leads = trained.filter((row) => row.train.fills >= 20 && row.train.meanNetBps > 0 && row.train.profitFactor > 1.05)
    .sort((a, b) => b.train.meanNetBps - a.train.meanNetBps).slice(0, 8);
  for (const row of leads) row.validation = test(map, rows, row.variant, split1, split2);
  const finalists = leads.filter((row) => row.validation.fills >= 10 && row.validation.meanNetBps > 0 && row.validation.profitFactor > 1.1)
    .sort((a, b) => b.validation.meanNetBps - a.validation.meanNetBps).slice(0, 3);
  for (const row of finalists) row.final = test(map, rows, row.variant, split2, rows.length);
  report.rows.push(...trained);
}

report.selectedForValidation = report.rows.filter((row) => row.validation).map((row) => `${row.symbol}:${row.id}`);
report.selectedForFinal = report.rows.filter((row) => row.final).map((row) => `${row.symbol}:${row.id}`);
fs.mkdirSync(path.dirname(output), { recursive: true });
fs.writeFileSync(output, JSON.stringify(report, null, 2));
process.stdout.write(JSON.stringify({ output, symbols: report.symbols, variants: report.rows.length,
  selectedForValidation: report.selectedForValidation, selectedForFinal: report.selectedForFinal }, null, 2) + "\n");
