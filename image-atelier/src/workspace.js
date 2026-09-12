// Local-only IndexedDB. Images stay in the backend; masks and text never use localStorage.
export const SCHEMA=1;
let database,handle;
export function openWorkspace(){
 if(!database)database=new Promise((resolve,reject)=>{
  const request=indexedDB.open('image-atelier-workspace',1);
  request.onupgradeneeded=()=>{for(const name of ['drafts','registrations','archives'])request.result.createObjectStore(name,{keyPath:'id'});};
  request.onsuccess=()=>{handle=request.result;resolve(handle);};request.onerror=()=>reject(new Error('下書きDBを開けません。'));
 });
 return database;
}
export async function readRecord(store,id){const db=await openWorkspace();return new Promise((resolve,reject)=>{const request=db.transaction(store).objectStore(store).get(id);request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(new Error('下書きを読み込めません。'));});}
export async function listRecords(store='drafts'){const db=await openWorkspace();return new Promise((resolve,reject)=>{const request=db.transaction(store).objectStore(store).getAll();request.onsuccess=()=>resolve(request.result.sort((a,b)=>(b.updated||0)-(a.updated||0)));request.onerror=()=>reject(new Error('下書き一覧を読み込めません。'));});}
export function writeRecord(store,record){
 if(!handle)return openWorkspace().then(()=>writeRecord(store,record));
 const db=handle;const frozen=structuredClone(record);
 return new Promise((resolve,reject)=>{
  const tx=db.transaction(store,'readwrite');const table=tx.objectStore(store);let stale=false;
  const get=table.get(frozen.id);
  get.onsuccess=()=>{if((get.result?.revision??-1)>frozen.revision){stale=true;return;}table.put(frozen);};
  tx.oncomplete=()=>resolve(!stale);tx.onerror=()=>reject(new Error('ローカル保存に失敗しました。容量やブラウザーの保存設定を確認してください。'));
  tx.onabort=()=>reject(new Error('ローカル保存が中断されました。'));
 });
}
export function validateDraft(record){
 if(!record)return [];
 const warnings=[];
 if(record.schema!==SCHEMA)warnings.push('古い形式の下書きです。原データを保護し、回復できる項目だけ読み込みました。');
 if(!record.payload||typeof record.payload!=='object')warnings.push('下書きの形式が不正です。保存済み原データは保持しています。');
 return warnings;
}
export async function archiveDraft(payload,label){
 const record={id:crypto.randomUUID(),schema:SCHEMA,revision:Date.now(),updated:Date.now(),label,payload:structuredClone(payload)};
 await writeRecord('archives',record);return record;
}
export async function acquireDraft(){
 let id=sessionStorage.getItem('atelier-draft-id')||localStorage.getItem('atelier-last-draft')||crypto.randomUUID();
 let release=()=>{};let conflict=false;let inherited;
 if(navigator.locks){
  const acquire=key=>new Promise(resolve=>{navigator.locks.request('atelier-draft-'+key,{ifAvailable:true},lock=>{
   if(!lock){resolve(false);return;}
   return new Promise(done=>{release=done;resolve(true);});
  }).catch(()=>resolve(false));});
  if(!await acquire(id)){conflict=true;inherited=await readRecord('drafts',id);id=crypto.randomUUID();await acquire(id);}
 }else{
  inherited=await readRecord('drafts',id);id=crypto.randomUUID();conflict=true;
 }
 sessionStorage.setItem('atelier-draft-id',id);localStorage.setItem('atelier-last-draft',id);
 return {id,release,conflict,record:inherited||await readRecord('drafts',id)};
}

export class Registration{
 constructor(id,request,save=writeRecord){this.id=id;this.request=request;this.save=save;this.record=null;this.busy=false;}
 async begin(payload){
  if(this.busy||['unconfirmed','unsaved'].includes(this.record?.state))throw new Error('未確認・未保存の登録があります。先に登録状況を確認してください。');
  this.busy=true;
  try{
   const body=structuredClone({...payload,id:crypto.randomUUID()});
   this.record={id:this.id,revision:Math.max(Date.now(),(this.record?.revision||0)+1),state:'unsaved',attempted:false,payload:body};
   return await this.send();
  }finally{this.busy=false;}
 }
 async send(){
  // This gate also applies to a POST reached through 404 reconciliation.
  // Persist the exact ID and payload before any possibly transmitting operation.
  const previous=this.record;
  const durable={...previous,state:'unconfirmed',attempted:true,revision:Math.max(Date.now(),(previous.revision||0)+1)};
  try{
   if(await this.save('registrations',durable)===false)throw new Error('古い登録情報の保存は拒否されました。');
  }catch(error){
   this.record={...previous,state:previous.attempted||previous.state==='unconfirmed'?'unconfirmed':'unsaved'};
   throw new Error(this.record.state==='unsaved'?'登録情報を保存できないため未送信です。保存できるまで新しいPOSTは行いません。':'登録情報を保存できないため、今回のPOSTは行っていません。以前の受付は未確認です。');
  }
  this.record=durable;
  try{
   const job=await this.request('jobs',this.record.payload);
   const accepted={...this.record,state:'accepted',revision:Math.max(Date.now(),(this.record.revision||0)+1),jobId:job.id};
   if(await this.save('registrations',accepted)===false)throw new Error('受付結果を保存できません。');this.record=accepted;return job;
  }catch(error){
   if(error.status>=400&&error.status<500&&error.status!==408&&error.status!==429){
    const rejected={...this.record,state:'rejected',revision:Math.max(Date.now(),(this.record.revision||0)+1)};
    if(await this.save('registrations',rejected)!==false)this.record=rejected;
   }
   throw error;
  }
 }
 async reconcile(){
  if(this.busy)return null;this.busy=true;
  try{
   let job;
   try{job=await this.request('jobs/'+this.record.payload.id);}
   catch(error){if(error.status===404)return await this.send();throw new Error('登録状況を確認できません。同じIDと入力を保持しています。');}
   const accepted={...this.record,state:'accepted',revision:Math.max(Date.now(),(this.record.revision||0)+1),jobId:job.id};
   if(await this.save('registrations',accepted)===false)throw new Error('受付結果を保存できません。');this.record=accepted;return job;
  }finally{this.busy=false;}
 }
}
