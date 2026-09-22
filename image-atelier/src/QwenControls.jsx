import React,{useEffect,useState} from 'react';
import {api} from './api';
import {qwenProblem} from './qwenOptions';
export default function QwenControls({p,change,target}){
 const [config,setConfig]=useState(null),[models,setModels]=useState(null),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
 useEffect(()=>{let alive=true;api('qwen/config').then(c=>{if(alive)setConfig(c);}).catch(e=>{if(alive)setMessage(e.message);});return()=>{alive=false;};},[]);
 async function perform(fn){setBusy(true);try{await fn();}catch(e){setMessage(e.message);}finally{setBusy(false);}}
 return <section className="qwen-controls"><strong>Qwen · ComfyUIローカルGPU · API料金なし</strong><div className="inline">
 <label>ステップ数<input aria-label="Qwenステップ数" type="number" min="1" max="50" value={p.qwen_steps} onChange={e=>change('qwen_steps',Number(e.target.value))}/></label>
 <label>CFG<input aria-label="Qwen CFG" type="number" min="1" max="5" step="0.1" value={p.qwen_cfg??1} onChange={e=>change('qwen_cfg',Number(e.target.value))}/></label>
 <label>シード<input aria-label="Qwenシード" type="number" min="-1" max="4294967295" value={p.qwen_seed} onChange={e=>change('qwen_seed',Number(e.target.value))}/></label>
 <button onClick={()=>change('qwen_seed',-1)}>シードを変更</button></div>
 <small>-1は実行時にランダム。PNG・1枚、元画像・参照・マスクの合計10枚まで。終了後にComfyUIへモデル解放を要求します。Atelier専用のComfyUIを使用してください。</small>
 <small>ComfyUI版はEuler／simpleを使用します。CPUオフロードはComfyUI側で管理します。ステップ別時間計測はこの試作では未対応です。</small>
 {p.qwen_timing?<p className="warning">旧方式の時間計測が有効です。<button onClick={()=>change('qwen_timing',false)}>時間計測を解除</button></p>:null}
 {qwenProblem(p,target)?<p className="warning">{qwenProblem(p,target)}</p>:null}
 <details open><summary>ComfyUI接続・モデル設定</summary>{config?<>
 <label className="field">ComfyUI URL<input aria-label="ComfyUI URL" value={config.url} onChange={e=>setConfig({...config,url:e.target.value})}/></label>
 <button disabled={busy} onClick={()=>perform(async()=>{await api('qwen/config',config);const result=await api('comfy/check',{});setModels(result);setMessage(result.missing.length?'不足ノード: '+result.missing.join(', '):'接続成功。Qwen-Image-2.1用のモデルを選択して保存してください。');})}>接続確認・モデル一覧を取得</button>
 {Object.entries({diffusion:'画像生成モデル',text_encoder:'テキストエンコーダー',vae:'VAE'}).map(([key,label])=><label className="field" key={key}>{label}<select aria-label={label} value={config[key]} onChange={e=>setConfig({...config,[key]:e.target.value})}><option value="">選択してください</option>{[...new Set([config[key],...(models?.[key]||[])])].filter(Boolean).map(v=><option key={v} value={v}>{v}</option>)}</select></label>)}
 <button disabled={busy} onClick={()=>perform(async()=>{await api('qwen/config',config);setMessage('ComfyUI設定を保存しました。次のジョブから適用します。');})}>ComfyUI設定を保存</button>
 <button disabled={busy} onClick={()=>perform(async()=>{const results=await api('comfy/recover',{});setMessage(results.length?results.map(r=>r.id+': '+(r.recovered?'照合完了':r.message)).join(' / '):'未確認のジョブはありません。');})}>中断ジョブを照合・残処理を取消</button>
 </>:null}</details>{message?<p role="status">{message}</p>:null}</section>;
}
