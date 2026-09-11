// Keep recoverable text while rejecting shapes that would break the editor.
export function recoverDraft(saved,defaults){
 const warnings=[];
 if(!saved||typeof saved!=='object')return {payload:{p:{...defaults}},warnings:['不正な下書きです。原データを保護しました。']};
 const payload={...saved,p:{...defaults}};
 for(const key of Object.keys(defaults)){
  const value=saved.p?.[key];if(value===undefined)continue;
  if(typeof value!==typeof defaults[key]||(typeof value==='number'&&!Number.isFinite(value))){warnings.push('不正な設定 '+key+' を既定値で表示します。');continue;}
  payload.p[key]=value;
 }
 for(const [key,allowed] of Object.entries({mode:['generate','polish','inpaint'],provider:['mock','openai'],model:['gpt-image-2.5-sunburst','gpt-image-2.5-flare'],quality:['low','medium','high','xhigh','max','auto'],format:['png','jpeg','webp']})){
  if(!allowed.includes(payload.p[key])){warnings.push('未対応の設定 '+key+' を原データに保持しました。');payload.p[key]=defaults[key];}
 }
 const validStroke=s=>s&&Number.isFinite(s.width)&&s.width>0&&Array.isArray(s.points)&&s.points.every(p=>Array.isArray(p)&&p.length===2&&p.every(Number.isFinite));
 for(const key of ['strokes','redo']){
  const values=Array.isArray(saved[key])?saved[key]:[];
  payload[key]=values.filter(validStroke);
  if(!Array.isArray(saved[key])||payload[key].length!==values.length)warnings.push('不正・旧形式の '+key+' を原データに保持しました。');
 }
 payload.refs=(Array.isArray(saved.refs)?saved.refs:[]).filter(r=>r&&typeof r.id==='string'&&['face','body','style','outfit'].includes(r.role)).map(r=>({...r,person:typeof r.person==='string'?r.person:''}));
 if(payload.refs.length!==(saved.refs?.length||0))warnings.push('不正な参照資料を原データに保持しました。');
 payload.pan=Array.isArray(saved.pan)&&saved.pan.length===2&&saved.pan.every(Number.isFinite)?saved.pan:[0,0];
 payload.zoom=Number.isFinite(saved.zoom)?saved.zoom:0;
 payload.prompt=typeof saved.prompt==='string'?saved.prompt:null;
 payload.promptArchive=Array.isArray(saved.promptArchive)?saved.promptArchive.filter(x=>typeof x==='string'):[];
 return {payload,warnings};
}

export async function resolveDraftAssets(saved,load){
 const warnings=[];
 const get=async id=>{if(!id)return null;try{return await load(id);}catch{warnings.push('欠損画像 '+id+' は表示できません。下書きの原データは保護しました。');return null;}};
 const [base,out]=await Promise.all([get(saved.targetId),get(saved.resultId)]);
 for(const ref of saved.refs||[])await get(ref.id);
 return {base,out,warnings};
}
