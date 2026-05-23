const paper = require("../core/paper");
const argsUtil = require("./args");
const runtime = require("./runtime");

const args = argsUtil.parseArgs(process.argv.slice(2));
const enabled = args.enable === true;

try {
  const result = paper.setPaperEnabled({
    configPath: args.config || "config/paper-candidate.json",
    statePath: args.state || "data/paper-state.json"
  }, enabled);
  process.stdout.write(JSON.stringify(result, null, 2));
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 0 });
} catch (error) {
  process.stderr.write(JSON.stringify({ error: error.message, missingMarkets: error.missingMarkets || [] }, null, 2));
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 1 });
}
