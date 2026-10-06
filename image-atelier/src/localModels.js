import models from '../local_models.json' with {type:'json'};
import sdxlDefaults from '../sdxl_defaults.json' with {type:'json'};
export const localModels=models;
export const localSpec=model=>models[model];
export const isLocalModel=model=>!!models[model];
export const supportsReferences=model=>!models[model]||models[model].references;
export const localDefaults={...sdxlDefaults,denoise:0.65,guidance:3.5};
export const sdxlHiresEnabled=p=>p.model==='SDXL'&&p.mode==='generate'&&p.hires_fix===true;
export function localOutputSize(p,target=null){
 if(p.model!=='SDXL')return [p.width,p.height];
 if(p.mode==='inpaint'&&p.inpaint_area==='masked'&&target)return [target.width,target.height];
 const scale=sdxlHiresEnabled(p)?p.hires_scale:1;
 return [p.width,p.height].map(n=>Math.floor(n*scale/32+.5)*32);
}
export function selectLocalModel(p,model){
 const spec=localSpec(model);
 return {...p,model,...(spec?{format:'png',n:1,qwen_steps:spec.steps,qwen_cfg:spec.cfg,guidance:spec.guidance??3.5,...(spec.family==='sdxl'?sdxlDefaults:{}),...(p.mode==='inpaint'?{composite:true}:{})}:{})};
}
