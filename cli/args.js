function parseArgs(argv) {
  var args = {};
  for (var i = 0; i < argv.length; i += 1) {
    var token = argv[i];
    if (token.indexOf("--") !== 0) continue;
    var key = token.slice(2);
    var next = argv[i + 1];
    if (!next || next.indexOf("--") === 0) {
      args[key] = true;
    } else {
      args[key] = next;
      i += 1;
    }
  }
  return args;
}

function daysToFrom(days) {
  var ms = Number(days || 60) * 24 * 60 * 60 * 1000;
  return new Date(Date.now() - ms).toISOString();
}

module.exports = {
  parseArgs: parseArgs,
  daysToFrom: daysToFrom
};
