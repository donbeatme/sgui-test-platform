// Run: node --test tests/localHistory.test.cjs (no browser or model calls).
const {test} = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const ts = require('typescript')
const {ref, computed, watch} = require('vue')
const compiled = ts.transpileModule(fs.readFileSync(require.resolve('../src/composables/useLocalHistory.ts'),'utf8'), {
  compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020},
}).outputText
const local = {exports:{}}
new Function('require','module','exports',compiled)(require,local,local.exports)
const {useLocalHistory} = local.exports

test('a field edit and review invalidation undo together, without mutating the saved input',()=>{
  const original={name:'旅客姓名',bbox:[.1,.2,.3,.1],reviewed:true}
  const state=ref(structuredClone(original)),h=useLocalHistory(state)
  h.transaction(()=>{state.value.name='姓名';state.value.reviewed=false},'name')
  h.transaction(()=>{state.value.name='中文姓名'},'name')
  h.undo();assert.deepEqual(state.value,original);assert.equal(h.canUndo.value,false)
  h.redo();assert.equal(state.value.name,'中文姓名');assert.equal(state.value.reviewed,false)
  assert.equal(original.name,'旅客姓名')
})
test('deleting an existing control is reversible, including its bounding box',()=>{
  const state=ref([{id:'C1',excluded:false,bbox:[.2,.3,.1,.1]}]),h=useLocalHistory(state)
  h.transaction(()=>{state.value[0].excluded=true;state.value[0].bbox=null})
  h.undo();assert.equal(state.value[0].excluded,false);assert.deepEqual(state.value[0].bbox,[.2,.3,.1,.1])
})
test('undoing an addition then editing starts a new branch',()=>{
  const state=ref([{name:'姓名'}]),h=useLocalHistory(state)
  h.transaction(()=>state.value.push({name:'新增'}));h.undo()
  h.transaction(()=>{state.value[0].name='证件姓名'})
  assert.equal(h.canRedo.value,false);assert.equal(state.value.length,1)
})
test('server checkpoint clears undo/redo so cancel cannot restore stale server IDs',()=>{
  const state=ref([{id:'',name:'新控件'}]),h=useLocalHistory(state)
  state.value[0].name='新姓名控件'
  h.reset([{id:'U1-C2',name:'新姓名控件'}]);h.undo()
  assert.equal(state.value[0].id,'U1-C2');assert.equal(h.canUndo.value,false);assert.equal(h.canRedo.value,false)
})
test('draft case deletion and nested step edits restore in a writable computed form',()=>{
  const job=ref({revision:1,cases:[{id:'a',steps:['step1','step2']},{id:'b',steps:['step3']}]})
  const cases=computed({get:()=>job.value.cases,set:v=>{job.value.cases=v}}),h=useLocalHistory(cases)
  watch(()=>job.value.revision,()=>h.reset(structuredClone(JSON.parse(JSON.stringify(cases.value)))) )
  cases.value=cases.value.filter(c=>c.id!=='b');h.undo();assert.equal(cases.value.length,2)
  h.transaction(()=>cases.value[0].steps.splice(0,1));h.undo()
  assert.deepEqual(cases.value[0].steps,['step1','step2'])
})
test('empty editor refs and bounded history are supported',()=>{
  const state=ref(),h=useLocalHistory(state,2)
  h.reset({n:0});state.value.n=1;state.value.n=2;state.value.n=3
  h.undo();h.undo();h.undo();assert.equal(state.value.n,1)
})
