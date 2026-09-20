import React,{useState,useEffect} from 'react';
import History from './History';
import LocalHistory from './LocalHistory';
import {historyPage} from './historyPages';

const tabs=[['generation','生成・編集'],['upscale','アップスケール'],['local','ローカル編集']];
export default function HistoryTabs({mode,jobs,gpuJobs,localEdits,onLocalEdit,...actions}){
 const [tab,setTab]=useState(mode==='upscale'?'upscale':'generation'),[pages,setPages]=useState({});
 useEffect(()=>{const next=mode==='upscale'?'upscale':'generation';setTab(next);setPages(old=>({...old,[next]:1}));},[mode]);
 const groups={generation:jobs,upscale:gpuJobs,local:localEdits};
 const data=historyPage([...groups[tab]].sort((a,b)=>Date.parse(b.created||0)-Date.parse(a.created||0)),pages[tab]||1);
 function move(page){setPages(old=>({...old,[tab]:page}));}
 function pager(){return <nav className="history-pagination" aria-label="履歴のページ切り替え">
  <button disabled={data.page===1} onClick={()=>move(1)}>先頭</button><button disabled={data.page===1} onClick={()=>move(data.page-1)}>前へ</button>
  <span aria-live="polite">{data.start}–{data.end} / {data.total}件 · {data.page} / {data.pages}ページ</span>
  <button disabled={data.page===data.pages} onClick={()=>move(data.page+1)}>次へ</button><button disabled={data.page===data.pages} onClick={()=>move(data.pages)}>最後</button>
 </nav>;}
 return <section className="history history-tabs"><h2>履歴・ジョブ</h2>
  <div role="tablist" aria-label="履歴の種類" className="history-tablist">{tabs.map(([id,label])=><button key={id} role="tab" id={'history-tab-'+id} aria-selected={tab===id} aria-controls={'history-panel-'+id} onClick={()=>setTab(id)}>{label}（{groups[id].length}件）</button>)}</div>
  {pager()}
  <div role="tabpanel" id={'history-panel-'+tab} aria-labelledby={'history-tab-'+tab} key={tab+data.page}>
   {tab==='local'?<LocalHistory embedded edits={data.items} onResult={actions.onResult} onEdit={onLocalEdit}/>:<History embedded jobs={tab==='generation'?data.items:[]} gpuJobs={tab==='upscale'?data.items:[]} {...actions}/>}
  </div>
  {data.pages>1?pager():null}
 </section>;
}
