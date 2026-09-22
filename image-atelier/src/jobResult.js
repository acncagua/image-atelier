// Local recovery states can also be observed between response receipt and save.
// Keep following them so a later completed snapshot still reaches the canvas.
export function followedResult(job){
 if(!job)return {image:null,done:false};
 const image=job.output||job.outputs?.at(-1)||null;
 const done=['failed','cancelled'].includes(job.status)||(job.status==='completed'&&!!image);
 return {image,done};
}
