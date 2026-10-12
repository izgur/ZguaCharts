const fs = require('fs');
const path = require('path');
const acorn = require('../vendor/javascript/node_modules/acorn');
const root = path.resolve(__dirname, '..');
const enginePath = path.join(root, 'core/backtest/regimeTrend.js');
const engine = fs.readFileSync(enginePath, 'utf8');
const tree = acorn.parse(engine, {ecmaVersion: 'latest'});
const functions = new Map(tree.body.filter(node => node.type === 'FunctionDeclaration').map(node => [node.id.name, node]));
const entries = {RelativeStrengthV2: 'relativeStrengthV2Entry', RelativeStrengthFreshV3: 'relativeStrengthFreshV3Entry', EmaBounceV2: 'emaBounceV2Entry', SimpleAtrTrendV2: 'simpleAtrTrendV2Entry', PullbackReclaimV2: 'pullbackReclaimV2Entry', BreakoutRetestV2: 'breakoutRetestV2Entry'};
function visit(node, callback) {
  if (!node || typeof node !== 'object') return;
  callback(node);
  for (const value of Object.values(node)) if (Array.isArray(value)) value.forEach(child => visit(child, callback)); else if (value && typeof value === 'object') visit(value, callback);
}
function source(name) {
  if (name === 'ZGUA') {
    const runner = require('../core/backtest/zgua');
    const params = require('../core/strategies').getStrategy(name).params;
    return {strategy:name, params, code:'// Confirmed divergences are computed by the Python Zgua detector using chart settings.\n// Edit these entry/exit decisions; no ATR or trend exit is added.\nconst BASE_STRATEGY = "ZGUA";\nconst PARAMS = '+JSON.stringify(params,null,2)+';\n'+runner.entry.toString()+'\n'+runner.exit.toString()+'\nmodule.exports={baseStrategy:BASE_STRATEGY,params:PARAMS,entry,exit};\n'};
  }
  const entry = entries[name]; if (!entry) throw new Error('Unknown tactic');
  const included = new Set(), pending = [entry];
  while (pending.length) {
    const key = pending.pop(); if (included.has(key)) continue;
    included.add(key);
    visit(functions.get(key), node => { if (node.type === 'Identifier' && functions.has(node.name) && !included.has(node.name)) pending.push(node.name); });
  }
  const params = require(enginePath).defaultParams(name);
  const parts = [...included].map(key => { const node = functions.get(key); return engine.slice(node.start, node.end); });
  return {strategy: name, params, code: '// Selected entry rule and required helpers. Shared engine handles sizing, costs and exits.\nconst BASE_STRATEGY = ' + JSON.stringify(name) + ';\nconst PARAMS = ' + JSON.stringify(params, null, 2) + ';\n\n' + parts.join('\n\n') + '\n\nmodule.exports = {baseStrategy: BASE_STRATEGY, params: PARAMS, entry: ' + entry + '};\n'};
}
function runtime() {
  const dataSource = fs.readFileSync(path.join(root, 'core/data/index.js'), 'utf8');
  const dataTree = acorn.parse(dataSource, {ecmaVersion:'latest'});
  const normalize = dataTree.body.find(node => node.type === 'FunctionDeclaration' && node.id.name === 'normalizeCandles');
  const interval = dataTree.body.find(node => node.type === 'FunctionDeclaration' && node.id.name === 'intervalToMs');
  const modules = {'../data': dataSource.slice(normalize.start, normalize.end) + '\n' + dataSource.slice(interval.start, interval.end) + '\nmodule.exports={normalizeCandles, intervalToMs};', '../indicators':fs.readFileSync(path.join(root,'core/indicators/index.js'),'utf8'), '../regime':fs.readFileSync(path.join(root,'core/regime/index.js'),'utf8'), engine};
  modules['./marketAssessment'] = fs.readFileSync(path.join(root,'core/backtest/marketAssessment.js'),'utf8');
  modules['./zgua'] = fs.readFileSync(path.join(root,'core/backtest/zgua.js'),'utf8');
  const metrics = fs.readFileSync(path.join(root,'core/backtest/index.js'),'utf8');
  const metricFunctions = new Map(acorn.parse(metrics,{ecmaVersion:'latest'}).body.filter(node=>node.type==='FunctionDeclaration').map(node=>[node.id.name,node]));
  const included = new Set(), pending = ['formatResult'];
  while(pending.length) { const key=pending.pop(); if(included.has(key))continue; included.add(key); visit(metricFunctions.get(key),node=>{if(node.type==='Identifier'&&metricFunctions.has(node.name)&&!included.has(node.name))pending.push(node.name);}); }
  modules['./metrics'] = [...included].map(key=>{const node=metricFunctions.get(key);return metrics.slice(node.start,node.end);}).join('\n')+'\nmodule.exports=formatResult;';
  return 'const factories = {\n' + Object.entries(modules).map(([name, code]) => JSON.stringify(name) + ': function(module, exports, require) {\n' + code + '\n}').join(',\n') + '\n};\nconst loaded={};\nfunction require(name){ if(!loaded[name]){if(!factories[name])throw new Error("Unsupported module"); const module={exports:{}};loaded[name]=module;factories[name](module,module.exports,require);} return loaded[name].exports;}\nself.TacticEngine=require("engine");\n';
}
if (require.main === module) process.stdout.write(process.argv[2] === '--runtime' ? runtime() : JSON.stringify(source(process.argv[2])));
module.exports = {source, runtime};
