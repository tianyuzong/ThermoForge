const assert=require('node:assert/strict'),test=require('node:test'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
const code=source.slice(source.indexOf('function showFrame('),source.indexOf('const clipPlane='));
function setup(mode='transient',times=[0,240,480,600,720]){
 const elements=new Map(),timers=new Set();
 const $=id=>{if(!elements.has(id))elements.set(id,{value:'4',disabled:false,textContent:''});return elements.get(id)};
 const result={frames:times.length,times_s:times,stats:times.map(t=>({maximum_C:20+t/10,average_C:19+t/10,minimum_C:18+t/10}))};
 const context=vm.createContext({$,result,cfg:{analysis_mode:mode},frame:0,playing:false,playTimer:null,Math,Number,
  number:id=>Number($(id).value),surfaceStats:i=>result.stats[i],updateDeformation(){},recolor(){},
  setInterval:fn=>{timers.add(fn);return fn},clearInterval:id=>timers.delete(id)});
 vm.runInContext(code,context);return {context,$,result,timers};
}
test('steady output shows equilibrium and cannot animate initial-to-label endpoints',()=>{
 const p=setup('steady',[0,600]);p.context.showFrame(0);
 assert.equal(p.context.frame,1);assert.equal(p.$('maximum').textContent,'80.000');
 assert.equal(p.$('time-label').textContent,'稳态平衡结果');
 for(const id of ['play','timeline','playback-fps'])assert.equal(p.$(id).disabled,true);
 p.$('play').onclick();assert.equal(p.timers.size,0);
 p.context.cfg.analysis_mode='transient';p.context.showFrame(0);assert.equal(p.context.frame,1);
});
test('nonuniform saved intervals retain exact timestamps while scrubbing',()=>{
 const p=setup();p.context.showFrame(3);
 assert.match(p.$('time-label').textContent,/600 \/ 720 s/);
 assert.match(p.$('time-label').textContent,/距上一帧 120 s/);
 assert.equal(p.$('maximum').textContent,'80.000');assert.equal(p.$('play').disabled,false);
 p.context.showFrame(0);assert.match(p.$('time-label').textContent,/初始帧/);
});
test('playback advances stored field frames and playback speed leaves simulation times unchanged',()=>{
 const p=setup();p.context.showFrame(0);p.$('play').onclick();
 [...p.timers][0]();assert.equal(p.context.frame,1);assert.equal(p.$('maximum').textContent,'44.000');
 p.$('playback-fps').value='8';p.$('playback-fps').onchange();
 assert.equal(p.context.playbackInterval(),125);assert.deepEqual(p.result.times_s,[0,240,480,600,720]);
 p.$('timeline').value='3';p.$('timeline').oninput();assert.equal(p.timers.size,0);assert.equal(p.context.frame,3);
});
