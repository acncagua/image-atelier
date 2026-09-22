"""Qwen 2.1 through a dedicated local ComfyUI server; never resubmit on ambiguity."""
import base64
import io
import json
import secrets
import time
import uuid
from urllib.parse import urlsplit
import httpx
from PIL import Image
from persistence import atomic_write
from imaging import mask_image, png

DEFAULTS={'backend':'comfyui','url':'http://127.0.0.1:8188','diffusion':'','text_encoder':'','vae':''}
REQUIRED=('UNETLoader','CLIPLoader','VAELoader','TextEncodeQwenImage21','EmptyLatentImage','KSampler','VAEDecode','SaveImage','LoadImage')

def validate_url(value):
    parsed=urlsplit(value)
    if parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','localhost','::1') or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment:
        raise ValueError('ComfyUIは同じPCのhttp://127.0.0.1:ポート番号で指定してください。')
    if not parsed.port:raise ValueError('ComfyUIのポート番号が必要です。')
    return value.rstrip('/')

def configuration(store):
    file=store.path/'comfy-settings.json'
    return {**DEFAULTS,**(json.loads(file.read_text('utf-8')) if file.exists() else {})}

def configure(store,p):
    values={**DEFAULTS,**{k:str(p.get(k,configuration(store)[k])) for k in ('url','diffusion','text_encoder','vae')}}
    values['url']=validate_url(values['url'])
    atomic_write(store.path/'comfy-settings.json',json.dumps(values).encode())
    return values

def client(config):
    return httpx.Client(base_url=validate_url(config['url']),timeout=20,trust_env=False,follow_redirects=False)

def get(c,path):
    r=c.get(path);r.raise_for_status();return r.json()

def post(c,path,body):
    r=c.post(path,json=body);r.raise_for_status();return r.json() if r.content else {}

def inspect(c):
    info=get(c,'/object_info')
    missing=[name for name in REQUIRED if name not in info]
    def choices(node,key):
        return info.get(node,{}).get('input',{}).get('required',{}).get(key,[[]])[0]
    standard=choices('UNETLoader','unet_name');gguf=choices('UnetLoaderGGUF','unet_name')
    return {'missing':missing,'gguf_available':'UnetLoaderGGUF' in info,'gguf_diffusion':gguf,'standard_diffusion':standard,'diffusion':list(dict.fromkeys([*standard,*gguf])),
            'text_encoder':choices('CLIPLoader','clip_name'),'vae':choices('VAELoader','vae_name')}

def check(store):
    try:
        with client(configuration(store)) as c:return inspect(c)
    except httpx.HTTPError as error:raise ValueError('ComfyUIに接続できません。接続先URLとComfyUIの起動状態を確認してください。') from error

def validate_models(c,config):
    available=inspect(c)
    if available['missing']:raise ValueError('Qwen 2.1対応ComfyUIが必要です。不足ノード: '+', '.join(available['missing']))
    is_gguf=config['diffusion'].lower().endswith('.gguf')
    if is_gguf and not available['gguf_available']:raise ValueError('GGUFにはComfyUI-GGUFのUnetLoaderGGUFが必要です。導入後にComfyUIを再起動してください。')
    if config['diffusion'] not in available['gguf_diffusion' if is_gguf else 'standard_diffusion']:raise ValueError('選択形式のローダーにモデルがありません。モデル一覧を再取得してください。')
    for name in ('diffusion','text_encoder','vae'):
        if config[name] not in available[name]:raise ValueError('ComfyUIにモデルがありません: '+name)

def workflow(p,config,seed,images,ident):
    def node(kind,**inputs):return {'class_type':kind,'inputs':inputs}
    graph={
        '1':node('UnetLoaderGGUF',unet_name=config['diffusion']) if config['diffusion'].lower().endswith('.gguf') else node('UNETLoader',unet_name=config['diffusion'],weight_dtype='default'),
        '2':node('CLIPLoader',clip_name=config['text_encoder'],type='qwen_image',device='default'),
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
    if not running and not pending:post(c,'/free',{'unload_models':True,'free_memory':True})

def unresolved(store):
    return any(j['status']=='unknown' and j.get('local_machine',{}).get('backend')=='comfyui' for j in store.jobs())

def guard(store):
    if unresolved(store):raise ValueError('ComfyUIに未確認の処理があります。「中断ジョブを照合」を実行してください。')

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
        if im.size!=(job['params']['width'],job['params']['height']):raise ValueError('ComfyUIの結果寸法が要求と一致しません。')
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
                    free(c)
                results.append({'id':job['id'],'recovered':True})
            except Exception as error:results.append({'id':job['id'],'recovered':False,'message':str(error)[:200]})
    return results

def run(worker,ident):
    store=worker.store;started=time.monotonic();attempted=False;submitted=False
    with store.gpu_execution:
        try:
            guard(store)
            job=store.job(ident);p=job['params'];config=job['local_machine']
            with client(config) as c:
                validate_models(c,config)
                running,pending=queue_ids(c)
                if running or pending:raise ValueError('ComfyUIは他の処理を実行中です。専用インスタンスを空にして再試行してください。')
                images=[]
                raw_inputs=[store.file(i).read_bytes() for i in p['input_ids']]
                if p['mode']=='inpaint':
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
                    job.update(status='sending',started=time.time(),qwen_seed_used=seed,comfy_models={k:config[k] for k in ('diffusion','text_encoder','vae')},comfy_prompt_id=remote_id,message='ComfyUIへ登録中')
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
                        job.update(status='cancelled',message='ComfyUIの処理を取り消しました。');store.save_job(job);free(c);return
                    history=get(c,'/history/'+remote_id)
                    if collect(store,job,c,history):
                        job.update(status='local_error');store.save_job(job)
                        worker._reprocess(ident);free(c);return
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
