const assert=require('node:assert/strict');
const fs=require('node:fs');
const {chromium}=require('playwright');
const source=fs.readFileSync('static/app.js','utf8');
function extract(name){const start=source.indexOf('function '+name+'(');return source.slice(start,source.indexOf('\nfunction ',start+1));}
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try{
    const page=await browser.newPage();
    await page.setContent('<div id="backtest-modal" class="modal-backdrop"><section class="modal"><div id="content"></div></section></div>');
    await page.addStyleTag({content:fs.readFileSync('static/styles.css','utf8')});
    await page.addScriptTag({content:`
      window.ChartAccounts={canManageTactics:()=>false};
      const config={chart_signal_strategies:[{id:'demo_relativestrengthv2',strategy:'RelativeStrengthV2',label:'RelativeStrengthV2',params:{},demo:true}]};
      function escapeHtml(value){return String(value);}
      function defaultExecutionTimeframe(){return '5m';}
      function openBacktestModal(html){document.getElementById('content').innerHTML=html;}
      function runBacktest(){window.runRequested=true;}
      ${extract('backtestLimitOptions')}
      ${extract('openBacktestControls')}
      const pane={requestId:1,sourceSelect:{value:'bybit'},symbolSelect:{value:'BTCUSDT'},timeframeSelect:{value:'1h'},presetSelect:{value:'demo_relativestrengthv2'}};
      openBacktestControls(pane);
    `});
    assert.equal(await page.locator('#modal-preset-select').isDisabled(),true);
    assert.equal(await page.locator('#modal-allow-shorts').isEnabled(),true);
    assert.equal(await page.locator('#modal-fee-input').isDisabled(),true);
    assert.equal(await page.locator('#modal-rs-params').isVisible(),true);
    assert.equal(await page.locator('#modal-regime-mode').inputValue(),'looseBtcBull');
    await page.locator('#modal-allow-shorts').check();
    await page.locator('#modal-rs-lookback').fill('12');
    await page.locator('#modal-run-backtest').click();
    assert.equal(await page.evaluate(()=>window.runRequested),true);
    for(const width of [1440,390]){
      await page.setViewportSize({width,height:900});
      const box=await page.locator('#modal-rs-params').boundingBox();
      assert.ok(box.width<=width);
    }
    await page.screenshot({path:'reports/guest-backtest-controls-20261013.png'});
    console.log('Guest parameter controls, short checkbox, locked strategy/costs and responsive layout passed.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
