import 'fake-indexeddb/auto';
import assert from 'node:assert/strict';
import test from 'node:test';
import {Registration,writeRecord,readRecord,validateDraft,archiveDraft,listRecords} from '../src/workspace.js';
import {recoverDraft,resolveDraftAssets} from '../src/draftValidation.js';
import {readFileSync} from 'node:fs';
import {upscalePlan,upscaleDefaults,restoreUpscaleOptions} from '../src/upscalePlan.js';

test('frozen registration survives editing and lost acknowledgement without extra job',async()=>{
 const accepted=new Map();let submits=0;let lose=true;
 const request=async(path,body)=>{
  if(body){submits++;accepted.set(body.id,structuredClone(body));if(lose){lose=false;throw new Error('lost');}return {id:body.id};}
  const id=path.split('/')[1];if(!accepted.has(id)){let e=new Error('missing');e.status=404;throw e;}return {id};
 };
 const manager=new Registration('tab-one',request);const draft={prompt:'A',refs:[{id:'reference',role:'face'}]};
 await assert.rejects(manager.begin(draft));draft.prompt='B';draft.refs[0].role='style';
 const pending=await readRecord('registrations','tab-one');assert.equal(pending.payload.prompt,'A');assert.equal(pending.payload.refs[0].role,'face');
 const restored=new Registration('tab-one',request);restored.record=pending;await restored.reconcile();assert.equal(submits,1);
 await restored.begin(draft);assert.equal(submits,2);assert.equal(accepted.size,2);
});
test('double-click creates one registration',async()=>{
 let release;const hold=new Promise(r=>release=r);let sends=0;
 const manager=new Registration('double',async(_,p)=>{sends++;await hold;return {id:p.id};});
 const first=manager.begin({prompt:'same'});await assert.rejects(manager.begin({prompt:'same'}));
 release();await first;assert.equal(sends,1);
});
test('local persistence failure never registers a job',async()=>{
 let sends=0;const manager=new Registration('failure',async()=>sends++,async()=>{throw new Error('disk');});
 await assert.rejects(manager.begin({prompt:'A'}));assert.equal(sends,0);
});
test('unavailable lookup retains the original ID',async()=>{
 const manager=new Registration('lookup',async()=>{throw new Error('offline');});
 manager.record={payload:{id:'unchanged',prompt:'A'},state:'unconfirmed'};
 await assert.rejects(manager.reconcile(),/確認できません/);assert.equal(manager.record.payload.id,'unchanged');
});
test('404 reconciliation reuses frozen ID and payload',async()=>{
 let posted;const manager=new Registration('lookup404',async(path,body)=>{if(body){posted=body;return {id:body.id};}let e=new Error('missing');e.status=404;throw e;});
 manager.record={id:'lookup404',revision:1,payload:{id:'fixed',prompt:'unchanged'},state:'unconfirmed'};
 await manager.reconcile();assert.equal(posted.id,'fixed');assert.equal(posted.prompt,'unchanged');
});
test('generation guard rejects out-of-order saves',async()=>{
 await writeRecord('drafts',{id:'ordered',revision:5,schema:1,payload:{prompt:'new'}});
 assert.equal(await writeRecord('drafts',{id:'ordered',revision:4,schema:1,payload:{prompt:'old'}}),false);
 assert.equal((await readRecord('drafts','ordered')).payload.prompt,'new');
});
test('draft roundtrip retains mask, undo, references and manually edited prompt',async()=>{
 const payload={targetId:'original',refs:[{id:'one',role:'face'},{id:'two',role:'style'}],prompt:'MANUALLY EDITED',promptValid:true,strokes:[{width:20,points:[[10,30]]}],redo:[{width:40,points:[[20,60]]}],p:{provider:'openai'}};
 await writeRecord('drafts',{id:'roundtrip',revision:1,schema:1,payload});assert.deepEqual((await readRecord('drafts','roundtrip')).payload,payload);
});
test('separate tab IDs never overwrite each other',async()=>{
 await Promise.all(['tab-A','tab-B'].map(id=>writeRecord('drafts',{id,revision:1,payload:{prompt:id}})));
 assert.equal((await readRecord('drafts','tab-A')).payload.prompt,'tab-A');assert.equal((await readRecord('drafts','tab-B')).payload.prompt,'tab-B');
});
test('legacy or malformed drafts remain recoverable in archives',async()=>{
 const record={schema:0,payload:{prompt:'legacy text',targetId:'missing'}};
 assert.equal(validateDraft(record).length,1);await archiveDraft(record,'legacy');
 assert.ok((await listRecords('archives')).some(r=>r.payload.payload.prompt==='legacy text'));
 assert.ok(validateDraft({schema:1,payload:null}).length);
});
test('invalid shapes cannot destroy recoverable manual text',()=>{
 const defaults={mode:'polish',provider:'mock',model:'gpt-image-2.5-sunburst',quality:'medium',format:'png',width:1920,height:1088};
 const saved={p:{model:'missing',width:'bad'},prompt:'keep this text',refs:[null],strokes:[{width:NaN,points:[]}],redo:[],pan:'invalid'};
 const {payload,warnings}=recoverDraft(saved,defaults);
 assert.equal(payload.prompt,'keep this text');assert.equal(payload.p.model,defaults.model);assert.deepEqual(payload.strokes,[]);assert.ok(warnings.length);
});
test('missing assets warn without changing text or source IDs',async()=>{
 const saved={targetId:'missing',refs:[{id:'missing-reference'}],prompt:'protected text'};
 const result=await resolveDraftAssets(saved,async()=>{const e=new Error('missing');e.status=404;throw e;});
 assert.equal(result.base,null);assert.equal(result.warnings.length,2);assert.equal(saved.prompt,'protected text');assert.equal(saved.targetId,'missing');
});
test('acknowledgement persistence failure keeps registration unconfirmed',async()=>{
 let writes=0;const manager=new Registration('ack-failure',async(_,body)=>({id:body.id}),async()=>{if(++writes===2)throw new Error('storage full');});
 await assert.rejects(manager.begin({prompt:'A'}));assert.equal(manager.record.state,'unconfirmed');
 await assert.rejects(manager.begin({prompt:'B'}));
});
test('persistent registration write failure blocks POST even after 404 reconciliation',async()=>{
 let posts=0;
 const manager=new Registration('write-failed',async(path,body)=>{if(body){posts++;return {id:body.id};}const e=new Error('missing');e.status=404;throw e;},async()=>{throw new Error('full');});
 await assert.rejects(manager.begin({prompt:'fixed'}));
 await assert.rejects(manager.reconcile());await assert.rejects(manager.reconcile());
 assert.equal(posts,0);
 assert.equal(manager.record.state,'unsaved');
});
test('transient draft asset failure must not return null assets',async()=>{
 const saved={targetId:'existing',resultId:'existing-result',refs:[],prompt:'keep'};
 await assert.rejects(resolveDraftAssets(saved,async()=>{const e=new Error('temporary');e.status=503;throw e;}));
 assert.equal(saved.targetId,'existing');assert.equal(saved.resultId,'existing-result');
});
test('registration recovery sends once after durable storage recovers',async()=>{
 let full=true;let posts=0;let sent;
 const save=async()=>{if(full)throw new Error('full');return true;};
 const manager=new Registration('restore-storage',async(path,body)=>{if(body){posts++;sent=structuredClone(body);return {id:body.id};}const e=new Error('missing');e.status=404;throw e;},save);
 await assert.rejects(manager.begin({prompt:'fixed'}));const id=manager.record.payload.id;
 await assert.rejects(manager.reconcile());assert.equal(posts,0);
 full=false;await manager.reconcile();assert.equal(posts,1);assert.equal(sent.id,id);assert.equal(sent.prompt,'fixed');
});
test('stale persistence refusal is not considered a successful save',async()=>{
 let posts=0;const manager=new Registration('stale',async()=>posts++,async()=>false);
 await assert.rejects(manager.begin({prompt:'A'}));assert.equal(posts,0);assert.equal(manager.record.state,'unsaved');
});
test('offline draft load preserves IDs until the successful retry',async()=>{
 const saved={targetId:'target',resultId:'result',refs:[{id:'ref'}],prompt:'manual'};
 const snapshot=structuredClone(saved);
 await assert.rejects(resolveDraftAssets(saved,async()=>{throw new TypeError('Failed to fetch');}));
 assert.deepEqual(saved,snapshot);
 const restored=await resolveDraftAssets(saved,async id=>({id,width:1024,height:1024}));
 assert.equal(restored.base.id,'target');assert.equal(restored.out.id,'result');assert.deepEqual(restored.warnings,[]);
});
test('SwinIR preview follows the same round-half-up sizes and limits',()=>{
 const limits=JSON.parse(readFileSync(new URL('../upscale_limits.json',import.meta.url)));
 for(const [factor,width,height] of [[2,3840,2176],[1.5,2880,1632],[4,7680,4352]]){
  const p=upscalePlan(1920,1088,{...upscaleDefaults,factor},limits);assert.equal(p.width,width);assert.equal(p.height,height);
 }
 assert.equal(upscalePlan(11,13,{...upscaleDefaults,factor:1.5},limits).width,17);
 for(const options of [{factor:NaN},{factor:5},{tile:193},{overlap:192},{mode:'size',width:100,height:100}])assert.throws(()=>upscalePlan(1920,1088,{...upscaleDefaults,...options},limits));
});
test('old drafts gain SwinIR defaults; new drafts retain local options',async()=>{
 assert.deepEqual(restoreUpscaleOptions(undefined),upscaleDefaults);
 const options={...upscaleDefaults,mode:'size',width:3000,height:2000,fit:'crop',tile:128,overlap:16};
 await writeRecord('drafts',{id:'upscale-options',revision:1,payload:{upscaleOptions:options}});
 assert.deepEqual(restoreUpscaleOptions((await readRecord('drafts','upscale-options')).payload.upscaleOptions),options);
});

