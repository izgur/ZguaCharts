function log(level, message, context) {
  var payload = {
    time: new Date().toISOString(),
    level: level,
    message: message,
    context: context || {}
  };
  if (process.env.ZGUA_QUIET === "1") return;
  console.error(JSON.stringify(payload));
}

module.exports = {
  info: function (message, context) { log("info", message, context); },
  warn: function (message, context) { log("warn", message, context); },
  error: function (message, context) { log("error", message, context); }
};
