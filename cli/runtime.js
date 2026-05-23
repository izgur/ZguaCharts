function readStdinIfPresent(options) {
  options = options || {};
  return new Promise(function (resolve) {
    var raw = "";
    var settled = false;

    function finish(value) {
      if (settled) return;
      settled = true;
      process.stdin.removeListener("data", onData);
      process.stdin.removeListener("end", onEnd);
      process.stdin.removeListener("error", onError);
      if (process.stdin.pause) process.stdin.pause();
      resolve(value || "");
    }

    function onData(chunk) {
      raw += chunk;
    }

    function onEnd() {
      finish(raw);
    }

    function onError() {
      finish("");
    }

    process.stdin.on("data", onData);
    process.stdin.on("end", onEnd);
    process.stdin.on("error", onError);

    if (process.stdin.isTTY) {
      finish("");
      return;
    }

    if (options.waitForEnd === true || process.argv.indexOf("--stdin-json") !== -1) {
      return;
    }

    // npm on Windows can expose stdin as a non-TTY stream even when no input
    // is being piped. If nothing arrives immediately, treat it as empty CLI
    // input and release the handle. Flask/subprocess stdin sends data/end
    // synchronously, so this does not block the bridge path.
    setTimeout(function () {
      finish(raw);
    }, 25).unref();
  });
}

function describeHandle(handle) {
  var name = handle && handle.constructor ? handle.constructor.name : typeof handle;
  var details = { name: name };
  if (!handle) return details;
  if (name === "Timeout") {
    details._idleTimeout = handle._idleTimeout;
    details.hasRef = handle.hasRef ? handle.hasRef() : undefined;
  } else if (name === "Socket") {
    details.localAddress = handle.localAddress;
    details.localPort = handle.localPort;
    details.remoteAddress = handle.remoteAddress;
    details.remotePort = handle.remotePort;
    details.destroyed = handle.destroyed;
    details.readable = handle.readable;
    details.writable = handle.writable;
  } else if (name === "Server") {
    details.listening = handle.listening;
  } else if (name === "ChildProcess") {
    details.pid = handle.pid;
    details.killed = handle.killed;
  } else if (name === "WriteStream" || name === "ReadStream") {
    details.fd = handle.fd;
    details.path = handle.path;
    details.destroyed = handle.destroyed;
  }
  return details;
}

function getHandleAudit() {
  var handles = process._getActiveHandles ? process._getActiveHandles() : [];
  var requests = process._getActiveRequests ? process._getActiveRequests() : [];
  return {
    handles: handles.map(describeHandle),
    requests: requests.map(function (request) {
      return { name: request && request.constructor ? request.constructor.name : typeof request };
    })
  };
}

function printHandleAudit(label) {
  var audit = getHandleAudit();
  process.stderr.write(JSON.stringify({
    label: label,
    activeHandles: audit.handles,
    activeRequests: audit.requests
  }, null, 2) + "\n");
}

function closeKnownIdleHandles() {
  if (process.stdin && process.stdin.pause) process.stdin.pause();
}

function finishCli(options) {
  options = options || {};
  closeKnownIdleHandles();
  if (options.debugHandles) printHandleAudit("shutdown-audit");
  process.exitCode = options.exitCode || 0;
  if (options.forceExit) {
    setTimeout(function () {
      process.exit(process.exitCode);
    }, 10).unref();
  }
}

module.exports = {
  readStdinIfPresent: readStdinIfPresent,
  printHandleAudit: printHandleAudit,
  finishCli: finishCli,
  getHandleAudit: getHandleAudit
};
