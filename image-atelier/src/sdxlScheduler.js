import defaults from '../sdxl_scheduler_defaults.json' with {type:'json'};

export function resolveSdxlScheduler(sampler,scheduler='automatic'){
 return scheduler==='automatic'?(defaults[sampler]||'normal'):scheduler;
}
