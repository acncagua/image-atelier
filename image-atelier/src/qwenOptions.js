import {localSpec,isLocalModel,localOutputSize,sdxlHiresEnabled} from './localModels.js';
export const QWEN_MODEL='Qwen-Image-2.1';
export const qwenDefaults={qwen_timing:false,qwen_cfg:1.0,qwen_negative:'',qwen_steps:40,qwen_seed:42,qwen_offload:'model',qwen_tile:512,qwen_stride:384};
export function qwenProblem(p,target=null){
 if(!isLocalModel(p.model))return '';
 const sdxl=localSpec(p.model).family==='sdxl';
 if(!Number.isFinite(p.qwen_cfg??1)|| (p.qwen_cfg??1)<1 || (p.qwen_cfg??1)>(sdxl?30:5))return sdxl?'CFGは1〜30で指定してください。':'CFGは1〜5で指定してください。';
 if(!['generate','polish','inpaint'].includes(p.mode))return 'ローカルモデルのモードが不正です。';
 if(!sdxl&&p.mode==='inpaint'&&target&&(p.width!==target.width||p.height!==target.height))return '部分修正は元画像と同じ出力寸法で実行してください。';
 if([p.width,p.height].some(v=>!Number.isInteger(v)||v<128||v>2048||v%32))return 'ローカルモデルの出力寸法は各辺128〜2048px・32の倍数で指定してください。';
 if(!Number.isInteger(p.qwen_steps)||p.qwen_steps<1||p.qwen_steps>(sdxl?150:50))return sdxl?'ステップ数は1〜150です。':'ステップ数は1〜50です。';
 if(sdxl){
  if(!Number.isFinite(p.denoise)||p.denoise<0||p.denoise>1)return 'デノイズ強度は0〜1です。';
  if(!Number.isInteger(p.clip_skip)||p.clip_skip<1||p.clip_skip>24)return 'CLIP skipは1〜24です。';
  if(!['whole','masked'].includes(p.inpaint_area))return 'Inpaint areaが不正です。';
  if(!Number.isInteger(p.inpaint_padding)||p.inpaint_padding<0||p.inpaint_padding>256)return 'マスク周辺の余白は0〜256pxです。';
  if(p.mode==='generate'){
   if(!Number.isInteger(p.hires_steps)||p.hires_steps<0||p.hires_steps>150)return 'Hiresのステップ数は0〜150です。';
   if(!Number.isFinite(p.hires_scale)||p.hires_scale<=1||p.hires_scale>4)return 'Hires倍率は1より大きく4以下です。';
   if(sdxlHiresEnabled(p)&&Math.max(...localOutputSize(p))>4096)return 'Hires後の寸法は各辺4096px以下です。';
  }
 }
 if(!Number.isInteger(p.qwen_seed)||p.qwen_seed< -1||p.qwen_seed>4294967295)return 'シードは-1（毎回ランダム）または0〜4294967295の整数です。';
 if(!['model','sequential'].includes(p.qwen_offload))return 'オフロード設定が不正です。';
 if(!Number.isInteger(p.qwen_tile)||p.qwen_tile<128||p.qwen_tile>1024||p.qwen_tile%32||!Number.isInteger(p.qwen_stride)||p.qwen_stride<=0||p.qwen_stride>=p.qwen_tile||p.qwen_stride%32)return 'タイルは128〜1024、strideはタイル未満、両方32の倍数です。';
 return '';
}

export function referenceLimit(p){const spec=localSpec(p.model);return spec?(spec.references?spec.max_images-(p.mode==='generate'?0:1)-(p.mode==='inpaint'&&spec.family==='qwen'?1:0):0):(p.mode==='generate'?8:7);}
