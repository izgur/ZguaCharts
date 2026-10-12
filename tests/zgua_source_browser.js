const assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{const browser=await chromium.launch({channel:'msedge',headless:true});try{
 const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:5000/community/library/indicators#zgua');
 await page.addScriptTag({url:'/static/custom-indicators.js'});
 const result=await page.evaluate(async()=>{
  const rows=Array.from({length:300},(_,i)=>{const value=100+8*Math.sin(i/5)+4*Math.sin(i/32);return {time:1700000000+i*3600,open:value-.3,high:value+2,low:value-2,close:value+Math.cos(i/14),volume:100+i%17};});
  const source=await fetch('/api/indicator-source/zgua').then(r=>r.json());
  const native=await fetch('/api/indicators?indicators=zgua',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candles:rows})}).then(r=>r.json());
  const copy=await ChartCustomIndicators.execute(source.files[0].code,rows);
  return {defaults:Object.keys(copy.defaults).length,markers:copy.payload.overlays.flatMap(s=>s.markers),native:native.overlays.flatMap(s=>s.markers),segments:copy.payload.overlays.flatMap(s=>s.segments),events:copy.payload.overlays.flatMap(s=>s.events)};
 });
 assert.equal(result.defaults,34);assert.deepEqual(result.markers,result.native);assert.ok(result.segments.length>0);assert.ok(result.events.length>0);assert.deepEqual(errors,[]);
 console.log('Zgua single-source Python runs in the isolated browser worker; 34 settings, signals, safe segments and reading metadata match the native study.');
}finally{await browser.close();}})().catch(error=>{console.error(error);process.exitCode=1;});
