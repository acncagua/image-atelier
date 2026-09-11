let token='';
export async function api(path, body){
  const response=await fetch('/api/'+path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json','X-Atelier-Token':token},body:body===undefined?undefined:JSON.stringify(body)});
  const result=await response.json();
  if(!response.ok)throw new Error(result.detail||'処理に失敗しました');
  return result;
}
export async function bootstrap(){const data=await api('bootstrap');token=data.token;return data;}
export const imageURL=id=>'/api/assets/'+id+'/image';
export async function upload(file){
 if(file.size>20000000)throw new Error('画像は20MB以下にしてください。');
 const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(file);});
 return api('assets',{name:file.name,data});
}
