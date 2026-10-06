import React from 'react';
const defaults={enabled:false,url:'http://127.0.0.1:8080',gpu_uuid:'',target_gib:18,probe_timeout:5,change_timeout:900};
export default function StrataSettings({value,onChange,state}){
 const c={...defaults,...value};const update=(key,next)=>onChange({...c,[key]:next});
 return <fieldset className="strata-settings"><legend>Strata連携</legend>
 <label><input aria-label="Strata連携を有効にする" type="checkbox" checked={c.enabled} onChange={e=>update('enabled',e.target.checked)}/>Strata連携を有効にする</label>
 <label className="field">StrataサーバーURL<input aria-label="StrataサーバーURL" value={c.url} onChange={e=>update('url',e.target.value)}/></label>
 <small>同じPCのURLを指定します。末尾の/v1は保存時に取り除きます。</small>
 <label className="field">対象GPU<select aria-label="対象GPU" value={c.gpu_uuid} onChange={e=>update('gpu_uuid',e.target.value)}><option value="">自動（同じPCの単一NVIDIA GPU）</option>{(state?.gpus||[]).map(g=><option key={g.uuid} value={g.uuid}>{g.index}: {g.name}</option>)}</select></label>
 <div className="inline"><label>目標空きVRAM（GiB）<input aria-label="目標空きVRAM（GiB）" type="number" min="0.1" step="0.5" value={c.target_gib} onChange={e=>update('target_gib',Number(e.target.value))}/></label></div>
 <div className="inline"><label>疎通待ち時間（秒）<input aria-label="疎通待ち時間（秒）" type="number" min="1" max="30" value={c.probe_timeout} onChange={e=>update('probe_timeout',Number(e.target.value))}/></label><label>VRAM変更待ち時間（秒）<input aria-label="VRAM変更待ち時間（秒）" type="number" min="750" max="7200" value={c.change_timeout} onChange={e=>update('change_timeout',Number(e.target.value))}/></label></div>
 <small>長いLLM処理の終了待ちに対応するため、VRAM変更は既定900秒で待ちます。</small>
 <p>Strata APIキー: {state?.key_set?'ローカル設定済み':'未設定（任意）'}。必要ならconfig.local.jsonにstrata_api_keyを追加するか、環境変数STRATA_API_KEYで指定してください。</p>
 <details><summary>Strata側の事前設定</summary><p>利用するstrata-&lt;model&gt;.jsonの最上位に <code>"vram_elastic": true</code> を追加し、Strataを手動で再起動してください。アプリはStrata設定を変更せず、Strata・ComfyUIの起動や終了も行いません。</p><p>対象は同じPC・同じ単一NVIDIA GPUです。low-RAMモード等で拒否される場合は理由を表示します。通常運用へ戻す場合は、必要に応じて先に解放してください。</p></details>
 </fieldset>;
}
