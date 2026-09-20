import test from 'node:test';
import assert from 'node:assert/strict';
import { mountVitals, explainTemperature } from './vitals-client.js';

test('ambient comfort copy does not diagnose fever and missing values are not classified',()=>{
  assert.equal(explainTemperature(null).tone,'neutral');assert.equal(explainTemperature(0).tone,'cool');
  assert.equal(explainTemperature(22).tone,'mild');assert.equal(explainTemperature(28).tone,'warm');
  assert.doesNotMatch(JSON.stringify(explainTemperature(39)),/fever|healthy|normal body/i);
});

test('the foreground page toggles only for fresh button A events and cleans up its stream',t=>{
  const elements=new Map(),listeners={};let stream,toggles=0,time=100000;
  const root={innerHTML:'',querySelector(s){if(!elements.has(s))elements.set(s,{textContent:'',classList:{toggle(){}}});return elements.get(s);}};
  class FakeStream{constructor(){stream=this;this.listeners={};}addEventListener(n,f){this.listeners[n]=f;}close(){this.closed=true;}send(n,v){this.listeners[n]?.({data:JSON.stringify(v)});}}
  const doc={hidden:false,addEventListener(n,f){listeners[n]=f;},removeEventListener(n){delete listeners[n];}};
  const view=mountVitals(root,()=>toggles++,{EventSource:FakeStream,document:doc,now:()=>time});t.after(()=>view.dispose());
  stream.send('button',{button:2,receivedAt:time});assert.equal(toggles,0);
  stream.send('button',{button:1,receivedAt:time-5000});assert.equal(toggles,0);
  stream.send('button',{button:1,receivedAt:time});stream.send('button',{button:1,receivedAt:time});assert.equal(toggles,1);
  doc.hidden=true;listeners.visibilitychange();assert.equal(stream.closed,true);
  time+=1000;stream.send('button',{button:1,receivedAt:time});assert.equal(toggles,1);
  doc.hidden=false;listeners.visibilitychange();time+=1000;stream.send('button',{button:1,receivedAt:time});assert.equal(toggles,2);
  stream.send('snapshot',{connection:{status:'connected',message:'Connected'},temperature:{status:'live',celsius:24,fahrenheit:75.2,humidity:40,measuredAt:new Date(time).toISOString()},buttons:{connected:true,pressed:[false,false,false]},addresses:[62,68]});
  assert.equal(root.querySelector('#temperature-value').textContent,'24.0');
  stream.onerror();assert.equal(root.querySelector('#temperature-value').textContent,'—');
  view.dispose();assert.equal(stream.closed,true);assert.deepEqual(listeners,{});
});
