import React from 'react';
import {sketchEnabled} from './sketch';

export default function DrawingControls({p,change,target,strokes,setStrokes,redo,setRedo,tool,setTool,brush,setBrush,supported=false}){
 const capable=supported&&p.model==='SDXL'&&['polish','inpaint'].includes(p.mode),sketch=capable&&sketchEnabled(p);
 if(p.mode!=='inpaint'&&!capable)return null;
 return <div className="mask-toolbar">
 {capable?<label><input type="checkbox" disabled={!target} checked={sketch} onChange={e=>{change('sketch',e.target.checked);setTool(e.target.checked?'brush':'pan');}}/>{p.mode==='inpaint'?'Inpaint Sketch（色で誘導）':'Sketch（色で誘導）'}</label>:null}
 {p.mode==='inpaint'||sketch?<>
 {['pan','brush','erase'].map((value,i)=><button key={value} className={tool===value?'selected':''} onClick={()=>setTool(value)}>{['移動','ブラシ','消しゴム'][i]}</button>)}
 <label>ブラシ径 <input aria-label="ブラシ径" type="range" min="2" max="300" value={brush} onChange={e=>setBrush(e.target.value)}/>{brush}px</label>
 <button disabled={!strokes.length} onClick={()=>{setRedo([...redo,strokes.at(-1)]);setStrokes(strokes.slice(0,-1));}}>取り消し</button>
 <button disabled={!redo.length} onClick={()=>{setStrokes([...strokes,redo.at(-1)]);setRedo(redo.slice(0,-1));}}>やり直し</button>
 <button onClick={()=>{setStrokes([]);setRedo([]);}}>全消去</button>
 </>:null}
 {sketch?<><label>描画色 <input aria-label="描画色" type="color" value={p.sketch_color} onChange={e=>change('sketch_color',e.target.value)}/></label>
 <label>描画の不透明度 <input aria-label="描画の不透明度" type="range" min="0.05" max="1" step="0.05" value={p.sketch_opacity} onChange={e=>change('sketch_opacity',Number(e.target.value))}/>{Math.round(p.sketch_opacity*100)}%</label>
 <small>元絵に描いた色を手がかりに生成します。{p.mode==='inpaint'?'塗った範囲だけを修正します。':'ブラッシュアップは画像全体を再生成します。'}デノイズ強度を下げると描いた色・形を残しやすくなります。消しゴムは描画を消して元絵を戻します。</small></>:capable&&p.mode==='polish'?<small>SketchをONにすると、元絵に色を描いてブラッシュアップできます。</small>:null}
 {p.mode==='inpaint'?<><label><input type="checkbox" checked={p.composite} onChange={e=>change('composite',e.target.checked)}/>変更範囲だけ合成</label>
 <label>境界ぼかし <input aria-label="境界ぼかし" type="number" min="0" max="100" value={p.feather} onChange={e=>change('feather',Number(e.target.value))}/>px</label>
 <small>{sketch?'描画した範囲が修正マスクになります。不透明度は色の混ざり方に使い、修正範囲は変わりません。':'塗った場所＝変更範囲。'}範囲外の画素を保護するには「変更範囲だけ合成」を選んでください。</small></>:null}
 </div>;
}
