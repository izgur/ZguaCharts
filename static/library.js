(function () {
  'use strict';
  const config = JSON.parse(document.getElementById('library-data').textContent);
  const toc = document.getElementById('library-toc'), content = document.getElementById('library-content');
  const search = document.getElementById('library-search'), count = document.getElementById('library-count');
  const help = window.ChartIndicatorHelp;
  const base = 'https://chartschool.stockcharts.com/table-of-contents/chart-analysis/chart-patterns/';
  // Original normalized sketches: x is time; y is relative price, not real market data.
  const shapes = {
    cup:[20,45,62,74,80,78,70,55,30,20,35,28,18,5],
    double:[75,25,70,28,72,95], triple:[80,25,70,28,72,26,75,95],
    head:[80,45,68,15,70,45,80,98], round:[15,40,62,78,83,80,66,45,20,5],
    triangle:[85,15,70,25,60,35,50,40,20,5], ascending:[85,25,68,25,50,25,38,25,8],
    descending:[15,75,28,75,45,75,60,75,95],
    flag:[95,70,45,12,32,25,40,32,47,30,12,2],
    wedge:[90,20,80,28,65,35,53,40,25,8],
    range:[70,25,75,24,72,28,75,25,15,5],
    channel:[95,65,80,45,60,30,45,15,25,5],
    broad:[50,40,60,28,74,15,86,5,98],
    abc:[90,20,65,5], harmonic:[15,85,35,65,95,45],
    doji:[75,50,51,50,51,30], hammer:[85,75,72,45,20], engulf:[80,70,75,25,15],
  };
  const patterns = [];
  function pattern(id,label,group,shape,bias,recognize,trigger,invalid,target,reference,invert=false) {
    patterns.push({id,label,group,shape,bias,recognize,trigger,invalid,target,reference,invert});
  }
  pattern('cup-handle','Cup and handle','Continuation','cup','Bullish','A rounded base followed by a smaller pullback near its upper rim; look for a prior advance.','A closed-bar breakout above handle resistance, preferably with expanding participation.','Losing the handle low weakens the setup; a deep handle can invalidate its structure.','Cup depth projected above the breakout is a planning heuristic, not a guaranteed destination.','cup-with-handle');
  pattern('inverse-cup','Inverse cup and handle','Reversal','cup','Bearish','An inverted rounded top with a small recovery near support.','Close below handle support; do not short merely because a curve resembles a cup.','Reclaim of the handle high or return above broken support.','Use nearby support before a mirrored cup-depth projection.',null,true);
  for (const [id,label,shape,ref] of [['double','Double','double','double'],['triple','Triple','triple','triple']]) {
    pattern(id+'-top',label+' top','Reversal',shape,'Bearish','Repeated tests of a resistance area, separated by meaningful pullbacks.','Close below the intervening support/neckline.','Recovery above the last peak; a failed breakdown can trap sellers.','Peak-to-neckline height projected down, checked against nearer support.',ref+'-top-reversal');
    pattern(id+'-bottom',label+' bottom','Reversal',shape,'Bullish','Repeated tests of support after a decline, with intervening recoveries.','Close above intervening resistance/neckline.','Loss of the last trough; failed breakouts can trap buyers.','Base-to-neckline height projected up, subject to nearby resistance.',ref+'-bottom-reversal',true);
  }
  pattern('head-shoulders','Head and shoulders','Reversal','head','Bearish','Three peaks; the central head is higher than both shoulders. The neckline joins intervening lows.','Close below the neckline, rather than trading the unfinished right shoulder.','Reclaim of neckline; a move above the right shoulder further challenges the thesis.','Head-to-neckline height projected from breakdown.','head-and-shoulders-top');
  pattern('inverse-head-shoulders','Inverse head and shoulders','Reversal','head','Bullish','Three troughs; the middle trough is deepest. Neckline joins recovery highs.','Close above neckline, with follow-through or a held retest.','Return below neckline or loss of right-shoulder support.','Head-to-neckline depth projected from breakout.','head-and-shoulders-bottom',true);
  pattern('rounding-bottom','Rounding bottom','Reversal','round','Bullish','A gradual change from falling to rising price; confirm on a suitable higher timeframe.','Recovery through the base rim or a clear resistance break.','Failure of higher lows or renewed loss of the base.','Prior resistance and base depth provide possible objectives.','rounding-bottom');
  pattern('rounding-top','Rounding top','Reversal','round','Bearish','A gradual roll-over rather than a single sharp peak.','Break of the supporting range after the roll-over.','Recovery through the rolling resistance structure.','Use lower support as the first objective.',null,true);
  for (const [id,label,bias,invert] of [['bull-flag','Bull flag','Bullish',false],['bear-flag','Bear flag','Bearish',true],['bull-pennant','Bull pennant','Bullish',false],['bear-pennant','Bear pennant','Bearish',true]]) {
    pattern(id,label,'Continuation',id.includes('pennant')?'triangle':'flag',bias,'An impulse followed by a short consolidation; a flag is roughly parallel, a pennant converges.','Close out of consolidation in the impulse direction; assess volume and available room.','Break through the opposite consolidation boundary.','An impulse-length projection is only a heuristic; take account of closer levels.','flag-pennant',invert);
  }
  pattern('ascending-triangle','Ascending triangle','Continuation','ascending','Bullish leaning','Repeated resistance tests with rising lows.','Close above the flat resistance; until then it remains a range.','Loss of rising support or immediate return inside after breakout.','Widest triangle height projected from breakout.','ascending-triangle');
  pattern('descending-triangle','Descending triangle','Continuation','descending','Bearish leaning','Repeated support tests with falling highs.','Close below flat support; the name alone does not confirm direction.','Break above falling resistance or failed breakdown.','Widest triangle height projected below support.','descending-triangle');
  pattern('symmetrical-triangle','Symmetrical triangle','Bilateral','triangle','Direction undecided','Lower highs and higher lows converge.','Wait for a confirmed break on either side; prior trend offers context, not certainty.','Return into the triangle after the breakout.','Use width and nearby levels, not the apex as a trade deadline.','symmetrical-triangle');
  pattern('falling-wedge','Falling wedge','Reversal / continuation','wedge','Bullish leaning','Both boundaries descend and converge; selling progress narrows.','Break above the upper boundary, ideally with a held retest.','Loss of the final swing low after the breakout.','Earlier swing resistance gives staged objectives.','falling-wedge',true);
  pattern('rising-wedge','Rising wedge','Reversal / continuation','wedge','Bearish leaning','Both boundaries rise and converge; upward progress narrows.','Break below the lower boundary; an unfinished wedge is not a short signal.','Recovery above the last high or reclaimed broken boundary.','Prior support gives staged objectives.','rising-wedge');
  pattern('rectangle','Rectangle / range','Bilateral','range','Direction undecided','Repeated highs and lows between roughly horizontal boundaries.','Break and acceptance outside the range, or a separately tested rejection strategy at an edge.','Breakout returning inside the range; edge trade losing the boundary.','Range height is a breakout heuristic; opposite edge is an edge-trade objective.','rectangle');
  pattern('price-channel','Price channel','Continuation','channel','Trend dependent','Roughly parallel swing boundaries containing an established trend.','A held pullback or confirmed channel break, according to the chosen strategy.','Loss of the supporting swing structure; do not mix breakout and mean-reversion rules.','Opposite channel edge or prior swing, adjusted as time advances.',null);
  pattern('megaphone','Broadening / megaphone','Bilateral','broad','Direction undecided','Higher highs and lower lows expand the range, often with unstable volatility.','Require a separately defined boundary break and follow-through.','Rapid return into the expanding range.','Opposite boundary moves over time; widen risk estimates rather than assuming fixed targets.','broadening-top-or-megaphone-top');
  pattern('measured-move','Measured move / ABC','Continuation','abc','Trend dependent','An initial leg, a corrective pause, then a second leg.','Completion of correction plus a price trigger in the original direction.','Correction breaches the chosen structural invalidation.','A second leg comparable to the first is a projection, not a probability.','measured-move-bullish');
  pattern('harmonics','Harmonic XABCD family','Reversal','harmonic','Pattern dependent','Gartley, Bat, Butterfly and Crab use different Fibonacci swing ratios. Ratios must be checked, not inferred from the sketch.','A reversal trigger near a correctly measured completion zone; confluence alone is not entry permission.','Price moves beyond the explicitly defined completion-zone invalidation.','Earlier swing/Fibonacci retracement levels; each subtype needs its own tested rules.',null);
  pattern('doji','Doji','Candlesticks','doji','Indecision','Open and close are close together relative to the candle range; wick lengths vary.','Look for a later directional break in context, especially at a significant level.','No follow-through, or continuation through the opposite side.','No standalone measured target; use the surrounding price structure.',null);
  pattern('hammer','Hammer / hanging man','Candlesticks','hammer','Context dependent','Small body and long lower wick. At a decline low it can resemble a hammer; after a rise, a hanging man.','A subsequent confirming close; the same candle shape has different meanings in different trends.','Loss of wick low for a bullish interpretation; bullish continuation challenges the bearish interpretation.','Nearby swing levels, with wick-based risk considered.',null);
  pattern('shooting-star','Shooting star / inverted hammer','Candlesticks','hammer','Context dependent','Small body and long upper wick. Location after a rise or decline changes interpretation.','A later confirming move away from the relevant level.','Continuation through the wick extreme against the proposed direction.','Use surrounding support/resistance, not a fixed candle target.',null,true);
  pattern('bull-engulfing','Bullish engulfing','Candlesticks','engulf','Bullish leaning','A bullish body envelops the preceding bearish body; wick engulfing is a different requirement.','Follow-through after a decline or supported pullback.','Price loses the relevant engulfing low.','Next resistance, assessed against distance to invalidation.',null);
  pattern('bear-engulfing','Bearish engulfing','Candlesticks','engulf','Bearish leaning','A bearish body envelops the preceding bullish body near a relevant price area.','Bearish follow-through after a rise or failed reclaim.','Price recovers above the relevant engulfing high.','Next support, assessed against risk and costs.',null,true);
  const rows = config.section === 'indicators' ? config.indicators.map(row=>({...row,group:row.placement==='overlay'?'On-chart overlays':'Lower-pane studies'})) : patterns;
  rows.sort((a,b)=>a.group.localeCompare(b.group)||a.label.localeCompare(b.label));
  let current;
  const text = (tag,value,parent=content) => {const node=document.createElement(tag);node.textContent=value;parent.append(node);return node;};
  function diagram(row) {
    const ns='http://www.w3.org/2000/svg', svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 640 210');svg.classList.add('pattern-diagram');svg.setAttribute('role','img');svg.setAttribute('aria-label',row.label+' illustrative price structure, not real candles');
    const values=shapes[row.shape], line=document.createElementNS(ns,'polyline');
    line.setAttribute('points',values.map((v,i)=>`${25+i*590/(values.length-1)},${25+(row.invert?100-v:v)*1.5}`).join(' '));line.setAttribute('fill','none');line.setAttribute('stroke',row.bias.includes('Bearish')?'#e8909d':'#70d1b4');line.setAttribute('stroke-width','3');
    if(row.group==='Candlesticks') {
      const bars=row.shape==='doji'?[[30,25,65,58],[65,45,115,100],[120,75,165,121],[112,80,135,95]]:row.shape==='hammer'?[[35,25,80,70],[75,60,115,103],[82,75,178,95],[95,55,110,65]]:[[30,25,85,75],[80,60,125,112],[112,95,155,140],[153,85,162,92]];
      bars.forEach(([open,high,low,close],i)=>{const flip=y=>row.invert?210-y:y, colour=(close<open)!==row.invert?'#70d1b4':'#e8909d', x=100+i*135;
        const wick=document.createElementNS(ns,'line');wick.setAttribute('x1',x);wick.setAttribute('x2',x);wick.setAttribute('y1',flip(high));wick.setAttribute('y2',flip(low));wick.setAttribute('stroke',colour);wick.setAttribute('stroke-width','2');
        const body=document.createElementNS(ns,'rect');body.setAttribute('x',x-17);body.setAttribute('y',Math.min(flip(open),flip(close)));body.setAttribute('width','34');body.setAttribute('height',Math.max(2,Math.abs(open-close)));body.setAttribute('fill',colour);svg.append(wick,body);
      });
    } else svg.append(line);
    content.append(svg);
    text('p','Illustrative structure: time left to right, relative price upward. Not market data or an automatic detector.').className='library-meta';
  }
  function render(row) {
    current=row;content.replaceChildren();text('p',row.group).className='library-meta';text('h1',row.label);
    if(config.section==='indicators') {
      text('p',help.guides[row.id] || 'Interpret this study using its actual source and parameter definitions. There is no universal entry rule for every market.');
      text('h2','Reading the study');
      for(const note of help.details[row.id] || ['No universal overbought or oversold level is defined for this study. Check whether its output is bounded, a price overlay, or an unbounded oscillator before interpreting thresholds.','Use closed-bar changes together with trend, support/resistance and a fresh price trigger. A cross is a candidate event, not proof of a profitable entry.','Colours identify rendered series or state, not a calibrated buy probability. Actual line colours are shown in the chart information window.'])text('p',note);
      text('h2','Parameters');text('p',row.parameters || 'No configurable parameters.');
      const table=document.createElement('table'), head=document.createElement('thead'), tr=document.createElement('tr');for(const title of ['Parameter','Default','Range / type'])text('th',title,tr);head.append(tr);table.append(head);const body=document.createElement('tbody');
      for(const value of row.schema || []) {const entry=document.createElement('tr');text('td',value.label || value.name,entry);text('td',String(value.default ?? '-'),entry);text('td',value.type==='boolean'?'On / off':[value.min,value.max].filter(v=>v!==undefined).join(' - ')||value.type||'-',entry);body.append(entry);}table.append(body);content.append(table);
      text('h2','Implementation');text('p','Native Python calculation. TradingView parity is not guaranteed; differences can include smoothing, initialization, candle source and settings. Personal saved indicators remain in your chart account, not this public built-in catalogue.');
      const button=text('button','View Python source');button.className='library-source';button.type='button';
      button.onclick=async()=>{button.disabled=true;try{const response=await fetch('/api/indicator-source/'+encodeURIComponent(row.id));const payload=await response.json();if(!response.ok)throw new Error(payload.error||'Source unavailable');if(current!==row)return;text('p',payload.origin);text('pre',payload.files.map(file=>file.code).join('\n\n'));button.remove();}catch(error){if(current===row)text('p',error.message);}finally{button.disabled=false;}};
    } else {
      text('p',row.bias).className='library-meta';diagram(row);
      for(const [heading,value] of [['Recognition',row.recognize],['Confirmation',row.trigger],['Invalidation',row.invalid],['Objectives',row.target]]) {text('h2',heading);text('p',value);}
      text('h2','Crypto context and failures');text('p','Crypto trades continuously. Session gaps and stock-market time assumptions may not transfer directly. Check liquidity, spread, funding and exchange-specific volume. Wicks can be liquidation events; wait for the chosen closed-bar confirmation and do not chase a stretched move. Define risk before entering.');
      const link=document.createElement('a');link.href=row.reference?base+row.reference:'https://chartschool.stockcharts.com/table-of-contents/chart-analysis/chart-patterns';link.target='_blank';link.rel='noopener noreferrer';link.textContent=row.reference?'Further reading: ChartSchool '+row.label:'Further reading: ChartSchool chart-pattern library';content.append(link);
    }
    text('p','Educational reference, not trade advice or a profit guarantee. Validate rules with modeled costs and separate training, validation and untouched holdout data.').className='library-caution';
    for(const link of toc.querySelectorAll('a')) {if(link.hash==='#'+row.id)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');}
  }
  function filter() {
    const query=search.value.trim().toLowerCase(), shown=rows.filter(row=>(row.label+' '+row.id+' '+row.group).toLowerCase().includes(query));toc.replaceChildren();let group='';
    for(const row of shown){if(group!==row.group){group=row.group;text('small',group,toc);}const link=text('a',row.label,toc);link.href='#'+row.id;if(row===current)link.setAttribute('aria-current','true');}
    count.textContent=shown.length+' of '+rows.length;if(!shown.length)text('p','No matching entries.',toc);
  }
  function select() {const row=rows.find(item=>item.id===location.hash.slice(1))||rows[0];if(row)render(row);filter();}
  search.addEventListener('input',filter);window.addEventListener('hashchange',select);select();
})();
