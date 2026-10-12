(function(root){
 'use strict';
 let prefs,config,hooks={},catalog=[],generation=0;
 const id=row=>row.source+':'+row.symbol;
 const majors='BTC ETH SOL BNB XRP DOGE ADA TRX AVAX LINK TON SUI LTC BCH DOT NEAR APT ARB OP ATOM XLM ETC UNI ICP FIL HBAR INJ AAVE RENDER TAO FET POL SEI TIA WIF PEPE BONK SHIB HYPE ENA JUP ONDO MKR SKY PENDLE RUNE ALGO VET QNT STX IMX GRT LDO CRV SNX SAND MANA AXS FLOKI PYTH WLD EOS KAS ZEC XMR DASH TRUMP JTO DEXE AERO COMP EIGEN ZK ZRO DYDX'.split(' ');
 function curate(rows){const base=row=>row.symbol.replace(/[-_]/g,'').replace(/(USDT|USDC|USD|EUR|BTC)$/,'').replace(/^1000/,'');const rank=row=>{const index=majors.indexOf(base(row));return index<0?1000:index;};const seen=new Set();return [...rows].sort((a,b)=>rank(a)-rank(b)||(a.symbol.endsWith('USDT')?-1:0)-(b.symbol.endsWith('USDT')?-1:0)||a.symbol.localeCompare(b.symbol)).filter(row=>{const key=base(row);if(seen.has(key))return false;seen.add(key);return true;}).slice(0,50);}
 const record=(symbol,source='bybit')=>({id:source+':'+symbol,source,symbol,displaySymbol:symbol+(source==='bybit'||source.includes('futures')?'.P':'')});
 function configure(options){prefs=options.prefs;config=options.config;hooks=options;
  prefs.watchLists=Array.isArray(prefs.watchLists)?prefs.watchLists.filter(list=>list&&/^[a-z0-9-]+$/.test(list.id)&&typeof list.name==='string'&&/^#[a-f0-9]{6}$/i.test(list.color)&&Array.isArray(list.members)).slice(0,5):[];
  for(const list of prefs.watchLists){list.name=list.name.trim().slice(0,24)||'List';const members=new Map();for(const row of list.members){if(row&&config.sources[row.source]&&typeof row.symbol==='string'&&row.symbol.length<=96)members.set(id(row),{...row,id:id(row),displaySymbol:typeof row.displaySymbol==='string'?row.displaySymbol:record(row.symbol,row.source).displaySymbol});}list.members=[...members.values()];}
  if(!prefs.watchLists.length)prefs.watchLists=[{id:'yellow',name:'Yellow',color:'#f4d35e',members:(prefs.watchlist||[]).map(symbol=>record(symbol))}];
  prefs.watchListTab=prefs.watchListTab==='all'||prefs.watchLists.some(list=>list.id===prefs.watchListTab)?prefs.watchListTab:'all';
  if(!config.sources[prefs.watchAllSource])prefs.watchAllSource='bybit';
 }
 const lists=()=>prefs.watchLists;
 const rows=()=>prefs.watchListTab==='all'?catalog:lists().find(list=>list.id===prefs.watchListTab)?.members||[];
 const changed=()=>{hooks.save();hooks.render();hooks.refresh?.();};
 function toggle(listId,row){const list=lists().find(item=>item.id===listId);if(!list)return;const index=list.members.findIndex(item=>id(item)===id(row));index<0?list.members.push(row):list.members.splice(index,1);changed();}
 function add(row){toggleMembership(prefs.watchListTab==='all'?lists()[0].id:prefs.watchListTab,row);}
 function toggleMembership(listId,row){const list=lists().find(item=>item.id===listId);if(!list.members.some(item=>id(item)===id(row)))list.members.push(row);changed();}
 function create(name,color){name=name.trim();if(!name||name.length>24)throw new Error('Use a list name of 1–24 characters.');if(lists().length>=5)throw new Error('Maximum five coloured lists.');if(lists().some(list=>list.name.toLowerCase()===name.toLowerCase()))throw new Error('List name already used.');if(!/^#[a-f0-9]{6}$/i.test(color))throw new Error('Choose a colour.');const list={id:crypto.randomUUID(),name,color,members:[]};lists().push(list);prefs.watchListTab=list.id;changed();return list;}
 async function load(){const version=++generation,source=prefs.watchAllSource;
  catalog=curate((config.sources[source].symbols||[]).map(symbol=>record(symbol,source)));hooks.render();
  try{const response=await fetch('/api/market/instruments?source='+encodeURIComponent(source)),data=await response.json();if(!response.ok)throw new Error(data.error||'Catalog unavailable');if(version!==generation)return;
   catalog=curate((data.instruments||[]).map(row=>({...row,id:id(row)})));hooks.status?.(data.warning||'');hooks.render();hooks.refresh?.();
  }catch(error){if(version===generation)hooks.status?.('Catalog unavailable: '+error.message);}
 }
 function tabs(host){host.replaceChildren();for(const list of [{id:'all',name:'All'},...lists()]){const button=document.createElement('button');button.type='button';button.role='tab';button.textContent=list.name.toUpperCase();button.title=list.name;button.setAttribute('aria-selected',String(prefs.watchListTab===list.id));button.style.setProperty('--list-color',list.color||'#a7bacb');button.onclick=()=>{prefs.watchListTab=list.id;changed();};host.append(button);}}
 root.ChartWatchlists={configure,lists,rows,record,id,toggle,add,create,load,tabs,get tab(){return prefs.watchListTab;},get source(){return prefs.watchAllSource;},setSource(source){if(!config.sources[source])return;prefs.watchAllSource=source;hooks.save();load();}};
})(window);
