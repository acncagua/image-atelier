import {useEffect,useRef,useState,useCallback} from 'react';
import {acquireDraft,archiveDraft,readRecord,writeRecord,validateDraft,SCHEMA} from './workspace';

export default function useDraft(snapshot,restore){
 const [ready,setReady]=useState(false),[status,setStatus]=useState('下書きを復元中…'),[id,setId]=useState(null),[notice,setNotice]=useState('');
 const latest=useRef(snapshot),generation=useRef(0),identity=useRef(null),restorer=useRef(restore),timer=useRef(null);
 const restoring=useRef(true);
 latest.current=snapshot;restorer.current=restore;
 const flush=useCallback(async()=>{
  clearTimeout(timer.current);
  if(restoring.current||!identity.current)return;
  const revision=++generation.current;const payload=structuredClone(latest.current);
  setStatus('保存中');
  try{await writeRecord('drafts',{id:identity.current,revision,schema:SCHEMA,updated:Date.now(),payload});if(revision===generation.current)setStatus('保存済み');}
  catch(error){setStatus(error.message);throw error;}
 },[]);
 useEffect(()=>{
  let release=()=>{},alive=true;
  (async()=>{
   try{
    const claim=await acquireDraft();release=claim.release;if(!alive){release();return;}
    const warnings=validateDraft(claim.record);
    if(warnings.length)await archiveDraft(claim.record,'復元前の原データ');
    if(claim.record?.payload)await restorer.current(claim.record.payload,warnings);
    identity.current=claim.id;generation.current=(claim.record?.revision||0)+1;setId(claim.id);
    setNotice(claim.conflict?'別タブの下書きから独立したコピーを作成しました。':'');setStatus('保存済み');setReady(true);
   }catch(error){identity.current=null;restoring.current=true;setReady(false);setStatus(error.message+' 保存済み下書きは削除していません。');}
  })();
  return()=>{alive=false;clearTimeout(timer.current);release();};
 },[]);
 useEffect(()=>{
  if(!ready)return;
  restoring.current=false;
  setStatus('未保存');timer.current=setTimeout(()=>{flush().catch(()=>{});},200);
  return()=>clearTimeout(timer.current);
 },[snapshot,ready,flush]);
 useEffect(()=>{
  const leaving=()=>{flush().catch(()=>{});};
  const hidden=()=>{if(document.visibilityState==='hidden')leaving();};
  window.addEventListener('pagehide',leaving);window.addEventListener('beforeunload',leaving);document.addEventListener('visibilitychange',hidden);
  return()=>{window.removeEventListener('pagehide',leaving);window.removeEventListener('beforeunload',leaving);document.removeEventListener('visibilitychange',hidden);};
 },[flush]);
 return {ready,status,id,flush,notice};
}
