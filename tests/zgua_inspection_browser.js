const assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{const browser=await chromium.launch({channel:'msedge',headless:true});try{
 const page=await browser.newPage({viewport:{width:1600,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:5000/charts');await page.waitForFunction(()=>panes[0]?.historyLoaded);
 await page.evaluate(async()=>{const p=panes[0];p.ws?.close();clearTimeout(p.pollTimer);clearTimeout(p.strategyPollTimer);p.requestId++;p.strategyRequestId++;
  p.candles=Array.from({length:600},(_,i)=>{const v=100+8*Math.sin(i/5)+4*Math.sin(i/32);return {time:1700000000+i*3600,open:v-.3,high:v+2,low:v-2,close:v+Math.cos(i/14),volume:100+i%17};});
  p.series.setData(p.candles);restoreIndicators(p,['zgua','rsi']);await renderIndicators(p,p.requestId);p.chart.timeScale().setVisibleLogicalRange({from:420,to:610});syncIndicatorTimeRanges(p);
  p.chart.subscribeCrosshairMove(param=>window.hitMarker=param.hoveredObjectId);
 });
 const points=await page.evaluate(()=>{const p=panes[0],r=p.chartEl.getBoundingClientRect();return p.overlayPayload.filter(s=>s.indicatorId==='zgua').flatMap(s=>s.markers||[]).filter(m=>p.chart.timeScale().timeToCoordinate(m.time)>0).map(m=>({id:m.id,x:r.x+p.chart.timeScale().timeToCoordinate(m.time),top:r.top,bottom:r.bottom}));});
 let clicked=false;
 for(const point of points.slice(-8)){
  for(let y=point.top+15;y<point.bottom-25;y+=5){await page.mouse.move(point.x,y);const id=await page.evaluate(()=>window.hitMarker);if(String(id).startsWith('zgua:')){await page.mouse.click(point.x,y);clicked=true;break;}}
  if(clicked)break;
 }
 assert.ok(clicked,'A real canvas divergence label must be clickable');
 await page.waitForSelector('[data-indicator-id="zgua-inspection"] .zgua-study-select');
 const state=await page.evaluate(()=>{const p=panes[0],inspection=p.divergenceInspection,lower=p.indicatorCharts.find(item=>item.element.dataset.indicatorId==='zgua-inspection'),t=inspection.event.pivotTime;return {studies:inspection.studies.length,main:p.chart.timeScale().timeToCoordinate(t),lower:lower.chart.timeScale().timeToCoordinate(t),data:lower.series[0].data().find(row=>row.time===t)?.value,expected:inspection.studies[0].pivotValue};});
 assert.ok(Math.abs(state.main-state.lower)<2);assert.equal(state.data,state.expected);
 await page.screenshot({path:'reports/zgua-inspection-20261009.png'});
 const select=page.locator('.zgua-study-select');if(state.studies>1){await select.selectOption('1');await page.waitForFunction(()=>panes[0].divergenceInspection.selected===1);}
 await page.getByRole('button',{name:'Close divergence inspection',exact:true}).click();await page.waitForFunction(()=>!document.querySelector('[data-indicator-id="zgua-inspection"]'));
 assert.ok(await page.evaluate(()=>panes[0].indicatorCharts.some(item=>item.element.dataset.indicatorId==='rsi')));
 const source=await page.request.get('http://127.0.0.1:5000/api/tactic-source/ZGUA');assert.equal(source.status(),200);
 const code=(await source.json()).code;
 const workerResult=await page.evaluate(async code=>{const p=panes[0],options=await customTacticOptions(p,{code});return await new Promise((resolve,reject)=>{const worker=new Worker('/api/tactic-source/worker.js');worker.onmessage=({data})=>{worker.terminate();data.error?reject(new Error(data.error)):resolve(JSON.parse(data.result));};worker.onerror=e=>{worker.terminate();reject(new Error(e.message));};worker.postMessage({code,options});});},code);
 assert.equal(workerResult.baseStrategy,'ZGUA');assert.ok(Number.isFinite(workerResult.result.netReturn));assert.ok(workerResult.result.numberOfTrades>0);
 const response=await page.request.post('http://127.0.0.1:5000/api/backtest/run-custom',{timeout:120000,data:{source:'bybit',symbol:'BTCUSDT',timeframe:'1h',strategy:'ZGUA',executionTimeframe:'5m',period:'21d',limit:504,feePct:.055,slippagePct:.02,saveHistory:false}});
 const native=await response.json();assert.equal(response.status(),200,JSON.stringify(native));assert.equal(native.executionTimeframe,'1h');assert.ok(Number.isFinite(native.total_return_pct));
 assert.deepEqual(errors,[]);console.log('ZGUA real marker click, oscillator values, aligned timestamps, selector, close and source passed');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
