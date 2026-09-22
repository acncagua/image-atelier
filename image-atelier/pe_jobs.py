"""Durable optional prompt enhancement; no automatic image submission."""
import json
import os
import threading
import time
from pathlib import Path
from persistence import atomic_write,safe_id,now
from managed_child import launch,reap_tree
from pe_runner import parse_result

def input_context(p):
    return {'mode':p.get('mode','generate'),'target':p.get('target') if p.get('mode')!='generate' else None,
            'refs':[r['id'] for r in p.get('refs',[])], 'strokes':p.get('strokes',[]) if p.get('mode')=='inpaint' else []}

def directory(store,ident):return store.path/'pe-jobs'/safe_id(ident)
def read_record(store,ident):
    file=store.path/'pe-jobs'/(safe_id(ident)+'.json')
    if not file.exists():
        from core import JobNotFound
        raise JobNotFound('補強ジョブが見つかりません。')
    return json.loads(file.read_text('utf-8'))

class PEJobs:
    def __init__(self,store,runner=None):
        self.store=store;self.root=store.path/'pe-jobs';self.root.mkdir(exist_ok=True)
        self.runner=Path(runner or Path(__file__).with_name('pe_runner.py'))
        self.lock=threading.RLock();self.stop=threading.Event();self.error=None
        self.thread=threading.Thread(target=self.loop,daemon=True)
    def config(self):
        file=self.store.path/'pe-settings.json';root=Path(__file__).resolve().parent
        values=json.loads(file.read_text('utf-8-sig')) if file.exists() else {'python':str(root/'.venv-qwen/Scripts/python.exe'),'model':str(root/'models/Qwen-Image-2.1-PE-T2I')}
        values.setdefault('model_i2i',str(root/'models/Qwen-Image-2.1-PE-I2I'));return values
    def configure(self,p):
        values={key:str(p.get(key,self.config().get(key,''))) for key in ('python','model','model_i2i')}
        if any(not Path(v).is_absolute() for v in values.values()) or Path(values['python']).name.lower() not in ('python.exe','python','python3'):raise ValueError('専用Pythonとモデルは絶対パスで指定してください。')
        atomic_write(self.store.path/'pe-settings.json',json.dumps(values).encode());return values
    def save(self,j):atomic_write(self.root/(j['id']+'.json'),json.dumps(j,ensure_ascii=False).encode('utf-8'))
    def public(self,j):return {k:v for k,v in j.items() if k!='machine'}|{'worker_error':self.error}
    def get(self,ident):
        with self.lock:return self.public(read_record(self.store,ident))
    def submit(self,p):
        from core import JobConflict
        ident=safe_id(p['id'])
        if p.get('model')!='Qwen-Image-2.1' or p.get('mode') not in ('generate','polish','inpaint'):raise ValueError('Qwenの生成・編集用の指示補強です。')
        context=input_context(p)
        ids=([context['target']] if context['mode']!='generate' else [])+context['refs']
        if len(ids)+(context['mode']=='inpaint')>10:raise ValueError('補強の入力はマスクを含め10枚までです。')
        for ident_image in ids:self.store.meta(ident_image or '')
        if context['mode']=='inpaint':
            from imaging import mask_image
            meta=self.store.meta(context['target'])
            if not mask_image((meta['width'],meta['height']),context['strokes']).getbbox():raise ValueError('編集範囲を指定してください。')
        prompt=p.get('prompt');limit=p.get('max_new_tokens',8192);seed=p.get('seed',42)
        if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>16000:raise ValueError('補強元の指示は1〜16,000文字で指定してください。')
        if type(limit) is not int or not 256<=limit<=24000 or type(seed) is not int or not 0<=seed<=4294967295:raise ValueError('補強パラメータが範囲外です。')
        params={'prompt':prompt,'max_new_tokens':limit,'seed':seed}
        if ids:params.update(task='edit',context=context,input_ids=ids)
        with self.lock:
            if (self.root/(ident+'.json')).exists():
                old=read_record(self.store,ident)
                if old['params']!=params:raise JobConflict('同じ補強IDの入力が異なります。')
                return self.public(old)
            machine=self.config()
            if ids:machine={**machine,'model':machine['model_i2i']}
            if not Path(machine['python']).is_file() or not (Path(machine['model'])/'system_prompt.txt').is_file():raise ValueError('補強モデルまたは専用Pythonが未設定です。')
            j={'id':ident,'params':params,'machine':machine,'status':'queued','created':now(),'result':None,'message':'補強処理を待機中。画像は生成しません。'}
            self.save(j);return self.public(j)
    def cancel(self,ident):
        with self.lock:
            j=read_record(self.store,ident)
            if j['status'] not in ('queued','running'):raise ValueError('補強処理は終了しています。')
            j.update(status='cancelled' if j['status']=='queued' else 'cancel_requested',message='補強の取消を受け付けました。元の指示は保持しています。');self.save(j);return self.public(j)
    def recover(self):
        for file in self.root.glob('*.json'):
            j=json.loads(file.read_text('utf-8'));folder=directory(self.store,j['id']);reap_tree(folder)
            if j['status']=='cancel_requested':j.update(status='cancelled',message='補強を取消しました。');self.save(j)
            if j['status'] in ('queued','running'):
                try:
                    result=json.loads((folder/'result.json').read_text('utf-8'));parse_result(json.dumps(result))
                    j.update(status='completed',result=result,message='保存済みの補強結果を回収しました。')
                except (OSError,ValueError,AttributeError):j.update(status='failed',message='補強処理は中断しました。自動で再実行しません。')
                self.save(j)
    def loop(self):
        while not self.stop.wait(.3):
            if self.error:continue
            try:
                jobs=[json.loads(f.read_text('utf-8')) for f in self.root.glob('*.json')]
                queued=sorted((j for j in jobs if j['status']=='queued'),key=lambda j:j['created'])
                if queued:self.run(queued[0]['id'])
            except Exception:self.error='補強記録を保存できません。保存先を確認してアプリを再起動してください。'
    def run(self,ident):
        child=None;folder=directory(self.store,ident)
        while not self.stop.is_set():
            if self.store.gpu_execution.acquire(timeout=.2):break
            if self.get(ident)['status']=='cancelled':return
        else:return
        try:
            self.store.qwen_session.unload()
            with self.lock:
                j=read_record(self.store,ident)
                if j['status']!='queued' or self.stop.is_set():return
                folder.mkdir(exist_ok=True);j.update(status='running',message='指示補強モデルを読み込み中');self.save(j)
            request={**j['params'],'model':j['machine']['model']}
            if j['params'].get('input_ids'):
                request['inputs']=[str(self.store.file(i)) for i in j['params']['input_ids']]
                context=j['params']['context']
                if context['mode']=='inpaint':
                    from imaging import mask_image,png
                    meta=self.store.meta(context['target'])
                    mask=mask_image((meta['width'],meta['height']),context['strokes'])
                    atomic_write(folder/'mask.png',png(mask.convert('RGB')))
                    request['inputs'].append(str(folder/'mask.png'))
            atomic_write(folder/'request.json',json.dumps(request,ensure_ascii=False).encode('utf-8'))
            env={k:v for k,v in os.environ.items() if not any(x in k.upper() for x in ('API_KEY','TOKEN','SECRET'))}
            env.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',PYTHONUTF8='1')
            child=launch([j['machine']['python'],'-I',str(self.runner),str(folder/'request.json')],folder,Path(__file__).resolve().parent,env)
            deadline=time.monotonic()+900;last_phase=None
            while child.poll() is None:
                if self.stop.is_set() or self.get(ident)['status'] in ('cancelled','cancel_requested'):
                    child.kill();child.wait(timeout=10)
                    with self.lock:
                        j=read_record(self.store,ident);j.update(status='cancelled',message='補強を取消しました。');self.save(j)
                    return
                if time.monotonic()>deadline:child.kill();child.wait(timeout=10);raise TimeoutError('補強が15分を超えました。')
                try:phase=json.loads((folder/'status.json').read_text('utf-8')).get('state')
                except (OSError,ValueError):phase=None
                if phase in ('loading','rewriting') and phase!=last_phase:
                    with self.lock:
                        j=read_record(self.store,ident)
                        if j['status']=='running':j['message']='指示文を補強中…' if phase=='rewriting' else '指示補強モデルを読み込み中…';self.save(j)
                    last_phase=phase
                time.sleep(.3)
            if child.returncode!=0:
                try:message=json.loads((folder/'status.json').read_text('utf-8')).get('message','補強に失敗しました。')
                except (OSError,ValueError):message='補強プロセスが異常終了しました。'
                raise RuntimeError(message)
            result=json.loads((folder/'result.json').read_text('utf-8'));parse_result(json.dumps(result))
            with self.lock:
                j=read_record(self.store,ident)
                if j['status'] in ('cancelled','cancel_requested'):
                    j.update(status='cancelled');self.save(j);return
                j.update(status='completed',result=result,message='補強が完了しました。内容を確認して採用してください。');self.save(j)
        except Exception as error:
            with self.lock:
                j=read_record(self.store,ident)
                if j['status']=='cancel_requested':j.update(status='cancelled',message='補強を取消しました。');self.save(j)
                elif j['status']!='cancelled':j.update(status='failed',message=str(error)[:500]);self.save(j)
        finally:
            if child:
                if child.poll() is None:child.kill();child.wait(timeout=10)
                if hasattr(child,'close'):child.close()
            self.store.gpu_execution.release()
    def shutdown(self):
        self.stop.set()
        if self.thread.is_alive():self.thread.join()
