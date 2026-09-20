export const upscaleDefaults={mode:'factor',factor:2,width:3840,height:2176,fit:'pad',lock:true,tile:192,overlap:8};
export function restoreUpscaleOptions(saved){
 const next={...upscaleDefaults};
 for(const key of Object.keys(next))if(saved&&typeof saved[key]===typeof next[key]&&(typeof next[key]!=='number'||Number.isFinite(saved[key])))next[key]=saved[key];
 return next;
}
export function upscalePlan(width,height,p,limits){
 if(!Number.isInteger(width)||!Number.isInteger(height)||Math.min(width,height)<=0)throw Error('入力寸法が不正です。');
 if(Math.max(width,height)>limits.max_input_edge||width*height>limits.max_input_pixels)throw Error('入力は各辺2048px以下・4,194,304画素以下です。');
 let w,h;
 if(p.mode==='factor'){
  if(!Number.isFinite(p.factor)||p.factor<=1||p.factor>4)throw Error('倍率は1倍超〜4倍で指定してください。');
  w=Math.floor(width*p.factor+.5);h=Math.floor(height*p.factor+.5);
 }else if(p.mode==='size'){
  w=p.width;h=p.height;
  if(!Number.isInteger(w)||!Number.isInteger(h)||Math.min(w,h)<=0)throw Error('解像度は正の整数です。');
  if(w<=width&&h<=height)throw Error('縮小のみは通常リサイズを使ってください。');
  if(w>width*4||h>height*4)throw Error('各辺は元画像の4倍以下です。');
 }else throw Error('指定方式が不正です。');
 if(!['pad','crop','stretch'].includes(p.fit))throw Error('縦横比の処理方法が不正です。');
 if(w*h>limits.max_output_pixels||Math.max(w,h)>limits.max_output_edge)throw Error('出力上限を超えています。');
 if(!Number.isInteger(p.tile)||p.tile<limits.tile_min||p.tile>limits.tile_max||p.tile%limits.tile_multiple)throw Error('タイルは32〜512px、8の倍数です。');
 if(!Number.isInteger(p.overlap)||p.overlap<0||p.overlap>=p.tile)throw Error('重なりは0以上・タイル未満の整数です。');
 const tiles=(Math.ceil(Math.max(0,width-p.tile)/(p.tile-p.overlap))+1)*(Math.ceil(Math.max(0,height-p.tile)/(p.tile-p.overlap))+1);
 if(tiles>limits.max_tiles)throw Error('タイル数が多すぎます。重なりを減らしてください。');
 return {width:w,height:h,nativeWidth:width*4,nativeHeight:height*4,mismatch:w*height!==h*width};
}