test('unified upscale mode and selected source survive validated draft recovery',async()=>{
 const payload={p:{mode:'upscale'},upscaleSource:'result',upscaleOptions:{...upscaleDefaults,factor:1.5},targetId:'base',resultId:'generated'};
 await writeRecord('drafts',{id:'unified-upscale',revision:1,payload});
 const recovered=recoverDraft((await readRecord('drafts','unified-upscale')).payload,{mode:'polish'});
 assert.equal(recovered.payload.p.mode,'upscale');assert.equal(recovered.payload.upscaleSource,'result');
 assert.equal(recovered.payload.upscaleOptions.factor,1.5);
 const assets=await resolveDraftAssets(recovered.payload,async id=>({id,width:1024,height:800}));
 assert.equal(assets.base.id,'base');assert.equal(assets.out.id,'generated');
});
import {instructionPresets,composeInstructions,parseInstructions,restoreInstructions} from '../src/instructionPresets.js';
test('instruction presets compose selected templates and extra text without duplication',()=>{
 const e={selected:['polish','expression'],values:{expression:'少し困った笑顔'},extra:'文字は読みやすくしてください。'};
 const text=composeInstructions('change',e);
 assert.ok(text.includes('【少し困った笑顔】'));assert.ok(text.endsWith(e.extra));
 assert.deepEqual(parseInstructions('change',text),e);
 assert.equal(composeInstructions('change',{...e,selected:[]}),e.extra);
});
test('all 17 templates roundtrip including parameter defaults',()=>{
 assert.equal(instructionPresets.change.length,9);assert.equal(instructionPresets.keep.length,8);
 for(const kind of ['change','keep'])for(const preset of instructionPresets[kind]){
  const text=composeInstructions(kind,{selected:[preset[0]],values:{},extra:''});
  const restored=parseInstructions(kind,text);assert.deepEqual(restored.selected,[preset[0]]);
  assert.equal(composeInstructions(kind,restored),text);
 }
});
test('legacy manual instructions and mixed historical ordering are preserved exactly',()=>{
 for(const text of ['顔立ちを維持する。\n自由な指示\n','手入力\n'+instructionPresets.keep[0][2],'']){
  assert.equal(composeInstructions('keep',restoreInstructions('keep',text,null)),text);
 }
});
test('draft restore retains deselected parameter values but rejects stale selections',()=>{
 const e={selected:[],values:{expression:'怒った顔'},extra:'手入力'};
 assert.deepEqual(restoreInstructions('change','手入力',e),e);
 assert.deepEqual(restoreInstructions('change','別の履歴の指示',e),{selected:[],values:{},extra:'別の履歴の指示'});
 assert.deepEqual(restoreInstructions('change','元の文章',{selected:['unknown'],values:{},extra:''}),{selected:[],values:{},extra:'元の文章'});
});

