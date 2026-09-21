export const QWEN_MODEL='Qwen-Image-2.1';
export const qwenDefaults={qwen_steps:40,qwen_seed:42,qwen_offload:'model',qwen_tile:512,qwen_stride:384};
export function qwenProblem(p,target=null){
 if(p.model!==QWEN_MODEL)return '';
 if(!['generate','polish','inpaint'].includes(p.mode))return 'Qwenのモードが不正です。';
 if(p.mode==='inpaint'&&target&&(p.width!==target.width||p.height!==target.height))return 'Qwen部分修正は元画像と同じ出力寸法で実行してください。';
 if([p.width,p.height].some(v=>!Number.isInteger(v)||v<128||v>2048||v%32))return 'Qwenの出力寸法は各辺128〜2048px・32の倍数で指定してください。';
 if(!Number.isInteger(p.qwen_steps)||p.qwen_steps<1||p.qwen_steps>50)return 'ステップ数は1〜50です。';
 if(!Number.isInteger(p.qwen_seed)||p.qwen_seed< -1||p.qwen_seed>4294967295)return 'シードは-1（毎回ランダム）または0〜4294967295の整数です。';
 if(!['model','sequential'].includes(p.qwen_offload))return 'オフロード設定が不正です。';
 if(!Number.isInteger(p.qwen_tile)||p.qwen_tile<128||p.qwen_tile>1024||p.qwen_tile%32||!Number.isInteger(p.qwen_stride)||p.qwen_stride<=0||p.qwen_stride>=p.qwen_tile||p.qwen_stride%32)return 'タイルは128〜1024、strideはタイル未満、両方32の倍数です。';
 return '';
}

export function referenceLimit(p){return p.model===QWEN_MODEL?(p.mode==='generate'?10:p.mode==='inpaint'?8:9):(p.mode==='generate'?8:7);}
