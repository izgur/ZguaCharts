const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

class Element {
  constructor() { this.children = []; this.dataset = {}; this.style = {}; this.classList = { toggle() {} }; }
  setAttribute() {}
  addEventListener() {}
  querySelector() { return null; }
  replaceChildren() { this.children = []; }
  append(...children) { this.children.push(...children); }
}

async function run() {
  const nodes = new Map(), values = new Map(), timers = new Map(), puts = [];
  let nextTimer = 0, reloads = 0;
  const document = {
    documentElement: new Element(), body: new Element(), addEventListener() {},
    createElement: () => new Element(),
    querySelector(selector) {
      if (!nodes.has(selector)) nodes.set(selector, new Element());
      return nodes.get(selector);
    }
  };
  const localStorage = {
    get length() { return values.size; }, key: index => [...values.keys()][index],
    getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value),
    removeItem: key => values.delete(key)
  };
  const window = { addEventListener() {} };
  const context = {
    window, document, localStorage, console, Date, location: { hash: '', reload() { reloads++; } },
    setTimeout(callback) { const id = ++nextTimer; timers.set(id, callback); return id; },
    clearTimeout: id => timers.delete(id),
    async fetch(url, options) {
      if (options.method === 'GET') return { ok: true, json: async () => ({ user: { id: 1 }, revision: 4, csrf: 'test' }) };
      puts.push(JSON.parse(options.body));
      return puts.length === 1
        ? { ok: true, json: async () => ({ saved: true, revision: 5 }) }
        : { ok: false, status: 409, json: async () => ({ error: 'Profile changed in another tab.', revision: 6, profile: { 'zgua-ui-preferences': 'remote' } }) };
    }
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/chart-accounts.js'), 'utf8'), context);
  await window.ChartAccounts.ready;
  const tick = async () => {
    const [id, callback] = [...timers][0]; timers.delete(id); await callback();
  };
  localStorage.setItem('account:1:zgua-ui-preferences', 'local');
  window.ChartAccounts.changed(); await tick();
  assert.equal(puts[0].revision, 4);
  assert.equal(puts[0].profile['zgua-ui-preferences'], 'local');
  localStorage.setItem('account:1:zgua-ui-preferences', 'local edits');
  window.ChartAccounts.changed(); await tick();
  assert.equal(puts[1].revision, 5);
  assert.equal(localStorage.getItem('account:1:zgua-ui-preferences'), 'local edits');
  assert.match(nodes.get('#layout-save-status').textContent, /another tab/);
  window.ChartAccounts.changed();
  assert.equal(timers.size, 0);
  assert.equal(puts.length, 2);
  // A layout action cannot reload the page after a rejected save.
  localStorage.setItem('account:1:zgua-layouts-v1', JSON.stringify({ active: 'one', items: [{ id: 'one', name: 'One', values: {} }], autosave: true }));
  // Reinitialize the menu through its save control, which also checks conflict handling.
  nodes.get('#layout-save').onclick();
  await nodes.get('#saved-layouts').children[0].onclick();
  assert.equal(reloads, 0);
  assert.equal(puts.length, 2);
  console.log('Profile revision and conflict persistence checks passed.');
}

run().catch(error => { console.error(error); process.exitCode = 1; });
