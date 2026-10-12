const assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{const browser=await chromium.launch({channel:'msedge',headless:true});try{
 const page=await browser.newPage({viewport:{width:1600,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:5000/charts');
 await page.waitForFunction(()=>window.ChartIndicatorHelp&&config);
 await page.evaluate(()=>{for(const id of ['rsi','macd','cipherb','ichimoku']){const button=document.createElement('button');button.id='help-test-'+id;button.textContent=id;document.body.append(button);ChartIndicatorHelp.attach(button,config.indicators.find(info=>info.id===id),()=>[]);button.click();}});
 const windows=page.locator('.indicator-help-tooltip');assert.equal(await windows.count(),4);
 await page.mouse.click(1590,990);assert.equal(await windows.count(),4);
 for(let i=3;i>=0;i--){const header=windows.nth(i).locator('strong'),box=await header.boundingBox();await page.mouse.move(box.x+20,box.y+10);await page.mouse.down();await page.mouse.move(20+i*395,80,{steps:8});await page.mouse.up();}
 for(const box of await windows.evaluateAll(nodes=>nodes.map(node=>{const r=node.getBoundingClientRect();return {left:r.left,top:r.top,right:r.right,bottom:r.bottom};}))){assert.ok(box.left>=0&&box.top>=0&&box.right<=1600&&box.bottom<=1000);}
 await page.evaluate(()=>document.querySelector('#help-test-rsi').click());assert.equal(await windows.count(),4);
 await page.screenshot({path:'reports/indicator-info-windows-20261008.png'});
 await windows.filter({has:page.getByText('RSI 14',{exact:true})}).getByRole('button',{name:'Close RSI 14 information'}).click();assert.equal(await windows.count(),3);
 await page.keyboard.press('Escape');assert.equal(await windows.count(),2);
 await windows.last().getByRole('button',{name:'Close all',exact:true}).click();assert.equal(await windows.count(),0);
 await page.setViewportSize({width:390,height:844});await page.evaluate(()=>document.querySelector('#help-test-cipherb').click());
 const box=await windows.first().boundingBox();assert.ok(box.x>=0&&box.x+box.width<=390&&box.y+box.height<=844);
 await page.screenshot({path:'reports/indicator-info-mobile-20261008.png'});
 assert.deepEqual(errors,[]);console.log('Four independent info windows: persistence, dragging, bounds, deduplication, close, Escape, close all and mobile passed');
}finally{await browser.close();}})().catch(error=>{console.error(error);process.exitCode=1;});
