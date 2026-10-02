const fs = require("fs");
const path = require("path");

const inputDir = path.resolve(process.argv[2] || "reports/market-flow");
const output = path.resolve(process.argv[3] || "reports/research-campaigns/trade-flow-initial.json");
const files = fs.existsSync(inputDir) ? fs.readdirSync(inputDir)
  .filter((name) => /^bybit-linear-flow-\d{4}-\d{2}-\d{2}\.jsonl$/.test(name)) : [];
const bySymbol = new Map();
for (const file of files) {
  for (const line of fs.readFileSync(path.join(inputDir, file), "utf8").split(/\r?\n/)) {
    if (!line) continue;
    const row = JSON.parse(line);
    if (row.partial) continue;
    if (!bySymbol.has(row.symbol)) bySymbol.set(row.symbol, new Map());
    const minutes = bySymbol.get(row.symbol);
    if (!minutes.has(row.minute)) minutes.set(row.minute, row);
  }
}

function evaluate(samples) {
  const count = samples.length;
  const meanBps = count ? samples.reduce((sum, value) => sum + value, 0) / count : 0;
  const winRatePct = count ? samples.filter((value) => value > 0).length / count * 100 : 0;
  return { count, meanGrossBps: Math.round(meanBps * 100) / 100,
    meanAfter15BpsCost: Math.round((meanBps - 15) * 100) / 100,
    winRatePct: Math.round(winRatePct * 100) / 100 };
}

const report = { generatedAt: new Date().toISOString(), inputDir, files, costHurdleBps: 15,
  method: "Nonoverlapping signals; completed 1m taker-notional imbalance versus close-to-close future return. Descriptive signal test, not a fill simulation.",
  symbols: [], results: [] };
for (const [symbol, map] of bySymbol) {
  const rows = Array.from(map.values()).sort((a, b) => a.minute - b.minute);
  const first = rows[0] && rows[0].minute;
  const last = rows[rows.length - 1] && rows[rows.length - 1].minute;
  const expected = first === undefined ? 0 : (last - first) / 60000 + 1;
  report.symbols.push({ symbol, minutes: rows.length, first: first && new Date(first).toISOString(),
    last: last && new Date(last).toISOString(), completenessPct: expected ? Math.round(rows.length / expected * 10000) / 100 : 0 });
  const split = Math.floor(rows.length * 0.7);
  for (const horizon of [1, 3, 5, 15]) {
    for (const direction of ["follow", "fade"]) {
      for (const threshold of [0.5, 0.7, 0.9]) {
        const train = [];
        const holdout = [];
        for (let i = 0; i + horizon < rows.length; i += horizon) {
          const row = rows[i];
          const future = map.get(row.minute + horizon * 60000);
          if (!future || !(row.lastPrice > 0)) continue;
          const buy = Number(row.buyNotional || 0);
          const sell = Number(row.sellNotional || 0);
          const imbalance = (buy - sell) / (buy + sell || 1);
          if (Math.abs(imbalance) < threshold) continue;
          const side = Math.sign(imbalance) * (direction === "follow" ? 1 : -1);
          const returnBps = side * (future.lastPrice / row.lastPrice - 1) * 10000;
          (i < split ? train : holdout).push(returnBps);
        }
        report.results.push({ symbol, horizonMinutes: horizon, direction, threshold,
          train: evaluate(train), holdout: evaluate(holdout) });
      }
    }
  }
}
fs.mkdirSync(path.dirname(output), { recursive: true });
fs.writeFileSync(output, JSON.stringify(report, null, 2));
process.stdout.write(JSON.stringify({ output, symbols: report.symbols,
  holdoutReady: report.results.filter((row) => row.holdout.count >= 30).length }, null, 2) + "\n");
