const paper = require("../core/paper");
const argsUtil = require("./args");
const runtime = require("./runtime");

const args = argsUtil.parseArgs(process.argv.slice(2));

paper.activeSignalDiagnostics({
  configPath: args.config || "config/local/paper-candidate.json",
  statePath: args.state || "data/paper-state.json",
  reportDir: args.output || "reports",
  limit: args.limit || 20,
  refresh: args.refresh === true,
  targetCandleTime: args["target-candle-time"]
}).then((result) => {
  process.stdout.write(JSON.stringify(result, null, 2));
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: result.ok === false ? 1 : 0 });
}).catch((error) => {
  process.stderr.write(error.stack || error.message);
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 1 });
});
