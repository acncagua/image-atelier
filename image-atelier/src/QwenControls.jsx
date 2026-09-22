import React,{useEffect,useState} from 'react';
import {api} from './api';
import {qwenProblem} from './qwenOptions';
export default function QwenControls({p,change,target}){
 const [config,setConfig]=useState(null),[message,setMessage]=useState('');
 useEffect(()=>{let alive=true;api('qwen/config').then(c=>{if(alive)setConfig(c);}).catch(e=>{if(alive)setMessage(e.message);});return()=>{alive=false;};},[]);
 return <section className="qwen-controls"><strong>QwenローカルGPU · API料金なし</strong><div className="inline">
  <label>ステップ数<input aria-label="Qwenステップ数" type="number" min="1" max="50" value={p.qwen_steps} onChange={e=>change('qwen_steps',Number(e.target.value))}/></label>
  <label>CFG<input aria-label="Qwen CFG" type="number" min="1" max="5" step="0.1" value={p.qwen_cfg??1} onChange={e=>change('qwen_cfg',Number(e.target.value))}/></label>
  <label>シード<input aria-label="Qwenシード" type="number" min="-1" max="4294967295" value={p.qwen_seed} onChange={e=>change('qwen_seed',Number(e.target.value))}/></label>
  <button onClick={()=>change('qwen_seed',-1)}>シードを変更</button><small>-1は実行ごとにランダム。実際の値は履歴に記録します。</small>
  <label>CPUオフロード<select aria-label="Qwenオフロード" value={p.qwen_offload} onChange={e=>change('qwen_offload',e.target.value)}><option value="model">標準（model）</option><option value="sequential">省メモリ優先（低速）</option></select></label>
 </div><label><input type="checkbox" checked={!!p.qwen_timing} onChange={e=>change('qwen_timing',e.target.checked)}/>処理時間を計測（各ステップ・処理段階）</label><small>GPUの完了を待って計測するため、計測中は多少遅くなる場合があります。履歴の「処理時間の内訳」で確認できます。</small><small>モデルは生成ごとに読み込み、処理終了時に解放します。</small><small>CFG 1は公式推奨（ネガティブ無効）。1より大きい値でネガティブが有効になり、ステップ当たりの計算量が約2倍になります。設定範囲1〜5はAtelierの調整範囲です。</small><small>BF16・PNG・1枚。VAE分割を使用します。部分修正は元画像と同寸法で実行。元画像・参照資料・マスクの合計10枚まで。指示文・プリセットはそのまま使用します。</small>
 {qwenProblem(p,target)?<p role="status" className="warning">{qwenProblem(p,target)}</p>:null}
 <details><summary>Qwenの詳細・環境設定</summary><div className="inline"><label>VAEタイル<input aria-label="Qwen VAEタイル" type="number" step="32" value={p.qwen_tile} onChange={e=>change('qwen_tile',Number(e.target.value))}/></label><label>VAE stride<input aria-label="Qwen VAE stride" type="number" step="32" value={p.qwen_stride} onChange={e=>change('qwen_stride',Number(e.target.value))}/></label></div><small>重なりはタイル − strideです。初期値512／384（重なり128）。</small>
 {config?<><label className="field">Qwen専用Python<input value={config.python} onChange={e=>setConfig({...config,python:e.target.value})}/></label><label className="field">Qwenモデルフォルダー<input value={config.model} onChange={e=>setConfig({...config,model:e.target.value})}/></label><button onClick={async()=>{try{await api('qwen/config',config);setMessage('Qwen環境設定を保存しました。');}catch(e){setMessage(e.message);}}}>Qwen環境を保存</button></>:null}<p>モデルは研究・評価向けの利用条件です。メモリ使用量は入力画像と他アプリの状態でも変わります。SwinIRとは同時実行せず順番に処理します。</p></details>{message?<p role="status">{message}</p>:null}</section>;
}
