(function (root) {
  'use strict';
  const cache = new Map(), sources = new Map(), results = new Map();
  let generation = 0;
  let controller;
  const seconds = tf => timeframeMinutes(tf) * 60;
  async function candles(source, symbol, timeframe, asOf, limit) {
    const key = JSON.stringify([source,symbol,timeframe,Math.floor(asOf / seconds(timeframe)),limit]);
    if (!cache.has(key)) {
      const request = apiGet('/api/candles?' + new URLSearchParams({source,symbol,timeframe,limit:String(limit),before:String(asOf)}))
        .then(payload => (payload.candles || []).filter(row => row.time + seconds(timeframe) <= asOf));
      cache.set(key, request); request.catch(() => cache.delete(key));
      while (cache.size > 16) cache.delete(cache.keys().next().value);
    }
    return cache.get(key);
  }
  function title(row, preset, source, symbol) {
    if (row.score === null) return [symbol+' / '+source+' / '+row.timeframe,
      'Directional bias / entry assessment unavailable', 'Selected tactic: '+preset.label,
      row.error || (row.blockers||[]).join('; '),
      'Missing data is not a neutral score. No entry confirmation is available.'].join('\n');
    if (row.version === 'market-assessment-v1') return [
      symbol+' / '+source+' / '+row.timeframe,
      'Directional bias + fresh-entry review / market-assessment-v1',
      'Experimental rule-based score, NOT win probability. 0 bearish / 50 neutral / 100 bullish.',
      'Selected tactic: '+preset.label+' (context only; this review does not place orders)',
      'Direction: '+row.direction+' '+row.score+'/100; entry quality: '+row.entryScore+'/100; '+row.status+' '+(row.side||''),
      'Latest closed trend bar: '+new Date(row.closedAt*1000).toLocaleString(),
      row.timingClosedAt?'Latest closed 5m trigger bar: '+new Date(row.timingClosedAt*1000).toLocaleString():'5m timing unavailable',
      'Components (signed weighted points): '+Object.entries(row.components||{}).map(([key,value])=>key+' '+value.toFixed(1)).join('; '),
      'Risk plan: price '+row.price?.toFixed(4)+'; invalidation '+row.stopPrice?.toFixed(4)+'; observed objective '+(row.objective?.toFixed(4)||'not available'),
      'Reward/risk: '+(row.rewardRisk?.toFixed(2)||'not available')+'; risk distance '+row.riskPct?.toFixed(3)+'%; modeled round-trip fee/slippage: '+row.roundTripCostPct?.toFixed(3)+'%',
      'Blockers: '+((row.blockers||[]).join('; ')||'none'),
      row.quotePrice?'Live entry check: '+row.quotePrice+'; quote age '+row.quoteAgeSeconds.toFixed(1)+'s':'',
      'Selected tactic entry rules: '+(row.tacticSetup?.passed?'passed':'not passed')+'; '+((row.tacticSetup?.blockers||[]).join(', ')||'none'),
      'This is independent of the tactic simulated position and your account. Bearish bias does not enable short trading.',
      'A high bullish score can still be LATE. ENTER requires a fresh 5m trigger, usable structure, costs and 4h/1h agreement on 15m.',
      ...(row.limitations||[])
    ].join('\n');
    return [symbol + ' / ' + source + ' / ' + row.timeframe, preset.label + ' / entry-readiness-v1',
      'Experimental entry readiness, NOT win probability. Paper analysis only.',
      row.error || ('Status: ' + row.status + (row.score === null ? '' : ' / readiness ' + row.score + '/100')),
      row.closedAt ? 'Last closed decision: ' + new Date(row.closedAt*1000).toLocaleString() : '',
      'Execution: ' + (row.executionTimeframe || '5m') + '; coverage: ' + (row.executionCovered ? 'complete' : 'unavailable'),
      'Setup age: ' + (row.setupAgeBars ?? '-') + ' bars; drift: ' + (row.driftAtr?.toFixed(2) ?? '-') + ' ATR',
      'Stop distance: ' + (row.stopDistancePct?.toFixed(3) ?? '-') + '%; modeled round-trip fee/slippage: ' + (row.roundTripCostPct?.toFixed(3) ?? '-') + '% (funding excluded)',
      'Blockers: ' + ((row.blockers || []).join(', ') || 'none'),
      row.checks ? 'As-of checks: price ' + row.checks.close + '; EMA20 ' + row.checks.ema20?.toFixed(2) + '; EMA50 ' + row.checks.ema50?.toFixed(2) + '; RSI ' + row.checks.rsi?.toFixed(1) + ' (configured bounds ' + (row.checks.rsiMin ?? '-') + '-' + (row.checks.rsiMax ?? '-') + '); ADX ' + row.checks.adx?.toFixed(1) : '',
      'Position context: ' + (row.positionContext || 'unavailable'),
      'Window: up to 600 closed signal bars; 1x paper exposure cap. The score uses a fixed rule-based cost/freshness rubric, not fitted odds.',
      'BTC benchmark: Bybit 4h. No strategy has been auto-promoted; GO notifications disabled.'
    ].filter(Boolean).join('\n');
  }
  function quoteReview(row,price,ageSeconds) {
    if(row.timeframe!=='15m'||row.status!=='ENTER')return row;
    const blockers=[...(row.blockers||[])],sign=row.side==='long'?1:-1;
    let status=row.status;
    if(!Number.isFinite(price)||price<=0||!Number.isFinite(ageSeconds)||ageSeconds>30||ageSeconds<0) {
      blockers.push('Fresh current market quote unavailable');status='UNAVAILABLE';
    } else if(sign*(price-row.stopPrice)<=0) {
      blockers.push('Current quote breached the planned invalidation');status='WAIT';
    } else if(Math.abs(price-row.price)>.5*row.atr) {
      blockers.push('Current quote moved more than 0.5 ATR from the closed trigger; recheck entry');status='LATE';
    } else if(sign*(price-row.stopPrice)/price*100<3*row.roundTripCostPct) {
      blockers.push('Current quote risk distance no longer covers modeled costs');status='WAIT';
    } else if(sign*(row.objective-price)/(sign*(price-row.stopPrice))<1.5) {
      blockers.push('Current quote no longer leaves 1.5R to observed structure');status='WAIT';
    }
    return {...row,status,blockers,quotePrice:price,quoteAgeSeconds:ageSeconds};
  }
  function paint(pane,preset,source,symbol,rows) {
    headerSignalStrip.innerHTML = rows.map(row => '<button type="button" class="header-signal-pill ' + (row.score>=60?'positive':row.score!==null&&row.score<=40?'negative':'neutral') + '" title="' + escapeHtml(title(row,preset,source,symbol)) + '">' + escapeHtml(symbol.replace(/USDT$/, '')) + ' ' + row.timeframe + ' <i data-lucide="'+(row.score>=60?'arrow-up':row.score!==null&&row.score<=40?'arrow-down':'minus')+'" aria-hidden="true"></i> ' + (row.score===null?'N/A':row.score) + (row.timeframe==='15m'?' <span>'+escapeHtml(row.status)+'</span>':'') + '</button>').join('');
    root.lucide?.createIcons();
    [...headerSignalStrip.children].forEach((button,index)=>{button.onclick=()=>openBacktestModal('<pre class="params-preview">'+escapeHtml(title(pane.entryReadiness.rows[index],preset,source,symbol))+'</pre>',symbol+' '+rows[index].timeframe+' · Direction and entry quality');});
  }
  function refreshQuote(pane) {
    const base=pane.marketAssessmentBase;
    if(!base||pane.disposed||pane.replayActive||pane.sourceSelect.value!==base.source||pane.symbolSelect.value!==base.symbol||pane.presetSelect.value!==base.selection||!pane.node.classList.contains('active-chart'))return;
    const rows=base.rows.map(row=>quoteReview(row,pane.lastPrice,(Date.now()-pane.lastQuoteAt)/1000));
    const changed=rows.some((row,index)=>row.status!==pane.entryReadiness?.rows[index]?.status||(row.blockers||[]).join(';')!==pane.entryReadiness?.rows[index]?.blockers?.join(';'));
    pane.entryReadiness={rows,asOf:base.asOf,presetId:base.preset.id};
    if(changed)paint(pane,base.preset,base.source,base.symbol,rows);
  }
  async function evaluate(pane, preset, asOf, signal) {
    const source = pane.sourceSelect.value, symbol = pane.symbolSelect.value;
      let code;
      if (preset.custom) code = root.ChartCustomTactics.get(preset.id)?.code;
      else {
        if (!sources.has(preset.strategy)) {
          const request = apiGet('/api/tactic-source/' + encodeURIComponent(preset.strategy));
          sources.set(preset.strategy, request); request.catch(() => sources.delete(preset.strategy));
        }
        const exported = await sources.get(preset.strategy);
        code = exported.code.replace('const PARAMS = ' + JSON.stringify(exported.params,null,2) + ';', 'const PARAMS = ' + JSON.stringify({...exported.params,...preset.params},null,2) + ';');
      }
      if (!code) throw new Error('Tactic source unavailable');
      const isZgua = /baseStrategy\s*:\s*BASE_STRATEGY/.test(code) && /BASE_STRATEGY\s*=\s*['"]ZGUA['"]/.test(code);
      const key = JSON.stringify([source,symbol,code,isZgua?pane.indicatorSettings.zgua:null,Math.floor(asOf/300)]);
      if(results.has(key)) return results.get(key);
      const [benchmark, execution] = await Promise.all([candles('bybit','BTCUSDT','4h',asOf,600).catch(()=>[]),candles(source,symbol,'5m',asOf,5000)]);
      const rows = await Promise.all(['4h','1h','15m'].map(async timeframe => {
        try {
          const bars = await candles(source,symbol,timeframe,asOf,600);
          if (!bars.length || asOf - bars.at(-1).time - seconds(timeframe) >= seconds(timeframe)+5) throw new Error('Missing or stale closed candles');
          let divergenceEvents;
          if (isZgua) {
            const last=bars.at(-1), nextTime=last.time+seconds(timeframe);
            const next={time:nextTime,open:last.close,high:last.close,low:last.close,close:last.close,volume:0};
            const response=await fetch('/api/zgua/events',{method:'POST',signal,headers:{'Content-Type':'application/json'},body:JSON.stringify({candles:[...bars,next],params:pane.indicatorSettings.zgua||{}})});
            const detected=await response.json();if(!response.ok)throw new Error(detected.error||'Divergence detection failed');
            divergenceEvents=detected.events;
          }
          const result = await root.ChartCustomTactics.execute(code, {candles:bars,divergenceEvents,regimeCandles:benchmark,benchmarkCandles:benchmark,
            interval:timeframe,benchmarkInterval:'4h',executionInterval:'5m',executionCandles:execution,
            source,symbol,collectReadiness:true,assessmentOnly:true,assessmentAsOf:asOf,incremental:true}, signal);
          const row = result.result.marketAssessment;
          if (!row) throw new Error('Tactic diagnostics unavailable');
          return {...row,timeframe};
        } catch (error) {return {timeframe,status:'UNAVAILABLE',score:null,error:error.message};}
      }));
      const aligned=rows.map(row=>{
        if(row.timeframe!=='15m'||!row.side)return row;
        const blockers=[...(row.blockers||[])];
        for(const tf of ['4h','1h']) {
          const higher=rows.find(item=>item.timeframe===tf);
          if(!higher||higher.score===null||higher.error)blockers.push(tf+' directional data unavailable');
          else if(row.side==='long'?higher.score<60:higher.score>40)blockers.push(tf+' does not support '+row.side);
        }
        return {...row,blockers,status:row.status==='ENTER'&&blockers.length?'WAIT':row.status};
      });
      if (aligned.every(row => !row.error)) {results.set(key, aligned);while(results.size>8) results.delete(results.keys().next().value);}
      return aligned;
  }
  async function refresh() {
    const token = ++generation;
    controller?.abort(); controller = new AbortController();
    const signal = controller.signal;
    if (!headerSignalStrip || !config) return;
    const pane = panes.find(item => item.node.classList.contains('active-chart')) || panes[0];
    const selected = config.chart_signal_strategies.find(item => item.id === pane?.presetSelect.value);
    const fallback = config.chart_signal_strategies.find(item => item.strategy === 'SimpleAtrTrendV2');
    const preset = selected || (fallback && {...fallback,label:'Market bias (no tactic selected)'});
    if (!pane?.historyLoaded) {headerSignalStrip.textContent = 'Bias: loading'; return;}
    if (!preset) {headerSignalStrip.textContent = 'Select tactic for bias review'; return;}
    const source=pane.sourceSelect.value,symbol=pane.symbolSelect.value;
    pane.marketAssessmentBase=null;
    const asOf = pane.replayActive ? pane.candles.at(-1)?.time + seconds(pane.timeframeSelect.value) : Math.floor(Date.now()/1000);
    headerSignalStrip.textContent = 'Bias: checking';
    try {
      const closedRows = await evaluate(pane,preset,asOf,signal);
      if (token !== generation || pane.disposed) return;
      pane.marketAssessmentBase={rows:closedRows,asOf,preset,source,symbol,selection:pane.presetSelect.value};
      const rows=pane.replayActive?closedRows:closedRows.map(row=>quoteReview(row,pane.lastPrice,(Date.now()-pane.lastQuoteAt)/1000));
      pane.entryReadiness = {rows,asOf,presetId:preset.id};
      paint(pane,preset,source,symbol,rows);
    } catch(error) {if(token===generation) {pane.entryReadiness=null;headerSignalStrip.textContent='Readiness unavailable';headerSignalStrip.title=error.message;}}
  }
  root.ChartEntryReadiness = {refresh,evaluate,title,quoteReview,refreshQuote,invalidate:message=>{generation++;controller?.abort();if(headerSignalStrip)headerSignalStrip.textContent=message;}};
  setInterval(()=>{if(typeof panes==='undefined')return;const pane=panes.find(item=>item.node.classList.contains('active-chart'))||panes[0];if(pane)refreshQuote(pane);},5000);
})(window);
