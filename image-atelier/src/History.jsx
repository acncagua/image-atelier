import React from 'react';
import {imageURL} from './api';
const names={queued:'待機中',sending:'送信中・生成中',completed:'完了',failed:'失敗',unknown:'結果不明',cancelled:'待機取消'};
export default function History({jobs,onLoad,onResult,onEdit,onCancel}){
 return <section className="history"><h2>履歴・ジョブ <small>{jobs.length}件</small></h2>
 {jobs.length===0?<div className="empty">まだ履歴がありません<span>画像を編集して、ここに履歴が表示されます</span></div>:
 <div className="table-scroll"><table><thead><tr><th>日時・状態</th><th>指示文</th><th>モデル・寸法</th><th>結果・操作</th></tr></thead>
 <tbody>{jobs.map(j=><tr key={j.id}>
 <td>{new Date(j.created).toLocaleString('ja-JP')}<strong className={'status '+j.status}>{names[j.status]} · {j.params.provider==='mock'?'モック':'実API'}</strong><small>{j.elapsed??(j.started?Math.max(0,Math.floor(Date.now()/1000-j.started)):0)}秒</small></td>
 <td><div className="truncate">{j.params.change||j.params.prompt}</div><small>{j.message}</small>{j.status==='unknown'?<p className="warning">再実行は追加課金の可能性があります。</p>:null}</td>
 <td>{j.params.model.replace('gpt-image-','')} / {j.params.quality}<small>要求 {j.params.width} × {j.params.height}</small><small>費用概算: {j.estimate==null?'不明':'USD '+j.estimate.toFixed(5)} / {j.budget_status==='settled_estimate'?'概算精算':'予約'} ${j.reserved.toFixed(5)}</small></td>
 <td><div className="result-thumbs">{j.outputs.map((o,index)=><div className="history-output" key={o.id}><button onClick={()=>onResult(o)} aria-label={`${o.name}を拡大表示`} title={`${o.name} ${o.width}×${o.height} — クリックで拡大表示`}><img src={imageURL(o.id)} alt={o.name}/></button><small>{o.name}{j.outputs.length>1?` ${index+1}`:''}</small><button onClick={()=>onEdit(j,o)} aria-label={`${o.name}を次の編集対象にする`}>次の編集対象にする</button></div>)}</div>
 <button onClick={()=>onLoad(j)}>条件を復元</button>{j.status==='queued'?<button onClick={()=>onCancel(j.id)}>待機取消</button>:null}
 <details><summary>記録</summary>{j.mask?<a href={'/api/assets/'+j.mask.id+'/download'} download>APIマスクPNG</a>:null}{j.recovery?<p><a href={'/api/jobs/'+j.id+'/recovery'} download>応答データを回収</a></p>:null}{j.outputs.map(o=><p key={o.id}><a href={'/api/assets/'+o.id+'/original'} download>{o.name}の原データ</a></p>)}<pre>{JSON.stringify(j,null,2)}</pre></details></td>
 </tr>)}</tbody></table></div>}</section>;
}
