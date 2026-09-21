"""Durable single-lane GPU jobs, isolated from Images API jobs and budget."""
import copy
import hashlib
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from PIL import Image
from managed_child import launch,reap_tree,tree_exited,legacy_exited
from core import ROOT,JobConflict,JobNotFound,canonical_input
from persistence import atomic_write,now,safe_id
from upscale_geometry import plan,LIMITS

ACTIVE={'loading','upscaling','adjusting','saving','cancel_requested'}

class UpscaleJobs:
    def __init__(self,store,runner=None):
        self.store=store;self.path=store.path/'upscale-jobs';self.path.mkdir(exist_ok=True)
        self.lock=threading.RLock();self.stop=threading.Event();self.current=None;self.process=None
        self.runner=Path(runner or ROOT/'swinir_runner.py')
        self.thread=threading.Thread(target=self.loop,daemon=True)
        self.error=None
        self.validated_results={}
        self.registrations=self.path/'registrations';self.registrations.mkdir(exist_ok=True)

    def config(self):
        file=self.store.path/'upscale-settings.json'
        return json.loads(file.read_text('utf-8-sig')) if file.exists() else {'python':str(ROOT/'.venv-swinir'/'Scripts'/'python.exe'),'model':str(ROOT/'models'/'SwinIR_4x.pth')}

    def configure(self,data):
        settings={key:str(data.get(key,'')) for key in ('python','model')}
        for key in settings:
            if settings[key] and not Path(settings[key]).is_absolute():raise ValueError('推論環境とモデルは絶対パスで指定してください。')
        if settings['python'] and Path(settings['python']).name.lower() not in ('python.exe','python','python3'):
            raise ValueError('推論用Pythonの実行ファイルを指定してください。')
        with self.lock:atomic_write(self.store.path/'upscale-settings.json',json.dumps(settings).encode())
        return settings

    def public_config(self):
        c=self.config();return {**c,'limits':LIMITS,'model_exists':Path(c['model']).is_file() if c['model'] else False,
                               'python_exists':Path(c['python']).is_file() if c['python'] else False,'worker_error':self.error}

    def get(self,ident):
        with self.lock:
            safe_id(ident);file=self.path/ident/'job.json'
            if not file.exists():raise JobNotFound('ローカルGPUジョブが見つかりません。')
            return json.loads(file.read_text('utf-8'))

    def save(self,job):atomic_write(self.path/job['id']/'job.json',json.dumps(job,ensure_ascii=False).encode('utf-8'))

    def jobs(self):
        with self.lock:return sorted([json.loads(p.read_text('utf-8')) for p in self.path.glob('*/job.json')],key=lambda j:j['created'],reverse=True)

    def public(self,job):
        available=self.result_available(job)
        return {**{k:v for k,v in job.items() if k not in ('machine',)},'manager_error':self.error,'result_available':available}

    def result_available(self,job,verify=False):
        directory=self.path/job['id']
        if job['params']['kind']!='upscale' or job['status'] in {'queued','cancelled','cancel_requested'} or (directory/'cancel').exists():return False
        if self.current==job['id'] and self.process and self.process.poll() is None:return False
        if not tree_exited(directory):return False
        if job.get('containment')!='windows-job-v1' and not legacy_exited(job.get('process_id')):return False
        try:
            if json.loads((directory/'status.json').read_text('utf-8')).get('state')=='cancelled':return False
        except (OSError,json.JSONDecodeError):pass
        try:
            file=directory/'result.png';stat=file.stat();options=job['params']['options']
            key=(stat.st_size,stat.st_mtime_ns,options['width'],options['height'])
            if not verify and self.validated_results.get(job['id'])==key:return True
            with Image.open(file) as image:
                if image.format!='PNG' or image.size!=(options['width'],options['height']) or image.mode not in {'RGB','RGBA'}:return False
                image.verify()
            with Image.open(file) as image:image.load()
            self.validated_results[job['id']]=key
            return True
        except (OSError,ValueError,SyntaxError):return False

    def submit(self,body):
        ident=safe_id(body['id']);kind=body.get('kind','upscale')
        if kind not in ('upscale','diagnostic'):raise ValueError('ローカル処理種別が不正です。')
        params={'kind':kind,'source_id':body.get('source_id'),'options':copy.deepcopy(body.get('options',{})),
                'context':canonical_input(body['context']) if body.get('context') else None}
        if kind=='upscale':
            image=self.store.meta(params['source_id']);params['options']=plan(image['width'],image['height'],params['options'])
        with self.lock:
            if (self.path/ident/'job.json').exists():
                existing=self.get(ident)
                if existing['params']!=params:raise JobConflict('同じローカルジョブIDの入力が異なります。')
                return self.public(existing)
            if self.error:raise ValueError('ローカルGPUワーカーの保存障害中です。保存先を確認してアプリを再起動してください。')
            directory=self.path/ident;intent=self.registrations/(ident+'.json')
            if intent.exists():
                job=json.loads(intent.read_text('utf-8'))
                if job['params']!=params:raise JobConflict('同じローカルジョブIDの入力が異なります。')
                if directory.exists() and any(not (p.is_file() and p.name.startswith('job.json.') and p.suffix=='.tmp') for p in directory.iterdir()):
                    raise JobConflict('未完了フォルダーに実行済みの可能性があるデータがあります。再推論せず保護しました。')
                directory.mkdir(exist_ok=True);self.save(job);return self.public(job)
            if directory.exists() and any(directory.iterdir()):
                raise JobConflict('未完了フォルダーに既存データがあります。上書きせず保護しました。')
            config=self.config()
            if not config['python'] or not Path(config['python']).is_file():raise ValueError('SwinIR専用Python環境が未設定です。setup-swinir.ps1を実行してください。')
            if not config['model'] or not Path(config['model']).is_file():raise ValueError('SwinIRモデルが未設定です。ローカルモデルを指定してください。')
            with Path(config['model']).open('rb') as model:digest=hashlib.file_digest(model,'sha256').hexdigest()
            job={'id':ident,'kind':'local_gpu','params':params,'machine':config,'model_name':Path(config['model']).name,
                 'model_sha256':digest,'native_scale':4,'native_scale_verified':False,'precision':'FP32',
                 'created':now(),'status':'queued','message':'ローカル処理を待機中（無料）','output':None,'environment':None}
            # Persist the immutable input before creating the job directory. A
            # failed job.json write can then be completed with the same identity.
            atomic_write(intent,json.dumps(job,ensure_ascii=False).encode('utf-8'))
            directory.mkdir(exist_ok=True);self.save(job);return self.public(job)

    def cancel(self,ident):
        with self.lock:
            job=self.get(ident)
            if job['status']=='queued':job.update(status='cancelled',message='待機取消',ended=now())
            elif job['status'] in ACTIVE:
                atomic_write(self.path/ident/'cancel',b'cancel')
                job.update(status='cancel_requested',message='取消要求中。モデル読込・現在のタイル処理の終了を待っています。')
            else:raise JobConflict('このローカル処理は終了しています。')
            self.save(job);return self.public(job)

    def recover(self):
        with self.lock:
            for job in self.jobs():
                directory=self.path/job['id']
                reap_tree(directory)
                if job['status'] in ACTIVE and job.get('containment')!='windows-job-v1' and not legacy_exited(job.get('process_id')):
                    raise RuntimeError('旧版の推論プロセスの終了を確認できません。無関係なPIDは停止せず、キューの再開を保留します。')
                if job['status'] in ACTIVE|{'queued'}:
                    cancelled=job['status']=='cancel_requested' or (directory/'cancel').exists()
                    job.update(status='cancelled' if cancelled else 'interrupted',message='アプリ終了により中断しました。自動で再推論しません。',ended=now())
                    self.save(job)

    def loop(self):
        while not self.stop.wait(.3):
            if self.error:continue
            try:
                jobs=[j for j in self.jobs() if j['status']=='queued']
                if jobs:self.run(jobs[-1]['id'])
            except Exception as error:self.error=type(error).__name__+'：ローカルジョブの保存先を確認し、アプリを再起動してください。'

    def run(self,ident):
        with self.store.gpu_execution:
            if not self.stop.is_set():self._run(ident)

    def _run(self,ident):
        with self.lock:
            job=self.get(ident)
            if job['status']!='queued':return
            job.update(status='loading',started=now(),started_epoch=time.time(),message='専用子プロセスを起動中',containment='windows-job-v1' if os.name=='nt' else 'popen')
            self.save(job);self.current=ident
        directory=self.path/ident
        request={'model':job['machine']['model'],'model_sha256':job['model_sha256'],'cancel':str(directory/'cancel'),
                 'status':str(directory/'status.json'),'output':str(directory/'result.png'),
                 'diagnose':job['params']['kind']=='diagnostic','options':job['params']['options']}
        try:
            if not request['diagnose']:request['input']=str(self.store.file(job['params']['source_id']))
            atomic_write(directory/'request.json',json.dumps(request).encode())
            env={k:v for k,v in os.environ.items() if not any(word in k.upper() for word in ('API_KEY','TOKEN','SECRET'))}
            env.update(PYTHONUTF8='1',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
            self.process=launch([job['machine']['python'],'-I',str(self.runner),str(directory/'request.json')],directory,ROOT,env)
            with self.lock:
                current=self.get(ident);current['process_id']=self.process.pid;self.save(current)
            deadline=time.monotonic()+1800
            while self.process.poll() is None:
                if self.stop.is_set():atomic_write(directory/'cancel',b'cancel')
                if time.monotonic()>deadline:
                    self.stop_child(directory);raise RuntimeError('子プロセスが時間制限を超えました。')
                self.progress(ident);time.sleep(.2)
            self.progress(ident)
            status=json.loads((directory/'status.json').read_text('utf-8')) if (directory/'status.json').exists() else {}
            with self.lock:
                job=self.get(ident)
                if job['status']=='cancel_requested' or status.get('state')=='cancelled':
                    job.update(status='cancelled',message='取消しました。子プロセスは終了済みです。',ended=now());self.save(job)
                elif self.process.returncode!=0 or status.get('state')!='completed':
                    job.update(status='failed',message=status.get('message','子プロセスが異常終了しました。'),error_type=status.get('error_type','ProcessError'),ended=now());self.save(job)
                elif request['diagnose']:
                    job.update(status='completed',native_scale_verified=not status['environment'].get('mock',False),environment=status['environment'],message='GPU・実モデルのFP32推論を確認しました。',ended=now());self.save(job)
                else:self.finalize(ident)
        except Exception as error:
            with self.lock:
                job=self.get(ident);job.update(status='failed',error_type=type(error).__name__,message='ローカル処理が失敗しました。保存済みの結果があれば保存を再試行できます。',ended=now());self.save(job)
        finally:
            try:self.stop_child(directory)
            finally:
                if self.process and hasattr(self.process,'close'):self.process.close()
                self.process=None;self.current=None
            with self.lock:
                job=self.get(ident);job['elapsed']=round(time.time()-job['started_epoch'],3);self.save(job)

    def stop_child(self,directory):
        if not self.process or self.process.poll() is not None:return
        try:atomic_write(directory/'cancel',b'cancel')
        except OSError:pass
        try:self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=10)

    def progress(self,ident):
        file=self.path/ident/'status.json'
        if not file.exists():return
        try:status=json.loads(file.read_text('utf-8'))
        except (OSError,json.JSONDecodeError):return
        with self.lock:
            job=self.get(ident)
            before=copy.deepcopy(job)
            if job['status']!='cancel_requested' and status.get('state') in ACTIVE:
                job['status']=status['state']
                job['message']={'loading':'モデルを読み込み中','upscaling':'SwinIRでタイルを拡大中','adjusting':'指定サイズへ調整中','saving':'結果を保存中'}.get(status['state'],job['message'])
            for field in ('done','total','environment','geometry'):
                if field in status:job[field]=status[field]
            if job!=before:self.save(job)

    def finalize(self,ident):
        with self.lock:
            job=self.get(ident);directory=self.path/ident
            if job['status']=='cancel_requested':
                job.update(status='cancelled',ended=now(),message='保存確定前に取消しました。');self.save(job);return self.public(job)
            if job['status']=='cancelled':raise JobConflict('取消済みです。')
            if not self.result_available(job,verify=True):raise ValueError('終了確認済みの再利用できる推論結果がありません。')
            try:status=json.loads((directory/'status.json').read_text('utf-8'))
            except (OSError,json.JSONDecodeError):status={}
            environment=status.get('environment') or job.get('environment') or {}
            job.update(status='saving',environment=environment,geometry=status.get('geometry') or job.get('geometry'),
                       native_scale_verified=environment.get('architecture')=='SwinIR' and environment.get('native_scale')==4 and not environment.get('mock',False))
            self.save(job)
            try:
                output=self.store.asset((directory/'result.png').read_bytes(),'SwinIR拡大結果',job['params']['source_id'],'upscaled',identity='upscale:'+ident)
                expected=job['params']['options']
                if (output['width'],output['height'])!=(expected['width'],expected['height']):raise ValueError('出力寸法が要求と一致しません。')
                job['output']=output;self.save(job)
                job['saved_path']=self.store.export(output['id'],operation='upscale-'+ident)
                job.update(status='completed',message='ローカル拡大が完了しました（無料）',ended=now())
            except Exception as error:
                job.update(status='export_failed',message='推論結果は保存済みです。「保存を再試行」で再推論せず回収できます。',error_type=type(error).__name__)
            self.save(job);return self.public(job)

    def retry_save(self,ident):
        with self.lock:
            job=self.get(ident)
            if job['status'] in ACTIVE|{'queued'}:raise JobConflict('処理中です。')
            return self.finalize(ident)

    def shutdown(self):
        self.stop.set()
        if self.current:atomic_write(self.path/self.current/'cancel',b'cancel')
        if self.thread.is_alive():self.thread.join()
