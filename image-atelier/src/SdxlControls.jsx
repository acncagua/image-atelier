import React,{useEffect,useState} from 'react';
import {api} from './api';
import {localOutputSize,sdxlHiresEnabled} from './localModels';
import {qwenProblem} from './qwenOptions';

const samplerNames={dpmpp_2m:'DPM++ 2M',dpmpp_sde:'DPM++ SDE',dpmpp_2m_sde:'DPM++ 2M SDE',dpmpp_3m_sde:'DPM++ 3M SDE',euler_ancestral:'Euler a',euler:'Euler',heun:'Heun',lms:'LMS',dpm_2:'DPM2',dpm_2_ancestral:'DPM2 a',uni_pc:'UniPC',lcm:'LCM'};
const upscaleName=value=>{const [kind,...rest]=value.split(':');return (kind==='latent'?'Latent · ':kind==='pixel'?'画像補間 · ':'モデル · ')+rest.join(':');};
const configKey=c=>JSON.stringify([c?.url,c?.checkpoint,c?.vae,c?.hires_checkpoint]);
export default function SdxlControls({p,change,target,keep,onKeep,onReady,strataManaged=false}){
 const [config,setConfig]=useState(null),[saved,setSaved]=useState(''),[models,setModels]=useState(null),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
 const dirty=!!config&&configKey(config)!==saved;
 const hires=sdxlHiresEnabled(p);
 useEffect(()=>{let alive=true;api('comfy/config?model=SDXL').then(c=>{if(alive){setConfig(c);setSaved(configKey(c));}}).catch(e=>{if(alive)setMessage(e.message);});return()=>{alive=false;};},[]);
 useEffect(()=>{onReady?.(!!config?.checkpoint&&!dirty);},[config?.checkpoint,dirty,onReady]);
 async function perform(fn){setBusy(true);try{await fn();}catch(e){setMessage(e.message);}finally{setBusy(false);}}
 function selection(key,value){setConfig(c=>({...c,[key]:value}));}
 function numeric(key,label,min,max,step=1){return <label>{label}<input aria-label={label} type="number" min={min} max={max} step={step} value={p[key]} onChange={e=>change(key,Number(e.target.value))}/></label>;}
 function parameter(key,label,choices,format=v=>v){const options=[...new Set([p[key],...(choices||[])])].filter(Boolean);return <label className="field">{label}<select aria-label={label} disabled={busy} value={p[key]} onChange={e=>change(key,e.target.value)}>{options.map(v=><option key={v} value={v}>{format(v)}</option>)}</select></label>;}
 function modelChoice(key,label,empty){const options=models?(models[key==='vae'?'vae':'checkpoint']||[]):[config[key]];return <label className="field">{label}<select aria-label={label} disabled={busy} value={config[key]} onChange={e=>selection(key,e.target.value)}><option value="">{empty}</option>{options.filter(Boolean).map(v=><option key={v} value={v}>{v}</option>)}</select></label>;}
 const canSave=models&&!models.missing.length&&models.checkpoint?.includes(config?.checkpoint)&&(!config?.vae||models.vae?.includes(config.vae))&&(!hires||!config?.hires_checkpoint||models.checkpoint?.includes(config.hires_checkpoint));
 const size=localOutputSize(p,target),problem=qwenProblem(p,target);
 return <section className="qwen-controls sdxl-controls"><strong>SDXL · ComfyUIローカルGPU · API料金なし</strong>
 <details open><summary>SDXLモデル・VAE</summary>{config?<>
 <label className="field">ComfyUI URL<input aria-label="ComfyUI URL" disabled={busy} value={config.url} onChange={e=>{selection('url',e.target.value);setModels(null);}}/></label>
 <button disabled={busy} onClick={()=>perform(async()=>{const result=await api('comfy/check',{model:'SDXL',url:config.url});setModels(result);setMessage((result.comfyui_version?'ComfyUI '+result.comfyui_version+'。':'')+(result.missing.length?'不足ノード: '+result.missing.join(', '):'接続成功。SDXL用のチェックポイントを選択してください。'));})}>接続確認・モデル一覧を取得</button>
 {modelChoice('checkpoint','SDXLモデル（チェックポイント）','選択してください')}
 {modelChoice('vae','VAE','未指定 · モデル内蔵VAE')}
 <small>ComfyUIのチェックポイント一覧です。SDXL用のモデルを選択してください。VAEを省略すると、各チェックポイントの内蔵VAEを使います。</small>
 {hires?modelChoice('hires_checkpoint','Hiresチェックポイント','未指定 · 元のモデルを使用'):null}
 <button disabled={busy||!canSave} onClick={()=>perform(async()=>{const result=await api('comfy/config',config);setConfig(result);setSaved(configKey(result));setMessage('SDXLモデル設定を保存しました。次のジョブから適用します。');})}>ComfyUI設定を保存</button>
 {dirty?<p className="warning">モデル設定が未保存です。保存後に実行できます。</p>:null}
 </>:null}</details>
 {parameter('sampler','サンプリング',models?.samplers,v=>samplerNames[v]||v)}
 {parameter('scheduler','スケジューラー',models?.schedulers)}
 <div className="inline">{numeric('qwen_steps','Step数',1,150)}{numeric('qwen_cfg','CFGスケール',1,30,0.5)}{numeric('clip_skip','CLIP skip',1,24)}</div>
 <div className="inline">{numeric('qwen_seed','シード',-1,4294967295)}<button onClick={()=>change('qwen_seed',-1)}>シードを変更</button></div>
 {numeric('denoise','デノイズ強度',0,1,0.05)}<small>編集とHiresの再生成に使用します。新規生成の1段目は1.0です。シード-1は毎回ランダムです。</small>
 <label><input aria-label="HiresFix" type="checkbox" checked={hires} disabled={p.mode!=='generate'} onChange={e=>change('hires_fix',e.target.checked)}/>HiresFix（新規生成のみ）</label>
 {p.mode!=='generate'?<small>ブラッシュアップ・部分修正ではHiresFixを使用しません。</small>:null}
 {hires?<details open><summary>Hires設定</summary><div className="inline">{numeric('hires_scale','Hires倍率',1.05,4,0.05)}{numeric('hires_steps','Hires Step数',0,150)}</div>
 {parameter('hires_upscaler','Hiresアップスケーラー',models?.upscalers,upscaleName)}
 <small>Step数0は1段目と同じ回数。選択アップスケーラーで拡大後、デノイズ強度を使って再生成します。Hiresモデルは上の「Hiresチェックポイント」で指定します。</small></details>:null}
 {p.mode==='inpaint'?<><label className="field">Inpaint area<select aria-label="Inpaint area" value={p.inpaint_area} onChange={e=>change('inpaint_area',e.target.value)}><option value="whole">Whole picture</option><option value="masked">Only masked</option></select></label>
 {p.inpaint_area==='masked'?<>{numeric('inpaint_padding','マスク周辺の余白（px）',0,256)}<small>マスク周辺を指定寸法で処理し、塗った範囲だけ元画像へ戻します。全体の寸法と範囲外の画素は元画像を維持します。</small></>:<small>全体を指定寸法で処理します。範囲外の画素を保護するには「変更範囲だけ合成」を使ってください。</small>}</>:null}
 <small>上部の幅・高さは1段目の生成寸法です。{p.mode==='inpaint'&&p.inpaint_area==='masked'&&!target?'元画像を選択すると最終出力寸法を表示します。':`今回の最終出力: ${size[0]}×${size[1]}px。`}Hires後の寸法は32px単位に丸めます。</small>
 <label><input type="checkbox" checked={strataManaged||!!keep} disabled={strataManaged} onChange={e=>onKeep(e.target.checked)}/>選択モデルをメモリに保持</label>{strataManaged?<small>Strata連携中は手動解放まで保持します。生成完了・失敗・取消・モデル切替でも自動解放しません。</small>:null}
 <small>ComfyUIにあるサンプラー・アップスケーラーを使用します。CPUオフロードはComfyUI側で管理します。参照資料と指示補強PEはSDXLでは使いません。</small>
 {p.qwen_timing?<button onClick={()=>change('qwen_timing',false)}>旧方式の時間計測を解除</button>:null}
 {problem?<p className="warning">{problem}</p>:null}
 <button disabled={busy} onClick={()=>perform(async()=>{const result=await api('comfy/recover',{});setMessage(result.length?result.map(r=>r.id+': '+(r.recovered?'照合完了':r.message)).join(' / '):'未確認のジョブはありません。');})}>中断ジョブを照合・残処理を取消</button>
 {message?<p role="status">{message}</p>:null}</section>;
}
