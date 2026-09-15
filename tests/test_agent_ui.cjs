const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const handler = source.slice(source.indexOf('function resetAgentConversation('), source.indexOf(' function applyAgentConfig()'));

function page(post) {
  const elements = new Map();
  const timers = new Set();
  const traces = [];
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      value: id === 'agent-mode' ? 'codex' : '20 W 加热', disabled: false, hidden: false, textContent: '',
      classList: {add() {}, toggle() {}},
      replaceChildren() {}, append() {}, focus() {},
    });
    return elements.get(id);
  };
  const original = {model_id: 'model-1', duration_s: 60};
  const context = vm.createContext({
    $: element, cfg: structuredClone(original), model: {id: 'model-1', name: 'Test model'},
    agentConfig: {model_id: 'model-1', duration_s: 300},
    agentConversation: null, agentRequestGeneration: 0, loadGeneration: 0,
    document: {createElement: () => ({append() {}, textContent:''})},
    clone: structuredClone, post, protect: fn => fn, agentModeLabels: {codex: 'Codex'},
    agentTraceReset: () => {}, agentTraceSet: (...args) => traces.push(args),
    setInterval: fn => {timers.add(fn); return fn;}, clearInterval: fn => timers.delete(fn),
  });
  vm.runInContext(handler, context);
  return {context, element, timers, traces, original};
}

test('timeout keeps current configuration and disables any previous draft', async () => {
  let complete;
  const p = page(() => new Promise(resolve => {complete = resolve;}));
  const request = p.element('agent-plan').onclick();
  assert.equal(p.context.agentConfig, null);
  assert.equal(p.element('agent-confirm').disabled, true);
  complete({ok: false, error_code: 'timeout', questions: ['Codex 上游连接超时']});
  await request;
  assert.deepEqual(p.context.cfg, p.original);
  assert.equal(p.context.agentConfig, null);
  assert.equal(p.element('agent-apply').disabled, true);
  assert.equal(p.element('agent-confirm').disabled, true);
  assert.match(p.element('agent-plan-text').textContent, /配置生成未完成/);
  assert.match(p.element('agent-plan-questions').textContent, /上游连接超时/);
  assert.equal(p.traces.some(([,state]) => state === 'done'), false);
  assert.equal(p.timers.size, 0);
});

test('clarifications retain intent, allow partial answers and block confirmation until all are solved', async () => {
  const calls=[];
  const p=page(async (_,body)=>{
    calls.push(structuredClone(body));
    if(calls.length<3)return {ok:false,error_code:'clarification',questions:[calls.length===1?'请确定外置方式和总时长。':'还需确定总时长。']};
    return {ok:true,config:{...body.config,heat_sources:[{power_W:3000}],duration_s:300},changes:['外置热源 3000 W；总时长 300 秒']};
  });
  p.element('agent-prompt').value='模型中心添加 3000 W 热源';
  await p.element('agent-plan').onclick();
  assert.equal(p.element('agent-plan').textContent,'发送补充');
  assert.equal(p.element('agent-prompt').value,'');
  assert.equal(p.element('agent-confirm').disabled,true);
  p.element('agent-prompt').value='同意外置';
  await p.element('agent-plan').onclick();
  assert.equal(p.element('agent-confirm').disabled,true);
  assert.equal(p.context.agentConversation.applied,0);
  p.element('agent-prompt').value='300 秒';
  await p.element('agent-plan').onclick();
  assert.equal(calls[2].history.length,4);
  assert.equal(calls[2].history[0].content,'模型中心添加 3000 W 热源');
  assert.equal(calls[2].history[3].content,'还需确定总时长。');
  assert.deepEqual(calls[2].config,p.original);
  assert.equal(p.context.agentConversation.applied,6);
  assert.equal(p.element('agent-confirm').disabled,false);
  assert.deepEqual(p.context.cfg,p.original);
});

test('later edits use the validated draft without replaying previous additions', async () => {
  const calls=[];
  const p=page(async (_,body)=>{calls.push(structuredClone(body));return {ok:true,config:{...body.config,heat_sources:[{power_W:20}],duration_s:300},changes:['热源 20 W']};});
  await p.element('agent-plan').onclick();
  p.element('agent-prompt').value='基础材料改为铝';
  p.element('agent-prompt').oninput();
  assert.equal(p.element('agent-confirm').disabled,true);
  await p.element('agent-plan').onclick();
  assert.equal(calls[1].applied_message_count,2);
  assert.equal(calls[1].config.heat_sources.length,1);
  assert.equal(calls[1].config.duration_s,300);
});

