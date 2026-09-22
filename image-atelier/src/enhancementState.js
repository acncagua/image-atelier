export function enhancementState(job,prompt,matching,appliedId){
 const completed=job?.status==='completed'&&!!job.result;
 const active=!!(completed&&matching&&appliedId===job.id&&prompt===job.result.rewritten_prompt);
 const canApply=!!(completed&&matching&&!active&&prompt===job.params.prompt);
 return {active,canApply,previous:!!(completed&&!active&&!canApply)};
}

export function enhancementVisible(job,prompt,matching,appliedId,resultId){
 const state=enhancementState(job,prompt,matching,appliedId);
 return !!(job?.status==='completed'&&job.result&&!state.previous&&(job.id===resultId||job.id===appliedId));
}
