import React from 'react';
import {instructionPresets,presetText,composeInstructions} from './instructionPresets';
import TagTextarea from './TagTextarea';

export default function InstructionEditor({kind,value,onChange,title:customTitle,referenceEnabled=false,onReferenceChange,tagCompletion=false}){
 const title=customTitle||(kind==='change'?'変更すること':'維持すること');
 function toggle(id,checked){onChange({...value,selected:checked?[...value.selected,id]:value.selected.filter(x=>x!==id)});}
 return <fieldset className="instruction-editor"><legend>{title}</legend>
  <label className="field">{title}：追加指示（手入力）<TagTextarea enabled={tagCompletion} aria-label={title+'：追加指示（手入力）'} value={value.extra} placeholder="プリセット以外の指示を追加できます" onChange={text=>onChange({...value,extra:text})}/></label>
  <details><summary>プリセットを選択（複数可・{value.selected.length+(referenceEnabled?1:0)}件選択）</summary>
   <div className="instruction-choices">{instructionPresets[kind].map(preset=><label key={preset[0]} title={presetText(preset,value.values)}>
    <input type="checkbox" checked={value.selected.includes(preset[0])} onChange={e=>toggle(preset[0],e.target.checked)}/>{preset[1]}
   </label>)}{kind==='change'&&onReferenceChange?<label><input type="checkbox" checked={referenceEnabled} onChange={e=>onReferenceChange(e.target.checked)}/>リファレンスを参照する</label>:null}</div>
  </details>
  {kind==='change'&&referenceEnabled?<small>「リファレンス（三面図・多方向）」に指定した資料ごとに、同じ人物の複数方向である旨を追加します。実際の画像番号と文面は「送信指示文」で確認できます。該当資料がない場合は追加しません。</small>:null}
  {value.selected.map(id=>{const preset=instructionPresets[kind].find(p=>p[0]===id);const slot=preset[2].match(/【([^】]+)】/);return <div className="instruction-selected" key={id}>
   <div><strong>{preset[1]}</strong><button type="button" aria-label={preset[1]+'を解除'} onClick={()=>toggle(id,false)}>解除</button></div>
   {slot?<label className="field">{preset[3]}<input value={value.values[id]??slot[1]} onChange={e=>onChange({...value,values:{...value.values,[id]:e.target.value}})}/></label>:null}
   <small>{presetText(preset,value.values)}</small>
  </div>;})}
  <details><summary>組み合わせた指示文</summary><pre>{composeInstructions(kind,value)||'未指定'}</pre></details>
 </fieldset>;
}
