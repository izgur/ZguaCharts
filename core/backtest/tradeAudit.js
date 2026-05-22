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
    var entry = candleByTime[Number(trade.entryTime)];
    var exit = candleByTime[Number(trade.exitTime)];
    if (!entry) errors.push("Trade " + index + " entryTime missing from candles: " + trade.entryTime);
    if (!exit) errors.push("Trade " + index + " exitTime missing from candles: " + trade.exitTime);
    if (!(trade.entryTime < trade.exitTime)) errors.push("Trade " + index + " has entryTime >= exitTime");
    if (entry && !closeEnough(trade.entryPrice, entry.close)) {
      errors.push("Trade " + index + " entryPrice does not match entry candle close");
    }
    if (exit && !validExitPrice(trade, exit)) {
      errors.push("Trade " + index + " exitPrice is outside intended exit candle range");
    }
    var expectedReturn = ((trade.exitPrice - trade.entryPrice) / trade.entryPrice) * 100;
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
    var buy = markers.filter(function (marker) {
      return marker.time === trade.entryTime && marker.shape === "arrowUp";
    });
    var sell = markers.filter(function (marker) {
      return marker.time === trade.exitTime && marker.shape === "arrowDown";
    });
    if (buy.length !== 1) errors.push("Trade " + index + " missing exact BUY marker");
    if (sell.length !== 1) errors.push("Trade " + index + " missing exact SELL marker");
    if (buy[0] && sell[0] && buy[0].time >= sell[0].time) errors.push("Trade " + index + " has reversed markers");
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

function validExitPrice(trade, candle) {
  if (trade.exitReason === "Strategy exit" || trade.exitReason === "End of data") {
    return closeEnough(trade.exitPrice, candle.close);
  }
  return trade.exitPrice >= candle.low && trade.exitPrice <= candle.high;
}

function closeEnough(a, b, tolerance) {
  tolerance = tolerance === undefined ? 0.000001 : tolerance;
  return Math.abs(Number(a) - Number(b)) <= tolerance;
}

function round(value, digits) {
  var factor = Math.pow(10, digits || 4);
  return Math.round((Number(value) || 0) * factor) / factor;
}

module.exports = {
  auditTrades: auditTrades
};
