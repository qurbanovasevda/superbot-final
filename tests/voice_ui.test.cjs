// Run from the project root: node tests/voice_ui.test.cjs
const assert=require('node:assert/strict');
const fs=require('node:fs');const vm=require('node:vm');const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../app/ui.py'),'utf8');
const controller=source.slice(source.indexOf('class VoiceController'),source.indexOf('// Voice controller end'));
let total=0;
function setup(){
  let now=0,seq=0;const tasks=new Map();const instances=[],sent=[],states=[],errors=[];
  function later(fn,delay){const id=++seq;tasks.set(id,{fn,at:now+delay});return id;}
  function advance(ms){const until=now+ms;let guard=0;while(true){const next=[...tasks.entries()].filter(([,v])=>v.at<=until).sort((a,b)=>a[1].at-b[1].at)[0];if(!next)break;if(++guard>100)throw Error('Unbounded restart');now=next[1].at;tasks.delete(next[0]);next[1].fn();}now=until;}
  class Recognition{constructor(){instances.push(this);}start(){this.starts=(this.starts||0)+1;this.onstart();}abort(){this.aborted=true;this.onend();}}
  const context=vm.createContext({setTimeout:later,clearTimeout:id=>tasks.delete(id)});
  vm.runInContext(controller+'\nthis.VoiceController=VoiceController;',context);
  const voice=new context.VoiceController({Recognition,onTranscript:t=>sent.push(t),onState:m=>states.push(m),onError:e=>errors.push(e)});
  function result(text,final=true){const item=[{transcript:text}];item.isFinal=final;voice.recognition.onresult({resultIndex:0,results:[item]});}
  return {voice,instances,sent,states,errors,advance,result};
}
function test(name,fn){fn();total++;console.log('PASS',name);}
test('Final speech prepares one draft after a pause',()=>{const x=setup();x.voice.start('az-AZ');assert.equal(x.instances[0].lang,'az-AZ');assert.equal(x.instances[0].continuous,true);x.result('Qulaqlıq göstər');x.advance(999);assert.equal(x.sent.length,0);x.advance(1);assert.deepEqual(x.sent,['Qulaqlıq göstər']);x.advance(3000);assert.equal(x.sent.length,1);assert.equal(x.voice.suspended,true);});
test('Interim speech waits for finalization or explicit Stop',()=>{const x=setup();x.voice.start('en-US');x.result('Show',false);x.advance(2000);assert.equal(x.sent.length,0);x.result('Show headphones',true);x.advance(1000);assert.deepEqual(x.sent,['Show headphones']);});
test('Final segments combine and reset the silence timer',()=>{const x=setup();x.voice.start('ru-RU');x.result('Покажи');x.advance(600);x.result('ноутбуки');x.advance(600);assert.equal(x.sent.length,0);x.advance(400);assert.deepEqual(x.sent,['Покажи ноутбуки']);});
test('Stop cancels buffered speech and restarts',()=>{const x=setup();x.voice.start('en-US');x.result('Do not send');x.voice.stop();x.advance(3000);assert.equal(x.sent.length,0);assert.equal(x.instances.length,1);assert.equal(x.voice.enabled,false);});
test('Pause ignores late recognition and resumes once',()=>{const x=setup();x.voice.start('en-US');const old=x.instances[0];x.voice.pause();const item=[{transcript:'echo'}];item.isFinal=true;old.onresult({resultIndex:0,results:[item]});x.advance(2000);assert.equal(x.sent.length,0);x.voice.resume();assert.equal(x.instances.length,2);old.onend();x.advance(2000);assert.equal(x.instances.length,2);});
test('Normal service end restarts hands-free',()=>{const x=setup();x.voice.start('en-US');x.instances[0].onend();x.advance(500);assert.equal(x.instances.length,2);});
test('Permission denial stops without a retry loop',()=>{const x=setup();x.voice.start('en-US');x.instances[0].onerror({error:'not-allowed'});x.advance(5000);assert.equal(x.voice.enabled,false);assert.equal(x.instances.length,1);assert.match(x.errors[0],/permission/i);});
test('Repeated network errors stop after three failures',()=>{const x=setup();x.voice.start('en-US');for(let i=0;i<3;i++){const r=x.voice.recognition;r.onerror({error:'network'});r.onend();x.advance(500);}assert.equal(x.voice.enabled,false);assert.equal(x.instances.length,3);assert.equal(x.errors.length,1);});
test('Oversized transcript is not sent',()=>{const x=setup();x.voice.start('en-US');x.result('x'.repeat(2001));x.advance(1000);assert.equal(x.sent.length,0);assert.equal(x.voice.enabled,false);});
test('Explicit Stop preserves final speech as a review draft',()=>{const x=setup();x.voice.start('en-US');x.result('Show headphones');x.voice.stop(true);assert.deepEqual(x.sent,['Show headphones']);x.advance(2000);assert.equal(x.sent.length,1);assert.equal(x.voice.enabled,false);});
test('Explicit Stop preserves interim speech for manual review',()=>{const x=setup();x.voice.start('az-AZ');x.result('Qulaqlıq göstər',false);x.voice.stop(true);assert.deepEqual(x.sent,['Qulaqlıq göstər']);x.advance(2000);assert.equal(x.sent.length,1);});
test('UI transcript handler only fills the draft and never invokes chat sending',()=>{const handler=source.match(/onTranscript:text=>\{([^\n]+)\},/)[1];assert.match(handler,/\$\('input'\)\.value=text/);assert.doesNotMatch(handler,/guard\(|send\(|api\(|fetch\(/);assert.match(source,/voice\.stop\(true\)/);});

console.log(total+' voice controller checks passed. Microphone/browser verification remains manual.');
