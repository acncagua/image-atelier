import React,{useEffect,useState} from 'react';
import {api} from './api';
import {localSpec} from './localModels';
import {qwenProblem} from './qwenOptions';
import SdxlControls from './SdxlControls';
export default function LocalControls(props){
 return localSpec(props.p.model).family==='sdxl'?<SdxlControls {...props}/>:<OtherLocalControls {...props}/>;
}
function OtherLocalControls({p,change,target,keep,onKeep,strataManaged=false}){
 const spec=localSpec(p.model);
 const [config,setConfig]=useState(null),[models,setModels]=useState(null),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
 useEffect(()=>{let alive=true;api('comfy/config?model='+encodeURIComponent(p.model)).then(c=>{if(alive)setConfig(c);}).catch(e=>{if(alive)setMessage(e.message);});return()=>{alive=false;};},[]);
 async function perform(fn){setBusy(true);try{await fn();}catch(e){setMessage(e.message);}finally{setBusy(false);}}
 return <section className="qwen-controls"><strong>{p.model} · ComfyUIローカルGPU · API料金なし</strong><div className="inline">
 <label>ステップ数<input aria-label="ステップ数" type="number" min="1" max="50" value={p.qwen_steps} onChange={e=>change('qwen_steps',Number(e.target.value))}/></label>
 <label>CFG<input aria-label="CFG" type="number" min="1" max="5" step="0.1" value={p.qwen_cfg??1} onChange={e=>change('qwen_cfg',Number(e.target.value))}/></label>
 <label>シード<input aria-label="シード" type="number" min="-1" max="4294967295" value={p.qwen_seed} onChange={e=>change('qwen_seed',Number(e.target.value))}/></label>
 <button onClick={()=>change('qwen_seed',-1)}>シードを変更</button></div>
 {spec.family==='flux'?<label>Guidance<input aria-label="Guidance" type="number" min="0" max="100" step="0.1" value={p.guidance} onChange={e=>change('guidance',Number(e.target.value))}/></label>:null}
 {spec.family!=='qwen'&&p.mode!=='generate'?<label>変更強度（denoise）<input aria-label="変更強度" type="number" min="0.01" max="1" step="0.05" value={p.denoise} onChange={e=>change('denoise',Number(e.target.value))}/><small>小さいほど元画像を残します。部分修正は塗った領域にノイズマスクを適用します。</small></label>:null}
 <label><input type="checkbox" checked={strataManaged||!!keep} disabled={strataManaged} onChange={e=>onKeep(e.target.checked)}/>選択モデルをメモリに保持</label><small>{strataManaged?'Strata連携中は手動解放まで保持します。生成完了・失敗・取消・モデル切替でも自動解放しません。':'ONは生成後も保持。OFF・GPT Imageへの切替時は全モデルを解放します。生成中の変更は完了後に適用します。'}</small>
 <small>-1は実行時にランダム。PNG・1枚、画像入力の上限はモデルにより異なります。Atelier専用のComfyUIを使用してください。</small>
 <small>ComfyUI版はEuler／simpleを使用します。CPUオフロードはComfyUI側で管理します。ステップ別時間計測はこの試作では未対応です。</small>
 {p.qwen_timing?<p className="warning">旧方式の時間計測が有効です。<button onClick={()=>change('qwen_timing',false)}>時間計測を解除</button></p>:null}
 {qwenProblem(p,target)?<p className="warning">{qwenProblem(p,target)}</p>:null}
 <details open><summary>ComfyUI接続・モデル設定</summary><small>ファイル名から対応候補を絞り込みます。独自に改名したファイルは表示されない場合があります。モデル設定は種類別に保存します。</small>{config?<>
 <label className="field">ComfyUI URL<input aria-label="ComfyUI URL" disabled={busy} value={config.url} onChange={e=>{setConfig({...config,url:e.target.value});setModels(null);}}/></label>
 <button disabled={busy} onClick={()=>perform(async()=>{const result=await api('comfy/check',{model:p.model,url:config.url});setModels(result);const version=result.comfyui_version?'ComfyUI '+result.comfyui_version+'。':'';setMessage(version+(result.missing.length?'不足ノード: '+result.missing.join(', '):'接続成功。選択中のモデル用のファイルを選択して保存してください。'));})}>接続確認・モデル一覧を取得</button>
 {Object.entries({diffusion:'画像生成モデル',text_encoder:spec.family==='flux'?'テキストエンコーダー（T5）':'テキストエンコーダー',...(spec.family==='flux'?{text_encoder_2:'テキストエンコーダー（CLIP-L）'}:{}),vae:'VAE'}).map(([key,label])=><label className="field" key={key}>{label}<select aria-label={label} value={config[key]} onChange={e=>setConfig({...config,[key]:e.target.value})}><option value="">選択してください</option>{(models?.[key]||[config[key]]).filter(Boolean).map(v=><option key={v} value={v}>{v}</option>)}</select></label>)}
 <button disabled={busy||!models||models.missing.length>0||['diffusion','text_encoder',...(spec.family==='flux'?['text_encoder_2']:[]),'vae'].some(k=>!models[k]?.includes(config[k]))} onClick={()=>perform(async()=>{await api('comfy/config',config);setMessage('ComfyUI設定を保存しました。次のジョブから適用します。');})}>ComfyUI設定を保存</button><small>{config.text_encoder?.toLowerCase().endsWith('.gguf')?(spec.family==='qwen'?'GGUFローダーを使用します。対応するmmprojを同じフォルダーに置いてください。':'テキストエンコーダーはGGUFローダーを使用します。'):''}</small><small>{config.diffusion?.toLowerCase().endsWith('.gguf')?'GGUF専用ローダーを使用します。':'標準safetensorsローダーを使用します。'}</small>
 <button disabled={busy} onClick={()=>perform(async()=>{const results=await api('comfy/recover',{});setMessage(results.length?results.map(r=>r.id+': '+(r.recovered?'照合完了':r.message)).join(' / '):'未確認のジョブはありません。');})}>中断ジョブを照合・残処理を取消</button>
 </>:null}</details>{message?<p role="status">{message}</p>:null}</section>;
}
