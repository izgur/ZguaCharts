(function (root) {
  "use strict";
  const escape = (value) => String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  const label = (value) => value === "1d" ? "D" : value === "1w" ? "W" : value === "1M" ? "M" : value === "1y" ? "Y" : value;
  const operators = [["crossing", "Crossing"], ["crossing-up", "Crossing up"], ["crossing-down", "Crossing down"], ["above", "Greater than"], ["below", "Less than"]];
  let polling, lastEventTime = Date.now() / 1000;
  async function api(url, options) {
    if (options?.method && options.method !== "GET") {
      options = { ...options, headers: { ...options.headers, "X-CSRF-Token": root.ChartAccounts?.csrfToken() || "" } };
    }
    const response = await fetch(url, options), body = await response.json();
    if (!response.ok) throw new Error(body.error || "Request failed");
    return body;
  }
  const icons = () => root.lucide?.createIcons();
  function timeframeControls(pane, frames) {
    frames = frames.includes('1s') ? ['1s', ...frames.filter(value => value !== '1s')] : frames;
    const select = pane.timeframeSelect, host = pane.node.querySelector(".timeframe-shortcuts"), menu = pane.node.querySelector(".timeframe-menu");
    const visible = root.ChartHeaderTimeframes?.visible(frames) || frames;
    host.innerHTML = visible.map((value) => `<button type="button" data-timeframe="${value}" title="${value}">${label(value)}</button>`).join("");
    menu.innerHTML = frames.map((value) => `<button type="button" role="menuitemradio" data-timeframe="${value}">${label(value)}</button>`).join("");
    const sync = () => {
      for (const button of [...host.children, ...menu.children]) {
        button.setAttribute("aria-pressed", String(button.dataset.timeframe === select.value));
        if (button.getAttribute("role")) button.setAttribute("aria-checked", String(button.dataset.timeframe === select.value));
      }
    };
    const overflow = pane.node.querySelector(".timeframe-overflow");
    overflow.hidden = true;
    for (const target of [host, menu]) target.onclick = (event) => {
      const button = event.target.closest("[data-timeframe]");
      if (!button || button.disabled) return;
      select.value = button.dataset.timeframe;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      pane.node.querySelector(".timeframe-overflow").open = false;
      sync();
    };
    sync();
  }
  function modal(title) {
    document.querySelector(".alert-backdrop")?.remove();
    const backdrop = document.createElement("div"); backdrop.className = "alert-backdrop";
    backdrop.innerHTML = `<section class="alert-dialog" role="dialog" aria-modal="true" aria-label="Create alert"><header><h2>${escape(title)}</h2><button type="button" class="alert-close" aria-label="Close alert"><i data-lucide="x"></i></button></header><div class="alert-body">Loading conditions...</div></section>`;
    (document.fullscreenElement || document.body).append(backdrop);
    const previousFocus = document.activeElement;
    const close = () => { backdrop.remove(); document.removeEventListener("keydown", onKey); previousFocus?.focus(); };
    const onKey = (event) => {
      if (event.key === "Escape") close();
      if (event.key === "Tab") {
        const items = [...backdrop.querySelectorAll("button:not(:disabled),input:not(:disabled),select,textarea")];
        if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items.at(-1).focus(); }
        if (!event.shiftKey && document.activeElement === items.at(-1)) { event.preventDefault(); items[0].focus(); }
      }
    };
    document.addEventListener("keydown", onKey);
    backdrop.querySelector(".alert-close").onclick = close;
    backdrop.addEventListener("click", (event) => { if (event.target === backdrop) close(); });
    icons(); backdrop.querySelector("button").focus();
    return { backdrop, body: backdrop.querySelector(".alert-body"), close };
  }
  function alertName(row) {
    return row.symbol + " " + row.timeframe + " / " + row.source + " - " + row.conditions.map((condition) =>
      (condition.field.split(":").pop()) + " " + (operators.find(([id]) => id === condition.operator)?.[1] || condition.operator) + " " +
      (condition.reference ? condition.reference.split(":").pop() : condition.value)).join(row.match === "any" ? " OR " : " AND ");
  }
  async function openAlert(pane, editing = null) {
    const market = editing ? { source: editing.source, symbol: editing.symbol, timeframe: editing.timeframe } : { source: pane.sourceSelect.value, symbol: pane.symbolSelect.value, timeframe: pane.timeframeSelect.value };
    const displaySymbol = editing ? editing.symbol : pane.node.querySelector(".selected-symbol").textContent;
    const dialog = modal((editing ? "Edit alert on " : "Create alert on ") + displaySymbol);
    try {
      const [options, saved] = await Promise.all([api("/api/chart-alerts/options?" + new URLSearchParams(market)), api("/api/chart-alerts")]);
      if (!dialog.backdrop.isConnected) return;
      const seriesOptions = options.series.map((row) => `<option value="${escape(row.id)}">${escape(row.label)}</option>`).join("");
      const expires = new Date(editing ? editing.expiresAt * 1000 : Date.now() + 30 * 86400000); expires.setMinutes(expires.getMinutes() - expires.getTimezoneOffset());
      const sorted = saved.alerts.slice().sort((a, b) => Number(b.enabled) - Number(a.enabled));
      dialog.body.innerHTML = `<section class="saved-alerts" aria-label="Saved alerts"><div class="saved-alert-heading"><h3>Alerts (${saved.alerts.filter((row) => row.enabled).length} active)</h3>${editing ? '<button type="button" class="alert-new"><i data-lucide="plus"></i>New alert</button>' : ''}</div><div>${sorted.map((row) => `<div class="saved-alert ${row.id === editing?.id ? 'selected' : ''}"><button type="button" class="saved-alert-edit" data-edit-alert="${escape(row.id)}" title="${escape(row.message)}"><span>${escape(alertName(row))}</span><small>${row.enabled ? "Active" : "Stopped"}${row.error ? " - " + escape(row.error) : ""}</small></button><button type="button" data-remove-alert="${escape(row.id)}" aria-label="Remove alert" title="Remove alert"><i data-lucide="trash-2"></i></button></div>`).join("") || '<p class="saved-alert-empty">No saved alerts</p>'}</div></section><form class="alert-form">
        <div class="alert-market"><b>${escape(displaySymbol)}</b><span>${escape(market.source)} / ${escape(market.timeframe)}</span></div>
        <div class="alert-conditions"></div><button type="button" class="alert-add"><i data-lucide="plus"></i>Add condition</button>
        <label>Match<select name="match"><option value="all">All conditions</option><option value="any">Any condition</option></select></label>
        <label class="alert-check"><input name="once" type="checkbox" checked>Once only</label>
        ${editing ? '<label class="alert-check"><input name="enabled" type="checkbox">Active</label>' : ''}
        <label>Expiration<input name="expires" type="datetime-local" required value="${expires.toISOString().slice(0, 16)}"></label>
        <label>Message<textarea name="message" maxlength="1000" rows="2" required></textarea></label>
        <fieldset><legend>Notifications</legend><label title="A temporary notification in the chart window"><input name="toast" type="checkbox" checked>On-screen popup</label><label><input name="sound" type="checkbox">Sound</label><label><input name="email" type="checkbox" ${options.emailReady ? "" : "disabled"}>Email</label></fieldset>
        ${options.emailReady ? "" : '<p class="alert-mail-status">Email unavailable: mail delivery is not configured.</p>'}
        <p class="alert-error" role="status"></p><footer><button type="button" class="alert-cancel">Cancel</button><button class="alert-create" type="submit">${editing ? "Save changes" : "Create"}</button></footer>
        </form>`;
      const form = dialog.body.querySelector("form"), host = form.querySelector(".alert-conditions");
      let customMessage = !!editing;
      const conditions = () => [...host.children].map((row) => ({ field: row.querySelector(".alert-field").value, operator: row.querySelector(".alert-operator").value,
        ...(row.querySelector(".alert-reference").value ? { reference: row.querySelector(".alert-reference").value } : { value: Number(row.querySelector(".alert-value").value) }) }));
      const message = () => { if (!customMessage) form.elements.message.value = displaySymbol + " " + conditions().map((row) => {
        const field = options.series.find((item) => item.id === row.field)?.label;
        const reference = options.series.find((item) => item.id === row.reference)?.label;
        return field + " " + operators.find(([id]) => id === row.operator)[1] + " " + (reference || row.value);
      }).join(form.elements.match.value === "any" ? " OR " : " AND "); };
      const add = (condition = null) => {
        if (host.children.length >= 8) return;
        const row = document.createElement("div"); row.className = "alert-condition";
        row.innerHTML = `<select class="alert-field" aria-label="Condition series">${seriesOptions}</select><select class="alert-operator" aria-label="Condition operator">${operators.map(([id, text]) => `<option value="${id}">${text}</option>`).join("")}</select><div class="alert-target"><select class="alert-reference" aria-label="Compare with"><option value="">Value</option>${seriesOptions}</select><input class="alert-value" aria-label="Condition value" type="number" step="any" required value="${options.series[0].value}"><button type="button" class="alert-remove-condition" aria-label="Remove condition"><i data-lucide="x"></i></button></div>`;
        row.querySelector(".alert-remove-condition").onclick = () => { if (host.children.length > 1) { row.remove(); message(); } };
        row.querySelector(".alert-reference").onchange = () => { const input = row.querySelector(".alert-value"); input.hidden = !!row.querySelector(".alert-reference").value; input.required = !input.hidden; message(); };
        row.querySelector(".alert-field").onchange = () => { row.querySelector(".alert-value").value = options.series.find((item) => item.id === row.querySelector(".alert-field").value)?.value ?? 0; message(); };
        row.addEventListener("input", message); host.append(row); icons(); message();
        if (condition) {
          for (const [selector, value] of [[".alert-field", condition.field], [".alert-operator", condition.operator], [".alert-reference", condition.reference || ""]]) {
            const select = row.querySelector(selector);
            if (value && ![...select.options].some((option) => option.value === value)) { const option = document.createElement("option"); option.value = value; option.textContent = value + " (unavailable)"; select.append(option); }
            select.value = value;
          }
          const input = row.querySelector(".alert-value"); input.value = condition.value ?? 0; input.hidden = !!condition.reference; input.required = !input.hidden;
        }
      };
      form.elements.message.oninput = () => { customMessage = true; };
      form.elements.match.onchange = message;
      form.querySelector(".alert-add").onclick = () => add();
      form.querySelector(".alert-cancel").onclick = dialog.close;
      if (editing) {
        editing.conditions.forEach(add); form.elements.message.value = editing.message; form.elements.match.value = editing.match;
        form.elements.once.checked = editing.once; form.elements.enabled.checked = editing.enabled;
        for (const field of ["toast", "sound", "email"]) form.elements[field].checked = !!editing.notifications[field];
      } else add();
      dialog.body.querySelectorAll("[data-edit-alert]").forEach((button) => { button.onclick = () => { const row = saved.alerts.find((item) => item.id === button.dataset.editAlert); dialog.close(); openAlert(pane, row); }; });
      if (dialog.body.querySelector(".alert-new")) dialog.body.querySelector(".alert-new").onclick = () => { dialog.close(); openAlert(pane); };
      dialog.body.querySelectorAll("[data-remove-alert]").forEach((button) => { button.onclick = async () => {
        try { await api("/api/chart-alerts/" + button.dataset.removeAlert, { method: "DELETE" }); dialog.close(); openAlert(pane); }
        catch (error) { form.querySelector(".alert-error").textContent = error.message; }
      }; });
      form.onsubmit = async (event) => {
        event.preventDefault(); const button = form.querySelector(".alert-create"); button.disabled = true;
        try {
          await api("/api/chart-alerts" + (editing ? "/" + editing.id : ""), { method: editing ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...market, conditions: conditions(), once: form.elements.once.checked, enabled: editing ? form.elements.enabled.checked : true,
            match: form.elements.match.value, expiresAt: new Date(form.elements.expires.value).getTime() / 1000, message: form.elements.message.value,
            notifications: { toast: form.elements.toast.checked, sound: form.elements.sound.checked, email: form.elements.email.checked } }) });
          dialog.close(); toast("Alert " + (editing ? "updated" : "created") + " on " + market.symbol);
        } catch (error) { form.querySelector(".alert-error").textContent = error.message; button.disabled = false; }
      };
      icons();
    } catch (error) { dialog.body.textContent = error.message; }
  }
  function toast(message) {
    const element = document.createElement("div"); element.className = "chart-alert-toast"; element.setAttribute("role", "status");
    element.textContent = message; const close = document.createElement("button"); close.textContent = "x"; close.setAttribute("aria-label", "Dismiss notification"); close.onclick = () => element.remove(); element.append(close);
    document.body.append(element); setTimeout(() => element.remove(), 15000);
  }
  function pollAlerts() {
    if (polling) return;
    let pending = false;
    polling = setInterval(async () => {
      if (pending) return;
      pending = true;
      try {
        const saved = await api("/api/chart-alerts");
        for (const event of saved.events.filter((row) => row.time > lastEventTime)) {
          if (event.notifications.toast || event.emailStatus === "failed") toast(event.message + (event.emailStatus === "failed" ? " · Email delivery failed" : ""));
          if (event.notifications.sound) {
            try { const Audio = root.AudioContext || root.webkitAudioContext; const context = new Audio(), oscillator = context.createOscillator(), gain = context.createGain(); oscillator.connect(gain); gain.connect(context.destination); gain.gain.value = 0.08; oscillator.frequency.value = 660; oscillator.start(); oscillator.stop(context.currentTime + 0.3); oscillator.onended = () => context.close(); } catch { /* Browser audio permission may require user interaction. */ }
          }
          lastEventTime = Math.max(lastEventTime, event.time);
        }
      } catch { /* A disconnected server cannot deliver browser notifications. */ }
      finally { pending = false; }
    }, 10000);
  }
  function attach(pane, callbacks) {
    pane.node.querySelector(".alert-button").onclick = () => openAlert(pane);
    const button = pane.node.querySelector(".replay-button");
    let replay, timer, rendering = false;
    const pause = () => {
      clearInterval(timer); timer = null;
      const play = replay?.host.querySelector(".replay-play");
      if (play) { play.setAttribute("aria-pressed", "false"); play.innerHTML = '<i data-lucide="play"></i>'; icons(); }
    };
    const render = async () => {
      if (!replay || rendering) return;
      rendering = true;
      const current = replay;
      const renderedIndex = current.index;
      try {
        current.host.querySelector(".replay-time").textContent = new Date(current.rows[current.index - 1]?.time * 1000).toLocaleString();
        current.host.querySelector(".replay-progress").textContent = current.index + "/" + current.rows.length;
        await callbacks.slice(current.rows.slice(0, current.index), current.rows);
      } catch (error) { pause(); toast("Replay paused: " + error.message); }
      finally { rendering = false; if (replay === current && current.index !== renderedIndex) render(); }
    };
    const step = async () => { if (!replay) return; if (replay.index >= replay.rows.length) { pause(); return; } replay.index++; await render(); };
    const select = () => { pause(); replay.selecting = true; pane.node.classList.add("replay-selecting"); replay.host.querySelector(".replay-time").textContent = "Select a starting candle"; };
    const exit = (restore = true) => {
      if (!replay) return;
      pause(); const rows = replay.rows; replay.host.remove(); replay = null; pane.replayActive = false;
      pane.node.classList.remove("replay-active", "replay-selecting"); button.setAttribute("aria-pressed", "false");
      callbacks.resume(rows, restore);
    };
    button.onclick = () => {
      if (replay) { exit(); return; }
      if (!pane.candles.length || pane.olderHistoryLoading) { toast("Wait for chart history to load"); return; }
      const host = document.createElement("div"); host.className = "bar-replay-controls";
      host.innerHTML = `<button type="button" class="replay-select" title="Select starting candle" aria-label="Select replay bar"><i data-lucide="mouse-pointer-2"></i></button><button type="button" class="replay-play" title="Play or pause" aria-label="Play replay" aria-pressed="false"><i data-lucide="play"></i></button><button type="button" class="replay-step" title="Next candle" aria-label="Next replay candle"><i data-lucide="step-forward"></i></button><select class="replay-speed" aria-label="Replay speed"><option value="2000">0.5x</option><option value="1000" selected>1x</option><option value="500">2x</option><option value="250">4x</option></select><span class="replay-time"></span><span class="replay-progress"></span><button type="button" class="replay-exit" title="Exit replay" aria-label="Exit replay"><i data-lucide="x"></i></button>`;
      pane.node.querySelector(".main-chart-wrap").append(host);
      for (const [value, text] of [[125, "8x"], [62.5, "16x"]]) {
        const option = document.createElement("option"); option.value = value; option.textContent = text;
        host.querySelector(".replay-speed").append(option);
      }
      replay = { host, rows: pane.candles.slice(), index: pane.candles.length, selecting: true };
      pane.replayActive = true; pane.node.classList.add("replay-active"); button.setAttribute("aria-pressed", "true"); callbacks.pause();
      host.querySelector(".replay-select").onclick = select; host.querySelector(".replay-exit").onclick = () => exit();
      const play = () => { if (timer) { pause(); return; } if (replay.selecting) return; const toggle = host.querySelector(".replay-play"); toggle.setAttribute("aria-pressed", "true"); toggle.innerHTML = '<i data-lucide="pause"></i>'; icons(); timer = setInterval(step, Number(host.querySelector(".replay-speed").value)); };
      host.querySelector(".replay-play").onclick = play; host.querySelector(".replay-step").onclick = () => { pause(); if (!replay.selecting) step(); };
      host.querySelector(".replay-speed").onchange = () => { if (timer) { pause(); play(); } };
      select(); icons();
    };
    pane.chart.subscribeClick((param) => {
      if (!replay?.selecting || typeof param.time !== "number") return;
      const index = replay.rows.findIndex((row) => row.time === param.time);
      if (index < 1) { toast("Choose a candle after the first bar"); return; }
      replay.index = index; replay.selecting = false; pane.node.classList.remove("replay-selecting"); render();
    });
    pane.exitReplay = exit;
    pollAlerts();
    return () => { pause(); replay?.host.remove(); replay = null; };
  }
  root.ChartWorkspace = { attach, timeframeControls, label };
})(window);
