import React from 'react';
const fields=[['output_t2i','T2I保存先'],['output_i2i','I2I／Inpaint保存先'],['output_upscale','アップスケール保存先']];
export default function OutputFolders({value,onChange}){
 return <fieldset><legend>出力画像の保存先</legend>{fields.map(([key,label])=><label className="field" key={key}>{label}<input aria-label={label} value={value[key]??value.output??''} onChange={e=>onChange({...value,[key]:e.target.value})}/></label>)}<small>絶対パスで指定してください。新規生成（HiresFixを含む）、編集・部分修正、アップスケールをそれぞれの保存先へ書き出します。</small></fieldset>;
}
