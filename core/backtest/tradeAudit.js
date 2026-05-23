const reporting = require("../reporting");

function auditTrades(result, candles) {
  var normalizedCandles = (candles || []).slice().sort(function (a, b) { return a.time - b.time; });
  var candleByTime = {};
  normalizedCandles.forEach(function (candle) {
    candleByTime[Number(candle.time)] = candle;
  });

  var errors = [];
  var warnings = [];
  var markerKeys = {};
  var trades = result.tradeList || [];
  var markers = result.markers || [];
  var range = {
    first: normalizedCandles.length ? normalizedCandles[0].time : null,
    last: normalizedCandles.length ? normalizedCandles[normalizedCandles.length - 1].time : null
  };

  trades.forEach(function (trade, index) {
    var entrySignal = candleByTime[Number(trade.entrySignalTime || trade.entryTime)];
    var entryFill = candleByTime[Number(trade.entryFillTime || trade.entryTime)];
    var exitSignal = candleByTime[Number(trade.exitSignalTime || trade.exitTime)];
    var exitFill = candleByTime[Number(trade.exitFillTime || trade.exitTime)];
    if (!entrySignal) errors.push("Trade " + index + " entrySignalTime missing from candles: " + (trade.entrySignalTime || trade.entryTime));
    if (!entryFill) errors.push("Trade " + index + " entryFillTime missing from candles: " + (trade.entryFillTime || trade.entryTime));
    if (!exitSignal) errors.push("Trade " + index + " exitSignalTime missing from candles: " + (trade.exitSignalTime || trade.exitTime));
    if (!exitFill) errors.push("Trade " + index + " exitFillTime missing from candles: " + (trade.exitFillTime || trade.exitTime));
    if (!((trade.entryFillTime || trade.entryTime) <= (trade.exitFillTime || trade.exitTime))) errors.push("Trade " + index + " has entryFillTime > exitFillTime");
    if ((trade.entryFillTime || trade.entryTime) === (trade.exitFillTime || trade.exitTime)) warnings.push("Trade " + index + " entered and exited on the same candle; allowed for next-open fills followed by intrabar stops.");
    if (entrySignal && !closeEnough(trade.entrySignalPrice || trade.entryPrice, entrySignal.close, priceTolerance(entrySignal.close))) {
      errors.push("Trade " + index + " entrySignalPrice does not match entry signal candle close");
    }
    if (entryFill && !validEntryFill(trade, entrySignal, entryFill)) {
      errors.push("Trade " + index + " entryFillPrice does not match fill model");
    }
    if (exitFill && !validExitPrice(trade, exitSignal, exitFill)) {
      errors.push("Trade " + index + " exitFillPrice is outside intended fill model/candle range");
    }
    var expectedReturn = trade.pnl !== undefined && trade.accountEquity
      ? trade.pnl / trade.accountEquity * 100
      : ((trade.exitPrice - trade.entryPrice) / trade.entryPrice) * 100;
    if (!closeEnough(trade.returnPct, round(expectedReturn, 4), 0.02)) {
      errors.push("Trade " + index + " returnPct formula mismatch");
    }
  });

  if (markers.length !== trades.length * 2) {
    errors.push("Marker count mismatch: expected " + (trades.length * 2) + ", got " + markers.length);
  }

  markers.forEach(function (marker, index) {
    var key = marker.time + ":" + marker.shape + ":" + marker.text;
    if (markerKeys[key]) errors.push("Duplicate marker: " + key);
    markerKeys[key] = true;
    if (!candleByTime[Number(marker.time)]) errors.push("Marker " + index + " outside candle range/time set");
  });

  trades.forEach(function (trade, index) {
    var entryTime = trade.entryFillTime || trade.entryTime;
    var exitTime = trade.exitFillTime || trade.exitTime;
    var buy = markers.filter(function (marker) {
      return marker.time === entryTime && marker.shape === "arrowUp";
    });
    var sell = markers.filter(function (marker) {
      return marker.time === exitTime && marker.shape === "arrowDown";
    });
    if (buy.length !== 1) errors.push("Trade " + index + " missing exact BUY marker");
    if (sell.length !== 1) errors.push("Trade " + index + " missing exact SELL marker");
    if (buy[0] && sell[0] && buy[0].time > sell[0].time) errors.push("Trade " + index + " has reversed markers");
  });

  if (!trades.length) warnings.push("No trades to audit.");

  var audit = {
    ok: errors.length === 0,
    errors: errors,
    warnings: warnings,
    tradesChecked: trades.length,
    markersChecked: markers.length,
    candleRange: range,
    sampleTrades: trades.slice(0, 5)
  };
  reporting.writeTradeAuditReport(audit, "reports");
  return audit;
}

