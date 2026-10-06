"""Selected local models through ComfyUI; never resubmit on ambiguity."""
import base64
import io
import json
import secrets
import time
import uuid
import re
from local_models import MODELS, DEFAULT_MODEL, output_size
from urllib.parse import urlsplit
import httpx
from PIL import Image
from persistence import atomic_write
from imaging import mask_image, png
import sdxl_backend

DEFAULTS={'backend':'comfyui','url':'http://127.0.0.1:8188','diffusion':'','text_encoder':'','vae':'','checkpoint':'','hires_checkpoint':''}
REQUIRED=('UNETLoader','CLIPLoader','VAELoader','TextEncodeQwenImage21','EmptyLatentImage','KSampler','VAEDecode','SaveImage','LoadImage')

def validate_url(value):
    parsed=urlsplit(value)
    if parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','localhost','::1') or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment:
        raise ValueError('ComfyUIは同じPCのhttp://127.0.0.1:ポート番号で指定してください。')
    if not parsed.port:raise ValueError('ComfyUIのポート番号が必要です。')
    return value.rstrip('/')

def configuration(store,model=DEFAULT_MODEL):
    if model not in MODELS:raise ValueError('未対応のローカルモデルです。')
    file=store.path/'comfy-settings.json'
    saved=json.loads(file.read_text('utf-8')) if file.exists() else {}
    profile=saved.get('profiles',{}).get(model,{})
    if model==DEFAULT_MODEL and 'profiles' not in saved:profile=saved
    return {**DEFAULTS,'text_encoder_2':'',**profile,'url':saved.get('url',DEFAULTS['url']),'model':model}

def configure(store,p):
    with store.lock:return _configure(store,p)

def _configure(store,p):
    model=p.get('model',DEFAULT_MODEL)
    old=configuration(store,model)
    keys=('diffusion','text_encoder','text_encoder_2','vae','checkpoint','hires_checkpoint')
    values={**old,**{k:str(p.get(k,old[k])) for k in ('url',*keys)}}
    values['url']=validate_url(values['url'])
    if hasattr(store,'vram'):store.vram.comfy_url_guard(old['url'],values['url'])
    file=store.path/'comfy-settings.json'
    saved=json.loads(file.read_text('utf-8')) if file.exists() else {}
    profiles=saved.get('profiles',{})
    if 'profiles' not in saved and saved:profiles[DEFAULT_MODEL]={k:saved.get(k,'') for k in ('diffusion','text_encoder','vae')}
    profiles[model]={k:values[k] for k in keys}
    atomic_write(file,json.dumps({'url':values['url'],'profiles':profiles}).encode())
    return values

def client(config):
    return httpx.Client(base_url=validate_url(config['url']),timeout=20,trust_env=False,follow_redirects=False)

def get(c,path):
    r=c.get(path);r.raise_for_status();return r.json()

def post(c,path,body):
    r=c.post(path,json=body);r.raise_for_status();return r.json() if r.content else {}

def inspect(c,model=DEFAULT_MODEL):
    info=get(c,'/object_info')
    spec=MODELS[model]
    if spec['family']=='sdxl':return sdxl_backend.inspect(info)
    required=REQUIRED if spec['family']=='qwen' else ('UNETLoader','VAELoader','KSampler','VAEDecode','SaveImage',spec['latent'],'CLIPTextEncode','DualCLIPLoader' if spec['family']=='flux' else 'CLIPLoader')
    missing=[name for name in required if name not in info]
    def choices(node,key):
        return info.get(node,{}).get('input',{}).get('required',{}).get(key,[[]])[0]
    standard=choices('UNETLoader','unet_name');gguf=choices('UnetLoaderGGUF','unet_name')
    def encoder_files(names):return [name for name in names if not name.replace('\\','/').rsplit('/',1)[-1].lower().startswith('mmproj-')]
    standard_te=encoder_files(choices('CLIPLoader','clip_name'));gguf_te=encoder_files(choices('CLIPLoaderGGUF','clip_name'))
    result={'nodes':list(info),'gguf_text_available':'CLIPLoaderGGUF' in info,'standard_text_encoders':standard_te,'gguf_text_encoders':gguf_te,'missing':missing,'gguf_available':'UnetLoaderGGUF' in info,'gguf_diffusion':gguf,'standard_diffusion':standard,'diffusion':list(dict.fromkeys([*standard,*gguf])),
            'text_encoder':list(dict.fromkeys([*standard_te,*gguf_te])),'vae':choices('VAELoader','vae_name')}
    if spec['family']=='flux':
        result['standard_text_encoders']=choices('DualCLIPLoader','clip_name1')
        result['gguf_text_encoders']=choices('DualCLIPLoaderGGUF','clip_name1')
        result['gguf_text_available']='DualCLIPLoaderGGUF' in info
        result['text_encoder']=list(dict.fromkeys(result['standard_text_encoders']+result['gguf_text_encoders']))
    result['text_encoder_2']=result['text_encoder'][:]
    for key in ('diffusion','text_encoder','text_encoder_2','vae'):
        result[key]=[name for name in result[key] if compatible(model,key,name)]
    return result

