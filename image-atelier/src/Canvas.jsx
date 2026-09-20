import React,{useRef,useEffect,useState} from 'react';
import {imageURL} from './api';

export function pixelPoint(event,rect,width,height){return [(event.clientX-rect.left)*width/rect.width,(event.clientY-rect.top)*height/rect.height];}

export default function Canvas({asset,strokes=[],setStrokes,tool='pan',brush=40,zoom,setZoom,pan,setPan,onCrop,onOpen,label}){
 const canvas=useRef(null),host=useRef(null),drag=useRef(null),[image,setImage]=useState(null),[fit,setFit]=useState(1),[selection,setSelection]=useState(null);
 useEffect(()=>{setImage(null);if(!asset)return;const i=new Image();i.onload=()=>setImage(i);i.src=imageURL(asset.id);return()=>{i.onload=null;};},[asset?.id]);
 useEffect(()=>{const obs=new ResizeObserver(([e])=>{if(asset)setFit(Math.min((e.contentRect.width-24)/asset.width,(e.contentRect.height-24)/asset.height,1));});if(host.current)obs.observe(host.current);return()=>obs.disconnect();},[asset]);
 const scale=zoom===0?fit:zoom;
 // React's delegated wheel listener may be passive. Attach directly so that
 // image zoom can cancel page scrolling, only inside a populated viewport.
 useEffect(()=>{
  const element=host.current;if(!element||!asset)return;
  const wheel=e=>{e.preventDefault();e.stopPropagation();if(e.deltaY!==0)setZoom(Math.max(.05,Math.min(8,scale*(e.deltaY<0?1.1:.9))));};
  element.addEventListener('wheel',wheel,{passive:false});
  return()=>element.removeEventListener('wheel',wheel);
 },[asset,scale,setZoom]);

 useEffect(()=>{const c=canvas.current;if(!c||!image||!asset)return;c.width=asset.width;c.height=asset.height;const ctx=c.getContext('2d');ctx.drawImage(image,0,0);
  const mask=document.createElement('canvas');mask.width=c.width;mask.height=c.height;const m=mask.getContext('2d');
  for(const s of strokes){m.globalCompositeOperation=s.erase?'destination-out':'source-over';m.strokeStyle='#0ca7b3';m.fillStyle='#0ca7b3';m.lineWidth=s.width;m.lineCap='round';m.lineJoin='round';m.beginPath();s.points.forEach(([x,y],i)=>i?m.lineTo(x,y):m.moveTo(x,y));m.stroke();for(const [x,y]of s.points){m.beginPath();m.arc(x,y,s.width/2,0,Math.PI*2);m.fill();}}
  ctx.globalAlpha=.4;ctx.drawImage(mask,0,0);ctx.globalAlpha=1;
  if(selection){ctx.strokeStyle='#08a3ac';ctx.lineWidth=2/scale;ctx.strokeRect(selection.x,selection.y,selection.w,selection.h);}
 },[image,asset,strokes,selection,scale]);
 function point(e){return pixelPoint(e,canvas.current.getBoundingClientRect(),asset.width,asset.height).map((v,i)=>Math.max(0,Math.min(v,i?asset.height:asset.width)));}
 function down(e){if(!asset||!image)return;e.currentTarget.setPointerCapture(e.pointerId);const pt=point(e);drag.current={onImage:e.target.tagName==='CANVAS',point:pt,screen:[e.clientX,e.clientY],pan:[...pan]};if(tool==='brush'||tool==='erase'){const s={width:+brush,erase:tool==='erase',points:[pt]};drag.current.stroke=s;setStrokes([...strokes,s]);}if(tool==='crop')setSelection({x:pt[0],y:pt[1],w:0,h:0});}
 function move(e){const d=drag.current;if(!d)return;if(tool==='pan'){setPan([d.pan[0]+e.clientX-d.screen[0],d.pan[1]+e.clientY-d.screen[1]]);return;}const pt=point(e);if(d.stroke){d.stroke={...d.stroke,points:[...d.stroke.points,pt]};setStrokes(prev=>[...prev.slice(0,-1),d.stroke]);}if(tool==='crop')setSelection({x:Math.round(Math.min(d.point[0],pt[0])),y:Math.round(Math.min(d.point[1],pt[1])),w:Math.floor(Math.abs(pt[0]-d.point[0])),h:Math.floor(Math.abs(pt[1]-d.point[1]))});}
 function up(e){const d=drag.current;drag.current=null;if(onOpen&&d&&d.onImage&&Math.hypot(e.clientX-d.screen[0],e.clientY-d.screen[1])<4)onOpen();if(tool==='crop'&&selection?.w>0&&selection?.h>0){onCrop(selection);setSelection(null);}}
 return <section className="image-panel"><h2>{label}<span>{asset?`${asset.width} × ${asset.height}`:''}</span></h2><div className={'viewport tool-'+tool} ref={host} onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={()=>{drag.current=null;}}>
 {asset?<canvas ref={canvas} role={onOpen?'button':undefined} tabIndex={onOpen?0:undefined} aria-label={onOpen?'結果画像を大きく表示':undefined} onKeyDown={e=>{if(onOpen&&(e.key==='Enter'||e.key===' ')){e.preventDefault();onOpen();}}} style={{width:asset.width*scale,height:asset.height*scale,transform:`translate(${pan[0]}px,${pan[1]}px)`}}/>:<div className="empty">ここに{label==='元画像'?'元画像':'結果画像'}が表示されます<span>{label==='元画像'?'左のパネルから画像を読み込んでください':'実行後の画像を比較できます'}</span></div>}</div><div className="image-footer"><button onClick={()=>setZoom(Math.max(.05,scale/1.25))}>縮小</button><button onClick={()=>setZoom(Math.min(8,scale*1.25))}>拡大</button><button onClick={()=>{setZoom(0);setPan([0,0]);}}>画面に合わせる</button><button onClick={()=>setZoom(1)}>100%</button><small>{Math.round(scale*100)}%</small></div></section>;
}