function validEntryFill(trade, signalCandle, fillCandle) {
  if (!signalCandle || !fillCandle) return false;
  var model = trade.fillModel || "close";
  var side = trade.side || "long";
  var slippage = tradeSlippagePct(trade);
  var entryFillPrice = trade.entryFillPrice || trade.entryPrice;
  if (model === "close") {
    return closeEnough(stripSlippage(entryFillPrice, side, "entry", slippage), signalCandle.close, priceTolerance(signalCandle.close));
  }
  if (model === "next-open") {
    return closeEnough(stripSlippage(entryFillPrice, side, "entry", slippage), fillCandle.open, priceTolerance(fillCandle.open));
  }
  if (model === "conservative") {
    var expected = side === "long" ? Math.max(signalCandle.close, fillCandle.open) : Math.min(signalCandle.close, fillCandle.open);
    return closeEnough(stripSlippage(entryFillPrice, side, "entry", slippage), expected, priceTolerance(expected));
  }
  return false;
}

function validExitPrice(trade, signalCandle, fillCandle) {
  if (!signalCandle || !fillCandle) return false;
  var model = trade.fillModel || "close";
  var side = trade.side || "long";
  var exitFill = Number(trade.exitFillPrice || trade.exitPrice);
  var signalPrice = Number(trade.exitSignalPrice || trade.exitPrice);
  var baseExit = stripSlippage(exitFill, side, "exit", tradeSlippagePct(trade));
  if (trade.exitReason === "EMA50 exit" || trade.exitReason === "EMA100 exit" || trade.exitReason === "Donchian exit" || trade.exitReason === "Strategy exit") {
    if (model === "next-open") return closeEnough(baseExit, fillCandle.open, priceTolerance(fillCandle.open));
    if (model === "conservative") {
      var conservative = side === "long" ? Math.min(signalPrice, fillCandle.open) : Math.max(signalPrice, fillCandle.open);
      return closeEnough(baseExit, conservative, priceTolerance(conservative));
    }
    return closeEnough(baseExit, signalCandle.close, priceTolerance(signalCandle.close));
  }
  if (trade.exitReason === "End of data") {
    return closeEnough(baseExit, signalCandle.close, priceTolerance(signalCandle.close));
  }
  if (model === "conservative") {
    if (side === "long" && fillCandle.open < signalPrice) return closeEnough(baseExit, fillCandle.open, priceTolerance(fillCandle.open));
    if (side === "short" && fillCandle.open > signalPrice) return closeEnough(baseExit, fillCandle.open, priceTolerance(fillCandle.open));
  }
  return baseExit >= fillCandle.low - priceTolerance(fillCandle.low) && baseExit <= fillCandle.high + priceTolerance(fillCandle.high);
}

function closeEnough(a, b, tolerance) {
  tolerance = tolerance === undefined ? 0.000001 : tolerance;
  return Math.abs(Number(a) - Number(b)) <= tolerance;
}

function priceTolerance(price) {
  return Math.max(Math.abs(Number(price)) * 0.00001, 0.000001);
}

function tradeSlippagePct(trade) {
  return Number(trade.slippageBps || 0) / 10000;
}

function stripSlippage(price, side, action, slippagePct) {
  var adverse = (side === "long" && action === "entry") || (side === "short" && action === "exit");
  return adverse ? Number(price) / (1 + slippagePct) : Number(price) / (1 - slippagePct);
}

function round(value, digits) {
  var factor = Math.pow(10, digits || 4);
  return Math.round((Number(value) || 0) * factor) / factor;
}

module.exports = {
  auditTrades: auditTrades
};
