import React from 'react';
const stages={initializing:'起動・ライブラリ準備',loading:'モデル読込・VAE設定',configuring_offload:'オフロード設定・入力準備',reading_input:'入力画像読込',inference_start:'プロンプト・参照画像の処理／推論準備',denoising:'ノイズ除去（全ステップ）',decoding:'画像変換・後処理',saving:'PNG保存・環境情報取得',reusing_model:'モデル再利用準備'};
export default function QwenTiming({timing}){
 if(!timing)return null;
 return <details><summary>処理時間の内訳</summary><p>子プロセス内の計測：{(timing.total_ms/1000).toFixed(2)}秒。待機・子プロセス起動・履歴登録／書き出しは含みません。</p>
 <ul>{Object.entries(timing.stages_ms).map(([key,ms])=><li key={key}>{stages[key]||key}: {ms.toFixed(2)} ms</li>)}</ul>
 <p>ステップはTransformerの最初の呼出し直前から計測。CPU処理・データ転送・CFGの両推論を含む経過時間です。</p>
 <table><thead><tr><th>ステップ</th><th>時間（ms）</th></tr></thead><tbody>{timing.steps.map(s=><tr key={s.step}><td>{s.step}</td><td>{s.ms.toFixed(2)}</td></tr>)}</tbody></table></details>;
}
