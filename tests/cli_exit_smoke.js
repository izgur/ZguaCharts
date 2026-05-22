const assert = require("assert");
const childProcess = require("child_process");

function run(command, args) {
  const result = childProcess.spawnSync(command, args, {
    cwd: process.cwd(),
    encoding: "utf8",
    timeout: 15000,
    env: Object.assign({}, process.env, { ZGUA_QUIET: "1" })
  });
  assert.notStrictEqual(result.error && result.error.code, "ETIMEDOUT", command + " timed out");
  assert.strictEqual(result.status, 0, result.stderr);
  return result;
}

run("node", [
  "cli/backtest.js",
  "--symbol", "BTCUSDT",
  "--interval", "1h",
  "--days", "7",
  "--strategy", "AlwaysLongTest",
  "--limit", "120",
  "--audit-trades"
]);

run("node", [
  "cli/optimize.js",
  "--symbol", "BTCUSDT",
  "--interval", "1h",
  "--days", "7",
  "--strategy", "AlwaysLongTest",
  "--limit", "120",
  "--ranges", "{\"noop\":[1]}",
  "--output", "reports-cli-smoke"
]);

console.log("cli exit smoke tests passed");