def compatible(model,key,name):
    name=name.replace('\\','/').rsplit('/',1)[-1].lower()
    compact=re.sub(r'[^a-z0-9]','',name)
    family=MODELS[model]['family']
    if name.startswith('mmproj-'):return False
    if key=='diffusion':
        if family=='anima':return 'anima' in compact
        if family=='qwen':return 'qwen' in compact and ('21' in compact or '2.1' in name)
        variant='kontext' if 'Kontext' in model else 'schnell' if 'schnell' in model else 'dev'
        return 'flux' in compact and variant in compact and not any(x in compact for x in ('flux2','fill','canny','depth')) and (variant=='kontext' or 'kontext' not in compact)
    if key=='vae':
        # v0.38 exposes the paired approximation weights as this virtual VAE name.
        if family=='qwen':return name=='taeqi2_1' or ('qwen' in compact and '21' in compact and 'vae' in compact)
        if family=='anima':return 'qwenimagevae' in compact
        return name=='ae.safetensors' or ('flux' in compact and 'vae' in compact and 'flux2' not in compact)
    if family=='anima':return 'qwen' in compact and ('06b' in compact or '0.6b' in name) and 'vl' not in compact
    if family=='qwen':return 'qwen' in compact and ('vl' in compact or '3vl' in compact)
    return 'clipl' in compact if key=='text_encoder_2' else 't5' in compact


def check(store,model=DEFAULT_MODEL,url=None):
    config=configuration(store,model)
    if url is not None:config['url']=validate_url(url)
    try:
        with client(config) as c:
            result=inspect(c,model)
            result['comfyui_version']=None
            # Version information is optional; node/model discovery remains authoritative.
            try:
                version=get(c,'/system_stats').get('system',{}).get('comfyui_version')
                if isinstance(version,str):result['comfyui_version']=version
            except (httpx.HTTPError,ValueError,AttributeError):pass
            return result
    except httpx.HTTPError as error:raise ValueError('ComfyUIに接続できません。接続先URLとComfyUIの起動状態を確認してください。') from error

def validate_models(c,config,p=None):
    model=config.get('model',DEFAULT_MODEL)
    available=inspect(c,model)
    if MODELS[model]['family']=='sdxl':return sdxl_backend.validate_models(available,config,p)
    if available['missing']:raise ValueError(model+'対応ComfyUIが必要です。不足ノード: '+', '.join(available['missing']))
    is_gguf=config['diffusion'].lower().endswith('.gguf')
    if is_gguf and not available['gguf_available']:raise ValueError('GGUFにはComfyUI-GGUFのUnetLoaderGGUFが必要です。導入後にComfyUIを再起動してください。')
    if config['diffusion'] not in available['gguf_diffusion' if is_gguf else 'standard_diffusion']:raise ValueError('選択形式のローダーにモデルがありません。モデル一覧を再取得してください。')
    te_keys=('text_encoder','text_encoder_2') if MODELS[model]['family']=='flux' else ('text_encoder',)
    te_gguf=any(config.get(k,'').lower().endswith('.gguf') for k in te_keys)
    if te_gguf and not available['gguf_text_available']:raise ValueError('GGUFテキストエンコーダーにはCLIPLoaderGGUFが必要です。')
    for key in te_keys:
        if config.get(key) not in available['gguf_text_encoders' if te_gguf else 'standard_text_encoders']:raise ValueError('選択形式のテキストエンコーダーがありません。mmprojは本体として選択できません。')
    for name in (('diffusion','text_encoder','text_encoder_2','vae') if MODELS[model]['family']=='flux' else ('diffusion','text_encoder','vae')):
        if config.get(name) not in available[name]:raise ValueError('選択モデルに対応するファイルがありません: '+name)
    if p:
        graph=workflow(p,config,0,['input.png']*len(p.get('input_ids',[]))+(['mask.png'] if p['mode']=='inpaint' else []),'validate')
        missing=sorted({n['class_type'] for n in graph.values()}-set(available['nodes']))
        if missing:raise ValueError('この操作に必要なノードがありません: '+', '.join(missing))

