export const instructionPresets={
 change:[
  ['hair-opaque','髪を不透明にする','髪は全て透過せず、下位レイヤーを描写しない\n髪の不透明度は100％とする\n陰影は細い線による描き込みではなく、毛束の立体に沿った「滑らかな色面」で表現する。\nまだら模様や点描などで断続、独立した精細かつ複雑な影を表現しない。\n黒、濃い灰色による髪影にはしない。'],
  ['polish','全体ブラッシュアップ','元絵の特徴を保ちながら、輪郭線の乱れ、描写の不整合、不自然な陰影を整えてください。細部や模様を必要以上に追加せず、自然に仕上げてください。'],
  ['face-style','顔の画風を合わせる','顔の線の描き方、陰影、瞳の塗り、肌の質感を画風参照画像に合わせてください。参照人物の顔そのものへ置き換えず、編集対象の顔の形状と表情を保ってください。'],
  ['face-shape','基準の顔立ちへ合わせる','顔基準画像をもとに、目の形と間隔、鼻・口の位置、顎と輪郭、顔の縦横比を調整してください。顔の向きと表情は編集対象に合わせ、塗りと照明は周囲になじませてください。'],
  ['expression','表情だけ変更','表情を【穏やかな微笑み】に変更してください。表情に必要な目元・眉・口元だけを調整し、同じ人物の自然な表情として描いてください。','表情'],
  ['part','指定部位の修正','【左手の指】の形状とつながりを自然に修正してください。周囲の線、色、陰影、質感になじませてください。','修正する部位'],
  ['outfit','衣装・装飾の修正','【左胸の薬師章】を指定の資料に合わせて修正してください。装着位置、遠近、布との重なり、照明は編集対象に合わせてください。','修正する衣装・装飾'],
  ['texture','布・肌のざらつきを整理','【衣服】に生じた不自然な粒状感、細かな斑点、過剰な線や陰影を整理してください。本来の縫い目、布目、大きなしわは残し、滑らかさと必要な質感を両立してください。','質感を整理する箇所'],
  ['background','背景だけ変更','背景を【雨の見える室内】に変更してください。人物との境界を自然に処理してください。','変更後の背景'],
 ],
 keep:[
  ['face','顔立ちを維持','編集対象の目の形と間隔、眉、鼻・口の位置、顎、輪郭、顔の縦横比、年齢感を維持してください。目を大きくする、顎を細くするなどの美化による変更はしないでください。'],
  ['expression','表情・視線を維持','編集対象の表情、視線、まぶたの開き、口の開閉、顔の向きと傾きを維持してください。'],
  ['body','体型・ポーズを維持','体型、頭身、肩幅、胸の大きさと形、腰の位置、手足の長さ、姿勢とポーズを維持してください。'],
  ['outfit','衣装・装飾を維持','衣装の形、襟と胸元、袖、丈、縫い目、配色、装飾の数と配置を維持してください。新しい模様や装飾を追加しないでください。'],
  ['color','色・照明を維持','編集対象の肌色、髪色、衣装色、全体の色温度、光の方向、明暗の関係を維持してください。彩度やコントラストを一律に強めないでください。'],
  ['style','画風・質感を維持','編集対象の線の太さと柔らかさ、塗りの密度、肌の滑らかさ、布の質感を維持してください。写実化や過剰な細密化をしないでください。'],
  ['composition','構図・背景を維持','人物の位置と大きさ、画角、余白、背景の物体配置、文字の内容と配置を維持してください。'],
  ['outside','指定箇所以外を維持','変更対象として指定した箇所以外は、形状・色・質感・配置を維持してください。周辺への調整は、修正箇所をなじませるために必要な範囲に限定してください。'],
 ]
};

export function presetText(preset,values={}){
 return preset[2].replace(/【([^】]+)】/g,(_,fallback)=>`【${values[preset[0]]??fallback}】`);
}
export function composeInstructions(kind,editor){
 return [...editor.selected.map(id=>instructionPresets[kind].find(p=>p[0]===id)).filter(Boolean).map(p=>presetText(p,editor.values)),editor.extra].filter(x=>x!=='').join('\n');
}
export function parseInstructions(kind,text=''){
 const editor={selected:[],values:{},extra:''};const extra=[];
 const lines=text.split('\n');
 for(let index=0;index<lines.length;index++){
  const line=lines[index];
  let matched=false;
  for(const preset of instructionPresets[kind]){
   if(editor.selected.includes(preset[0]))continue;
   const slot=preset[2].match(/【([^】]+)】/);
   if(!slot){
    const count=preset[2].split('\n').length;
    if(lines.slice(index,index+count).join('\n')===preset[2]){editor.selected.push(preset[0]);index+=count-1;matched=true;break;}
   }
   if(slot){
    const [before,after]=preset[2].split(slot[0]);
    if(line.startsWith(before+'【')&&line.endsWith('】'+after)){
     editor.values[preset[0]]=line.slice(before.length+1,line.length-after.length-1);
     editor.selected.push(preset[0]);matched=true;break;
    }
   }
  }
  if(!matched)extra.push(line);
 }
 editor.extra=extra.join('\n');
 // Preserve arbitrary historical text byte-for-byte when it cannot be split
 // without changing its order (for example hand-written text before a preset).
 return composeInstructions(kind,editor)===text?editor:{selected:[],values:{},extra:text};
}
export function restoreInstructions(kind,text,saved){
 if(saved&&Array.isArray(saved.selected)&&new Set(saved.selected).size===saved.selected.length&&saved.selected.every(id=>instructionPresets[kind].some(p=>p[0]===id))&&saved.values&&typeof saved.values==='object'&&Object.values(saved.values).every(x=>typeof x==='string')&&typeof saved.extra==='string'){
  if(composeInstructions(kind,saved)===text)return {selected:[...saved.selected],values:{...saved.values},extra:saved.extra};
 }
 return parseInstructions(kind,text);
}
