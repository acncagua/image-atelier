import React from 'react';
import {instructionPresets,presetText,composeInstructions} from './instructionPresets';

export default function InstructionEditor({kind,value,onChange,title:customTitle}){
 const title=customTitle||(kind==='change'?'変更すること':'維持すること');
 function toggle(id,checked){onChange({...value,selected:checked?[...value.selected,id]:value.selected.filter(x=>x!==id)});}
 return <fieldset className="instruction-editor"><legend>{title}</legend>
  <label className="field">{title}：追加指示（手入力）<textarea value={value.extra} placeholder="プリセット以外の指示を追加できます" onChange={e=>onChange({...value,extra:e.target.value})}/></label>
  <details><summary>プリセットを選択（複数可・{value.selected.length}件選択）</summary>
   <div className="instruction-choices">{instructionPresets[kind].map(preset=><label key={preset[0]} title={presetText(preset,value.values)}>
    <input type="checkbox" checked={value.selected.includes(preset[0])} onChange={e=>toggle(preset[0],e.target.checked)}/>{preset[1]}
   </label>)}</div>
  </details>
  {value.selected.map(id=>{const preset=instructionPresets[kind].find(p=>p[0]===id);const slot=preset[2].match(/【([^】]+)】/);return <div className="instruction-selected" key={id}>
   <div><strong>{preset[1]}</strong><button type="button" aria-label={preset[1]+'を解除'} onClick={()=>toggle(id,false)}>解除</button></div>
   {slot?<label className="field">{preset[3]}<input value={value.values[id]??slot[1]} onChange={e=>onChange({...value,values:{...value.values,[id]:e.target.value}})}/></label>:null}
   <small>{presetText(preset,value.values)}</small>
  </div>;})}
  <details><summary>組み合わせた指示文</summary><pre>{composeInstructions(kind,value)||'未指定'}</pre></details>
 </fieldset>;
}