def workflow(p,config,seed,images,ident):
    spec=MODELS[config.get('model',DEFAULT_MODEL)]
    if spec['family']=='sdxl':return sdxl_backend.workflow(p,config,seed,images,ident)
    if spec['family']!='qwen':return standard_workflow(p,config,seed,images,ident,spec)
    def node(kind,**inputs):return {'class_type':kind,'inputs':inputs}
    graph={
        '1':node('UnetLoaderGGUF',unet_name=config['diffusion']) if config['diffusion'].lower().endswith('.gguf') else node('UNETLoader',unet_name=config['diffusion'],weight_dtype='default'),
        '2':node('CLIPLoaderGGUF',clip_name=config['text_encoder'],type='qwen_image') if config['text_encoder'].lower().endswith('.gguf') else node('CLIPLoader',clip_name=config['text_encoder'],type='qwen_image',device='default'),
        '3':node('VAELoader',vae_name=config['vae']),
        '4':node('TextEncodeQwenImage21',clip=['2',0],prompt=p['prompt'],negative_prompt=p.get('qwen_negative',''),resolution=1024),
        '5':node('EmptyLatentImage',width=p['width'],height=p['height'],batch_size=1),
        '6':node('KSampler',model=['1',0],positive=['4',0],negative=['4',1],latent_image=['5',0],seed=seed,steps=p['qwen_steps'],cfg=p['qwen_cfg'],sampler_name='euler',scheduler='simple',denoise=1.0),
        '7':node('VAEDecode',samples=['6',0],vae=['3',0]),
        '8':node('SaveImage',images=['7',0],filename_prefix='atelier/'+ident),
    }
    if images:graph['4']['inputs']['vae']=['3',0]
    for index,name in enumerate(images,1):
        key=str(100+index);graph[key]=node('LoadImage',image=name)
        graph['4']['inputs'][f'images.image_{index}']=[key,0]
    return graph

def standard_workflow(p,config,seed,images,ident,spec):
    def node(kind,**inputs):return {'class_type':kind,'inputs':inputs}
    graph={
        '1':node('UnetLoaderGGUF',unet_name=config['diffusion']) if config['diffusion'].lower().endswith('.gguf') else node('UNETLoader',unet_name=config['diffusion'],weight_dtype='default'),
        '3':node('VAELoader',vae_name=config['vae']),
        '4':node('CLIPTextEncode',clip=['2',0],text=p['prompt']),
        '9':node('CLIPTextEncode',clip=['2',0],text=p.get('qwen_negative','')),
        '5':node(spec['latent'],width=p['width'],height=p['height'],batch_size=1),
        '6':node('KSampler',model=['1',0],positive=['4',0],negative=['9',0],latent_image=['5',0],seed=seed,steps=p['qwen_steps'],cfg=p['qwen_cfg'],sampler_name='euler',scheduler='simple',denoise=1.0),
        '7':node('VAEDecode',samples=['6',0],vae=['3',0]),
        '8':node('SaveImage',images=['7',0],filename_prefix='atelier/'+ident),
    }
    if spec['family']=='flux':
        gguf=any(config[k].lower().endswith('.gguf') for k in ('text_encoder','text_encoder_2'))
        graph['2']=node('DualCLIPLoaderGGUF' if gguf else 'DualCLIPLoader',clip_name1=config['text_encoder'],clip_name2=config['text_encoder_2'],type='flux',**({} if gguf else {'device':'default'}))
        graph['10']=node('FluxGuidance',conditioning=['4',0],guidance=p.get('guidance',spec['guidance']))
        graph['6']['inputs']['positive']=['10',0]
    else:
        gguf=config['text_encoder'].lower().endswith('.gguf')
        graph['2']=node('CLIPLoaderGGUF' if gguf else 'CLIPLoader',clip_name=config['text_encoder'],type=spec['clip_type'],**({} if gguf else {'device':'default'}))
    for index,name in enumerate(images):graph[str(101+index)]=node('LoadImage',image=name)
    if images:
        # Normalize the source to the explicit output size for img2img/inpaint.
        graph['20']=node('ImageScale',image=['101',0],upscale_method='lanczos',width=p['width'],height=p['height'],crop='disabled')
        graph['21']=node('VAEEncode',pixels=['20',0],vae=['3',0])
        if spec['references']:
            graph['22']=node('ReferenceLatent',conditioning=graph['6']['inputs']['positive'],latent=['21',0])
            graph['6']['inputs']['positive']=['22',0]
        if p['mode']!='generate':
            graph['6']['inputs'].update(latent_image=['21',0],denoise=p.get('denoise',0.65))
        if p['mode']=='inpaint':
            graph['23']=node('ImageToMask',image=[str(100+len(images)),0],channel='red')
            graph['24']=node('SetLatentNoiseMask',samples=['21',0],mask=['23',0])
            graph['6']['inputs']['latent_image']=['24',0]
    return graph

