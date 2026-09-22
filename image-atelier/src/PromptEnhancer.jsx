import React,{useEffect,useRef,useState} from 'react';
import {api} from './api';
import {Registration,readRecord} from './workspace';

export default function PromptEnhancer({prompt,context,draftId,flushDraft,onApply,onRestore}){
 const source={mode:context.mode,target:context.mode==='generate'?null:context.target,refs:context.refs.map(r=>r.id),strokes:context.mode==='inpaint'?context.strokes:[]};
 const i2i=source.mode!=='generate'||source.refs.length>0;
 const manager=useRef(null),gate=useRef(false);
 const [ready,setReady]=useState(false),[pending,setPending]=useState(null),[job,setJob]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[config,setConfig]=useState(null),[limit,setLimit]=useState(8192);
 useEffect(()=>{
  let alive=true;const r=new Registration('pe:'+draftId,(path,body)=>api('pe/'+path,body));manager.current=r;
  readRecord('registrations','pe:'+draftId).then(record=>{if(alive){r.record=record||null;setPending(record||null);setReady(true);}}).catch(e=>{if(alive)setError(e.message);});
  api('pe/config').then(c=>{if(alive)setConfig(c);}).catch(e=>{if(alive)setError(e.message);});
  return()=>{alive=false;};
 },[draftId]);
 const ident=pending?.jobId||pending?.payload?.id;
 useEffect(()=>{
  if(!ident)return;let alive=true;
  const read=()=>api('pe/jobs/'+ident).then(j=>{if(alive)setJob(j);}).catch(e=>{if(alive&&e.status!==404)setError(e.message);});
  read();const timer=setInterval(read,1500);return()=>{alive=false;clearInterval(timer);};
 },[ident]);
 const matching=job?.params.context?JSON.stringify(job.params.context)===JSON.stringify(source):!i2i;
 const unresolved=['unsaved','unconfirmed'].includes(pending?.state);
 const working=job&&['queued','running','cancel_requested'].includes(job.status);
 async function run(reconcile=false){
  if(gate.current||!ready)return;gate.current=true;setBusy(true);setError('');
  try{
   if(reconcile)setJob(await manager.current.reconcile());
   else{await flushDraft();setJob(await manager.current.begin({...context,model:'Qwen-Image-2.1',prompt,max_new_tokens:limit,seed:42}));}
  }catch(e){setError(e.message);}finally{setPending(manager.current.record);gate.current=false;setBusy(false);}
 }
 return <section className="prompt-enhancer"><strong>Qwenで指示を補強（{i2i?'PE-I2I':'PE-T2I'}）</strong><p>{i2i?'生成に使う元画像・参照画像・マスクと指示文を読み取ります。':'参照画像なしの新規生成用です。'}補強後の英語文を確認して採用できます。補強だけでは画像を生成せず、指定寸法も変更しません。</p>
  <button disabled={!ready||busy||working||unresolved||!prompt?.trim()} onClick={()=>run()}>指示を補強する</button>
  {unresolved?<button disabled={busy} onClick={()=>run(true)}>補強の登録状況を確認</button>:null}
  {working?<button disabled={job.status==='cancel_requested'} onClick={async()=>{try{setJob(await api('pe/jobs/'+job.id+'/cancel',{}));}catch(e){setError(e.message);}}}>補強を取消</button>:null}
  {job?<p role="status">{job.message}{job.worker_error?' / '+job.worker_error:''}</p>:null}{error?<p role="status" className="warning">{error}</p>:null}
  {job&&!matching?<p>補強時と画像・編集範囲が異なります。現在の条件で再補強してください。</p>:null}{job?.status==='completed'&&job.result?<><details><summary>補強前の指示</summary><pre>{job.params.prompt}</pre></details><label className="field">補強結果（英語）<textarea readOnly value={job.result.rewritten_prompt}/></label><p>推奨縦横比：{job.result.wh_ratio||job.result.ratio_follow+'に合わせる'}（現在の幅・高さはそのまま）</p>
   <button disabled={!matching||prompt!==job.params.prompt} onClick={()=>onApply(job)}>この補強結果を採用</button>{prompt!==job.params.prompt?<button onClick={()=>onRestore(job.params.prompt)}>補強前に戻す</button>:null}{prompt!==job.params.prompt?<small>採用済み、または元の指示が変更されています。必要なら現在の指示で再補強してください。</small>:null}
  </>:null}
  <details><summary>補強モデルの設定</summary><label className="field">出力トークン上限<input aria-label="補強トークン上限" type="number" min="256" max="24000" value={limit} onChange={e=>setLimit(Number(e.target.value))}/></label>
   {config?<><label className="field">PE専用Python<input value={config.python} onChange={e=>setConfig({...config,python:e.target.value})}/></label><label className="field">PE-T2Iモデルフォルダー<input value={config.model} onChange={e=>setConfig({...config,model:e.target.value})}/></label><label className="field">PE-I2Iモデルフォルダー<input value={config.model_i2i||''} onChange={e=>setConfig({...config,model_i2i:e.target.value})}/></label><button disabled={busy||working} onClick={async()=>{try{await api('pe/config',config);setError('補強モデルの設定を保存しました。');}catch(e){setError(e.message);}}}>補強設定を保存</button></>:null}
  </details>
 </section>;
}
