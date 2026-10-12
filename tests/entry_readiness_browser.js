const assert = require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1600,height:1000}}),errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/api/candles?*',route=>{
      const query=new URL(route.request().url()).searchParams, tf=query.get('timeframe');
      const step=Number(tf.slice(0,-1))*(tf.endsWith('h')?3600:tf.endsWith('d')?86400:60);
      const end=Math.floor(Number(query.get('before')||Date.now()/1000)/step)*step;
      const count=Math.min(Number(query.get('limit')||600),tf==='5m'?5000:600);
      const candles=Array.from({length:count},(_,i)=>{
        const time=end-(count-i)*step, close=1000+time/86400*.03+Math.sin(time/86400)*3;
        return {time,open:close-.1,high:close+5,low:close-5,close,volume:100};
      });
      route.fulfill({json:{candles,diagnostics:{}}});
    });
    await page.goto('http://127.0.0.1:5000/charts');
    await page.waitForFunction(()=>panes[0]?.historyLoaded);
    await page.evaluate(()=>{const pane=panes[0];pane.presetSelect.value=config.chart_signal_strategies.find(row=>row.strategy==='SimpleAtrTrendV2').id;pane.presetSelect.dispatchEvent(new Event('change'));});
    await page.waitForFunction(()=>panes[0].entryReadiness?.rows.length===3,{},{timeout:60000});
    const first=await page.locator('.header-signal-pill').first().getAttribute('title');
    assert.match(first,/market-assessment-v1.*SimpleAtrTrendV2/s);
    assert.match(first,/NOT win probability/);
    assert.match(first,/modeled round-trip fee\/slippage: 0.150%/);
    assert.equal(await page.locator('.header-signal-pill').count(),3);
    assert.equal(await page.evaluate(async()=>{const abort=new AbortController();abort.abort();try{await ChartCustomTactics.execute('',{},abort.signal);return false;}catch(error){return error.message.includes('superseded');}}),true);
    await page.locator('.header-signal-pill').first().click();
    assert.match(await page.locator('#backtest-content').innerText(),/Experimental rule-based score/);
    await page.locator('#backtest-close').click();
    await page.evaluate(async()=>{
      const pane=panes[0],time=pane.candles.at(-4).time;
      const bar=pane.candles.at(-4),x=pane.chart.timeScale().timeToCoordinate(time),y=pane.series.priceToCoordinate((bar.open+bar.close)/2);
      await showHistoricalSignalStrength(pane,{time,point:{x,y}});
    });
    assert.match(await page.locator('.historical-signals').innerText(),/Direction \/ entry quality/);
    assert.equal(await page.locator('.historical-signal-row').count(),3);
    await page.evaluate(async()=>{
      const pane=panes[0];pane.replayActive=true;pane.candles=pane.candles.slice(0,-10);await refreshHeaderSignalStrip();
    });
    assert.ok(await page.evaluate(()=>panes[0].entryReadiness.rows.every(row=>!row.closedAt||row.closedAt<=panes[0].entryReadiness.asOf)));
    await page.evaluate(async()=>{
      const pane=panes[0];pane.replayActive=false;pane.presetSelect.value=config.chart_signal_strategies.find(row=>row.strategy==='RelativeStrengthFreshV3').id;await refreshHeaderSignalStrip();
    });
    assert.match(await page.locator('.header-signal-pill').first().getAttribute('title'),/RelativeStrengthFreshV3/);
    assert.equal(await page.locator('.header-signal-pill svg').count(),3);
    const quoteChecks=await page.evaluate(()=>{
      const row={timeframe:'15m',status:'ENTER',side:'long',price:100,atr:2,stopPrice:98,objective:104,roundTripCostPct:.15,blockers:[]};
      return [ChartEntryReadiness.quoteReview(row,100,2).status,ChartEntryReadiness.quoteReview(row,102,2).status,
        ChartEntryReadiness.quoteReview(row,100,31).status,ChartEntryReadiness.quoteReview(row,97,2).status];
    });
    assert.deepEqual(quoteChecks,['ENTER','LATE','UNAVAILABLE','WAIT']);
    assert.doesNotMatch(await page.evaluate(()=>ChartEntryReadiness.title({version:'market-assessment-v1',score:null,timeframe:'15m',blockers:['missing data']},{label:'test'},'bybit','BTCUSDT')),/undefined|Invalid Date/);
    const snapshots=await page.evaluate(()=>panes[0].entryReadiness.rows);
    for(const row of snapshots) {
      assert.ok(row.score===null||(Number.isFinite(row.score)&&row.score>=0&&row.score<=100));
      if(row.status==='ENTER'){assert.equal(row.blockers.length,0);assert.equal(row.executionCovered,true);}
    }
    assert.equal(await page.evaluate(()=>config.chart_signal_strategies.filter(row=>row.params?.researchExitMode).length),3);
    const firstTimeframes=await page.locator('.timeframe-shortcuts [data-timeframe]').evaluateAll(nodes=>nodes.slice(0,2).map(node=>node.dataset.timeframe));
    assert.deepEqual(firstTimeframes,['1s','1m']);
    await page.evaluate(async()=>{panes[0].presetSelect.value='none';await refreshHeaderSignalStrip();});
    assert.equal(await page.locator('.header-signal-pill').count(),3);
    assert.match(await page.locator('.header-signal-pill').first().getAttribute('title'),/no tactic selected/);
    await page.screenshot({path:'reports/entry-readiness-20261008.png'});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:'reports/entry-readiness-mobile-20261008.png'});
    assert.deepEqual(errors,[]);
    console.log('Readiness UI: selected tactic, closed bars, cost disclosure, historical popup, replay cutoff, new variant and mobile passed');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
