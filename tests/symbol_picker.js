const assert = require("node:assert/strict");
const { categories, filterInstruments } = require("../static/symbol-picker.js");
const rows = [
  { id: "binance:BTCUSDT", symbol: "BTCUSDT", description: "Bitcoin / Tether", exchange: "Binance", category: "Crypto", type: "Spot", exchangeType: "Spot" },
  { id: "bybit:BTCUSDT", symbol: "BTCUSDT", description: "Bitcoin / Tether", exchange: "Bybit", category: "Crypto", type: "Perpetual", exchangeType: "Futures" },
  { id: "yfinance:AAPL", symbol: "AAPL", description: "Apple Inc.", exchange: "NASDAQ", category: "Stocks", type: "Stock", exchangeType: "Cash" },
];
assert.equal(categories.length, 10);
assert.equal(filterInstruments(rows, "btcusdt").length, 2);
assert.equal(filterInstruments(rows, "bitcoin bybit")[0].id, "bybit:BTCUSDT");
assert.equal(filterInstruments(rows, "BTC", { category: "Crypto", exchangeType: "Spot" })[0].id, "binance:BTCUSDT");
assert.equal(filterInstruments(rows, "", { category: "Futures" })[0].id, "bybit:BTCUSDT");
assert.equal(filterInstruments(rows, "", { category: "Stocks", exchange: "NASDAQ" })[0].symbol, "AAPL");
assert.equal(filterInstruments(rows, "", { type: "Perpetual", exchange: "Binance" }).length, 0);
assert.equal(filterInstruments(rows, "", { category: "Economy" }).length, 0);
assert.equal(filterInstruments(rows, "zzzz").length, 0);
assert.equal(rows.length, 3);
assert.equal(filterInstruments(rows, "BTCUSDT.P")[0].id, "bybit:BTCUSDT");
const dated = { ...rows[1], id: "future", symbol: "BTCUSDT_261225", type: "Futures" };
assert.equal(filterInstruments([...rows, dated], "BTC").length, 2);
assert.equal(filterInstruments([...rows, dated], "BTC", { category: "Futures" }).length, 2);
assert.equal(filterInstruments([...rows, dated], "BTC", { type: "Futures" }).length, 1);
console.log("Symbol picker passed: cross-source search, asset classes, exchange types and independent market identities.");
