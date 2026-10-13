(function(root) {
  'use strict';
  const STORE='zgua-custom-tactics-v1';
  const key=()=>root.ChartAccounts?.key(STORE)||STORE;
  let hooks={};
  function list() { if (!root.ChartAccounts?.canManageTactics()) return []; try { const rows=JSON.parse(localStorage.getItem(key())); return Array.isArray(rows)?rows.filter(row=>/^tactic_[a-z0-9-]{1,64}$/.test(row.id)&&typeof row.code==='string'&&row.code.length<=100000&&typeof row.name==='string').slice(0,10):[]; } catch {return [];} }
  const get=id=>list().find(row=>row.id===id);
  function suggestions(name) {const base=name.slice(0,55)+' - Custom', names=new Set(list().map(row=>row.name.toLowerCase()));let title=base,n=2;while(names.has(title.toLowerCase()))title=base+' '+n++;return title;}
  function execute(code, options, signal) {
    if (signal?.aborted) return Promise.reject(new Error('Readiness request superseded.'));
    if(code.length>100000||!options.candles?.length||options.candles.length>5000) return Promise.reject(new Error('Use up to 100 KB source and 5,000 loaded candles.'));
    return new Promise((resolve,reject)=>{
      const worker=new Worker('/api/tactic-source/worker.js');
      const timer=setTimeout(()=>finish(new Error('Backtest timed out and was stopped.')),10000);
      function finish(error,result){clearTimeout(timer);signal?.removeEventListener('abort',abort);worker.terminate();error?reject(error):resolve(result);}
      const abort=()=>finish(new Error('Readiness request superseded.'));
      signal?.addEventListener('abort',abort,{once:true});
      worker.onerror=()=>finish(new Error('Tactic worker failed to load.'));
      worker.onmessage=({data})=>{
        if(data.error)return finish(new Error(String(data.error).slice(0,3000)));
        try {
          if(typeof data.result!=='string'||data.result.length>4000000)throw new Error('Invalid tactic result');
          const parsed=JSON.parse(data.result), result=parsed.result;
          for(const field of ['netReturn','maxDrawdown','numberOfTrades','totalFees','totalSlippageCost']) if(!Number.isFinite(result?.[field]))throw new Error('Invalid backtest metrics');
          if(!Array.isArray(result.tradeList)||result.tradeList.length>options.candles.length||!Array.isArray(result.markers)||!Array.isArray(result.equityCurve))throw new Error('Invalid backtest trades');
          finish(null,parsed);
        }catch(error){finish(error);}
      };
      worker.postMessage({code,options});
    });
  }
  async function backtest(record,pane){return (await execute(record.code,await hooks.options(pane,record))).result;}
  async function openEditor(preset,pane) {
    const readOnly = !root.ChartAccounts?.canManageTactics();
    if (readOnly && preset?.strategy !== 'RelativeStrengthV2') return;
    const existing=get(preset?.id), dialog=document.createElement('dialog');dialog.className='indicator-source-dialog indicator-editor tactic-editor';
    dialog.innerHTML='<header><h2>Tactic source</h2><button type="button" class="source-close" aria-label="Close tactic source"><i data-lucide="x"></i></button></header><label class="indicator-editor-name">Name<input maxlength="80" aria-label="Tactic name"></label><p class="source-origin">JavaScript entry + shared exits. Local backtest model: taker 0.055%/side, slippage 2 bps/side, 1x cap.</p><textarea class="indicator-code-editor" aria-label="Tactic source code" spellcheck="false"></textarea><output class="tactic-test-result"></output><footer><span class="source-status" role="status">Loading...</span><button type="button" class="tactic-test"><i data-lucide="flask-conical"></i>Backtest</button><button type="button" class="tactic-copy" title="Copy source" aria-label="Copy tactic source"><i data-lucide="copy"></i></button><button type="button" class="tactic-new"><i data-lucide="plus"></i>New</button><button type="button" class="tactic-save-copy">Save as new</button><button type="button" class="tactic-save">Save</button><button type="button" class="tactic-delete" title="Delete tactic"><i data-lucide="trash-2"></i></button></footer>';
    (document.fullscreenElement||document.body).append(dialog);dialog.showModal();root.lucide?.createIcons({root:dialog});
    const editor=dialog.querySelector('textarea'), name=dialog.querySelector('input'), status=dialog.querySelector('.source-status'), output=dialog.querySelector('output'), buttons=[...dialog.querySelectorAll('footer button')];
    let dirty=false,busy=false;
    const close=()=>{if(!dirty||confirm('Discard unsaved tactic changes?'))dialog.close();};
    dialog.querySelector('.source-close').onclick=close;dialog.onclose=()=>dialog.remove();dialog.oncancel=e=>{if(dirty&&!confirm('Discard unsaved tactic changes?'))e.preventDefault();};
    editor.oninput=name.oninput=()=>{dirty=true;};
    dialog.querySelector('.tactic-save').hidden=!existing;dialog.querySelector('.tactic-delete').hidden=!existing;
    buttons.forEach(button=>button.disabled=true);
    try {
      if(existing){editor.value=existing.code;name.value=existing.name;}
      else {
        const response=await fetch('/api/tactic-source/'+encodeURIComponent(preset?.strategy||'SimpleAtrTrendV2')), source=await response.json();
        if(!response.ok)throw new Error(source.error||'Source unavailable');
        if(!dialog.isConnected)return;
        const merged={...source.params,...preset?.params};
        editor.value=source.code.replace('const PARAMS = '+JSON.stringify(source.params,null,2)+';', 'const PARAMS = '+JSON.stringify(merged,null,2)+';');
        name.value=suggestions(preset?.strategy||'SimpleAtrTrendV2');
      }
      if (existing?.baseStrategy === 'ZGUA' || preset?.strategy === 'ZGUA') dialog.querySelector('.source-origin').textContent='JavaScript divergence entry and exit. Python detection uses chart Zgua settings. Costs: 0.055% fees and 0.02% slippage per side; long-only, 1x.';
      buttons.forEach(button=>button.disabled=false);status.textContent='';
      if (readOnly) {
        editor.readOnly = name.readOnly = true; name.value = 'RelativeStrengthV2';
        for (const selector of ['.tactic-new','.tactic-save-copy','.tactic-save','.tactic-delete']) dialog.querySelector(selector).hidden = true;
        status.textContent = 'Read-only RelativeStrengthV2 demo';
      }
    }catch(error){status.textContent=error.message;return;}
    async function action(task){if(busy)return;busy=true;editor.readOnly=name.readOnly=true;buttons.forEach(button=>button.disabled=true);try{await task();}catch(error){status.textContent=error.message;}finally{busy=false;editor.readOnly=name.readOnly=readOnly;buttons.forEach(button=>button.disabled=false);}}
    async function run(saving){status.textContent=saving?'Saving...':'Backtesting...';const options=await hooks.options(pane,{code:editor.value});const result=await execute(editor.value,options);output.textContent=options.candles.length+' candles · '+options.interval+' signals / '+(options.executionInterval||options.interval)+' execution · '+result.result.numberOfTrades+' trades · Net '+result.result.netReturn.toFixed(2)+'% · DD '+result.result.maxDrawdown.toFixed(2)+'% · Fees '+result.result.totalFees.toFixed(2)+' · Slippage '+result.result.totalSlippageCost.toFixed(2)+' · 1x cap · accountEquity '+result.params.accountEquity.toFixed(2)+' · Ending equity '+(result.params.accountEquity*(1+result.result.netReturn/100)).toFixed(2)+' · Win '+result.result.winRate.toFixed(1)+'% · PF '+result.result.profitFactor.toFixed(2);return result;}
    dialog.querySelector('.tactic-test').onclick=()=>action(async()=>{await run(false);status.textContent='Backtest complete';});
    async function save(copy){if(readOnly)return;await action(async()=>{
      let title=name.value.trim();if(title.length<2||title.length>80)throw new Error('Name must be 2–80 characters.');
      if(copy&&existing&&title===existing.name)title=suggestions(title);
      const rows=list();if(rows.some(row=>row.name.toLowerCase()===title.toLowerCase()&&(copy||row.id!==existing?.id)))throw new Error('Tactic name already used');
      if(copy&&rows.length>=10)throw new Error('Maximum 10 custom tactics');
      const tested=await run(true);if(!dialog.isConnected)return;
      const id=!copy&&existing?existing.id:'tactic_'+crypto.randomUUID();
      const saved=rows.filter(row=>row.id!==id).concat({id,name:title,code:editor.value,baseStrategy:tested.baseStrategy});
      const encoded=JSON.stringify(saved);if(encoded.length>750000)throw new Error('Tactic library exceeds 750 KB');
      localStorage.setItem(key(),encoded);root.ChartAccounts?.changed();hooks.changed?.();dirty=false;dialog.close();
    });}
    dialog.querySelector('.tactic-save-copy').onclick=()=>save(true);dialog.querySelector('.tactic-save').onclick=()=>save(false);
    dialog.querySelector('.tactic-copy').onclick=()=>action(async()=>{await navigator.clipboard.writeText(editor.value);status.textContent='Copied';});
    dialog.querySelector('.tactic-new').onclick=()=>{if(!dirty||confirm('Discard unsaved changes and start a new tactic?')){dirty=false;dialog.close();openEditor(null,pane);}};
    dialog.querySelector('.tactic-delete').onclick=()=>{if(existing&&confirm('Delete '+existing.name+'?')){localStorage.setItem(key(),JSON.stringify(list().filter(row=>row.id!==existing.id)));root.ChartAccounts?.changed();hooks.changed?.();dirty=false;dialog.close();}};
  }
  root.ChartCustomTactics={list,get,execute,backtest,openEditor,configure:value=>{hooks=value;}};
})(window);
