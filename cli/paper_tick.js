const paper = require("../core/paper");
const argsUtil = require("./args");
const runtime = require("./runtime");

const args = argsUtil.parseArgs(process.argv.slice(2));

paper.runPaperTick({
  configPath: args.config || "config/paper-candidate.json",
  statePath: args.state || "data/paper-state.json",
  reportDir: args.output || "reports",
  includeOpenCandle: args["include-open-candle"] === true,
  allowBootstrapImport: args["allow-bootstrap-import"] === true,
  dryRun: args["dry-run"] === true,
  refreshFirst: args["refresh-first"] === true,
  allowStale: args["allow-stale"] === true
}).then((result) => {
  process.stdout.write(JSON.stringify(result, null, 2));
  runtime.finishCli({
    debugHandles: args["debug-handles"] === true,
    forceExit: args["force-exit"] === true,
    exitCode: 0
  });
}).catch((error) => {
  process.stderr.write(error.stack || error.message);
  runtime.finishCli({
    debugHandles: args["debug-handles"] === true,
    forceExit: args["force-exit"] === true,
    exitCode: 1
  });
});
