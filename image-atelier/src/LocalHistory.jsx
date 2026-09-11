import React from 'react';
import {imageURL} from './api';
export default function LocalHistory({edits,onResult,onEdit}){
 return <section className="history"><h2>ローカル編集の履歴 <small>無料・API予算には加算しません</small></h2>
 {!edits.length?<p className="empty">まだローカル編集はありません</p>:<div className="table-scroll"><table><thead><tr><th>日時・方法</th><th>親画像・寸法</th><th>結果</th></tr></thead><tbody>{edits.map(e=><tr key={e.id}><td>{e.created?new Date(e.created).toLocaleString('ja-JP'):'旧画像：日時不明'}<p>{{resize:'リサイズ',crop:'中央切り抜き',pad:'余白追加'}[e.method]||'方法の記録なし'}</p></td><td><small>親: {e.source_id||'不明'}</small>{e.result.width}×{e.result.height}<details><summary>加工情報</summary><pre>{JSON.stringify(e.details,null,2)}</pre></details></td><td><button onClick={()=>onResult(e.result)} aria-label="ローカル編集結果を拡大表示"><img width="80" src={imageURL(e.result.id)} alt="ローカル編集結果"/></button><button onClick={()=>onEdit(e)}>次の編集対象にする</button><a href={'/api/assets/'+e.result.id+'/download'} download>PNGダウンロード</a></td></tr>)}</tbody></table></div>}
 </section>;
}
