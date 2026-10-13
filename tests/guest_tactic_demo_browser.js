const assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{const browser=await chromium.launch({channel:'msedge',headless:true});try{
 const page=await browser.newPage({viewport:{width:1600,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:5000/charts');await page.waitForFunction(()=>panes[0]?.historyLoaded);
 const select=page.locator('.preset-select').first();assert.equal(await select.isVisible(),true);assert.equal(await select.isDisabled(),true);assert.equal(await select.locator('option').count(),1);assert.equal(await select.inputValue(),'demo_relativestrengthv2');
 for(const selector of ['.tactic-source-button','.tactic-visibility-button','.backtest-button'])assert.equal(await page.locator(selector).first().isVisible(),true,selector);
 assert.equal(await page.locator('.page-menu').isVisible(),false);await page.waitForFunction(()=>panes[0].strategyPayload?.guestDemo===true,null,{timeout:120000});
 await page.locator('.tactic-source-button').first().click();await page.getByText('Read-only RelativeStrengthV2 demo',{exact:true}).waitFor();assert.equal(await page.locator('.tactic-editor textarea').evaluate(el=>el.readOnly),true);
 for(const selector of ['.tactic-new','.tactic-save-copy','.tactic-save','.tactic-delete'])assert.equal(await page.locator('.tactic-editor '+selector).isVisible(),false);
 await page.locator('.tactic-editor .source-close').click();await page.locator('.backtest-button').first().click();assert.equal(await page.locator('#modal-preset-select').isDisabled(),true);assert.equal(await page.locator('#modal-preset-select option').count(),1);await page.locator('#modal-run-backtest').click();await page.waitForFunction(()=>document.querySelector('#modal-results')?.textContent.includes('Total return'),null,{timeout:120000});
 await page.screenshot({path:'reports/guest-tactic-demo-20261012.png'});
 const response=await page.request.post('http://127.0.0.1:5000/api/backtest/run-custom',{data:{strategy:'ZGUA'}});assert.equal(response.status(),403);assert.deepEqual(errors,[]);
 console.log('Guest header, locked V2 selector, read-only source, real bounded backtest and forbidden tactic API passed.');
}finally{await browser.close();}})().catch(error=>{console.error(error);process.exitCode=1;});
