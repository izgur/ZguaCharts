# Editable Python indicators

Each built-in study exports only its own formula and necessary helper functions.
The base label dictionary contains 11 entries; catalogue modules extend it to 58.

Open a study's source, edit it and choose Save as new. The editor suggests a name.
My indicators contains saved studies; stars work just as for built-in indicators.
New indicator starts with a working SMA template. Paste Python into the editor,
keep an indicator(candles, params=None) function, DEFAULTS and PLACEMENT, then test
and save. Custom copies can be updated, renamed, downloaded or deleted.

Scripts execute in fresh browser WebAssembly workers with Pyodide 0.29.2,
NumPy and pandas. They never execute in the Python web server. Worker CSP blocks
application API and external network requests. Execution is limited to 10 seconds
after initialization, 5,000 candles, 100 KB source and validated chart output.
This is not a memory quota: buggy scripts can still consume browser memory.
Use code you trust. Custom scripts are chart-only, not alert, backtest or trading
engines. This does not implement Pine Script.

Guests save locally; signed-in users synchronize the library with their profile.
Up to 20 studies and 1.5 MB of library data are supported. Existing built-ins are
never overwritten. Preserve community authorship and MPL notices in derivatives.

Install pinned local runtime assets on a fresh checkout with
python scripts/install_indicator_runtime.py. Runtime assets are ignored by Git;
package wheel hashes are verified against the distribution lockfile. AST source
generation uses the vendored astor 0.8.1 package and its included license.
