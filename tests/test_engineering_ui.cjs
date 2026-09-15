const assert=require('node:assert/strict');
const test=require('node:test');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
const code=source.slice(source.indexOf("let resultField='temperature'"),source.indexOf("document.addEventListener('visibilitychange'"));
function setup(){
 const elements=new Map();
 function element(){return {value:'0',hidden:false,disabled:false,textContent:'',dataset:{},children:[],options:[{value:'temperature'},{value:'displacement'},{value:'stress'}],append(x){this.children.push(x)},replaceChildren(){this.children=[]},querySelector(){return this}};}
 const $=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id)};
 const points=new Float32Array([0,0,0,1,0,0]);
 const u=new Float32Array([0,0,0,0,0,0,.001,0,0,.002,0,0]);
 const stress=new Float32Array([0,0,1e6,2e6]);
 const temperatures=new Float32Array([25,25,30,40]);
 const result={frames:2,vertices:2,structural:{mode:'constrained',maximum_von_mises_Pa:2e6,maximum_displacement_m:.002},
   assessment:{status:'exceeded',checks:[{name:'热位移',value:.002,limit:.001,unit:'m',time_s:30,status:'exceeded'}]}};
 const context=vm.createContext({$, $$:()=>[],document:{createElement:element},loadGeneration:1,Float32Array,Math,Number,Promise,
   geometry:{attributes:{position:{array:new Float32Array(points)}},computeVertexNormals(){}},points,creases:{visible:true},
   binary:async url=>url.includes('displacement')?u:stress,result,temperatures,view:'temperature',frame:1,surfaceComponent:'all',surfaceVertexComponents:null,
   applyClip(){},render(){},updateSurfaceRange(){},showFrame(){},uniformResult:false});
 vm.runInContext(code,context);return {context,$,result,temperatures,points};
}
test('switches physical fields and restores temperature without changing data',async()=>{
 const p=setup();await p.context.loadEngineeringResult('id',p.result,p.temperatures);
 assert.equal(p.$('engineering-results').hidden,false);
 assert.equal(p.$('assessment-status').textContent,'超出限值');
 const cells=p.$('assessment-table').children[0].children;
 assert.equal(cells[1].textContent,'2.000 mm');
 p.$('result-field').value='stress';p.$('result-field').onchange();
 assert.equal(p.context.fieldUnit(),'MPa');assert.deepEqual(Array.from(p.context.temperatures),[0,0,1,2]);
 assert.equal(p.$('show-slice').disabled,true);
 p.$('result-field').value='displacement';p.$('result-field').onchange();
 assert.ok(Math.abs(p.context.temperatures[3]-2)<1e-5);
 p.$('result-field').value='temperature';p.$('result-field').onchange();
 assert.equal(p.context.temperatures,p.temperatures);assert.equal(p.$('show-slice').disabled,false);
});
test('deformation scaling changes geometry only and can restore original mesh',async()=>{
 const p=setup();await p.context.loadEngineeringResult('id',p.result,p.temperatures);
 p.$('deformation-scale').value=100;p.context.updateDeformation();
 assert.ok(Math.abs(p.context.geometry.attributes.position.array[3]-1.2)<1e-6);
 assert.equal(p.points[3],1);
 p.context.resetDeformation();assert.equal(p.context.geometry.attributes.position.array[3],1);
});
test('old temperature-only result disables mechanical visualization',async()=>{
 const p=setup();await p.context.loadEngineeringResult('old',{frames:2,vertices:2},p.temperatures);
 assert.equal(p.$('engineering-results').hidden,true);
 assert.equal(p.$('deformation-scale').disabled,true);
 assert.equal(p.$('result-field').options[2].disabled,true);
});
