import React,{useEffect,useRef,useState} from 'react';
import Canvas from './Canvas';

export default function ImageViewer({asset,onClose}){
 const dialog=useRef(null),close=useRef(onClose);
 const [zoom,setZoom]=useState(0),[pan,setPan]=useState([0,0]);
 close.current=onClose;
 useEffect(()=>{
  const previous=document.activeElement;
  const overflow=document.body.style.overflow;
  document.body.style.overflow='hidden';
  dialog.current.showModal();
  return()=>{document.body.style.overflow=overflow;previous?.focus?.();};
 },[]);
 return <dialog ref={dialog} className="image-viewer" aria-label="結果画像の拡大表示"
  onCancel={e=>{e.preventDefault();close.current();}}
  onClick={e=>{if(e.target===e.currentTarget){const r=e.currentTarget.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)close.current();}}}>
  <div className="viewer-heading"><div><strong>結果画像の拡大表示</strong><small>ホイールで拡大・縮小 ／ ドラッグで移動 ／ Escで閉じる</small></div><button autoFocus onClick={onClose}>閉じる</button></div>
  <Canvas asset={asset} label={asset.kind==='composite'?'局所合成':asset.name} zoom={zoom} setZoom={setZoom} pan={pan} setPan={setPan}/>
 </dialog>;
}
