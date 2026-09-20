import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { createHardwareState, createLineDecoder } from './hardware-state.js';
import { createSerialHardware, identifyBoard } from './hardware-serial.js';
import { createApp } from './server.js';
import { getConfig } from './voice-config.js';

const frame=(seq=1,buttons=[false,false,false],extra={})=>({protocol:'carechair/1',type:'sample',seq,uptime_ms:seq*1000,temperature_c:23.45,humidity_pct:46.2,temperature_age_ms:20,buttons,i2c_addresses:[62,68],...extra});

test('sensor samples are validated and stale/disconnected temperatures are never presented as live',()=>{
  let time=100000;const hw=createHardwareState(()=>time);hw.setConnection('connected');
  assert.equal(hw.snapshot().temperature.celsius,null);
  assert.equal(hw.ingest(frame()),true);assert.equal(hw.snapshot().temperature.celsius,23.45);
  assert.equal(hw.snapshot().temperature.kind,'ambient');
  time+=5001;assert.equal(hw.snapshot().temperature.status,'stale');assert.equal(hw.snapshot().temperature.celsius,null);
  for(const extra of [{temperature_c:'24'},{temperature_c:126},{temperature_c:Infinity},{humidity_pct:101},{buttons:[true]},{buttons:[1,0,0]},{i2c_addresses:[62]},{seq:-1}])assert.equal(hw.ingest(frame(2,undefined,extra)),false);
  assert.equal(hw.ingest(frame(2,undefined,{temperature_c:null,humidity_pct:null,temperature_age_ms:null})),true);
  assert.equal(hw.snapshot().temperature.celsius,null);
  hw.setConnection('disconnected');assert.equal(hw.snapshot().buttons.pressed,null);
});

test('one press makes one event; held, replayed, rebooted, reconnected and stale buttons never retrigger',()=>{
  let time=100000;const hw=createHardwareState(()=>time),events=[];hw.setConnection('connected');hw.events.on('button',b=>events.push(b.button));
  hw.ingest(frame(1));hw.ingest(frame(2,[true,false,false]));hw.ingest(frame(2,[true,false,false]));hw.ingest(frame(3,[true,false,false]));
  assert.deepEqual(events,[1]);
  hw.ingest(frame(2,[false,false,false]));hw.ingest(frame(4,[true,false,false]));assert.deepEqual(events,[1]);
  hw.ingest(frame(5));hw.ingest(frame(6,[false,true,true]));assert.deepEqual(events,[1,2,3]);
  hw.ingest({protocol:'carechair/1',type:'boot',firmware:'carechair-sensors/1'});
  hw.ingest(frame(0,[true,false,false]));assert.deepEqual(events,[1,2,3]);
  time+=6000;hw.ingest(frame(1,[true,true,true]));assert.deepEqual(events,[1,2,3]);
  hw.setConnection('disconnected');hw.setConnection('connected');hw.ingest(frame(10,[true,false,false]));assert.deepEqual(events,[1,2,3]);
  hw.ingest(frame(11,null));hw.ingest(frame(12,[true,false,false]));assert.deepEqual(events,[1,2,3]);
});

test('framing handles split chunks, garbage, CRLF and oversized lines without unbounded buffering',()=>{
  const messages=[];const decode=createLineDecoder(v=>messages.push(v),30);
  decode(Buffer.from('booting\r\n{"a":'));decode(Buffer.from('1}\r\n'+ 'x'.repeat(10000)+'\n{"b":2}\n'));
  assert.deepEqual(messages,[{a:1},{b:2}]);
});

test('USB detection distinguishes UNO Q, R4 and unrelated serial devices',()=>{
  assert.equal(identifyBoard({vendorId:'2341',productId:'0078'}),'Arduino UNO Q');
  assert.equal(identifyBoard({vendorId:'2341',productId:'006D'}),'Arduino UNO R4 WiFi');
  assert.equal(identifyBoard({vendorId:'1234',productId:'0078'}),null);
});

test('serial bridge opens only a supported device, parses readings and releases the port',async()=>{
  let instance;
  class FakePort extends EventEmitter {
    static async list(){return [{path:'COM10',vendorId:'2341',productId:'0078'}];}
    constructor(options){super();instance=this;this.options=options;}
    open(callback){this.isOpen=true;callback();}
    close(callback){this.isOpen=false;this.emit('close');callback?.();}
  }
  const hw=createSerialHardware({Port:FakePort});hw.start();await new Promise(r=>setImmediate(r));
  assert.equal(instance.options.path,'COM10');instance.emit('data',Buffer.from(JSON.stringify(frame())+'\n'));
  assert.equal(hw.snapshot().temperature.celsius,23.45);
  instance.emit('close');assert.equal(hw.snapshot().temperature.celsius,null);
  await hw.stop();assert.equal(instance.isOpen,false);
});

test('HTTP and live stream share real sensor state; foreign origins cannot subscribe',async t=>{
  const hw=createHardwareState();hw.setConnection('connected');hw.ingest(frame());
  const server=createApp(getConfig({}),{hardware:hw});await new Promise(r=>server.listen(0,'127.0.0.1',r));
  t.after(()=>{server.closeAllConnections();server.close();});
  const base=`http://127.0.0.1:${server.address().port}`;
  assert.equal((await(await fetch(base+'/api/vitals')).json()).temperature.celsius,23.45);
  assert.equal((await fetch(base+'/api/hardware/events',{headers:{Origin:'http://other.test'}})).status,403);
  const controller=new AbortController();t.after(()=>controller.abort());
  const response=await fetch(base+'/api/hardware/events',{signal:controller.signal});
  assert.match(response.headers.get('content-type'),/text\/event-stream/);
  const reader=response.body.getReader();const initial=new TextDecoder().decode((await reader.read()).value);
  assert.match(initial,/event: snapshot/);assert.doesNotMatch(initial,/event: button/);
  hw.ingest(frame(2,[true,false,false]));
  const update=new TextDecoder().decode((await reader.read()).value);assert.match(update,/event: button/);
  await reader.cancel();
});
