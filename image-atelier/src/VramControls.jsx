import React from 'react';
const peerNames={disabled:'連携無効',offline:'未起動',unloaded:'モデル未ロード',loaded:'モデル稼働中',unsupported:'機能非対応・elastic未有効',auth_error:'認証失敗',unknown:'状態不明',unobserved:'未確認'};
const gib=mib=>typeof mib==='number'?(mib/1024).toFixed(2)+' GiB':'取得不可';
export default function VramControls({state,jobBusy,onAction,onRefresh}){
 const busy=!state||state.busy||jobBusy,unknown=!!state?.pending;
 return <section className="vram-controls"><strong>ComfyUI · VRAM管理</strong>
 <p role="status">{state?.message||'VRAM状態を確認中…'}{state?.operation_elapsed!=null?'（経過 '+state.operation_elapsed+'秒）':''}</p>
 <div className="inline"><button disabled={busy||unknown||!state?.config.enabled} onClick={()=>onAction('acquire')}>画像生成用VRAMを確保</button><button disabled={busy||unknown} onClick={()=>onAction('release')}>画像生成用VRAMを解放</button><button disabled={!!state?.busy} onClick={onRefresh}>状態を再確認</button></div>
 <small>目標: {state?.config.target_gib??18} GiB · 実際の空き: {gib(state?.gpu?.free_mib)} · GPU容量: {gib(state?.gpu?.total_mib)}</small>
 <small>Strata: {peerNames[state?.peer?.state]||'未確認'}{state?.peer?.model?' · '+state.peer.model:''}{state?.peer?.version?' · '+state.peer.version:''} · GPU: {state?.gpu?.name||'取得不可'}</small>
 <small>取得時刻: {state?.observed_at?new Date(state.observed_at*1000).toLocaleString('ja-JP'):'未取得'}</small>
 {state?.peer?.message&&state.peer.message!==state.message?<small>{state.peer.message}</small>:null}
 <details><summary>VRAM操作の使い方</summary><p>確保はStrataのexpertキャッシュを縮小して空きを作る操作です。専有領域の予約ではありません。確保→連続生成→手動解放の順で使用します。初回・状態未確認時は先に解放してください。目標との差が1GiB以内なら生成を許可します。実際の空き容量を確認して判定するため、生成条件によってはメモリ不足になる場合があります。</p><p>解放は同じComfyUIサーバーの全モデルに作用します。別アプリのモデルにも影響します。生成・取消後やアプリ終了時にStrataへ自動復帰しません。連携を無効にしても、縮小状態は自動で戻りません。</p></details>
 </section>;
}
