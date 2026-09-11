import 'fake-indexeddb/auto';
import assert from 'node:assert/strict';
import test from 'node:test';
import {Registration,writeRecord,readRecord,validateDraft,archiveDraft,listRecords} from '../src/workspace.js';
import {recoverDraft,resolveDraftAssets} from '../src/draftValidation.js';

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
 const result=await resolveDraftAssets(saved,async()=>{throw new Error('404');});
 assert.equal(result.base,null);assert.equal(result.warnings.length,2);assert.equal(saved.prompt,'protected text');assert.equal(saved.targetId,'missing');
});
test('acknowledgement persistence failure keeps registration unconfirmed',async()=>{
 let writes=0;const manager=new Registration('ack-failure',async(_,body)=>({id:body.id}),async()=>{if(++writes===2)throw new Error('storage full');});
 await assert.rejects(manager.begin({prompt:'A'}));assert.equal(manager.record.state,'unconfirmed');
 await assert.rejects(manager.begin({prompt:'B'}));
});
