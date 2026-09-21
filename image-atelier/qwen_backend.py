"""Qwen local adapter for the existing durable Atelier job/history contract."""
import base64
import json
import os
import time
from pathlib import Path
from PIL import Image
from persistence import atomic_write
from managed_child import launch,reap_tree,tree_exited

MODEL='Qwen-Image-2.1'
DEFAULTS={'qwen_steps':40,'qwen_seed':42,'qwen_offload':'model','qwen_tile':512,'qwen_stride':384}

def validate(p):
    if p['provider']!='qwen' or p['model']!=MODEL:raise ValueError('QwenモデルはローカルQwen接続で実行してください。')
    if p['mode'] not in ('generate','polish'):raise ValueError('Qwenの部分修正は未対応です。新規生成またはブラッシュアップを選んでください。')
    if p['n']!=1 or p['format']!='png':raise ValueError('Qwenは1枚・PNGで実行してください。')
    if any(type(p[k]) is not int or p[k]<128 or p[k]>2048 or p[k]%32 for k in ('width','height')):raise ValueError('Qwenの寸法は各辺128〜2048px、32の倍数で指定してください。')
    for key,low,high in [('qwen_steps',1,50),('qwen_seed',0,4294967295),('qwen_tile',128,1024)]:
        if type(p[key]) is not int or not low<=p[key]<=high:raise ValueError('Qwenパラメータが範囲外です: '+key)
    if p['qwen_offload'] not in ('model','sequential'):raise ValueError('Qwenのオフロード設定が不正です。')
    if p['qwen_tile']%32 or type(p['qwen_stride']) is not int or not 0<p['qwen_stride']<p['qwen_tile'] or p['qwen_stride']%32:raise ValueError('タイルとstrideは32の倍数、strideはタイル未満にしてください。')

def configuration(store):
    root=Path(__file__).resolve().parent;file=store.path/'qwen-settings.json'
    return json.loads(file.read_text('utf-8-sig')) if file.exists() else {'python':str(root/'.venv-qwen/Scripts/python.exe'),'model':str(root/'models/Qwen-Image-2.1')}

def configure(store,data):
    values={k:str(data.get(k,'')) for k in ('python','model')}
    if any(not Path(v).is_absolute() for v in values.values()):raise ValueError('Qwen環境は絶対パスで指定してください。')
    if Path(values['python']).name.lower() not in ('python.exe','python','python3'):raise ValueError('Python実行ファイルを指定してください。')
    atomic_write(store.path/'qwen-settings.json',json.dumps(values).encode());return values

def directory(store,ident):return store.path/'qwen-jobs'/ident

def available(store,job):
    folder=directory(store,job['id'])
    if job['params']['provider']!='qwen' or job['status'] in ('queued','sending','cancelled') or (folder/'cancel').exists() or not tree_exited(folder):return False
    try:
        with Image.open(folder/'result.png') as image:
            if image.format!='PNG' or image.size!=(job['params']['width'],job['params']['height']):return False
            image.verify()
        return True
    except (OSError,ValueError,SyntaxError):return False

def collect(store,job):
    if not available(store,job):raise ValueError('回収できるQwen結果がありません。')
    raw=(directory(store,job['id'])/'result.png').read_bytes()
    store.write_response(job['id'],json.dumps({'images':[base64.b64encode(raw).decode()],'usage':None,'request_id':'local-qwen-'+job['id']}).encode(),'local-qwen-'+job['id'])

def recover(store):
    for job in store.jobs():
        if job['params']['provider']!='qwen':continue
        folder=directory(store,job['id']);reap_tree(folder)
        if job['status'] in ('sending','queued'):
            job.update(status='failed',message='Qwen処理はアプリ終了で中断しました。自動で再推論しません。')
            if (folder/'cancel').exists():job.update(status='cancelled',message='Qwen処理を取消しました。')
            elif available(store,job):job.update(status='local_error',message='保存済みのQwen結果を再処理できます。再推論しません。')
            store.save_job(job)

def cancel(store,ident):
    with store.lock:
        job=store.job(ident)
        if job['status']=='queued':job.update(status='cancelled',message='待機取消');store.save_job(job);return job
        if job['status']!='sending':raise ValueError('このQwen処理は終了しています。')
        atomic_write(directory(store,ident)/'cancel',b'cancel')
        job.update(message='Qwenの取消処理中…');store.save_job(job);return job

