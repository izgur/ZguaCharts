(function (root) {
  "use strict";
  const STORE = "zgua-custom-indicators-v1", MAX_CODE = 100000;
  let hooks = {}, queue = Promise.resolve();
  const key = () => root.ChartAccounts?.key(STORE) || STORE;
  function records() {
    try {
      const data = JSON.parse(localStorage.getItem(key()));
      return Array.isArray(data) ? data.filter((item) => /^custom_[a-z0-9-]{1,64}$/.test(item?.id) && typeof item.name === "string" && typeof item.code === "string" && item.code.length <= MAX_CODE && Array.isArray(item.schema)).slice(0, 20) : [];
    } catch { return []; }
  }
  const get = (id) => records().find((item) => item.id === id);
  const list = () => records().map((item) => ({ id: item.id, label: item.name, parameters: "Custom Python", schema: item.schema, placement: item.placement, custom: true }));
  function suggest(name) {
    const base = name.replace(/ - Python$/, "").slice(0, 60) + " - Custom", names = new Set(records().map((row) => row.name.toLowerCase()));
    let result = base, count = 2;
    while (names.has(result.toLowerCase())) result = base + " " + count++;
    return result;
  }
  function validateResult(result, candles) {
    const allowed = new Set(candles.map((row) => row.time)), payload = result?.payload;
    if (!payload || !Array.isArray(payload.overlays) || !Array.isArray(payload.panes) || payload.panes.length > 4) throw new Error("Return a chart payload with overlays and panes arrays.");
    let count = 0, segmentBudget = 200;
    const series = (item) => {
      if (++count > 32 || !Array.isArray(item.data) || item.data.length > candles.length) throw new Error("Too many indicator series or points.");
      let previous = -Infinity;
      const data = item.data.map((point) => {
        if (!allowed.has(point.time) || point.time <= previous || point.value !== null && !Number.isFinite(point.value)) throw new Error("Points must use ordered candle timestamps and finite numbers or null.");
        previous = point.time;
        return { time: point.time, value: point.value, ...(typeof point.color === "string" && /^#[a-f0-9]{3,8}$/i.test(point.color) ? { color: point.color } : {}) };
      });
      const markers = (Array.isArray(item.markers) ? item.markers : []).slice(0, candles.length).filter((marker) => allowed.has(marker.time)).map((marker) => ({ time: marker.time, text: String(marker.text || "").slice(0, 80), position: ["aboveBar", "belowBar", "inBar"].includes(marker.position) ? marker.position : "aboveBar", shape: ["arrowUp", "arrowDown", "circle", "square"].includes(marker.shape) ? marker.shape : "circle", color: /^#[a-f0-9]{3,8}$/i.test(marker.color) ? marker.color : "#4dabf7" }));
      markers.sort((a, b) => a.time - b.time);
      const offset = Number.isInteger(item.offset) && Math.abs(item.offset) <= 500 ? item.offset : 0;
      const segments = (Array.isArray(item.segments) ? item.segments : []).slice(-200).filter(segment => allowed.has(segment.confirmedAt) && Array.isArray(segment.data) && segment.data.length === 2 && segment.data.every(point => allowed.has(point.time) && Number.isFinite(point.value)) && segment.data[0].time < segment.data[1].time && segment.data[1].time < segment.confirmedAt).slice(0,segmentBudget).map(segment => ({confirmedAt:segment.confirmedAt,data:segment.data.map(point=>({time:point.time,value:point.value}))}));
      segmentBudget -= segments.length;
      const events = (Array.isArray(item.events) ? item.events : []).slice(-200).filter(event=>allowed.has(event.confirmedAt)&&allowed.has(event.pivotTime)&&event.confirmedAt>event.pivotTime).map(event=>({confirmedAt:event.confirmedAt,pivotTime:event.pivotTime,priceConfirmed:event.priceConfirmed===true,studies:(Array.isArray(event.studies)?event.studies:[]).slice(0,8).map(name=>String(name).slice(0,40))}));
      return { name: String(item.name || "Indicator").slice(0, 100), type: item.type === "histogram" ? "histogram" : "line", color: /^#[a-f0-9]{3,8}$/i.test(item.color) ? item.color : "#4dabf7", guide: item.guide === true, data, markers, segments, events, markerGroup:item.markerGroup==='divergence'?'divergence':undefined, offset, cloud: ['a','b'].includes(item.cloud) ? item.cloud : undefined, pointOnly: item.pointOnly === true };
    };
    const clean = { overlays: payload.overlays.map(series), panes: payload.panes.map((pane) => ({ title: String(pane.title || "Indicator").slice(0, 100), markers: series({data: [], markers: pane.markers}).markers, series: Array.isArray(pane.series) ? pane.series.map(series) : [] })) };
    const defaults = result.defaults;
    if (!defaults || typeof defaults !== "object" || Array.isArray(defaults) || Object.keys(defaults).length > 40 || Object.entries(defaults).some(([name, value]) => !/^[a-zA-Z][a-zA-Z0-9_]{0,39}$/.test(name) || typeof value !== "boolean" && !Number.isFinite(value))) throw new Error("DEFAULTS must contain up to 40 named numeric or checkbox inputs.");
    return { payload: clean, defaults, placement: result.placement === "overlay" ? "overlay" : "pane" };
  }
  function execute(code, candles, params = {}, isCurrent = () => true) {
    if (code.length > MAX_CODE || candles.length > 5000) return Promise.reject(new Error("Custom indicators support up to 100 KB of code and 5,000 candles."));
    const job = () => new Promise((resolve, reject) => {
      if (!isCurrent()) { resolve({ payload: { overlays: [], panes: [] }, defaults: {}, placement: "pane" }); return; }
      const worker = new Worker("/api/indicator-source/worker.js");
      let timer, started = false;
      const finish = (error, result) => { clearTimeout(timer); worker.terminate(); error ? reject(error) : resolve(result); };
      const deadline = (ms) => { clearTimeout(timer); timer = setTimeout(() => finish(new Error("Python worker timed out and was stopped.")), ms); };
      deadline(90000);
      worker.onerror = () => finish(new Error("Python runtime could not load. Check the local runtime installation."));
      worker.onmessage = ({ data }) => {
        if (data?.type === "ready") { if (!started) { started = true; deadline(10000); } return; }
        if (data?.type === "error") { finish(new Error(String(data.error).slice(0, 3000))); return; }
        if (data?.type === "result") {
          try {
            if (typeof data.result !== "string" || data.result.length > 4000000) throw new Error("Invalid indicator output.");
            finish(null, validateResult(JSON.parse(data.result), candles));
          } catch (error) { finish(error); }
        }
      };
      worker.postMessage({ code, candles, params });
    });
    const result = queue.then(job, job); queue = result.catch(() => {}); return result;
  }
  async function calculate(ids, candles, settings, isCurrent = () => true) {
    const payload = { overlays: [], panes: [] };
    for (const id of ids) {
      if (!isCurrent()) break;
      const record = get(id); if (!record) throw new Error("Custom indicator was removed: " + id);
      const result = await execute(record.code, candles, settings?.[id] || {}, isCurrent);
      for (const overlay of result.payload.overlays) { overlay.indicatorId = id; payload.overlays.push(overlay); }
      for (const pane of result.payload.panes) { pane.id = id; pane.title = record.name; for (const series of pane.series) series.indicatorId = id; payload.panes.push(pane); }
    }
    return payload;
  }
  function store(record) {
    const saved = records(), index = saved.findIndex((item) => item.id === record.id);
    if (index >= 0) saved[index] = record; else {
      if (saved.length >= 20) throw new Error("Maximum 20 custom indicators. Remove one before adding another.");
      saved.push(record);
    }
    const encoded = JSON.stringify(saved);
    if (encoded.length > 1500000) throw new Error("Custom indicator library exceeds 1.5 MB.");
    localStorage.setItem(key(), encoded); root.ChartAccounts?.changed(); hooks.changed?.();
  }
  async function openEditor(id) {
    const existing = get(id), dialog = document.createElement("dialog"); dialog.className = "indicator-source-dialog indicator-editor";
    dialog.innerHTML = '<header><h2>Python indicator</h2><button type="button" class="source-close" title="Close" aria-label="Close source"><i data-lucide="x"></i></button></header><label class="indicator-editor-name">Name<input maxlength="80" aria-label="Indicator name"></label><p class="source-origin"></p><textarea class="indicator-code-editor" aria-label="Python indicator code" spellcheck="false" autocapitalize="off"></textarea><footer><span role="status" class="source-status">Loading...</span><button type="button" class="source-copy" title="Copy source" aria-label="Copy source"><i data-lucide="copy"></i></button><button type="button" class="source-download" title="Download source" aria-label="Download source"><i data-lucide="download"></i></button><button type="button" class="source-run" title="Test indicator" aria-label="Test indicator"><i data-lucide="play"></i></button><button type="button" class="source-save-copy">Save as new</button><button type="button" class="source-save">Save</button><button type="button" class="source-delete" title="Delete custom indicator" aria-label="Delete custom indicator"><i data-lucide="trash-2"></i></button></footer>';
    (document.fullscreenElement || document.body).append(dialog); dialog.showModal();
    const editor = dialog.querySelector("textarea"), name = dialog.querySelector("input"), status = dialog.querySelector(".source-status");
    const buttons = [...dialog.querySelectorAll("footer button")]; buttons.forEach((button) => { button.disabled = true; });
    let busy = false, dirty = false, ready = false;
    dialog.querySelector(".source-save").hidden = !existing;
    dialog.querySelector(".source-delete").hidden = !existing;
    dialog.querySelector(".source-close").onclick = () => { if (!dirty || confirm("Discard unsaved indicator changes?")) dialog.close(); };
    dialog.addEventListener("cancel", (event) => { if (dirty && !confirm("Discard unsaved indicator changes?")) event.preventDefault(); });
    dialog.addEventListener("close", () => dialog.remove());
    editor.oninput = name.oninput = () => { dirty = true; };
    root.lucide?.createIcons({ root: dialog });
    try {
      let payload;
      if (existing) payload = { label: existing.name, origin: existing.origin || "My indicator", files: [{ code: existing.code }] };
      else { const response = await fetch("/api/indicator-source/" + encodeURIComponent(id || "sma")); payload = await response.json(); if (!response.ok) throw new Error(payload.error || "Could not load source"); }
      if (!dialog.isConnected) return;
      editor.value = payload.files[0].code; name.value = existing ? existing.name : id ? suggest(payload.label) : "My SMA";
      dialog.querySelector("h2").textContent = existing ? existing.name : payload.label + " - Python source";
      dialog.querySelector(".source-origin").textContent = payload.origin;
      status.textContent = ""; ready = true; buttons.forEach((button) => { button.disabled = false; });
    } catch (error) { status.textContent = error.message; return; }
    editor.addEventListener("keydown", (event) => {
      if (event.key !== "Tab") return;
      event.preventDefault(); editor.setRangeText("    ", editor.selectionStart, editor.selectionEnd, "end"); dirty = true;
    });
    const test = async (saving = false) => {
      const rows = hooks.candles?.() || [];
      if (!rows.length) throw new Error("Wait for chart candles before testing this indicator.");
      status.textContent = saving ? "Saving..." : "Running Python...";
      return execute(editor.value, rows.slice(-5000));
    };
    const action = async (task) => {
      if (!ready || busy) return;
      busy = true; buttons.forEach((button) => { button.disabled = true; });
      editor.readOnly = true; name.readOnly = true;
      try { await task(); } catch (error) { status.textContent = error.message; }
      finally { busy = false; editor.readOnly = false; name.readOnly = false; if (dialog.isConnected) buttons.forEach((button) => { button.disabled = false; }); }
    };
    const save = (copy) => action(async () => {
      const title = name.value.trim(); if (title.length < 2 || title.length > 80) throw new Error("Use a name between 2 and 80 characters.");
      const result = await test(true);
      if (!dialog.isConnected) return;
      const schema = Object.entries(result.defaults).map(([field, value]) => ({ name: field, label: field.replaceAll("_", " "), default: value, min: typeof value === "boolean" ? 0 : -1000000, max: typeof value === "boolean" ? 1 : 1000000, step: Number.isInteger(value) ? 1 : 0.01, ...(typeof value === "boolean" || ["heikin_ashi", "true_range", "legacy_bb", "atr_sma", "source_close", "draw_lines", "engulfing", "sweep"].includes(field) || /_(enabled|bullish|bearish)$/.test(field) ? { type: "boolean", min: 0, max: 1, step: 1 } : {}) }));
      let titleToSave = title;
      if (copy && existing && title === existing.name) titleToSave = suggest(title);
      if (records().some((record) => record.name.toLowerCase() === titleToSave.toLowerCase() && (copy || record.id !== existing?.id))) throw new Error("This indicator name is already used.");
      store({ id: !copy && existing ? existing.id : "custom_" + crypto.randomUUID(), name: titleToSave, code: editor.value, schema, placement: result.placement, origin: dialog.querySelector(".source-origin").textContent });
      dirty = false; dialog.close();
    });
    dialog.querySelector(".source-save-copy").onclick = () => save(true);
    dialog.querySelector(".source-save").onclick = () => save(false);
    dialog.querySelector(".source-run").onclick = () => action(async () => { await test(); status.textContent = "Test passed"; });
    dialog.querySelector(".source-copy").onclick = () => action(async () => { await navigator.clipboard.writeText(editor.value); status.textContent = "Source copied"; });
    dialog.querySelector(".source-download").onclick = () => {
      const url = URL.createObjectURL(new Blob([editor.value], { type: "text/x-python" })), link = document.createElement("a");
      link.href = url; link.download = (existing?.id || id || "my_indicator") + ".py"; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    };
    dialog.querySelector(".source-delete").onclick = () => {
      if (!existing || !confirm("Delete " + existing.name + "?")) return;
      localStorage.setItem(key(), JSON.stringify(records().filter((item) => item.id !== existing.id)));
      root.ChartAccounts?.changed(); hooks.changed?.(); dirty = false; dialog.close();
    };
  }
  root.ChartCustomIndicators = { configure: (value) => { hooks = value; }, list, get, has: (id) => !!get(id), openEditor, calculate, execute, validateResult, suggest };
})(window);