def queue_ids(c):
    q=get(c,'/queue')
    return [x[1] for x in q.get('queue_running',[])],[x[1] for x in q.get('queue_pending',[])]

def cancel_remote(c,ident):
    post(c,'/queue',{'delete':[ident]})
    post(c,'/interrupt',{'prompt_id':ident})
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        running,pending=queue_ids(c)
        if ident not in running+pending:return
        time.sleep(.2)
    raise RuntimeError('ComfyUIの取消完了を確認できません。')

def free(c):
    running,pending=queue_ids(c)
    if running or pending:return False
    post(c,'/free',{'unload_models':True,'free_memory':True});return True

def confirmed_free(c,operation,save,timeout=60):
    """0.38.0 processes free flags AFTER task_done. Two later tasks prove completion.

    EmptyImage -> PreviewImage uses no model. The first marker also identifies the
    server's in-memory history before the free request, for uncertain-result recovery.
    """
    stats=get(c,'/system_stats')
    if stats.get('system',{}).get('comfyui_version')!='0.38.0':raise ValueError('このComfyUI版では解放完了の確認方式を検証していません。対応版は0.38.0です。')
    info=get(c,'/object_info')
    if not {'EmptyImage','PreviewImage'}<=set(info):raise ValueError('解放確認に必要な標準ノードがありません。')
    deadline=time.monotonic()+timeout
    def marker():
        ident=str(uuid.uuid4());operation.setdefault('markers',[]).append(ident);save()
        graph={'1':{'class_type':'EmptyImage','inputs':{'width':1,'height':1,'batch_size':1,'color':0}},'2':{'class_type':'PreviewImage','inputs':{'images':['1',0]}}}
        r=post(c,'/prompt',{'prompt':graph,'prompt_id':ident,'client_id':'atelier-vram-confirm'})
        if r.get('prompt_id')!=ident:raise RuntimeError('解放確認ジョブの応答を確認できません。')
        while time.monotonic()<deadline:
            running,pending=queue_ids(c)
            if set(running+pending)-{ident}:raise ValueError('別クライアントのジョブが登録されました。Strata復帰を停止します。')
            record=get(c,'/history/'+ident).get(ident,{})
            if record.get('status',{}).get('status_str')=='error':raise RuntimeError('ComfyUI解放確認ジョブが失敗しました。')
            if record.get('status',{}).get('completed'):return ident
            time.sleep(.2)
        raise TimeoutError('ComfyUIの解放完了を確認できません。')
    if not operation.get('anchor'):operation['anchor']=marker();save()
    if not operation.get('free_accepted'):
        operation['free_submitted']=True;save()
        if not free(c):raise ValueError('ComfyUIのキューが空ではありません。')
        operation['free_accepted']=True;save()
    while len(operation.setdefault('after_free',[]))<2:
        running,pending=queue_ids(c)
        if running or pending:raise ValueError('ComfyUIのキューが空ではありません。')
        ident=marker();operation['after_free'].append(ident);save()
    return {'anchor':operation['after_free'][-1],'stats':get(c,'/system_stats'),'method':'0.38.0 free flags / two model-free execution barriers'}