def run(worker,ident):
    store=worker.store;folder=directory(store,ident);child=None;started=time.monotonic()
    with store.gpu_execution:
        with store.lock:
            job=store.job(ident)
            if job['status']!='queued' or worker.stop.is_set():return
            folder.mkdir(parents=True,exist_ok=True)
            job.update(status='sending',started=time.time(),phase='qwen_loading',message='Qwenモデルを読み込み中（ローカルGPU）')
            store.save_job(job)
        try:
            p=job['params'];machine=job['local_machine']
            request={'model':machine['model'],'inputs':[str(store.file(i)) for i in p['input_ids']],
                     'prompt':p['prompt'],'width':p['width'],'height':p['height'],'steps':p['qwen_steps'],
                     'seed':p['qwen_seed'],'offload':p['qwen_offload'],'vae_tiling':True,
                     'vae_tile_size':p['qwen_tile'],'vae_tile_stride':p['qwen_stride'],'output':str(folder/'result.png')}
            atomic_write(folder/'request.json',json.dumps(request,ensure_ascii=False).encode('utf-8'))
            root=Path(__file__).resolve().parent
            env={k:v for k,v in os.environ.items() if not any(x in k.upper() for x in ('API_KEY','TOKEN','SECRET'))}
            env.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',PYTHONUTF8='1')
            child=launch([machine['python'],'-I',str(getattr(store,'qwen_runner',root/'qwen_trial.py')),'--worker',str(folder/'request.json')],folder,root,env)
            last=None;deadline=time.monotonic()+1800
            while child.poll() is None:
                if worker.stop.is_set() or (folder/'cancel').exists():
                    atomic_write(folder/'cancel',b'cancel');child.kill();child.wait(timeout=10);break
                if time.monotonic()>deadline:child.kill();child.wait(timeout=10);raise TimeoutError('Qwenの処理時間が30分を超えました。')
                try:state=json.loads((folder/'status.json').read_text('utf-8'))
                except (OSError,ValueError):state={}
                mark=(state.get('state'),state.get('step'))
                if mark!=last:
                    with store.lock:
                        job=store.job(ident)
                        if not (folder/'cancel').exists():
                            job.update(message='Qwen: '+{'loading':'モデル読込','initializing':'準備','inference_start':'推論準備','generating':'生成','decoding':'画像変換','saving':'画像保存'}.get(mark[0],str(mark[0] or '起動中'))+(f" {mark[1]}/{p['qwen_steps']}" if mark[1] else ''),qwen_progress=state.get('step'))
                            store.save_job(job)
                    last=mark
                time.sleep(.2)
            with store.lock:
                job=store.job(ident)
                if (folder/'cancel').exists():job.update(status='cancelled',message='Qwen処理を取消しました。子プロセスは終了済みです。');store.save_job(job);return
                try:state=json.loads((folder/'status.json').read_text('utf-8'))
                except (OSError,ValueError):state={}
                job.update(status='local_error',qwen_environment=state.get('environment'),qwen_peak_cuda_bytes=state.get('peak_cuda_allocated_bytes'))
                store.save_job(job)
                if not available(store,job):raise RuntimeError(state.get('message') or 'Qwenから有効な出力画像を取得できませんでした。')
                collect(store,job);worker._reprocess(ident)
        except Exception as error:
            if child and child.poll() is None:child.kill();child.wait(timeout=10)
            with store.lock:
                job=store.job(ident);job['status']='failed'
                saved=available(store,job) or store.response_file(ident) is not None
                job.update(status='local_error' if saved else 'failed',error_type=type(error).__name__,message=('Qwenの結果を保存済みです。再処理で回収できます。' if saved else 'Qwenローカル処理に失敗: '+str(error)[:300]))
                store.save_job(job)
        finally:
            if child:
                if child.poll() is None:child.kill();child.wait(timeout=10)
                if hasattr(child,'close'):child.close()
            with store.lock:
                job=store.job(ident);job['elapsed']=round(time.monotonic()-started,2);store.save_job(job)
