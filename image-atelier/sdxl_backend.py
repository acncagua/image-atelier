"""SDXL checkpoints, two-pass HiresFix and deterministic masked-area recovery."""
import json
import math
from pathlib import Path
from PIL import Image
from imaging import normalize, mask_image, composite, png

DEFAULTS=json.loads(Path(__file__).with_name('sdxl_defaults.json').read_text('utf-8'))
REQUIRED=('CheckpointLoaderSimple','CLIPSetLastLayer','CLIPTextEncodeSDXL','EmptyLatentImage','KSampler','VAEDecode','SaveImage')

def hires_enabled(p):return p.get('mode')=='generate' and p.get('hires_fix',False)

def sampling_size(p):
    scale=p.get('hires_scale',2) if hires_enabled(p) else 1
    return tuple(int(math.floor(p[k]*scale/32+.5))*32 for k in ('width','height'))

def only_masked(p):return p['mode']=='inpaint' and p.get('inpaint_area','whole')=='masked'

def output_size(p):
    if only_masked(p) and p.get('input_snapshots'):
        target=p['input_snapshots'][0]
        return target['width'],target['height']
    return sampling_size(p)

def validate(p):
    for key in ('sampler','scheduler',*(['hires_upscaler'] if p['mode']=='generate' else [])):
        if not isinstance(p[key],str) or not p[key] or len(p[key])>1024:raise ValueError('SDXL設定が不正です: '+key)
    if type(p['hires_fix']) is not bool:raise ValueError('HiresFixはON/OFFで指定してください。')
    for key,low,high in (('clip_skip',1,24),('inpaint_padding',0,256),*([('hires_steps',0,150)] if p['mode']=='generate' else [])):
        if type(p[key]) is not int or not low<=p[key]<=high:raise ValueError('SDXL設定が範囲外です: '+key)
    if p['mode']=='generate' and (type(p['hires_scale']) not in (int,float) or not math.isfinite(p['hires_scale']) or not 1<p['hires_scale']<=4):raise ValueError('Hires倍率は1より大きく4以下です。')
    if p['inpaint_area'] not in ('whole','masked'):raise ValueError('Inpaint areaが不正です。')
    if hires_enabled(p) and max(sampling_size(p))>4096:raise ValueError('Hires後の寸法は各辺4096px以下にしてください。')

def inspect(info):
    def choices(node,key):
        entry=info.get(node,{}).get('input',{}).get('required',{}).get(key,[])
        if entry and isinstance(entry[0],(list,tuple)):return list(entry[0])
        if len(entry)>1 and entry[0]=='COMBO' and isinstance(entry[1],dict):return entry[1].get('options',[])
        return []
    upscalers=[*('latent:'+n for n in choices('LatentUpscale','upscale_method')),
               *('pixel:'+n for n in choices('ImageScale','upscale_method'))]
    if 'ImageUpscaleWithModel' in info:upscalers+=['model:'+n for n in choices('UpscaleModelLoader','model_name')]
    return {'nodes':list(info),'missing':[n for n in REQUIRED if n not in info],
            'checkpoint':choices('CheckpointLoaderSimple','ckpt_name'),'vae':choices('VAELoader','vae_name'),
            'samplers':choices('KSampler','sampler_name'),'schedulers':choices('KSampler','scheduler'),
            'upscalers':upscalers}

def validate_models(available,config,p=None):
    if available['missing']:raise ValueError('SDXLに必要なノードがありません: '+', '.join(available['missing']))
    if config['checkpoint'] not in available['checkpoint']:raise ValueError('SDXLチェックポイントを選択し、一覧を再取得してください。')
    if config.get('vae') and config['vae'] not in available['vae']:raise ValueError('選択したVAEがComfyUIにありません。')
    if p:
        if p['sampler'] not in available['samplers']:raise ValueError('選択サンプラーがComfyUIにありません。')
        if p['scheduler'] not in available['schedulers']:raise ValueError('選択スケジューラーがComfyUIにありません。')
        if hires_enabled(p):
            if p['hires_upscaler'] not in available['upscalers']:raise ValueError('選択HiresアップスケーラーがComfyUIにありません。')
            if config.get('hires_checkpoint') and config['hires_checkpoint'] not in available['checkpoint']:raise ValueError('HiresチェックポイントがComfyUIにありません。')
        graph=workflow(p,config,0,['input.png'] if p['mode']=='polish' else ['input.png','mask.png'] if p['mode']=='inpaint' else [],'validate')
        missing=sorted({n['class_type'] for n in graph.values()}-set(available['nodes']))
        if missing:raise ValueError('この操作に必要なノードがありません: '+', '.join(missing))

