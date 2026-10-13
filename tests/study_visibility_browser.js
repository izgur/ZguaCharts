const assert=require('node:assert/strict');
const fs=require('node:fs');
const {chromium}=require('playwright');
const source=fs.readFileSync('static/app.js','utf8');
function extract(name){const start=source.indexOf('function '+name+'(');return source.slice(start,source.indexOf('\nfunction ',start+1));}
const drawings=fs.readFileSync('static/chart-drawings.js','utf8');
const visibilityStart=drawings.indexOf('    function refreshVisibility()');
const visibility=drawings.slice(visibilityStart,drawings.indexOf('    function buildControlMenu(',visibilityStart));
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1200,height:800}});
    await page.setContent('<div id="pane"><div class="main-chart-wrap"></div><div class="chart-stack"></div></div>');
    await page.addStyleTag({content:fs.readFileSync('static/styles.css','utf8')});
    await page.addScriptTag({content:`
      const config={indicators:[{id:'cipherb',label:'Cipher B-style Oscillator',schema:[],parameters:'default'}]};
      window.ChartIndicatorHelp={attach:()=>{}};
      let ids=['cipherb'];
      function selectedIndicators(){return ids;}
      function applyIndicatorHeights(){}
      function escapeHtml(value){return String(value);}
      function saveState(){}
      function buildMarkerDetailsMap(){return new Map();}
      function clearBacktestOverlaySeries(p){p.backtestOverlaySeries=[];}
      function indicatorSeriesData(p,data){return data;}
      function seriesOptions(){return {};}
      function addSeries(){return {visible:true,applyOptions(options){this.visible=options.visible;},setData(){}};}
      const settings={hidePositions:false,hideIndicators:false};
      const options={onVisibilityChange:()=>updateChartMarkers(pane)};
      const pane={node:document.getElementById('pane'),hiddenIndicators:new Set(['cipherb']),indicatorSettings:{},indicatorCharts:[],overlaySeries:[],backtestOverlaySeries:[],signalMarkerToggle:{checked:false},presetSelect:{value:'demo_relativestrengthv2'},series:{setMarkers(){}},signalMarkers:[],backtestMarkers:[],chart:{}};
      ${extract('renderIndicatorLabels')}
      ${extract('applyIndividualIndicatorVisibility')}
      ${extract('updateChartMarkers')}
      ${extract('renderBacktestOverlays')}
      ${visibility}
      pane.drawingTools={refreshVisibility};
      window.testPane=pane;
      window.changeVisibility=(visible)=>{pane.signalMarkerToggle.checked=visible;updateChartMarkers(pane);};
      window.newOverlays=()=>renderBacktestOverlays(pane,{overlays:[{type:'line',data:[]}]});
      window.refreshDrawings=refreshVisibility;
      window.unhideStudy=()=>{pane.hiddenIndicators.clear();applyIndividualIndicatorVisibility(pane);};
      window.removeStudy=()=>{ids=[];renderIndicatorLabels(pane);};
      renderIndicatorLabels(pane);window.newOverlays();
    `});
    assert.equal(await page.locator('.chart-indicator-label').isVisible(),false);
    assert.equal(await page.evaluate(()=>testPane.backtestOverlaySeries[0].visible),false);
    await page.evaluate(()=>{refreshDrawings();newOverlays();});
    assert.equal(await page.evaluate(()=>testPane.backtestOverlaySeries[0].visible),false);
    await page.evaluate(()=>changeVisibility(true));
    assert.equal(await page.evaluate(()=>testPane.backtestOverlaySeries[0].visible),true);
    await page.evaluate(()=>{testPane.presetSelect.value='none';changeVisibility(true);});
    assert.equal(await page.evaluate(()=>testPane.backtestOverlaySeries[0].visible),false);
    await page.evaluate(()=>unhideStudy());
    assert.equal(await page.locator('.chart-indicator-label').isVisible(),true);
    await page.evaluate(()=>removeStudy());
    assert.equal(await page.locator('.chart-indicator-label').count(),0);
    console.log('Hidden study labels, tactic eye, asynchronous overlays and drawing refresh visibility passed.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
