const assert=require('node:assert/strict');
const test=require('node:test');
const fs=require('node:fs');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/workflow-agent-ui.js'),'utf8');
const modulePromise=import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));

test('historical Agent summaries use stored changes without fabricated timestamps',async()=>{
 const {workflowActivity}=await modulePromise;
 const w={prompt:'test',cases:[{key:'a',name:'工况A',agent_prompt:'实际指令',changes:['功率150W'],status:'completed'}],report_ready:true};
 const rows=workflowActivity(w);
 assert.deepEqual(rows.map(e=>e.actor),['user','agent','host']);
 assert.ok(rows.every(e=>e.legacy&&!e.at));assert.deepEqual(rows[1].details,['功率150W']);
 assert.equal(w.activity,undefined);
});
test('real event provenance and timestamps pass through unchanged',async()=>{
 const {workflowActivity}=await modulePromise;
 const activity=[{id:'actual',at:10,actor:'host',stage:'reporting',text:'已生成报告'}];
 assert.equal(workflowActivity({activity}),activity);
});
test('Agent stages and host stages advance from saved task state',async()=>{
 const {workflowStages}=await modulePromise;
 assert.deepEqual(workflowStages({phase:'planning',planned_cases:[{key:'a'}]}).map(s=>s.state),['done','active','pending','pending']);
 assert.deepEqual(workflowStages({phase:'ready',plan_complete:true}).map(s=>s.state),['done','done','pending','pending']);
 assert.deepEqual(workflowStages({phase:'partial',plan_complete:true,report_ready:true}).map(s=>s.state),['done','done','attention','done']);
 assert.equal(workflowStages({phase:'planning',report_ready:true})[3].state,'pending');
});