def workflow(p,config,seed,images,ident):
    graph={}
    def add(key,kind,**inputs):graph[str(key)]={'class_type':kind,'inputs':inputs};return [str(key),0]
    add(1,'CheckpointLoaderSimple',ckpt_name=config['checkpoint'])
    clip=add(2,'CLIPSetLastLayer',clip=['1',1],stop_at_clip_layer=-p['clip_skip'])
    vae=add(3,'VAELoader',vae_name=config['vae']) if config.get('vae') else ['1',2]
    def encode(key,clip,text,size):
        return add(key,'CLIPTextEncodeSDXL',clip=clip,text_g=text,text_l=text,width=size[0],height=size[1],crop_w=0,crop_h=0,target_width=size[0],target_height=size[1])
    size=p['width'],p['height']
    positive=encode(4,clip,p['prompt'],size);negative=encode(9,clip,p.get('qwen_negative',''),size)
    latent=add(5,'EmptyLatentImage',width=size[0],height=size[1],batch_size=1)
    mask=None
    if images:
        source=add(101,'LoadImage',image=images[0])
        source=add(20,'ImageScale',image=source,upscale_method='lanczos',width=size[0],height=size[1],crop='disabled')
        latent=add(21,'VAEEncode',pixels=source,vae=vae)
        if p['mode']=='inpaint':
            mask_image_link=add(102,'LoadImage',image=images[-1])
            mask_image_link=add(22,'ImageScale',image=mask_image_link,upscale_method='nearest-exact',width=size[0],height=size[1],crop='disabled')
            mask=add(23,'ImageToMask',image=mask_image_link,channel='red')
            latent=add(24,'SetLatentNoiseMask',samples=latent,mask=mask)
    samples=add(6,'KSampler',model=['1',0],positive=positive,negative=negative,latent_image=latent,
                seed=seed,steps=p['qwen_steps'],cfg=p['qwen_cfg'],sampler_name=p['sampler'],scheduler=p['scheduler'],denoise=1.0 if p['mode']=='generate' else p['denoise'])
    pixels=add(7,'VAEDecode',samples=samples,vae=vae)
    if hires_enabled(p):
        final_size=sampling_size(p)
        different=bool(config.get('hires_checkpoint') and config['hires_checkpoint']!=config['checkpoint'])
        hires_model=['1',0];hires_vae=vae;hires_clip=clip
        if different:
            hires_model=add(30,'CheckpointLoaderSimple',ckpt_name=config['hires_checkpoint'])
            hires_clip=add(31,'CLIPSetLastLayer',clip=['30',1],stop_at_clip_layer=-p['clip_skip'])
            if not config.get('vae'):hires_vae=['30',2]
        mode,method=p['hires_upscaler'].split(':',1)
        if mode=='latent':
            if different and not config.get('vae'):samples=add(32,'VAEEncode',pixels=pixels,vae=hires_vae)
            hires_latent=add(33,'LatentUpscale',samples=samples,upscale_method=method,width=final_size[0],height=final_size[1],crop='disabled')
        else:
            if mode=='model':
                upscaler=add(32,'UpscaleModelLoader',model_name=method)
                pixels=add(33,'ImageUpscaleWithModel',upscale_model=upscaler,image=pixels)
                method='lanczos'
            pixels=add(34,'ImageScale',image=pixels,upscale_method=method,width=final_size[0],height=final_size[1],crop='disabled')
            hires_latent=add(35,'VAEEncode',pixels=pixels,vae=hires_vae)
        if mask is not None:
            hires_mask_pixels=add(36,'ImageScale',image=['102',0],upscale_method='nearest-exact',width=final_size[0],height=final_size[1],crop='disabled')
            hires_mask=add(37,'ImageToMask',image=hires_mask_pixels,channel='red')
            hires_latent=add(38,'SetLatentNoiseMask',samples=hires_latent,mask=hires_mask)
        hp=encode(39,hires_clip,p['prompt'],final_size);hn=encode(40,hires_clip,p.get('qwen_negative',''),final_size)
        samples=add(41,'KSampler',model=hires_model,positive=hp,negative=hn,latent_image=hires_latent,seed=seed,steps=p['hires_steps'] or p['qwen_steps'],cfg=p['qwen_cfg'],sampler_name=p['sampler'],scheduler=p['scheduler'],denoise=p['denoise'])
        pixels=add(42,'VAEDecode',samples=samples,vae=hires_vae)
    add(8,'SaveImage',images=pixels,filename_prefix='atelier/'+ident)
    return graph

def region(p,size):
    mask=mask_image(size,p['strokes']);box=mask.getbbox()
    if box is None:raise ValueError('変更範囲が空です。')
    padding=p.get('inpaint_padding',32);w,h=size
    left,top,right,bottom=box
    left=max(0,left-padding);top=max(0,top-padding);right=min(w,right+padding);bottom=min(h,bottom+padding)
    # Expand around the mask to the sampling aspect ratio when the canvas permits it.
    ratio=p['width']/p['height'];cw=right-left;ch=bottom-top
    if cw/ch<ratio:cw=min(w,math.ceil(ch*ratio))
    else:ch=min(h,math.ceil(cw/ratio))
    left=max(0,min(w-cw,math.floor((left+right-cw)/2)));top=max(0,min(h-ch,math.floor((top+bottom-ch)/2)))
    return left,top,left+cw,top+ch

def prepare_inputs(store,p):
    if p['mode']=='generate':return []
    original=normalize(store.file(p['target']).read_bytes())
    mask=mask_image(original.size,p['strokes']) if p['mode']=='inpaint' else None
    if only_masked(p):
        box=region(p,original.size);original=original.crop(box);mask=mask.crop(box)
    size=p['width'],p['height']
    images=[png(original.resize(size,Image.Resampling.LANCZOS))]
    if mask is not None:images.append(png(mask.resize(size,Image.Resampling.NEAREST).convert('RGB')))
    return images

def recover_image(store,p,result):
    if result.size!=sampling_size(p):raise ValueError('SDXLの結果寸法が要求と一致しません。')
    if not only_masked(p):return result.copy()
    original=normalize(store.file(p['target']).read_bytes());box=region(p,original.size)
    patch=result.resize((box[2]-box[0],box[3]-box[1]),Image.Resampling.LANCZOS)
    canvas=original.copy();canvas.paste(patch,box[:2])
    return composite(original,canvas,mask_image(original.size,p['strokes']),p['feather'])

def composite_source(store,p,size):
    original=normalize(store.file(p['target']).read_bytes());mask=mask_image(original.size,p['strokes'])
    if original.size!=size:
        original=original.resize(size,Image.Resampling.LANCZOS);mask=mask.resize(size,Image.Resampling.NEAREST)
    return original,mask