test('network retry does not duplicate messages or forget pending intent', async () => {
  const calls=[];
  const p=page(async (_,body)=>{calls.push(structuredClone(body));
    if(calls.length===2)throw Error('network');
    return {ok:false,error_code:'clarification',questions:['请指定功率。']};
  });
  await p.element('agent-plan').onclick();
  p.element('agent-prompt').value='3000 W';
  await assert.rejects(p.element('agent-plan').onclick(),/network/);
  assert.equal(p.context.agentConversation.history.length,2);
  assert.equal(p.element('agent-prompt').value,'3000 W');
  await p.element('agent-plan').onclick();
  assert.deepEqual(calls[1],calls[2]);
  assert.equal(p.context.agentConversation.history.length,4);
});

test('reset invalidates an in-flight response, including loading the same model again', async () => {
  for(const reset of [p=>p.element('agent-new').onclick(),p=>{p.context.loadGeneration++;p.context.resetAgentConversation();}]){
    let complete;
    const p=page(()=>new Promise(resolve=>{complete=resolve;}));
    const pending=p.element('agent-plan').onclick();
    reset(p);
    complete({ok:true,config:{model_id:'model-1',heat_sources:[{power_W:20}]}});
    await pending;
    assert.equal(p.context.agentConfig,null);
    assert.equal(p.context.agentConversation.history.length,0);
    assert.equal(p.element('agent-confirm').disabled,true);
    assert.equal(p.timers.size,0);
  }
});

test('even an ok response with unanswered questions cannot unlock confirmation', async () => {
  const p=page(async()=>({ok:true,config:{model_id:'model-1',heat_sources:[{power_W:20}]},questions:['位置尚未确定。']}));
  await p.element('agent-plan').onclick();
  assert.equal(p.context.agentConfig,null);
  assert.equal(p.element('agent-confirm').disabled,true);
  assert.equal(p.context.agentConversation.history.length,2);
});

test('close and reopen preserves conversation; manual configuration changes start a fresh base', async () => {
  const p=page(async()=>({ok:false,error_code:'clarification',questions:['热源位置？']}));
  await p.element('agent-plan').onclick();
  p.element('agent-close').onclick();p.element('agent-open').onclick();
  assert.equal(p.context.agentConversation.history.length,2);
  p.context.cfg.duration_s=800;
  p.element('agent-prompt').value='初始温度 30°C';
  await p.element('agent-plan').onclick();
  assert.equal(p.context.agentConversation.base.duration_s,800);
  assert.equal(p.context.agentConversation.history[0].content,'初始温度 30°C');
});

test('successful generation presents a draft without applying or simulating it', async () => {
  const draft = {model_id: 'model-1', duration_s: 600, heat_sources: [{power_W:20, faces:[1]}]};
  const p = page(async () => ({ok: true, config: draft, changes: ['时长 600 秒'], workflow:'thermal-config', metrics:{elapsed_s:12.3}}));
  await p.element('agent-plan').onclick();
  assert.deepEqual(p.context.cfg, p.original);
  assert.deepEqual(p.context.agentConfig, draft);
  assert.equal(p.element('agent-confirm').disabled, false);
  assert.equal(p.element('agent-apply').disabled, false);
  assert.equal(p.element('agent-plan').disabled, false);
  assert.match(p.element('agent-plan-mode').textContent, /配置 Skill/);
  assert.match(p.element('agent-plan-mode').textContent, /12\.3 秒/);
  assert.equal(p.timers.size, 0);
});

test('HTTP failure clears stale drafts and stops waiting feedback', async () => {
  const p = page(async () => {throw new Error('Failed to fetch');});
  await assert.rejects(p.element('agent-plan').onclick(), /Failed to fetch/);
  assert.equal(p.context.agentConfig, null);
  assert.equal(p.element('agent-confirm').disabled, true);
  assert.equal(p.element('agent-plan').disabled, false);
  assert.equal(p.timers.size, 0);
  assert.equal(p.element('agent-plan-questions').textContent, 'Failed to fetch');
});

test('an ok response without a heat source cannot enable confirmation', async () => {
  const p = page(async () => ({ok: true, config: {model_id:'model-1', heat_sources:[]}}));
  await assert.rejects(p.element('agent-plan').onclick(), /没有生成热源/);
  assert.deepEqual(p.context.cfg, p.original);
  assert.equal(p.context.agentConfig, null);
  assert.equal(p.element('agent-confirm').disabled, true);
  assert.equal(p.element('agent-apply').disabled, true);
});

test('explicit environment-only draft enables confirmation without applying it', async () => {
  const p = page(async () => ({ok:true, config:{model_id:'model-1',heat_sources:[],environment_only:true},questions:[],changes:[]}));
  await p.element('agent-plan').onclick();
  assert.equal(p.element('agent-confirm').disabled,false);
  assert.equal(p.context.agentConfig.environment_only,true);
  assert.deepEqual(p.context.cfg,p.original);
});
