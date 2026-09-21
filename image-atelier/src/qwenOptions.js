export const QWEN_MODEL='Qwen-Image-2.1';
export const qwenDefaults={qwen_steps:40,qwen_seed:42,qwen_offload:'model',qwen_tile:512,qwen_stride:384};
export function qwenProblem(p){
 if(p.model!==QWEN_MODEL)return '';
 if(!['generate','polish'].includes(p.mode))return 'Qwenの部分修正は未対応です。新規生成またはブラッシュアップを選んでください。';
 if([p.width,p.height].some(v=>!Number.isInteger(v)||v<128||v>2048||v%32))return 'Qwenの出力寸法は各辺128〜2048px・32の倍数で指定してください。';
 if(!Number.isInteger(p.qwen_steps)||p.qwen_steps<1||p.qwen_steps>50)return 'ステップ数は1〜50です。';
 if(!Number.isInteger(p.qwen_seed)||p.qwen_seed<0||p.qwen_seed>4294967295)return 'シードは0〜4294967295の整数です。';
 if(!['model','sequential'].includes(p.qwen_offload))return 'オフロード設定が不正です。';
 if(!Number.isInteger(p.qwen_tile)||p.qwen_tile<128||p.qwen_tile>1024||p.qwen_tile%32||!Number.isInteger(p.qwen_stride)||p.qwen_stride<=0||p.qwen_stride>=p.qwen_tile||p.qwen_stride%32)return 'タイルは128〜1024、strideはタイル未満、両方32の倍数です。';
 return '';
}
