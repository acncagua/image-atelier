"""Single worker and idempotent LOCAL response processing. Resume never resends."""
import base64
import json
import threading
import time
import httpx
import core
from persistence import atomic_write
from imaging import normalize, png, mask_image, api_mask, composite


class Worker:
    def __init__(self,store,mock_gate=None):
        self.store=store;self.stop=threading.Event()
        self.thread=threading.Thread(target=self.loop,daemon=True)
        self.execution=threading.RLock()
        self.state='running';self.current=None;self.fault_type=None
        self.volatile={};self.mock_gate=mock_gate

    def health(self):
        return {'state':'stopped' if self.stop.is_set() else self.state,'current_job':self.current,
                'error_type':self.fault_type,'volatile_response_ids':list(self.volatile),
                'message':'永続化または処理に失敗しました。保存先・空き容量を確認し、ワーカーを再開してください。APIは自動再送しません。' if self.state=='fault' else '並列数1・結果不明は自動再送しません。'}

    def fault(self,error):
        self.state='fault';self.fault_type=type(error).__name__

    def persist(self,job):
        try:self.store.save_job(job);return True
        except Exception as error:self.fault(error);return False

    def resume(self):
        with self.execution:
            try:
                # Prove both DB and filesystem durability, then recover interrupted jobs.
                with self.store.lock:
                    with self.store.db:self.store.db.execute("INSERT OR REPLACE INTO settings VALUES ('worker-probe','{}')")
                atomic_write(self.store.path/'worker-probe',b'ok')
                for ident,(content,request_id) in list(self.volatile.items()):
                    self.store.write_response(ident,content,request_id,http=True)
                    del self.volatile[ident]
                self.store.recover(cancel_queued=False)
                self.state='running';self.current=None;self.fault_type=None
                if self.thread.ident is not None and not self.thread.is_alive() and not self.stop.is_set():
                    self.thread=threading.Thread(target=self.loop,daemon=True);self.thread.start()
            except Exception as error:self.fault(error)
            return self.health()

    def loop(self):
        try:
            while not self.stop.wait(1 if self.state=='fault' else .2):
                if self.state!='fault':self.tick()
        except BaseException as error:
            self.fault(error)

    def tick(self):
        if self.state=='fault' or self.stop.is_set():return
        try:
            queued=[j for j in self.store.jobs() if j['status']=='queued']
            if queued:self.run(queued[-1]['id'])
        except Exception as error:self.fault(error)

    def run(self,ident):
        with self.execution:
            if self.state=='fault' or self.stop.is_set():return
            job=None;attempted=False;started=time.time()
            try:
                job=self.store.job(ident)
                if job['status']!='queued':return
                self.current=ident
                p=job['params']
                # All local preparation happens before the durable sending marker.
                try:
                    if p['provider']=='openai':
                        if not self.store.settings()['live']:
                            job.update(status='cancelled',reserved=0,message='実APIを無効化したため待機取消。')
                            self.persist(job);return
                        if not core.api_key():raise ValueError('APIキー未設定')
                    for asset_id in p['input_ids']:self.store.file(asset_id).read_bytes()
                    if p['mode']=='inpaint':
                        meta=self.store.meta(p['target'])
                        mask=mask_image((meta['width'],meta['height']),p['strokes'])
                        job['mask']=self.store.asset(api_mask(mask),'APIマスク',p['target'],'mask',identity=f'{ident}:mask')
                except (ValueError,FileNotFoundError) as error:
                    job.update(status='failed',phase='preflight_failed',reserved=0,error_type=type(error).__name__,message='送信前の入力・設定確認に失敗しました。外部API未送信。資料と設定を確認してください。')
                    self.persist(job);return
                job.update(status='sending',phase='dispatching',started=started,message='送信中・生成中')
                if not self.persist(job):return
                attempted=True
                if p['provider']=='mock' and self.mock_gate:
                    while not self.mock_gate.wait(.1):
                        if self.stop.is_set():return
                raw,usage,request_id=(core.real_request if p['provider']=='openai' else core.mock_request)(p,self.store)
                self.store.write_response(ident,json.dumps({'images':[base64.b64encode(b).decode() for b in raw],'usage':usage,'request_id':request_id}).encode(),request_id)
                self._reprocess(ident)
                return
            except core.PreflightError as error:
                if job:
                    job.update(status='failed',phase='preflight_failed',reserved=0,error_type=type(error).__name__,message='外部送信前の準備に失敗しました。API未送信です。')
                    self.persist(job)
            except httpx.HTTPStatusError as error:
                if job:
                    status=error.response.status_code
                    job.update(status='unknown' if status>=500 or status==408 else 'failed',phase='api_error',request_id=error.response.headers.get('x-request-id'),message=f'HTTP {status}。自動再送しません。')
                    if job['status']=='failed':job['reserved']=0
                    self.persist(job)
            except Exception as error:
                if isinstance(error,core.ResponsePersistenceError):
                    self.volatile[ident]=(error.content,error.request_id)
                    self.fault(error)
                if job:
                    try:job=self.store.job(ident)
                    except Exception:pass  # The in-memory state is still exposed through health.
                    try:saved=self.store.response_file(ident) is not None
                    except Exception:saved=False
                    job.update(status='local_error' if saved else ('unknown' if attempted else 'failed'),
                               phase='response_saved' if saved else ('dispatch_uncertain' if attempted else 'preflight_failed'),
                               error_type=type(error).__name__,message='保存応答のローカル再処理が必要です。API再送はしません。' if saved else ('送信結果を確認できません。予約を保持し、API再送しません。' if attempted else '送信前に失敗しました。'))
                    if not attempted:job['reserved']=0
                    self.persist(job)
                if isinstance(error,(OSError,core.sqlite3.Error)):self.fault(error)
            finally:
                if job:
                    try:
                        latest=self.store.job(ident)
                        latest['elapsed']=round(time.time()-started,2)
                        self.persist(latest)
                    except Exception as error:self.fault(error)
                self.current=None

    def reprocess(self,ident):
        # This path only decodes/saves local bytes. It cannot invoke either provider.
        with self.execution:
            try:return self._reprocess(ident)
            except Exception as error:
                self.fault(error)
                raise ValueError('ローカル再処理に失敗しました。ワーカー状態と保存先を確認してください。API再送はしていません。') from None

    def _reprocess(self,ident):
        s=self.store;job=s.job(ident)
        if job['status']=='queued':raise ValueError('待機中のジョブは再処理できません。')
        file=s.response_file(ident)
        if not file:raise ValueError('保存済みの応答がありません。')
        p=job['params'];job['recovery']=True;job['phase']='response_saved'
        try:
            body=json.loads(file.read_bytes())
            usage=body.get('usage');job['usage']=usage
            if job.get('estimate') is None:job['estimate']=core.usage_cost(usage)
            job.setdefault('price_checked',core.CAP['checked'])
            job['request_id']=body.get('request_id') or job.get('request_id')
            sidecar=s.path/('response_meta_'+ident+'.json')
            if sidecar.exists():job['request_id']=json.loads(sidecar.read_bytes()).get('request_id') or job['request_id']
            items=body['images'] if 'images' in body else [item.get('b64_json') for item in body['data']]
            if not isinstance(items,list) or not items:raise ValueError('No image items')
            job['phase']='parsed'
        except Exception as error:
            job.update(status='local_error',error_type=type(error).__name__,message='保存応答の解析に失敗しました。生応答をダウンロードできます。API再送はしません。')
            if not self.persist(job):raise OSError('State persistence failed')
            return job
        if not self.persist(job):raise OSError('State persistence failed')
        legacy=not job.get('processing_items')
        previous=job.get('processing_items',{})
        raw_old=[o for o in job.get('outputs',[]) if o.get('kind')=='raw']
        composite_old=[o for o in job.get('outputs',[]) if o.get('kind')=='composite']
        outputs=[];errors=[];warnings=[]
        for index,encoded in enumerate(items):
            record=previous.setdefault(str(index),{})
            try:
                if legacy and 'raw' not in record and index<len(raw_old):record['raw']=raw_old[index]
                if 'raw' not in record:
                    content=base64.b64decode(encoded,validate=True)
                    record['raw']=s.asset(content,'API生出力' if p['provider']=='openai' else 'モック出力',p.get('target'),'raw',identity=f'{ident}:raw:{index}')
                result=record['raw'];s.file(result['id'])
                outputs.append(result)
                if (result['width'],result['height'])!=(p['width'],p['height']):warnings.append('要求寸法と実寸法が異なります。')
                if p['mode']=='inpaint' and p.get('composite'):
                    if legacy and 'composite' not in record and index<len(composite_old):record['composite']=composite_old[index]
                    if 'composite' not in record:
                        original=normalize(s.file(p['target']).read_bytes())
                        mask=mask_image(original.size,p['strokes'])
                        merged=composite(original,normalize(s.file(result['id']).read_bytes()),mask,p['feather'])
                        record['composite']=s.asset(png(merged),'局所合成',p['target'],'composite',identity=f'{ident}:composite:{index}')
                    outputs.append(record['composite'])
                record.pop('error',None)
            except Exception as error:
                record['error']=type(error).__name__;errors.append(f'画像{index+1}: デコードまたは合成失敗。取得済み画像を保持しています。')
                if isinstance(error,(OSError,core.sqlite3.Error)):self.fault(error)
            # Persist progress after every image. Deterministic identities recover even if this fails.
            job.update(processing_items=previous,outputs=outputs,phase='images_acquired')
            if not self.persist(job):raise OSError('State persistence failed')
        exported=job.setdefault('exported',{})
        for output in outputs:
            try:
                if output['id'] not in exported:
                    exported[output['id']]=s.export(output['id'],operation=f'{ident}-{output["id"]}')
            except OSError:errors.append('保存先に書き込めません。PNGダウンロードで回収し、再処理で書き出しを再試行できます。')
        job.update(saved_paths=list(exported.values()),status='local_error' if errors else 'completed',
                   phase='local_processing_failed' if errors else 'exported',local_errors=errors,
                   message=' / '.join(errors+warnings) or ('完了' if p['provider']=='openai' else 'モック処理が完了しました。画質評価には使えません。'))
        if len(items)!=p.get('n',1):job['message']+=' / 要求枚数と取得枚数が異なります。'
        if exported:job['saved_path']=list(exported.values())[-1]
        if not self.persist(job):raise OSError('State persistence failed')
        return job
