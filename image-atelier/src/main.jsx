import React,{useState,useEffect,useRef,useMemo} from 'react';
import {createRoot} from 'react-dom/client';
import {api,bootstrap,upload} from './api';
import Canvas from './Canvas';
import ImageViewer from './ImageViewer';
import {DropZone,References} from './Inputs';
import History from './History';
import './styles.css';
import useDraft from './useDraft';
import {Registration,readRecord,listRecords,writeRecord,archiveDraft} from './workspace';
import LocalHistory from './LocalHistory';
import {recoverDraft,resolveDraftAssets} from './draftValidation';

const defaults={mode:'polish',provider:'mock',model:'gpt-image-2.5-sunburst',quality:'medium',width:1920,height:1088,n:1,format:'png',change:'',keep:'顔立ち、表情、構図、髪型、衣装を維持する。肌の塗りと光は編集対象に合わせる。',composite:false,feather:8};
function App(){
 const [viewerAsset,setViewerAsset]=useState(null);
 const [boot,setBoot]=useState(null),[p,setP]=useState(defaults),[target,setTarget]=useState(null),[refs,setRefs]=useState([]),[result,setResult]=useState(null),[jobs,setJobs]=useState([]),[message,setMessage]=useState(''),[settingsOpen,setSettingsOpen]=useState(false),[settings,setSettings]=useState(null),[presets,setPresets]=useState([]),[presetName,setPresetName]=useState(''),[prompt,setPrompt]=useState(null),[promptOpen,setPromptOpen]=useState(false),[busy,setBusy]=useState(false),[tool,setTool]=useState('pan'),[brush,setBrush]=useState(40),[strokes,setStrokes]=useState([]),[redo,setRedo]=useState([]),[zoom,setZoom]=useState(0),[pan,setPan]=useState([0,0]),[lockRatio,setLockRatio]=useState(false),[adjust,setAdjust]=useState('crop');
 const gate=useRef(false),restoredPrompt=useRef(null),registration=useRef(null);
 const [promptValid,setPromptValid]=useState(false),[promptArchive,setPromptArchive]=useState([]),[pending,setPending]=useState(null),[tracked,setTracked]=useState([]),[follow,setFollow]=useState(null),[registrationReady,setRegistrationReady]=useState(false),[localEdits,setLocalEdits]=useState([]),[health,setHealth]=useState(null),[draftOptions,setDraftOptions]=useState([]);
 const change=(k,v)=>setP(old=>({...old,[k]:v}));
 const act=async fn=>{try{return await fn();}catch(e){setMessage(e.message);}};
 const refresh=async()=>{const [j,l]=await Promise.all([api('jobs'),api('local-edits')]);setJobs(j);setLocalEdits(l);};
 const snapshot=useMemo(()=>({p,targetId:target?.id||null,refs,resultId:result?.id||null,prompt,promptValid,promptArchive,strokes,redo,zoom,pan,brush,tool,lockRatio,tracked,follow}),[p,target,refs,result,prompt,promptValid,promptArchive,strokes,redo,zoom,pan,brush,tool,lockRatio,tracked,follow]);
 async function restoreDraft(saved,warnings=[]){
  if(!saved||typeof saved!=='object'){setMessage('下書きの形式を確認できません。原データを保護しました。');return;}
  const recovered=recoverDraft(saved,defaults);
  if(recovered.warnings.length){await archiveDraft(saved,'不正・旧形式の下書き原データ');warnings.push(...recovered.warnings);}
  saved=recovered.payload;
  const assets=await resolveDraftAssets(saved,id=>api('assets/'+id));
  const {base,out}=assets;warnings.push(...assets.warnings);
  const next={...defaults};for(const key of Object.keys(defaults))if(saved.p&&key in saved.p)next[key]=saved.p[key];
  const references=Array.isArray(saved.refs)?saved.refs:[];
  if(!Array.isArray(saved.strokes)||!Array.isArray(saved.redo))warnings.push('古いマスク形式を原データに保護しました。');
  if(warnings.length)await archiveDraft(saved,'警告付き下書きの原データ');
  setP(next);setTarget(base);setResult(out);setRefs(references);
  setStrokes(Array.isArray(saved.strokes)?saved.strokes:[]);setRedo(Array.isArray(saved.redo)?saved.redo:[]);
  setPrompt(typeof saved.prompt==='string'?saved.prompt:null);setPromptValid(saved.promptValid===true);setPromptArchive(Array.isArray(saved.promptArchive)?saved.promptArchive:[]);
  restoredPrompt.current={text:typeof saved.prompt==='string'?saved.prompt:null,valid:saved.promptValid===true};
  setZoom(typeof saved.zoom==='number'?saved.zoom:0);setPan(Array.isArray(saved.pan)?saved.pan:[0,0]);setBrush(saved.brush||40);setTool(saved.tool||'pan');setLockRatio(!!saved.lockRatio);setTracked(Array.isArray(saved.tracked)?saved.tracked:[]);setFollow(saved.follow||null);
  if(warnings.length)setMessage(warnings.join(' / '));
 }
 const draft=useDraft(snapshot,restoreDraft);
 useEffect(()=>{Promise.all([bootstrap(),api('jobs'),api('presets'),api('local-edits')]).then(([b,j,pr,l])=>{setBoot(b);setSettings(b.settings);setJobs(j);setPresets(pr);setLocalEdits(l);}).catch(e=>setMessage(e.message));let mounted=true;const timer=setInterval(()=>{
  api('worker/health').then(h=>{if(mounted)setHealth(h);}).catch(()=>{if(mounted)setHealth({state:'unavailable',message:'サーバーの状態を確認できません。'});});
  api('jobs').then(j=>{if(mounted)setJobs(j);}).catch(()=>{if(mounted)setMessage('ジョブの状態を確認できません。自動で新しい登録はしません。');});
 },1500);return()=>{mounted=false;clearInterval(timer);};},[]);
 useEffect(()=>{if(!draft.id)return;let alive=true;const manager=new Registration(draft.id,api);registration.current=manager;
  readRecord('registrations',draft.id).then(record=>{if(!alive)return;manager.record=record||null;setPending(record||null);if(record?.jobId)setTracked(ids=>[...new Set([...ids,record.jobId])]);const legacy=sessionStorage.getItem('pending-job');if(legacy){api('jobs/'+legacy).then(job=>{setTracked(ids=>[...new Set([...ids,job.id])]);sessionStorage.removeItem('pending-job');setMessage('旧版の未確認ジョブを照会しました。自動再送はしていません。');}).catch(()=>setMessage('旧版の登録状況を確認できません。ID '+legacy+' を保持しています。新規登録は別操作です。'));}setRegistrationReady(true);}).catch(e=>setMessage(e.message));
  return()=>{alive=false;};
 },[draft.id]);
 useEffect(()=>{if(!draft.ready)return;if(restoredPrompt.current){setPrompt(restoredPrompt.current.text);setPromptValid(restoredPrompt.current.valid);restoredPrompt.current=null;}else setPromptValid(false);},[p,refs,target,draft.ready]);
 useEffect(()=>{const job=jobs.find(j=>j.id===follow);if(job&&['completed','failed','unknown','local_error','cancelled'].includes(job.status)){if(job.outputs?.length)setResult(job.outputs.at(-1));setFollow(null);}},[jobs,follow]);
 async function acknowledge(job){if(!job)return;setTracked(ids=>[...new Set([...ids,job.id])]);setFollow(job.id);setPending(registration.current.record);setPromptOpen(false);setMessage('ジョブを受け付けました。別の実行は新しいIDで登録できます。');await refresh();}
 async function reconcile(){await act(async()=>{setBusy(true);try{await bootstrap();await acknowledge(await registration.current.reconcile());}finally{setPending(registration.current.record);setBusy(false);}});}
 async function loadSavedDraft(record){await act(async()=>{await draft.flush();const id=crypto.randomUUID();const payload=record.payload?.payload||record.payload;await writeRecord('drafts',{...record,payload,id,revision:1,updated:Date.now()});sessionStorage.setItem('atelier-draft-id',id);window.location.reload();});}
 async function adjustAsset(asset,input){await act(async()=>{
  if(input&&strokes.length&&!window.confirm('寸法変更に伴い現在のマスクを消去します。変更前の画像・マスク・指示は下書きの履歴へ保持します。続けますか？'))return;
  await archiveDraft(snapshot,'寸法調整前の作業');
  const adjusted=await api('resize',{id:asset.id,width:p.width,height:p.height,method:adjust,context:params()});
  if(input){setBase(adjusted);setP(old=>({...old,width:adjusted.width,height:adjusted.height}));setMessage('入力を別画像へ調整しました。出力寸法を '+adjusted.width+'×'+adjusted.height+' に設定し、マスクをリセットしました。');}
  else{setResult(adjusted);setFollow(null);setMessage('ローカル編集として履歴に保存しました（無料）。');}
  await refresh();
 });}
 const inputSizeWarning=target&&(target.width%16||target.height%16||Math.max(target.width,target.height)>3840||Math.max(target.width,target.height)/Math.min(target.width,target.height)>3||target.width*target.height<655360||target.width*target.height>8294400);
 function setBase(a){setTarget(a);setStrokes([]);setRedo([]);setZoom(0);setPan([0,0]);}
 function editHistory(job,asset){
  const next={...defaults};
  for(const key of Object.keys(defaults))if(key in job.params)next[key]=job.params[key];
  setP({...next,mode:'polish',width:asset.width,height:asset.height,n:1,composite:false});
  setRefs((job.params.refs||[]).map(ref=>({...ref})));
  setFollow(null);restoredPrompt.current=null;setPromptValid(false);setPromptOpen(false);setViewerAsset(null);
  setBase(asset);setResult(null);setTool('pan');
  setMessage('履歴の画像を次の編集対象にしました。参照資料と変更・維持指示を引き継ぎました。変更内容を調整してから指示文を確認してください。');
  window.scrollTo({top:0,behavior:'instant'});
 }
 async function files(fs,isRef=false){await act(async()=>{if(!fs.length)return;const assets=[];for(const f of (isRef?fs:fs.slice(0,1)))assets.push(await upload(f));if(isRef)setRefs(old=>[...old,...assets.map(a=>({id:a.id,role:'face',person:''}))]);else setBase(assets[0]);});}
 const params=()=>({...p,target:target?.id||null,refs,strokes});
 async function preview(){await act(async()=>{if(!promptValid){const r=await api('prompt',params());if(prompt)setPromptArchive(old=>[...old,prompt]);setPrompt(r.prompt);setPromptValid(true);}setPromptOpen(true);});}
 async function execute(){
  if(gate.current||!registrationReady)return;
  if(['unconfirmed','unsaved'].includes(pending?.state)){setMessage('未確認の登録を先に確認してください。確定済みのIDと入力を保持しています。');return;}
  if(!promptValid){await preview();return;}
  if(p.provider==='openai'&&!window.confirm('実APIで'+p.n+'枚を新しく実行します。結果不明の条件の再実行は追加課金の可能性があります。続けますか？'))return;
  gate.current=true;setBusy(true);
  try{await draft.flush();await acknowledge(await registration.current.begin({...params(),prompt}));}
  catch(error){setMessage(error.message+' / 登録が未確認の場合は登録状況の確認を行ってください。');}
  finally{setPending(registration.current?.record);gate.current=false;setBusy(false);}
 }
 async function restore(j){await act(async()=>{const base=j.params.target?await api('assets/'+j.params.target):null;setFollow(null);restoredPrompt.current={text:j.params.prompt,valid:true};setP({...defaults,...j.params});setRefs(j.params.refs||[]);setBase(base);setStrokes(j.params.strokes||[]);setResult(j.outputs.at(-1)||null);setMessage('条件を復元しました。再実行する場合は送信指示文を確認してください。同一画像の再現は保証されません。');});}
 async function crop(rect){await act(async()=>{const a=await api('crop',{id:target.id,...rect});setRefs(r=>[...r,{id:a.id,role:'face',person:''}]);setTool('pan');setMessage('選択範囲を顔資料として追加しました。');});}
 function dimension(key,value){const next=Number(value);setP(old=>({...old,[key]:next,...(lockRatio?{[key==='width'?'height':'width']:Math.round(next*(key==='width'?old.height/old.width:old.width/old.height)/16)*16}:{})}));}
 if(!boot||!draft.ready)return <main className="loading">{!draft.ready?draft.status:(message||'Image Atelier を起動しています…')}<p>{health?.message}</p><button onClick={()=>window.location.reload()}>{!draft.ready?'下書き復元を再試行':'再読み込み'}</button></main>;
 return <><header><h1>Image Atelier</h1><div><span className="connection">{p.provider==='mock'?'モック接続':boot.key_set?'APIキー設定済み・アクセス未検証':'APIキー未設定'}</span><button onClick={()=>setSettingsOpen(!settingsOpen)}>設定</button></div></header>
 {message?<div className="notice" role="status">{message}<button onClick={()=>setMessage('')}>閉じる</button></div>:null}
 <div className="workspace-status">{boot.test_mode?<><button onClick={()=>act(()=>api('__test/mock-gate',{open:true}))}>テスト:処理を進める</button><button onClick={()=>act(()=>api('__test/mock-gate',{open:false}))}>テスト:処理を止める</button></>:null}<span>下書き: {draft.status} · {draft.id?.slice(0,8)}</span>{draft.notice?<span>{draft.notice}</span>:null}<button onClick={()=>act(()=>draft.flush())}>下書きを保存</button><details><summary>他の下書き・調整前の状態</summary><button onClick={()=>act(async()=>setDraftOptions([...(await listRecords()),...(await listRecords('archives'))]))}>一覧を表示</button>{draftOptions.map(d=><button key={d.id} onClick={()=>loadSavedDraft(d)}>{d.label||'下書き'} · {new Date(d.updated).toLocaleString('ja-JP')} · {d.id.slice(0,6)}</button>)}</details><span>ワーカー: {({running:'稼働中',fault:'障害で一時停止',stopped:'停止中',unavailable:'確認不能'})[health?.state]||'確認中'} {health?.current_job?'実行中 '+health.current_job.slice(0,8):''}</span>{health?.state==='fault'?<><span>{health.message}</span><button onClick={()=>act(async()=>setHealth(await api('worker/resume',{})))}>ワーカーを再開（API再送なし）</button></>:null}</div>
 {['unconfirmed','unsaved'].includes(pending?.state)?<div className="notice"><span>{pending.state==='unsaved'?'保存失敗・未送信の登録':'送信結果が未確認の登録'}: {pending.payload.id}。送信時の入力を固定して保持しています。</span><button disabled={busy} onClick={reconcile}>{pending.state==='unsaved'?'登録情報を保存して再確認':'登録状況を確認（同じID）'}</button></div>:null}
 <div className="app-layout"><aside>
 <DropZone title="編集対象" asset={target} onFiles={fs=>files(fs)}/>{target?<div className="inline"><button onClick={()=>{setTool('crop');setMessage('元画像上をドラッグして顔資料の範囲を選択してください。');}}>顔を切り出す</button><button onClick={()=>{change('width',target.width);change('height',target.height);}}>元画像の寸法</button></div>:null}
 {target?<details><summary>編集対象の寸法を調整</summary><p>現在 {target.width}×{target.height} → 上部の指定 {p.width}×{p.height}</p>{inputSizeWarning?<p className="warning">入力寸法はAPI条件外です。16pxの倍数などの条件に合わせ、方法を選んで調整してください。候補: {Math.ceil(target.width/16)*16}×{Math.ceil(target.height/16)*16}（余白）または1920×1088。自動変形はしません。</p>:null}<select aria-label="入力の寸法調整方法" value={adjust} onChange={e=>setAdjust(e.target.value)}><option value="crop">中央を切り抜き</option><option value="pad">透明余白を追加</option><option value="resize">リサイズ（変形を含む）</option></select><button onClick={()=>adjustAsset(target,true)}>入力を別画像として調整</button></details>:null}
 <DropZone title="参照資料" multiple onFiles={fs=>files(fs,true)}/><References refs={refs} setRefs={setRefs}/>
 <label className="field">変更すること<textarea placeholder="変更したい内容を具体的に入力してください" value={p.change} onChange={e=>change('change',e.target.value)}/></label>
 <label className="field">維持すること<textarea value={p.keep} onChange={e=>change('keep',e.target.value)}/></label>
 <details><summary>維持指定・キャラクタープリセット</summary><div className="preset-buttons">{['顔立ち','表情','構図','髪型','衣装','塗り'].map(x=><button key={x} onClick={()=>change('keep',p.keep+'\n'+x+'を維持する。')}>{x}</button>)}</div><input aria-label="プリセット名" placeholder="キャラクター名など" value={presetName} onChange={e=>setPresetName(e.target.value)}/><button onClick={()=>act(async()=>setPresets(await api('presets',{name:presetName,keep:p.keep,refs}))) }>資料と維持指示を保存</button>{presets.map(pr=><button key={pr.name} onClick={()=>{change('keep',pr.keep);setRefs(pr.refs);}}>{pr.name}</button>)}</details>
 <div className="run-actions">{promptArchive.length?<details><summary>以前の送信指示文（回収用）</summary>{promptArchive.map((text,i)=><pre key={i}>{text}</pre>)}</details>:null}<button className="wide" onClick={preview}>指示文を確認・編集</button><button className="primary wide" disabled={busy||!registrationReady||['unconfirmed','unsaved'].includes(pending?.state)} onClick={execute}>{busy?'登録中…':p.provider==='mock'?'モックで実行':'実APIで実行（有料）'}</button></div>
 <small>資料の役割は指示文による指定です。顔の同一性や塗りの一致を保証するものではありません。</small>
 </aside><main>
 <div className="toolbar"><select aria-label="モード" value={p.mode} onChange={e=>{change('mode',e.target.value);setTool('pan');}}><option value="polish">ブラッシュアップ</option><option value="generate">新規生成</option><option value="inpaint">部分修正</option></select><select aria-label="モデル" value={p.model} onChange={e=>change('model',e.target.value)}>{Object.keys(boot.capabilities.models).map(m=><option key={m}>{m}</option>)}</select><select aria-label="品質" value={p.quality} onChange={e=>change('quality',e.target.value)}>{boot.capabilities.models[p.model].qualities.map(q=><option key={q}>{q}</option>)}</select><div className="dimensions"><input aria-label="幅" type="number" step="16" value={p.width} onChange={e=>dimension('width',e.target.value)}/><span>×</span><input aria-label="高さ" type="number" step="16" value={p.height} onChange={e=>dimension('height',e.target.value)}/></div><select aria-label="出力形式" value={p.format} onChange={e=>change('format',e.target.value)}>{boot.capabilities.formats.map(f=><option key={f} value={f}>{f.toUpperCase()}</option>)}</select><select aria-label="生成枚数" value={p.n} onChange={e=>change('n',Number(e.target.value))}>{[1,2,3,4].map(n=><option key={n} value={n}>{n}枚</option>)}</select></div>
 <div className="subtoolbar"><button onClick={()=>setP({...p,width:p.height,height:p.width})}>縦横交換</button><label><input type="checkbox" checked={lockRatio} onChange={e=>setLockRatio(e.target.checked)}/>比率保持（16px単位）</label><select aria-label="寸法プリセット" defaultValue="" onChange={e=>{if(e.target.value){const [width,height]=e.target.value.split('x').map(Number);setP({...p,width,height});e.target.value='';}}}><option value="">寸法プリセット</option><option value="1920x1088">1920×1088（SD）</option><option value="2048x1152">2048×1152（16:9）</option><option value="1024x1024">1024×1024</option></select><span>比較はズーム・移動が連動</span></div>
 {p.width*p.height>2560*1440?<p className="warning">2560×1440を超える解像度は実験的です。</p>:null}
 {p.mode==='inpaint'?<div className="mask-toolbar">{[['pan','移動'],['brush','ブラシ'],['erase','消しゴム']].map(([v,l])=><button className={tool===v?'selected':''} key={v} onClick={()=>setTool(v)}>{l}</button>)}<label>ブラシ径 <input aria-label="ブラシ径" type="range" min="2" max="300" value={brush} onChange={e=>setBrush(e.target.value)}/>{brush}px</label><button disabled={!strokes.length} onClick={()=>{setRedo([...redo,strokes.at(-1)]);setStrokes(strokes.slice(0,-1));}}>取り消し</button><button disabled={!redo.length} onClick={()=>{setStrokes([...strokes,redo.at(-1)]);setRedo(redo.slice(0,-1));}}>やり直し</button><button onClick={()=>{setStrokes([]);setRedo([]);}}>全消去</button><label><input type="checkbox" checked={p.composite} onChange={e=>change('composite',e.target.checked)}/>変更範囲だけ合成</label><label>境界ぼかし <input aria-label="境界ぼかし" type="number" min="0" max="100" value={p.feather} onChange={e=>change('feather',Number(e.target.value))}/>px</label><small>塗った場所＝変更範囲。APIのマスクは厳密な画素保護ではありません。局所合成は境界を内側にぼかし、塗っていない画素を保持します。</small></div>:null}
 <div className="compare"><Canvas label="元画像" asset={target} strokes={p.mode==='inpaint'?strokes:[]} setStrokes={updater=>{setStrokes(updater);setRedo([]);}} tool={tool} brush={brush} zoom={zoom} setZoom={setZoom} pan={pan} setPan={setPan} onCrop={crop}/><Canvas label="結果" asset={result} onOpen={()=>{setFollow(null);setViewerAsset(result);}} zoom={zoom} setZoom={setZoom} pan={pan} setPan={setPan}/></div>
 {result?<div className="result-actions"><button onClick={()=>setViewerAsset(result)}>大きく表示</button><strong>{result.kind==='composite'?'局所合成':result.name}</strong><button onClick={()=>{setFollow(null);setBase(result);change('mode','polish');setMessage('結果を次の編集対象にしました。資料と指示を引き継ぎます。');}}>次の編集対象にする</button><button onClick={()=>act(async()=>{const r=await api('export',{id:result.id});setMessage('保存しました: '+r.path);})}>PNGを保存</button><a href={'/api/assets/'+result.id+'/download'} download>PNGダウンロード</a><details><summary>寸法を明示的に調整</summary><p>上部の幅・高さに合わせて別画像を作成します。</p><select aria-label="寸法調整方法" value={adjust} onChange={e=>setAdjust(e.target.value)}><option value="crop">中央を切り抜き</option><option value="pad">透明余白を追加</option><option value="resize">リサイズ（変形を含む）</option></select><button onClick={()=>adjustAsset(result,false)}>別画像として作成</button></details></div>:null}
 <History jobs={jobs} onLoad={restore} onEdit={editHistory} onResult={asset=>{setFollow(null);setResult(asset);setViewerAsset(asset);}} onReprocess={id=>act(async()=>{await api('jobs/'+id+'/reprocess',{});await refresh();setMessage('保存応答をローカルで再処理しました。API再送はしていません。');})} onCancel={id=>act(async()=>{await api('jobs/'+id+'/cancel',{});await refresh();})}/>
 <LocalHistory edits={localEdits} onResult={asset=>{setFollow(null);setResult(asset);setViewerAsset(asset);}} onEdit={(entry)=>editHistory({params:entry.context||defaults},entry.result)}/>
 </main></div>
 {viewerAsset?<ImageViewer key={viewerAsset.id} asset={viewerAsset} onClose={()=>setViewerAsset(null)}/>:null}
 {promptOpen?<div className="modal-backdrop"><section role="dialog" aria-modal="true" aria-label="送信指示文" className="modal"><h2>送信指示文</h2><p>画像の順序と役割を確認してください。ここで編集した文章をそのまま送信します。</p><textarea aria-label="送信指示文" value={prompt||''} onChange={e=>{setPrompt(e.target.value);setPromptValid(true);}}/><p>実行前の費用概算: {p.provider==='mock'?'対象外（外部APIは呼びません）':'不明'} · {p.width}×{p.height} · {p.format.toUpperCase()} · {p.n}枚</p><div className="inline"><button onClick={()=>setPromptOpen(false)}>戻る</button><button className="primary" disabled={busy||!registrationReady||['unconfirmed','unsaved'].includes(pending?.state)} onClick={execute}>この指示文で実行</button></div></section></div>:null}
 {settingsOpen?<div className="modal-backdrop"><section role="dialog" aria-modal="true" aria-label="設定" className="modal"><h2>設定</h2><button onClick={()=>act(async()=>{const r=await api('connection-check',{});window.alert(r.message+'\n'+Object.entries(r.models).map(([model,available])=>model+': '+(available?'一覧にあり':'一覧にありません')).join('\n'));})}>実APIの接続を確認（生成なし）</button><label className="field">接続方式<select value={p.provider} onChange={e=>change('provider',e.target.value)}><option value="mock">モック（外部通信なし）</option><option value="openai">OpenAI API（有料）</option></select></label><p>APIキー: {boot.key_set?'ローカル設定済み。モデルアクセスは未検証。':'未設定。アプリ直下の config.local.json の openai_api_key に保存し、画面を再読み込みしてください。環境変数 OPENAI_API_KEY も使えます。'}</p><label className="field">保存先フォルダー<input value={settings.output} onChange={e=>setSettings({...settings,output:e.target.value})}/></label><label className="field">このアプリの累計予算（USD）<input type="number" min="0" step="0.1" value={settings.budget} onChange={e=>setSettings({...settings,budget:e.target.value})}/></label><label className="field">1枚の予約額（USD・利用者指定）<input type="number" min="0.01" step="0.1" value={settings.reservation} onChange={e=>setSettings({...settings,reservation:e.target.value})}/></label><label><input type="checkbox" checked={settings.live} onChange={e=>setSettings({...settings,live:e.target.checked})}/>実APIの送信を有効にする</label><p>このUIの概算利用額＋未精算の予約額: ${jobs.reduce((s,j)=>s+j.reserved,0).toFixed(2)}。予約額は費用見積もりではありません。成功分は取得済みの概算額で精算します。結果不明・費用不明の予約は保持します。請求総額の上限は保証できません。</p><p>アプリ予算の残り: ${Math.max(0,Number(settings.budget)-jobs.reduce((s,j)=>s+j.reserved,0)).toFixed(5)}</p><p>使用量は各履歴に記録します。価格表確認日: {boot.capabilities.checked}。使用量の内訳が十分な場合だけ概算し、情報不足なら不明と表示します。</p><a href="https://platform.openai.com/usage" target="_blank" rel="noreferrer">公式Usageで利用状況を確認</a><div className="inline"><button onClick={()=>setSettingsOpen(false)}>閉じる</button><button className="primary" onClick={()=>act(async()=>{const s=await api('settings',settings);setSettings(s);setSettingsOpen(false);setMessage('設定を保存しました。');})}>設定を保存</button></div></section></div>:null}
 </>;
}
createRoot(document.getElementById('root')).render(<App/>);
