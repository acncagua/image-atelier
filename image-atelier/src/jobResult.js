// Local recovery states can also be observed between response receipt and save.
// Keep following them so a later completed snapshot still reaches the canvas.
export function followedResult(job){
 if(!job)return {image:null,done:false};
 const image=job.output||job.outputs?.at(-1)||null;
 const done=['failed','cancelled'].includes(job.status)||(job.status==='completed'&&!!image);
 return {image,done};
}

export function elapsedText(job,now=Date.now()){
 if(Number.isFinite(job.elapsed)&&job.elapsed>=0)return job.elapsed+'秒';
 if(['queued','sending'].includes(job.status)&&Number.isFinite(job.started))return Math.max(0,Math.floor(now/1000-job.started))+'秒';
 return '時間未記録';
}
