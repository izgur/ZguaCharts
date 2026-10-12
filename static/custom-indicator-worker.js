"use strict";
// This route has its own CSP: only pinned runtime assets can be fetched.
importScripts("/static/pyodide/pyodide.js");
const ready = (async () => {
  const runtime = await loadPyodide({ indexURL: "/static/pyodide/", stdout: () => {}, stderr: () => {} });
  await runtime.loadPackage(["numpy", "pandas"]);
  return runtime;
})();
self.onmessage = async ({ data }) => {
  try {
    const runtime = await ready;
    self.postMessage({ type: "ready" });
    runtime.globals.set("_indicator_source", data.code);
    runtime.globals.set("_indicator_input", JSON.stringify({ candles: data.candles, params: data.params || {} }));
    const result = runtime.runPython(`
import json as _json
_input = _json.loads(_indicator_input)
_namespace = {}
exec(compile(_indicator_source, "my_indicator.py", "exec"), _namespace)
if not callable(_namespace.get("indicator")):
    raise ValueError("Define indicator(candles, params=None).")
_defaults = _namespace.get("DEFAULTS", {})
if not isinstance(_defaults, dict) or len(_defaults) > 40:
    raise ValueError("DEFAULTS must contain at most 40 numeric inputs.")
_result = _namespace["indicator"](_input["candles"], _input["params"])
_json.dumps({"payload": _result, "defaults": _defaults, "placement": _namespace.get("PLACEMENT", "pane")}, allow_nan=False)
`);
    if (result.length > 4000000) throw new Error("Indicator output exceeds 4 MB.");
    self.postMessage({ type: "result", result });
  } catch (error) { self.postMessage({ type: "error", error: String(error.message || error).slice(0, 3000) }); }
};
