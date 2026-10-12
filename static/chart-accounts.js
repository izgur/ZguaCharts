(function (root) {
  "use strict";
  let user = null, csrf = "", timer, saving = false, dirty = false, registrationEnabled = false;
  const baseKeys = ["tvk-dashboard-state", "zgua-ui-preferences", "zgua-default-chart-layout-v2", "zgua-manual-backtest-comparisons", "zgua-good-people-presets", "zgua-layouts-v1"];
  const key = (name) => user ? `account:${user.id}:${name}` : name;
  const accepted = (name) => baseKeys.includes(name) || ["zgua-indicator-favourites-v1", "zgua-custom-indicators-v1", "zgua-custom-tactics-v1"].includes(name) || name.startsWith("zgua-drawings-v1:") || name.startsWith("zgua-drawing-controls-v1:");
  const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const usernamePattern = '[a-zA-Z0-9_.\\-]{3,40}';
  async function api(path, method = "GET", body) {
    const response = await fetch("/api/account/" + path, { method, headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf }, ...(body ? { body: JSON.stringify(body) } : {}) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Account request failed");
    if (result.csrf) csrf = result.csrf;
    return result;
  }
  function capture() {
    const values = {}, prefix = user ? `account:${user.id}:` : "";
    for (let index = 0; index < localStorage.length; index++) {
      const name = localStorage.key(index);
      if (!name.startsWith(prefix)) continue;
      const plain = name.slice(prefix.length);
      if (accepted(plain)) values[plain] = localStorage.getItem(name);
    }
    return values;
  }
  function restore(values) {
    for (const name of Object.keys(capture())) localStorage.removeItem(key(name));
    for (const [name, value] of Object.entries(values || {})) if (accepted(name) && typeof value === "string") localStorage.setItem(key(name), value);
  }
  function layouts() {
    try { const value = JSON.parse(localStorage.getItem(key("zgua-layouts-v1"))); if (value && Array.isArray(value.items)) return value; } catch { /* Start with an empty layout library. */ }
    return { active: null, autosave: true, items: [] };
  }
  function storeLayouts(value) { localStorage.setItem(key("zgua-layouts-v1"), JSON.stringify(value)); }
  function chartSnapshot() { const values = capture(); delete values["zgua-layouts-v1"]; return values; }
  async function persist() {
    if (!user || saving) { if (saving) dirty = true; return; }
    saving = true; dirty = false;
    try { await api("profile", "PUT", capture()); document.querySelector("#layout-save-status").textContent = "Saved " + new Date().toLocaleTimeString(); }
    catch (error) { document.querySelector("#layout-save-status").textContent = error.message; }
    finally { saving = false; if (dirty) changed(); }
  }
  function changed() {
    const value = layouts();
    if (value.autosave) {
      const active = value.items.find((item) => item.id === value.active);
      if (active) { active.values = chartSnapshot(); active.saved = Date.now(); storeLayouts(value); }
    }
    clearTimeout(timer); timer = setTimeout(persist, 600);
  }
  function dialog(title, build) {
    const backdrop = document.createElement("div"); backdrop.className = "alert-backdrop";
    const panel = document.createElement("section"); panel.className = "alert-dialog account-dialog"; panel.setAttribute("role", "dialog"); panel.setAttribute("aria-modal", "true");
    const heading = document.createElement("header"), name = document.createElement("h2"), close = document.createElement("button");
    name.textContent = title; close.type = "button"; close.textContent = "Close"; heading.append(name, close);
    const content = document.createElement("div"); content.className = "alert-body"; panel.append(heading, content); backdrop.append(panel);
    (document.fullscreenElement || document.body).append(backdrop);
    const previous = document.activeElement;
    const dismiss = () => { backdrop.remove(); document.removeEventListener("keydown", keyboard); previous?.focus(); };
    const keyboard = (event) => {
      if (event.key === "Escape") dismiss();
      if (event.key === "Tab") {
        const controls = [...panel.querySelectorAll("button,input,select,textarea")].filter((item) => !item.disabled && item.getClientRects().length);
        if (event.shiftKey && document.activeElement === controls[0]) { event.preventDefault(); controls.at(-1).focus(); }
        else if (!event.shiftKey && document.activeElement === controls.at(-1)) { event.preventDefault(); controls[0].focus(); }
      }
    };
    document.addEventListener("keydown", keyboard); close.onclick = dismiss; backdrop.onclick = (event) => { if (event.target === backdrop) dismiss(); };
    build(content, dismiss); content.querySelector("input,button")?.focus();
  }
  function accountDialog(notice = "", success = true, initialMode = "login") {
    dialog(initialMode === 'forgot' ? 'Reset password' : user ? "Account: " + user.name : "ZguaCharts Account", (content) => {
      if (user && initialMode !== 'forgot') {
        settingsDialogContent(content); return;
      }
      content.closest(".account-dialog").classList.add("account-auth-dialog");
      content.innerHTML = '<nav class="account-tabs" aria-label="Account access"><button type="button" data-mode="login" aria-pressed="true">Sign in</button><button type="button" data-mode="register" aria-pressed="false">Register</button></nav><div class="account-form-host"></div>';
      const host = content.querySelector(".account-form-host");
      function show(mode) {
        const register = mode === "register", resend = mode === "resend" || mode === "forgot", forgot = mode === "forgot";
        content.querySelectorAll(".account-tabs button").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.mode === mode)));
        const names = register ? '<div class="account-name-row"><label>First name<input name="firstName" autocomplete="given-name" maxlength="80" required></label><label>Surname<input name="lastName" autocomplete="family-name" maxlength="80" required></label></div>' : '';
        const username = resend ? '' : `<label>${register ? "Username" : "Username or email"}<input name="username" autocomplete="username" maxlength="${register ? 40 : 254}" ${register ? 'pattern="' + usernamePattern + '" minlength="3"' : ''} required></label>`;
        const email = register || resend ? '<label>Email address<input name="email" type="email" autocomplete="email" maxlength="254" required></label>' : '';
        const password = resend ? '' : `<label>Password<div class="account-password"><input name="password" type="password" autocomplete="${register ? "new-password" : "current-password"}" minlength="12" maxlength="128" required><button type="button" class="password-visibility" aria-label="Show password"><i data-lucide="eye"></i></button></div></label>`;
        const confirm = register ? '<label>Confirm password<input name="confirmPassword" type="password" autocomplete="new-password" minlength="12" maxlength="128" required></label><small class="account-hint">Use 12 or more characters. Confirm your email before signing in.</small>' : '';
        const mailNotice = mode !== "login" && !registrationEnabled ? '<p class="account-email-notice" role="status">Email delivery is unavailable. Please try again once the mail service is connected.</p>' : '';
        host.innerHTML = `<form class="account-form">${names}${username}${email}${password}${confirm}${mode === "login" ? '<label class="account-remember"><input name="remember" type="checkbox" checked>Remember me for 30 days</label>' : ''}${mailNotice}<p class="account-error ${notice && success ? 'account-success' : ''}" role="status" aria-live="polite">${escape(notice)}</p><button type="submit" class="account-primary" ${mode !== "login" && !registrationEnabled ? 'disabled' : ''}>${register ? "Create account" : forgot ? "Send reset link" : resend ? "Resend verification email" : "Sign in"}</button><button type="button" class="account-secondary">${resend ? "Back to sign in" : "Resend verification email"}</button>${mode === "login" ? '<button type="button" class="account-secondary account-forgot">Forgot password?</button>' : ''}</form>`;
        notice = "";
        const form = host.querySelector("form");
        if (mode === 'login') {
          const label = document.createElement('label'); label.textContent = 'Authenticator or recovery code (if enabled)';
          const input = document.createElement('input'); input.name = 'twoFactorCode'; input.autocomplete = 'one-time-code'; input.maxLength = 80;
          label.append(input); form.querySelector('[type="submit"]').before(label);
        }
        form.querySelector(".account-secondary").onclick = () => show(resend ? "login" : "resend");
        if (form.querySelector(".account-forgot")) form.querySelector(".account-forgot").onclick = () => {
          const email = form.elements.namedItem('username').value.trim(); show('forgot');
          if (email.includes('@')) host.querySelector('[name="email"]').value = email;
        };
        const toggle = form.querySelector(".password-visibility");
        if (toggle) toggle.onclick = () => { const input = form.elements.namedItem("password"); input.type = input.type === "password" ? "text" : "password"; toggle.setAttribute("aria-label", input.type === "password" ? "Show password" : "Hide password"); };
        const confirmation = form.elements.namedItem("confirmPassword");
        if (confirmation) {
          const validate = () => confirmation.setCustomValidity(confirmation.value === form.elements.namedItem("password").value ? "" : "Passwords do not match.");
          confirmation.oninput = validate; form.elements.namedItem("password").oninput = validate;
        }
        form.onsubmit = async (event) => {
          event.preventDefault(); const submit = form.querySelector('[type="submit"]'); submit.disabled = true;
          const body = Object.fromEntries(new FormData(form)); body.remember = form.elements.namedItem("remember")?.checked === true;
          try {
            const result = await api(forgot ? "forgot-password" : resend ? "resend-verification" : mode, "POST", body);
            if (mode === "login") location.reload();
            else { const message = form.querySelector(".account-error"); message.classList.add("account-success"); message.textContent = result.message; if (register) { form.reset(); show("login"); host.querySelector(".account-error").classList.add("account-success"); host.querySelector(".account-error").textContent = result.message; } }
          } catch (error) { const message = form.querySelector(".account-error"); message.classList.remove("account-success"); message.textContent = error.message; }
          finally { submit.disabled = mode !== "login" && !registrationEnabled; }
        };
        root.lucide?.createIcons(); host.querySelector("input")?.focus();
      }
      content.querySelectorAll(".account-tabs button").forEach((button) => button.onclick = () => show(button.dataset.mode));
      show(initialMode);
    });
  }
  function setupAccountMenu() {
    const button = document.querySelector('#account-button'), menu = document.createElement('div');
    menu.id = 'account-dropdown'; menu.className = 'account-dropdown'; menu.hidden = true; menu.setAttribute('role','menu'); menu.setAttribute('aria-label','Account');
    button.setAttribute('aria-haspopup','menu'); button.setAttribute('aria-controls',menu.id); button.setAttribute('aria-expanded','false');
    const close = (focus = false) => { menu.hidden = true; button.setAttribute('aria-expanded','false'); if (focus) button.focus(); };
    const command = (label, icon, action, href) => {
      const item = document.createElement(href ? 'a' : 'button'); item.setAttribute('role','menuitem');
      if (href) item.href = href; else item.type = 'button';
      item.innerHTML = `<i data-lucide="${icon}"></i><span>${label}</span>`;
      item.onclick = () => { close(true); action?.(); }; menu.append(item);
    };
    const position = () => {
      const box = button.getBoundingClientRect();
      menu.style.top = Math.max(8, Math.min(box.bottom + 5, innerHeight - menu.offsetHeight - 8)) + 'px';
      menu.style.right = Math.max(8, innerWidth - box.right) + 'px';
    };
    button.onclick = () => {
      if (!menu.hidden) { close(); return; }
      menu.replaceChildren();
      if (user) {
        command('Edit account','user-round-pen',() => accountDialog());
        if (user.role === 'admin') command('Admin dashboard','shield-check',null,'/api/account/admin');
        command('Sign out','log-out',async () => {
          try { await persist(); await api('logout','POST',{}); location.reload(); }
          catch (error) { accountDialog(error.message,false); }
        });
      } else {
        command('Sign in','log-in',() => accountDialog());
        command('Register','user-round-plus',() => accountDialog('',true,'register'));
      }
      command('Forgot password?','key-round',() => accountDialog('',true,'forgot'));
      (document.fullscreenElement || document.body).append(menu); menu.hidden = false; position();
      button.setAttribute('aria-expanded','true'); root.lucide?.createIcons(); menu.querySelector('[role="menuitem"]').focus();
    };
    document.addEventListener('pointerdown',event => { if (!menu.hidden && !menu.contains(event.target) && !button.contains(event.target)) close(); });
    document.addEventListener('keydown',event => {
      if (menu.hidden) return;
      if (event.key === 'Escape') { event.preventDefault(); close(true); }
      else if (['ArrowDown','ArrowUp','Home','End'].includes(event.key)) {
        event.preventDefault(); const items = [...menu.querySelectorAll('[role="menuitem"]')], index = items.indexOf(document.activeElement);
        items[event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length].focus();
      } else if (event.key === 'Tab') close();
    });
    window.addEventListener('resize',() => { if (!menu.hidden) position(); });
  }
  async function settingsDialogContent(content) {
    content.closest(".account-dialog").classList.add("account-settings-dialog");
    content.innerHTML = '<p role="status">Loading account settings...</p>';
    try { user = (await api("session")).user; } catch (error) { content.textContent = error.message; return; }
    if (!user) { content.textContent = "Session expired. Close and sign in again."; return; }
    let avatar = user.avatar || "";
    content.innerHTML = `<nav class="account-tabs" aria-label="Account settings"><button type="button" data-tab="profile" aria-pressed="true">Profile</button><button type="button" data-tab="security" aria-pressed="false">Security</button><button type="button" data-tab="appearance" aria-pressed="false">Appearance</button></nav>
      <section data-account-section="profile"><form class="account-form account-details"><div class="account-avatar-row"><div class="account-avatar-preview"></div><label class="avatar-upload">Avatar<input name="avatar" type="file" accept="image/png,image/jpeg,image/webp"></label><button type="button" class="avatar-remove" title="Remove avatar" aria-label="Remove avatar"><i data-lucide="trash-2"></i></button></div><div class="account-name-row"><label>First name<input name="firstName" value="${escape(user.first_name)}" maxlength="80" required></label><label>Surname<input name="lastName" value="${escape(user.last_name)}" maxlength="80" required></label></div><label>Username<input name="username" value="${escape(user.name)}" pattern="${usernamePattern}" maxlength="40" required></label><label>Email address<input value="${escape(user.email)}" readonly></label><p class="account-error" role="status"></p><button type="submit" class="account-primary">Save profile</button></form></section>
      <section data-account-section="security" hidden><form class="account-form account-change-password"><h3>Password</h3><label>Current password<input name="currentPassword" type="password" autocomplete="current-password" maxlength="128" required></label><label>New password<input name="password" type="password" autocomplete="new-password" minlength="12" maxlength="128" required></label><label>Confirm new password<input name="confirmPassword" type="password" autocomplete="new-password" minlength="12" maxlength="128" required></label><p class="account-error" role="status"></p><button type="submit" class="account-primary">Change password</button></form><form class="account-form account-change-email"><h3>Email address</h3><label>New email<input name="email" type="email" maxlength="254" required></label><label>Current password<input name="currentPassword" type="password" autocomplete="current-password" maxlength="128" required></label><p class="account-error" role="status"></p><button type="submit">Send confirmation</button></form><button type="button" class="account-revoke">Sign out other sessions</button><p class="account-session-status" role="status"></p></section>
      <section data-account-section="appearance" hidden><div class="account-typography"></div><button type="button" class="account-font-reset">Reset font sizes</button></section><footer class="account-settings-footer"><button type="button" class="account-logout"><i data-lucide="log-out"></i>Sign out</button></footer>`;
    content.querySelectorAll("[data-tab]").forEach((button) => { button.onclick = () => {
      content.querySelectorAll("[data-tab]").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
      content.querySelectorAll("[data-account-section]").forEach((section) => { section.hidden = section.dataset.accountSection !== button.dataset.tab; });
    }; });
    const preview = content.querySelector(".account-avatar-preview");
    const refreshAvatar = () => { preview.replaceChildren(); if (avatar) { const image = document.createElement("img"); image.src = avatar; image.alt = "Your avatar"; preview.append(image); } else preview.textContent = (user.first_name || user.name).slice(0, 1).toUpperCase(); };
    refreshAvatar();
    content.querySelector(".avatar-remove").onclick = () => { avatar = ""; refreshAvatar(); };
    content.querySelector('[name="avatar"]').onchange = async (event) => {
      const file = event.target.files[0], status = content.querySelector(".account-details .account-error");
      if (!file) return;
      if (!['image/png','image/jpeg','image/webp'].includes(file.type) || file.size > 5 * 1024 * 1024) { status.textContent = "Choose a PNG, JPEG or WebP under 5 MB."; return; }
      try {
        const bitmap = await createImageBitmap(file), canvas = document.createElement("canvas");
        if (bitmap.width > 10000 || bitmap.height > 10000) { bitmap.close(); throw new Error("Image dimensions are too large."); }
        canvas.width = canvas.height = 128;
        const side = Math.min(bitmap.width, bitmap.height);
        canvas.getContext("2d").drawImage(bitmap, (bitmap.width-side)/2, (bitmap.height-side)/2, side, side, 0, 0, 128, 128);
        bitmap.close(); avatar = canvas.toDataURL("image/png"); refreshAvatar(); status.textContent = "";
      } catch (error) { status.textContent = error.message || "Could not load that image."; }
    };
    const wire = (selector, endpoint, method, body, after) => {
      const form = content.querySelector(selector);
      form.onsubmit = async (event) => {
        event.preventDefault(); const button = form.querySelector('[type="submit"]'), status = form.querySelector(".account-error"); button.disabled = true;
        try { const result = await api(endpoint, method, body(form)); form.querySelectorAll('[type="password"]').forEach((input) => { input.value = ""; }); status.classList.add("account-success"); status.textContent = result.message || "Profile saved."; if (after) await after(); }
        catch (error) { status.classList.remove("account-success"); status.textContent = error.message; } finally { button.disabled = false; }
      };
    };
    wire(".account-details", "details", "PUT", (form) => ({ ...Object.fromEntries(new FormData(form)), avatar }), async () => { user = (await api("session")).user; refreshAccountButton(); });
    wire(".account-change-password", "password", "PUT", (form) => Object.fromEntries(new FormData(form)));
    if (user.twoFactorEnabled) {
      for (const selector of ['.account-change-password','.account-change-email']) {
        const form = content.querySelector(selector), label = document.createElement('label'); label.textContent = 'Authenticator or recovery code';
        const input = document.createElement('input'); input.name = 'twoFactorCode'; input.autocomplete = 'one-time-code'; input.maxLength = 80; input.required = true;
        label.append(input); form.querySelector('[type="submit"]').before(label);
      }
    }
    wire(".account-change-email", "email", "POST", (form) => Object.fromEntries(new FormData(form)));
    content.querySelector(".account-revoke").onclick = async () => { const status = content.querySelector(".account-session-status"); try { status.textContent = (await api("sessions/revoke", "POST", {})).message; } catch (error) { status.textContent = error.message; } };
    const typography = content.querySelector(".account-typography"), fonts = root.ChartTypography;
    if (fonts) {
      typography.innerHTML = fonts.fields.map((field) => `<label>${escape(field.label)}<input type="number" data-account-font="${field.key}" aria-label="${escape(field.label)} font size" min="${field.min}" max="${field.max}" value="${fonts.get(field.key)}"><span>px</span></label>`).join("");
      typography.querySelectorAll("input").forEach((input) => { input.oninput = () => { if (input.value && input.checkValidity()) fonts.set(input.dataset.accountFont, Number(input.value)); }; });
      content.querySelector(".account-font-reset").onclick = () => { fonts.reset(); typography.querySelectorAll("input").forEach((input) => { input.value = fonts.get(input.dataset.accountFont); }); };
    }
    content.querySelector(".account-logout").onclick = async () => { try { await persist(); await api("logout", "POST", {}); location.reload(); } catch (error) { content.querySelector(".account-session-status").textContent = error.message; } };
    const mfa = document.createElement('form'); mfa.className = 'account-form';
    mfa.innerHTML = `<h3>Two-factor authentication</h3><p>${user.twoFactorEnabled ? 'Enabled' : 'Not enabled'}</p><label>Current password<input name="currentPassword" type="password" autocomplete="current-password" required maxlength="128"></label>${user.twoFactorEnabled ? '<label>Authenticator or recovery code<input name="twoFactorCode" autocomplete="one-time-code" required maxlength="80"></label>' : ''}<p class="account-error" role="status"></p><button type="submit">${user.twoFactorEnabled ? 'Disable 2FA' : 'Set up authenticator'}</button>`;
    content.querySelector('[data-account-section="security"]').append(mfa);
    mfa.onsubmit = async (event) => {
      event.preventDefault(); const button = mfa.querySelector('button'), status = mfa.querySelector('.account-error'); button.disabled = true;
      try {
        const result = await api(user.twoFactorEnabled ? 'mfa/disable' : 'mfa/setup', 'POST', Object.fromEntries(new FormData(mfa)));
        if (user.twoFactorEnabled) { status.textContent = result.message; user.twoFactorEnabled = false; button.textContent = 'Disabled; reopen settings to set up again'; return; }
        mfa.innerHTML = '<h3>Connect your authenticator</h3><p>Add a time-based account in your authenticator using this setup key. Do not share it.</p><code class="mfa-setup-key"></code><label>Six-digit code<input name="twoFactorCode" autocomplete="one-time-code" pattern="[0-9]{6}" required></label><p class="account-error" role="status"></p><button type="submit">Enable 2FA</button>';
        mfa.querySelector('code').textContent = result.secret;
        mfa.onsubmit = async (e) => {
          e.preventDefault(); const confirm = mfa.querySelector('button'); confirm.disabled = true;
          try { const saved = await api('mfa/confirm','POST',Object.fromEntries(new FormData(mfa))); user.twoFactorEnabled = true; mfa.innerHTML = '<h3>2FA enabled</h3><p>Keep these one-use recovery codes in a password manager. They are shown only once.</p><pre class="mfa-recovery"></pre>'; mfa.querySelector('pre').textContent = saved.recoveryCodes.join('\n'); }
          catch (error) { mfa.querySelector('.account-error').textContent = error.message; confirm.disabled = false; }
        };
      } catch (error) { status.textContent = error.message; button.disabled = false; }
    };
    root.lucide?.createIcons();
  }
  function refreshAccountButton() {
    document.documentElement.dataset.accountSession = user ? 'signed-in' : 'guest';
    document.documentElement.dataset.tacticAccess = ['admin','tactics'].includes(user?.role) ? 'allowed' : 'denied';
    const button = document.querySelector("#account-button"); button.title = user ? "Account menu: " + user.name : "Sign in, register or reset password";
    button.querySelector(".account-button-avatar")?.remove();
    if (user?.avatar) { const image = document.createElement("img"); image.className = "account-button-avatar"; image.src = user.avatar; image.alt = ""; button.append(image); }
    button.classList.toggle("signed-in", !!user);
  }
  function passwordResetDialog(token) {
    dialog("Reset password", (content, close) => {
      content.innerHTML = '<form class="account-form"><label>New password<input name="password" type="password" autocomplete="new-password" minlength="12" maxlength="128" required></label><label>Confirm new password<input name="confirmPassword" type="password" autocomplete="new-password" minlength="12" maxlength="128" required></label><p class="account-error" role="status"></p><button type="submit" class="account-primary">Reset password</button></form>';
      const form = content.querySelector("form");
      form.onsubmit = async (event) => { event.preventDefault(); const button = form.querySelector("button"); button.disabled = true; try { const result = await api("reset-password", "POST", { ...Object.fromEntries(new FormData(form)), token }); user = null; refreshAccountButton(); close(); accountDialog(result.message); } catch (error) { form.querySelector(".account-error").textContent = error.message; button.disabled = false; } };
    });
  }
  function nameDialog(title, defaultName, callback) {
    dialog(title, (content, close) => {
      const form = document.createElement("form"), input = document.createElement("input"), save = document.createElement("button");
      input.value = defaultName; input.maxLength = 60; input.required = true; input.setAttribute("aria-label", "Layout name"); save.textContent = "Save";
      form.append(input, save); content.append(form); form.onsubmit = (event) => { event.preventDefault(); const name = input.value.trim(); if (name) { callback(name); close(); } };
    });
  }
  function refreshMenu() {
    const value = layouts(), active = value.items.find((item) => item.id === value.active);
    document.querySelector("#layout-name").textContent = active?.name || "Layout";
    document.querySelector("#layout-autosave").checked = value.autosave;
    const host = document.querySelector("#saved-layouts"); host.replaceChildren();
    for (const item of value.items) {
      const button = document.createElement("button"); button.textContent = item.name; button.type = "button";
      button.onclick = async () => { value.active = item.id; restore({ ...item.values, "zgua-layouts-v1": JSON.stringify(value) }); await persist(); location.reload(); }; host.append(button);
    }
  }
  function saveLayout(name, create = false) {
    const value = layouts(); let active = !create && value.items.find((item) => item.id === value.active);
    if (!active) { active = { id: crypto.randomUUID(), name }; value.items.push(active); value.active = active.id; }
    active.name = name || active.name; active.values = chartSnapshot(); active.saved = Date.now();
    storeLayouts(value); refreshMenu(); changed(); document.querySelector("#layout-save-status").textContent = "Saved " + new Date().toLocaleTimeString();
  }
  async function copyChart() {
    const pane = document.querySelector(".pane.active-chart"), stack = pane?.querySelector(".chart-stack");
    if (!stack) return;
    const rect = stack.getBoundingClientRect(), scale = devicePixelRatio || 1;
    const canvas = document.createElement("canvas"); canvas.width = Math.round(rect.width * scale); canvas.height = Math.round(rect.height * scale);
    const context = canvas.getContext("2d"); context.fillStyle = "#11161d"; context.fillRect(0, 0, canvas.width, canvas.height);
    for (const layer of stack.querySelectorAll("canvas")) {
      if (!layer.width || !layer.height || getComputedStyle(layer).visibility === "hidden") continue;
      const box = layer.getBoundingClientRect(); context.drawImage(layer, (box.left - rect.left) * scale, (box.top - rect.top) * scale, box.width * scale, box.height * scale);
    }
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
    try { await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]); }
    catch { const url = URL.createObjectURL(blob), link = document.createElement("a"); link.href = url; link.download = "ZguaCharts.png"; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
  }
  const ready = (async () => {
    try { const result = await api("session"); user = result.user; registrationEnabled = result.registrationEnabled === true; if (user && result.profile) restore(result.profile); }
    catch (error) { document.querySelector("#layout-save-status").textContent = error.message; }
    setupAccountMenu(); refreshAccountButton();
    document.querySelector("#layout-menu").addEventListener("toggle", refreshMenu);
    document.querySelector("#layout-save").onclick = () => { const active = layouts().items.find((item) => item.id === layouts().active); active ? saveLayout(active.name) : nameDialog("Save layout", "My layout", saveLayout); };
    document.querySelector("#layout-new").onclick = () => nameDialog("Create new layout", "New layout", (name) => saveLayout(name, true));
    document.querySelector("#layout-rename").onclick = () => nameDialog("Rename layout", layouts().items.find((item) => item.id === layouts().active)?.name || "My layout", saveLayout);
    document.querySelector("#layout-autosave").onchange = (event) => { const value = layouts(); value.autosave = event.target.checked; storeLayouts(value); changed(); };
    document.querySelector("#layout-copy").onclick = () => copyChart().catch((error) => { document.querySelector("#layout-save-status").textContent = error.message; });
    refreshMenu();
    const verification = /^#verify-email=([A-Za-z0-9_-]{43})$/.exec(location.hash);
    if (verification) {
      history.replaceState(null, "", location.pathname + location.search);
      try { const result = await api("verify", "POST", { token: verification[1] }); user = (await api("session")).user; refreshAccountButton(); accountDialog(result.message); }
      catch (error) { accountDialog(error.message, false); }
    }
    const reset = /^#reset-password=([A-Za-z0-9_-]{43})$/.exec(location.hash);
    if (reset) { history.replaceState(null, "", location.pathname + location.search); passwordResetDialog(reset[1]); }
  })();
  root.ChartAccounts = { ready, key, changed, isSignedIn: () => !!user, canUseTactics: () => ['admin','tactics'].includes(user?.role), csrfToken: () => csrf, api, dialog };
})(window);
