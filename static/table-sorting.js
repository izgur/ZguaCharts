(function(root){
  'use strict';
  const collator=new Intl.Collator(undefined,{numeric:true,sensitivity:'base'});
  function value(cell){
    if(!cell)return null;
    const full=cell.title.trim();
    const text=(cell.dataset.sortValue||(/^[+-]?[\d.,eE%$€\s]+$/.test(full)?full:cell.textContent)).trim();
    if(!text||text==='-'||text==='ERR')return null;
    const date=text.match(/^(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})(?:,?\s*(\d{1,2}):(\d{2})(?::(\d{2}))?)?$/);
    if(date)return Date.UTC(+date[3],+date[2]-1,+date[1],+(date[4]||0),+(date[5]||0),+(date[6]||0));
    const numeric=text.replace(/[%$€\s]/g,'').replace(/,/g,'');
    if(/^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(numeric))return Number(numeric);
    return text;
  }
  function compare(a,b,direction){if(typeof a==='number'&&!Number.isFinite(a))a=null;if(typeof b==='number'&&!Number.isFinite(b))b=null;if(a===null)return b===null?0:1;if(b===null)return -1;return direction*(typeof a==='number'&&typeof b==='number'?a-b:collator.compare(String(a),String(b)));}
  function sort(table,index,direction){for(const body of table.tBodies){const rows=[...body.rows];if(rows.some(row=>[...row.cells].some(cell=>cell.colSpan>1)))continue;rows.sort((a,b)=>compare(value(a.cells[index]),value(b.cells[index]),direction));body.append(...rows);}}
  function decorate(){for(const table of document.querySelectorAll('table:not(.watchlist-table)')){if(!table.tHead||!table.tBodies.length)continue;for(const th of table.tHead.rows[table.tHead.rows.length-1].cells){if(th.tagName!=='TH'||th.colSpan!==1||!th.textContent.trim()||th.querySelector('button,input,select,a'))continue;const button=document.createElement('button');button.type='button';button.className='table-sort-button';button.title='Sort ascending / descending';button.append(...th.childNodes);button.onclick=()=>{const direction=th.getAttribute('aria-sort')==='ascending'?-1:1;for(const header of table.tHead.querySelectorAll('th'))header.removeAttribute('aria-sort');th.setAttribute('aria-sort',direction===1?'ascending':'descending');sort(table,th.cellIndex,direction);};th.append(button);}}}
  let frame;const observer=new MutationObserver(()=>{if(frame)return;frame=requestAnimationFrame(()=>{frame=null;decorate();});});
  document.addEventListener('DOMContentLoaded',()=>{decorate();observer.observe(document.body,{childList:true,subtree:true});});
  root.ChartTableSorting={value,compare,sort};
})(window);