def release_models(store,c):
    if hasattr(store,'vram') and store.vram.managed():return
    if not getattr(store,'comfy_keep_models',False):free(c)

def select_session(store,selected,keep):
    store.comfy_keep_models=selected and keep
    if hasattr(store,'vram') and store.vram.managed():return {'retaining':True,'deferred':False,'strata_managed':True}
    if store.comfy_keep_models:return {'retaining':True,'deferred':False}
    if not store.gpu_execution.acquire(blocking=False):return {'retaining':False,'deferred':True}
    try:
        if not store.comfy_keep_models:
            with client(configuration(store)) as c:free(c)
        return {'retaining':store.comfy_keep_models,'deferred':False}
    finally:store.gpu_execution.release()

def release_for_other_gpu(store):
    if hasattr(store,'vram') and store.vram.managed():return
    if getattr(store,'comfy_keep_models',False):
        with client(configuration(store)) as c:free(c)

def unresolved(store):
    return any(j['status']=='unknown' and j.get('local_machine',{}).get('backend')=='comfyui' for j in store.jobs())

def guard(store):
    if unresolved(store):raise ValueError('ComfyUIに未確認の処理があります。「中断ジョブを照合」を実行してください。')

def cancel(store,ident):
    with store.lock:
        job=store.job(ident)
        if job['status']=='queued':
            job.update(status='cancelled',message='待機取消');store.save_job(job);return job
        if job['status']!='sending':raise ValueError('このローカル処理は終了しています。')
        # Keep the existing location so interrupted records remain recoverable.
        atomic_write(store.path/'qwen-jobs'/ident/'cancel',b'cancel')
        job.update(message='ComfyUIの取消処理中…');store.save_job(job);return job

def collect(store,job,c,history):
    record=history.get(job['comfy_prompt_id'])
    if not record:return False
    if record.get('status',{}).get('status_str')=='error':raise RuntimeError('ComfyUIの推論が失敗しました。中断ジョブを照合し、ComfyUIのログを確認してください。')
    if not record.get('status',{}).get('completed'):return False
    images=record.get('outputs',{}).get('8',{}).get('images',[])
    if len(images)!=1:raise RuntimeError('ComfyUIの出力画像が1枚ではありません。')
    file=images[0]
    r=c.get('/view',params={k:file.get(k,'') for k in ('filename','subfolder','type')});r.raise_for_status()
    with Image.open(io.BytesIO(r.content)) as im:
        im.load()
        if job['params'].get('model')=='SDXL':im=sdxl_backend.recover_image(store,job['params'],im)
        if im.size!=output_size(job['params']):raise ValueError('ComfyUIの結果寸法が要求と一致しません。')
        raw=png(im)
    folder=store.path/'qwen-jobs'/job['id'];folder.mkdir(parents=True,exist_ok=True)
    atomic_write(folder/'result.png',raw)
    store.write_response(job['id'],json.dumps({'images':[base64.b64encode(raw).decode()],'usage':None,'request_id':'comfyui-'+job['comfy_prompt_id']}).encode(),'comfyui-'+job['comfy_prompt_id'])
    return True

def recover(store,worker):
    results=[]
    with store.gpu_execution:
        for job in store.jobs():
            if job.get('local_machine',{}).get('backend')!='comfyui' or job['status']!='unknown':continue
            try:
                with client(job['local_machine']) as c:
                    ident=job['comfy_prompt_id']
                    history=get(c,'/history/'+ident)
                    if history.get(ident,{}).get('status',{}).get('status_str')=='error':
                        cancel_remote(c,ident);job.update(status='failed',message='ComfyUI側の失敗を確認しました。自動再生成はしません。');store.save_job(job)
                    elif collect(store,job,c,history):
                        job.update(status='local_error');store.save_job(job);worker._reprocess(job['id'])
                    else:
                        cancel_remote(c,ident)
                        job.update(status='cancelled',message='ComfyUIの残処理を取り消しました。自動再生成はしません。');store.save_job(job)
                    release_models(store,c)
                results.append({'id':job['id'],'recovered':True})
            except Exception as error:results.append({'id':job['id'],'recovered':False,'message':str(error)[:200]})
    return results

