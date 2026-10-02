const fs = require("fs");
const path = require("path");

const symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"];
const outputDir = path.resolve(process.argv[2] || "reports/market-flow");
const durationHours = Math.max(0.01, Number(process.argv[3] || 24));
const deadline = Date.now() + durationHours * 3600000;
const buckets = new Map();
const books = new Map();
let socket;
let reconnectTimer;
let pingTimer;
let stopping = false;
let lastMessageAt = 0;
let received = 0;
let reconnects = 0;
let connectionStartedAt = 0;

fs.mkdirSync(outputDir, { recursive: true });

function writeEvent(event) {
  const date = new Date(event.minute || Date.now()).toISOString().slice(0, 10);
  fs.appendFileSync(path.join(outputDir, `bybit-linear-flow-${date}.jsonl`), JSON.stringify(event) + "\n");
}

function flushOld(force) {
  const cutoff = Date.now() - 20000;
  for (const [key, bucket] of buckets) {
    if (!force && bucket.minute + 60000 > cutoff) continue;
    if (force && bucket.minute + 60000 > Date.now()) bucket.partial = true;
    if (bucket.bookSamples) {
      bucket.meanSpreadBps = bucket.spreadBpsSum / bucket.bookSamples;
      bucket.meanBookImbalance = bucket.bookImbalanceSum / bucket.bookSamples;
    }
    delete bucket.spreadBpsSum;
    delete bucket.bookImbalanceSum;
    writeEvent(bucket);
    buckets.delete(key);
  }
}

function recordTrade(trade) {
  const time = Number(trade.T);
  const price = Number(trade.p);
  const size = Number(trade.v);
  const symbol = String(trade.s || "");
  if (!symbols.includes(symbol) || !Number.isFinite(time) || !Number.isFinite(price) || !Number.isFinite(size)) return;
  const minute = Math.floor(time / 60000) * 60000;
  const key = `${symbol}:${minute}`;
  let row = buckets.get(key);
  if (!row) {
    row = { source: "bybit", category: "linear", symbol, minute, buyQty: 0, sellQty: 0,
      buyNotional: 0, sellNotional: 0, buyTrades: 0, sellTrades: 0,
      firstTradeTime: time, lastTradeTime: time, firstPrice: price, lastPrice: price,
      highPrice: price, lowPrice: price, bookSamples: 0, spreadBpsSum: 0, bookImbalanceSum: 0,
      partial: connectionStartedAt >= minute && connectionStartedAt < minute + 60000 };
    buckets.set(key, row);
  }
  const notional = price * size;
  if (trade.S === "Buy") {
    row.buyQty += size;
    row.buyNotional += notional;
    row.buyTrades++;
  } else if (trade.S === "Sell") {
    row.sellQty += size;
    row.sellNotional += notional;
    row.sellTrades++;
  }
  if (time < row.firstTradeTime) { row.firstTradeTime = time; row.firstPrice = price; }
  if (time >= row.lastTradeTime) { row.lastTradeTime = time; row.lastPrice = price; }
  row.highPrice = Math.max(row.highPrice, price);
  row.lowPrice = Math.min(row.lowPrice, price);
  const book = books.get(symbol);
  if (book && time - book.time < 5000) {
    row.bookSamples++;
    row.spreadBpsSum += book.spreadBps;
    row.bookImbalanceSum += book.imbalance;
    row.lastBid = book.bid;
    row.lastAsk = book.ask;
    row.lastBidSize = book.bidSize;
    row.lastAskSize = book.askSize;
    row.lastBookTime = book.time;
  }
  received++;
}

function recordBook(payload) {
  const symbol = String(payload.topic || "").split(".").pop();
  const data = payload.data || {};
  const bid = Array.isArray(data.b) && data.b[0] ? Number(data.b[0][0]) : NaN;
  const ask = Array.isArray(data.a) && data.a[0] ? Number(data.a[0][0]) : NaN;
  const bidSize = Array.isArray(data.b) && data.b[0] ? Number(data.b[0][1]) : NaN;
  const askSize = Array.isArray(data.a) && data.a[0] ? Number(data.a[0][1]) : NaN;
  if (!symbols.includes(symbol) || !(bid > 0) || !(ask > bid) || !(bidSize >= 0) || !(askSize >= 0)) return;
  const midpoint = (bid + ask) / 2;
  books.set(symbol, { bid, ask, bidSize, askSize, time: Number(payload.ts || Date.now()),
    spreadBps: (ask - bid) / midpoint * 10000,
    imbalance: (bidSize - askSize) / (bidSize + askSize || 1) });
}

function connect() {
  if (stopping) return;
  socket = new WebSocket("wss://stream.bybit.com/v5/public/linear");
  socket.addEventListener("open", () => {
    connectionStartedAt = Date.now();
    const topics = symbols.flatMap((symbol) => [`publicTrade.${symbol}`, `orderbook.1.${symbol}`]);
    socket.send(JSON.stringify({ op: "subscribe", args: topics }));
    pingTimer = setInterval(() => {
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ op: "ping" }));
    }, 20000);
    process.stdout.write(`connected ${new Date().toISOString()}\n`);
  });
  socket.addEventListener("message", (event) => {
    lastMessageAt = Date.now();
    try {
      const payload = JSON.parse(String(event.data));
      if (payload.op === "subscribe" && payload.success === false) process.stderr.write(`subscribe failed ${JSON.stringify(payload)}\n`);
      if (payload.topic && payload.topic.startsWith("publicTrade.") && Array.isArray(payload.data)) payload.data.forEach(recordTrade);
      if (payload.topic && payload.topic.startsWith("orderbook.1.")) recordBook(payload);
    } catch (error) { process.stderr.write(`message error ${error.message}\n`); }
  });
  socket.addEventListener("error", (event) => { process.stderr.write(`socket error ${event.message || "unknown"}\n`); });
  socket.addEventListener("close", () => {
    clearInterval(pingTimer);
    for (const bucket of buckets.values()) bucket.partial = true;
    if (!stopping) {
      reconnects++;
      reconnectTimer = setTimeout(connect, 5000);
    }
  });
}

function stop() {
  if (stopping) return;
  stopping = true;
  clearTimeout(reconnectTimer);
  clearInterval(pingTimer);
  clearInterval(flushTimer);
  flushOld(true);
  if (socket) socket.close();
  process.stdout.write(`stopped ${new Date().toISOString()} trades=${received} reconnects=${reconnects}\n`);
}

const flushTimer = setInterval(() => {
  flushOld(false);
  if (Date.now() >= deadline) stop();
  else if (lastMessageAt && Date.now() - lastMessageAt > 60000 && socket && socket.readyState === WebSocket.OPEN) socket.close();
}, 10000);
process.on("SIGINT", stop);
process.on("SIGTERM", stop);
connect();
