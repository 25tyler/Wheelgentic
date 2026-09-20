import test from 'node:test';
import assert from 'node:assert/strict';
import {createSpeechPlayer} from './voice-speech.js';

function setup(fetchFn,play=()=>Promise.resolve()){
  const states=[],audios=[],revoked=[];
  const player=createSpeechPlayer(state=>states.push(state),{
    fetch:fetchFn,
    makeAudio:()=>{const audio={play,pause(){this.paused=true;},removeAttribute(){},load(){}};audios.push(audio);return audio;},
    urls:{createObjectURL:()=>`blob:${audios.length}`,revokeObjectURL:url=>revoked.push(url)},
  });
  return {player,states,audios,revoked};
}
const audioResponse=()=>new Response('audio',{headers:{'Content-Type':'audio/mpeg'}});

test('new speech cancels old playback and releases its audio URL',async()=>{
  const {player,states,audios,revoked}=setup(async()=>audioResponse());
  await player.speak('First reply');
  assert.equal(states.at(-1),'speaking');
  await player.speak('Second reply');
  assert.equal(audios[0].paused,true);
  assert.equal(revoked.length,1);
  audios[1].onended();
  assert.equal(states.at(-1),'idle');
  assert.equal(revoked.length,2);
  player.dispose();
});

test('stale speech cannot start after cancellation even if fetch ignores abort',async()=>{
  let resolve;
  const {player,audios}=setup(()=>new Promise(r=>{resolve=r;}));
  const pending=player.speak('An old reply');
  player.stop();
  resolve(audioResponse());
  await pending;
  assert.equal(audios.length,0);
  player.dispose();
});

test('autoplay blocking and synthesis failure preserve the visible reply with a retry state',async()=>{
  const blocked=setup(async()=>audioResponse(),async()=>{throw new DOMException('gesture required','NotAllowedError');});
  await blocked.player.speak('Hello');
  assert.equal(blocked.states.at(-1),'blocked');
  assert.equal(blocked.revoked.length,1);
  const failure=setup(async()=>new Response('error',{status:503}));
  await failure.player.speak('Hello');
  assert.equal(failure.states.at(-1),'error');
  assert.equal(failure.audios.length,0);
});