def run(worker,ident):
    store=worker.store;started=time.monotonic();attempted=False;submitted=False
    with store.gpu_execution:
        try:
            guard(store)
            job=store.job(ident);p=job['params'];config=job['local_machine']
            if hasattr(store,'vram'):store.vram.before_generation(config)
            with client(config) as c:
                validate_models(c,config,p)
                running,pending=queue_ids(c)
                if running or pending:raise ValueError('ComfyUIは他の処理を実行中です。専用インスタンスを空にして再試行してください。')
                images=[]
                raw_inputs=sdxl_backend.prepare_inputs(store,p) if p['model']=='SDXL' else [store.file(i).read_bytes() for i in p['input_ids']]
                if p['mode']=='inpaint' and p['model']!='SDXL':
                    meta=store.meta(p['target']);raw_inputs.append(png(mask_image((meta['width'],meta['height']),p['strokes']).convert('RGB')))
                for index,raw in enumerate(raw_inputs):
                    with store.lock:
                        if store.job(ident)['status']!='queued' or worker.stop.is_set():return
                    r=c.post('/upload/image',files={'image':(f'atelier-{ident}-{index}.png',raw,'image/png')},data={'type':'input','overwrite':'false'});r.raise_for_status()
                    upload=r.json();images.append((upload.get('subfolder','').rstrip('/')+'/' if upload.get('subfolder') else '')+upload['name'])
                with store.lock:
                    job=store.job(ident)
                    if job['status']!='queued' or worker.stop.is_set():return
                    seed=secrets.randbits(32) if p['qwen_seed']==-1 else p['qwen_seed']
                    remote_id=str(uuid.UUID(ident))
                    graph=workflow(p,config,seed,images,ident)
                    job.update(status='sending',started=time.time(),qwen_seed_used=seed,comfy_models={k:config[k] for k in ('diffusion','text_encoder','text_encoder_2','vae','checkpoint','hires_checkpoint') if k in config},comfy_prompt_id=remote_id,message='ComfyUIへ登録中')
                    store.save_job(job)
                    folder=store.path/'qwen-jobs'/ident;folder.mkdir(parents=True,exist_ok=True)
                    atomic_write(folder/'comfy-workflow.json',json.dumps(graph,ensure_ascii=False).encode())
                    attempted=True
                    result=post(c,'/prompt',{'prompt':graph,'prompt_id':remote_id,'client_id':'atelier-'+ident})
                    actual=result.get('prompt_id')
                    if not actual:raise RuntimeError('ComfyUIからジョブIDを取得できませんでした。')
                    job['comfy_prompt_id']=actual;store.save_job(job);submitted=True
                deadline=time.monotonic()+1800
                while time.monotonic()<deadline:
                    job=store.job(ident);remote_id=job['comfy_prompt_id']
                    if worker.stop.is_set() or (folder/'cancel').exists():
                        cancel_remote(c,remote_id)
                        job.update(status='cancelled',message='ComfyUIの処理を取り消しました。');store.save_job(job);release_models(store,c);return
                    history=get(c,'/history/'+remote_id)
                    if collect(store,job,c,history):
                        job.update(status='local_error');store.save_job(job)
                        worker._reprocess(ident);release_models(store,c);return
                    with store.lock:
                        job=store.job(ident);job.update(message='ComfyUIで生成中（経過 '+str(round(time.monotonic()-started))+'秒）');store.save_job(job)
                    time.sleep(.5)
                raise TimeoutError('ComfyUI処理が30分を超えました。中断ジョブを照合してください。')
        except Exception as error:
            with store.lock:
                job=store.job(ident)
                if job['status']=='cancelled':return
                rejected=isinstance(error,httpx.HTTPStatusError) and error.response.status_code==400 and not submitted
                job.update(status='local_error' if store.response_file(ident) else 'unknown' if attempted and not rejected else 'failed',message='ComfyUI: '+str(error)[:300],error_type=type(error).__name__)
                store.save_job(job)
        finally:
            with store.lock:
                job=store.job(ident);job['elapsed']=round(time.monotonic()-started,2);store.save_job(job)
