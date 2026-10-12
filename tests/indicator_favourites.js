const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(require.resolve("../static/app.js"), "utf8");
const context = vm.createContext({ window: {}, localStorage: { getItem: () => null }, Set });
for (const name of ["indicatorFavouriteKey", "indicatorFavourites", "indicatorPickerMatches"]) {
  const start = source.indexOf("function " + name + "(");
  const end = source.indexOf("\nfunction ", start + 1);
  vm.runInContext(source.slice(start, end < 0 ? undefined : end), context);
}
assert.equal(context.indicatorFavouriteKey(), "zgua-indicator-favourites-v1");
context.window.ChartAccounts = { key: (key) => "account:42:" + key };
assert.equal(context.indicatorFavouriteKey(), "account:42:zgua-indicator-favourites-v1");
for (const value of [null, "{}", "null", "invalid"]) {
  context.localStorage.getItem = () => value;
  assert.equal(context.indicatorFavourites().size, 0);
}
context.localStorage.getItem = () => '["rsi",7,"macd","rsi"]';
assert.deepEqual([...context.indicatorFavourites()], ["rsi", "macd"]);
const favourites = new Set(["rsi"]);
assert.equal(context.indicatorPickerMatches("rsi", "RSI 14", " RSI ", "all", favourites), true);
assert.equal(context.indicatorPickerMatches("rsi", "RSI 14", "", "favourites", favourites), true);
assert.equal(context.indicatorPickerMatches("macd", "MACD", "", "favourites", favourites), false);
assert.equal(context.indicatorPickerMatches("wavetrend", "WaveTrend", "", "top", favourites), true);
assert.equal(context.indicatorPickerMatches("alma", "ALMA", "", "top", favourites), false);
assert.equal(context.indicatorPickerMatches("rsi", "RSI 14", "volume", "top", favourites), false);
console.log("Indicator favourites checks passed: account keys, malformed storage, category/search filtering.");
