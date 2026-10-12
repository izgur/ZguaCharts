(function(root){
 'use strict';
 function applyTrade(pane,trade,seen){
  const id=String(trade.i||''),time=Math.floor(Number(trade.T)/1000),price=Number(trade.p),size=Number(trade.v);
  if(!id||seen.has(id)||![time,price,size].every(Number.isFinite)||price<=0||size<0)return null;
  seen.add(id);if(seen.size>20000)seen.delete(seen.values().next().value);
  const last=pane.candles.at(-1);if(last&&time<last.time)return null;
  const candle=last?.time===time?{...last,high:Math.max(last.high,price),low:Math.min(last.low,price),close:price,volume:last.volume+size}:{time,open:price,high:price,low:price,close:price,volume:size};
  return mergeLiveCandle(pane,candle)?candle:null;
 }
 function connect(pane,requestId){
  if(pane.disposed||pane.replayActive||requestId!==pane.requestId)return;
  const category={bybit:'linear',bybit_spot:'spot',bybit_inverse:'inverse'}[pane.sourceSelect.value];
  const ws=new WebSocket('wss://stream.bybit.com/v5/public/'+category),seen=new Set(pane.candleDiagnostics?.seedTradeIds||[]);
  pane.ws=ws;let heartbeat,lastRefresh=0,busy=false;
  const active=()=>!pane.disposed&&!pane.replayActive&&pane.ws===ws&&requestId===pane.requestId;
  ws.onopen=()=>{if(!active()){ws.close();return;}ws.send(JSON.stringify({op:'subscribe',args:['publicTrade.'+pane.symbolSelect.value]}));heartbeat=setInterval(()=>{if(ws.readyState===WebSocket.OPEN)ws.send(JSON.stringify({op:'ping'}));},20000);pane.status.textContent='1s public trades connected · Limited seed/live history; gaps are not fabricated';};
  ws.onmessage=event=>{
   if(!active())return;let message;try{message=JSON.parse(event.data);}catch{return;}
   if(!message.topic?.startsWith('publicTrade.')||!Array.isArray(message.data))return;
   for(const trade of [...message.data].sort((a,b)=>Number(a.T)-Number(b.T))){const candle=applyTrade(pane,trade,seen);if(!candle)continue;
    pane.historyLoaded=true;if(pane.chartStyle&&pane.chartStyle!=='candles')updatePresentationSeries(pane,candle);else pane.series.update(candle);updateTicker(pane,candle.close);
   }
   if(pane.candles.length>5000){pane.candles=pane.candles.slice(-5000);if(pane.chartStyle&&pane.chartStyle!=='candles')updatePresentationSeries(pane);else pane.series.setData(pane.candles);}
   if(!busy&&Date.now()-lastRefresh>=2000){busy=true;lastRefresh=Date.now();renderIndicators(pane,requestId).catch(error=>{if(active())pane.status.textContent='1s indicators: '+error.message;}).finally(()=>{busy=false;});}
  };
  ws.onerror=()=>{if(active())pane.status.textContent='1s trade feed connection error';};
  ws.onclose=()=>{clearInterval(heartbeat);if(active()){pane.status.textContent='1s disconnected; reconnecting · Missed trades cannot be backfilled';setTimeout(()=>{if(active())connect(pane,requestId);},3000);}};
 }
 root.ChartSecondCandles={connect,applyTrade};
})(window);
