(function () {
  'use strict';
  const columns = [['entryTime','Buy time'],['exitTime','Exit time'],['returnPct','Account %'],['priceMovePct','Price %'],['money','1000 EUR balance'],['entryPrice','Entry price'],['exitPrice','Exit price']];
  const bound = new WeakSet();
  function position(control) {
    if (!control.open) return;
    const menu = control.querySelector('#watchlist-column-settings,.sidebar-settings-menu'), box = control.querySelector('summary').getBoundingClientRect();
    if (!menu) return;
    menu.style.position = 'fixed'; menu.style.right = 'auto';
    menu.style.maxHeight = Math.max(100, innerHeight - 24) + 'px';
    menu.style.left = Math.max(8, Math.min(innerWidth - menu.offsetWidth - 8, box.right - menu.offsetWidth)) + 'px';
    menu.style.top = Math.max(8, Math.min(innerHeight - menu.offsetHeight - 8, box.bottom + 6)) + 'px';
  }
  function refresh() {
    const watch = document.querySelector('.watchlist-settings');
    const heading = document.querySelector('#watchlist-section > summary');
    if (watch && heading && watch.parentElement !== heading) heading.insertBefore(watch, heading.querySelector('#watchlist-add-current'));
    const title = document.querySelector('#current-strategy-section > summary');
    if (title && !title.querySelector('.table-settings')) {
      const settings = document.createElement('details'); settings.className = 'table-settings';
      settings.innerHTML = '<summary title="Tactic columns" aria-label="Tactic columns"><i data-lucide="sliders-horizontal"></i></summary><div class="sidebar-settings-menu"></div>';
      const menu = settings.querySelector('div');
      for (const [id,label] of columns) {
        const row = document.createElement('label'), input = document.createElement('input'); input.type='checkbox'; input.value=id;
        input.checked = !Array.isArray(uiPrefs.tradeColumns) || uiPrefs.tradeColumns.includes(id);
        row.append(input, document.createTextNode(label)); menu.append(row);
        input.onchange = () => {uiPrefs.tradeColumns = [...menu.querySelectorAll('input:checked')].map(item => item.value); saveUiPrefs(); refresh();};
      }
      title.append(settings); window.lucide?.createIcons({root:settings});
    }
    for (const cell of document.querySelectorAll('[data-trade-column]')) cell.hidden = Array.isArray(uiPrefs.tradeColumns) && !uiPrefs.tradeColumns.includes(cell.dataset.tradeColumn);
    for (const input of document.querySelectorAll('.table-settings input')) input.checked = !Array.isArray(uiPrefs.tradeColumns) || uiPrefs.tradeColumns.includes(input.value);
    for (const control of document.querySelectorAll('.watchlist-settings,.table-settings')) {
      if (!bound.has(control)) {bound.add(control); control.addEventListener('toggle', () => position(control));}
      position(control);
    }
  }
  document.addEventListener('click', event => {
    for (const menu of document.querySelectorAll('.watchlist-settings[open],.table-settings[open],.chart-style-control[open],.layout-menu[open],.community-menu[open],.page-menu[open]')) {
      if (!menu.contains(event.target)) menu.open = false;
    }
  }, true);
  // Nested settings must not collapse the section that owns them.
  document.addEventListener('click', event => {if (event.target.closest('.watchlist-settings,.table-settings')) event.stopPropagation();});
  document.addEventListener('keydown', event => {if (event.key === 'Escape') for (const menu of document.querySelectorAll('.watchlist-settings,.table-settings,.chart-style-control')) menu.open = false;});
  new MutationObserver(refresh).observe(document.querySelector('.right-sidebar'), {childList:true,subtree:true});
  refresh();
})();