import {historyPage} from '../src/historyPages.js';
test('history pagination shows all records exactly once across 20-item pages',()=>{
 const items=Array.from({length:41},(_,id)=>({id}));
 const pages=[1,2,3].map(n=>historyPage(items,n));
 assert.deepEqual(pages.map(p=>p.items.length),[20,20,1]);
 assert.deepEqual(pages.flatMap(p=>p.items),items);
 assert.equal(historyPage(items,99).page,3);
 assert.equal(historyPage([],3).page,1);assert.equal(historyPage([],3).start,0);
 assert.equal(historyPage(items.slice(0,20),2).page,1);
});

test('upscale input accepts 2048 square and rejects an oversized edge',()=>{
 const limits=JSON.parse(readFileSync(new URL('../upscale_limits.json',import.meta.url)));
 assert.equal(upscalePlan(2048,2048,upscaleDefaults,limits).width,4096);
 assert.equal(upscalePlan(1152,2048,upscaleDefaults,limits).height,4096);
 assert.throws(()=>upscalePlan(2049,2048,upscaleDefaults,limits));
 assert.throws(()=>upscalePlan(2048,2048,{...upscaleDefaults,factor:4},limits));
});

import {forecastCost} from '../src/usageData.js';
test('cost forecast excludes mocks, unknown prices and other settings',()=>{
 const p={mode:'polish',model:'test',quality:'medium',width:1024,height:1024,n:2};
 const j={params:{...p,provider:'openai',n:1},status:'completed',estimate:.04};
 assert.equal(forecastCost([{...j,estimate:null}],p),null);
 assert.deepEqual(forecastCost([j,{...j,estimate:10,params:{...j.params,provider:'mock'}},{...j,estimate:5,params:{...j.params,width:2048}}],p),{amount:.08,samples:1});
});
