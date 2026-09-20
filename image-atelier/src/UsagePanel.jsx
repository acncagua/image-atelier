import React from 'react';
import {forecastCost} from './usageData';
export default function UsagePanel({usage,jobs,p,error}){
 const forecast=forecastCost(jobs,p);
 return <div className="usage-panel">
  <strong>AtelierのAPI利用額</strong>
  {usage?<><div className="usage-totals">{[['day','今日'],['month','今月'],['all','累計']].map(([key,label])=><div key={key}><span>{label}</span><b>${usage.totals[key].estimate.toFixed(4)}</b><small>料金不明 {usage.totals[key].unknown}件 · 処理待ち／中 {usage.totals[key].pending}件</small></div>)}</div>
  <small>使用量から計算した概算です。OpenAIの残高・確定請求額ではありません。登録日基準・日本時間。</small>
  {p.provider==='openai'&&p.mode!=='upscale'?<p>今回の予想（{p.n}枚）：{forecast?`約 $${forecast.amount.toFixed(4)}（同じモード・モデル・品質・寸法の直近${forecast.samples}件から）`:'不明（比較できる履歴なし）'}<small>参照画像や指示文で変わるため、上限保証ではありません。</small></p>:null}
  <p>制限：{{off:'なし（記録のみ）',notify:'通知のみ・生成は停止しません',stop:'上限で停止'}[usage.mode]}</p>
  {usage.notice?<p className="warning" role="status">{({day:'今日',month:'今月',all:'累計'})[usage.period]}の概算利用額が通知額 ${usage.threshold.toFixed(2)} に達しました。生成は続けられます。</p>:null}
  {usage.mode==='stop'?<p>Atelierの{({day:'本日',month:'今月',all:'累計'})[usage.period]}上限：${usage.threshold.toFixed(2)}<small>判定額 ${usage.limit_accounted.toFixed(4)}（概算＋未精算分の仮計上 ${usage.pending_allowance.toFixed(4)}）。実料金の上限は保証できません。</small></p>:null}</>:<p>{error||'料金集計を確認中…'}</p>}
  {usage&&error?<p className="warning">{error} 表示は直前の取得値です。</p>:null}
  <a href="https://platform.openai.com/settings/organization/billing/overview" target="_blank" rel="noreferrer">OpenAIの実残高を確認</a> · <a href="https://platform.openai.com/usage" target="_blank" rel="noreferrer">公式Usage</a>
 </div>;
}
