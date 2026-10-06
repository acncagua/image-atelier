"""Manual shared-GPU session. No automatic Strata resize, server control or release."""
import copy
import json
import math
import threading
import time
import uuid
import httpx
import comfy_backend as comfy
import strata_client as strata
import gpu_status
from persistence import atomic_write
from local_config import strata_api_key

SHORTFALL_TOLERANCE_MIB=1024

def capacity_accepted(actual,target):
    return type(actual) in (int,float) and math.isfinite(actual) and actual>0 and target-actual<=SHORTFALL_TOLERANCE_MIB

def observed_free(gpu,peer):
    value=peer.get('vram',{}).get('vram_free_mib',gpu['free_mib'])
    return min(gpu['free_mib'],value) if type(value) in (int,float) and math.isfinite(value) and value>=0 else None

class VRAMManager:
    def __init__(self,store):
        self.store=store;self.lock=threading.RLock();self.thread=None;self.workers=()
        self.file=store.path/'vram-session.json';self.audit=store.path/'vram-operations.jsonl'
        self.state={'phase':'unreserved','busy':False,'message':'VRAM未確保','peer':{'state':'unobserved'},'gpu':None,'gpus':[],
                    'lease':None,'resume_pending':False,'pending':None,'comfy_confirmed':False,'comfy_anchor':None,'context':None,'observed_at':None,'revision':0}
        if self.file.exists():
            try:
                previous=json.loads(self.file.read_text('utf-8'))
                for k in ('resume_pending','pending','context'):self.state[k]=previous.get(k)
                self.state.update(phase='unknown' if previous.get('pending') else 'unconfirmed',message='再起動後の状態は未確認です。保存済みの確保状態は復元しません。')
            except (OSError,ValueError):self.state.update(phase='unknown',message='前回のVRAM操作記録を確認できません。')

    def bind(self,*workers):self.workers=workers
    def config(self):return strata.validate_settings(self.store.settings().get('strata',{}))
    def snapshot(self):
        with self.lock:return copy.deepcopy(self.state)
    def set(self,**values):
        with self.lock:
            self.state.update(copy.deepcopy(values));atomic_write(self.file,json.dumps(self.state,ensure_ascii=False).encode())
    def log(self,action,result,config,elapsed):
        s=self.snapshot();row={'time':time.time(),'action':action,'strata_url':config['url'],'gpu':(s.get('gpu') or {}).get('uuid'),
            'target_mib':strata.target_mib(config),'free_mib':(s.get('gpu') or {}).get('free_mib'),'result':result,'elapsed':round(elapsed,2)}
        with self.audit.open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    def managed(self):
        c=self.config();s=self.snapshot()
        return c['enabled'] or bool(s['lease'] or s['resume_pending'] or s['pending'])
    def busy_guard(self):
        s=self.snapshot()
        if s['busy']:raise ValueError('VRAMの確保・解放中です。完了を待ってください。')
        if s['pending']:raise ValueError('VRAM操作の結果が不明です。先に状態を再確認してください。')
    def settings_guard(self,new):
        old=self.config();s=self.snapshot()
        if new==old:return
        if s['busy']:raise ValueError('VRAM操作中はStrata設定を変更できません。')
        if (s['lease'] or s['resume_pending'] or s['pending']) and any(new[k]!=old[k] for k in ('url','gpu_uuid','target_gib')):
            raise ValueError('接続先・GPU・目標容量の変更前に、一度「画像生成用VRAMを解放」を実行してください。')
    def comfy_url_guard(self,old,new):
        s=self.snapshot()
        if old!=new and (s['lease'] or s['resume_pending'] or s['pending'] or s['busy']):raise ValueError('ComfyUI接続先の変更前にVRAMを解放してください。')
        if old!=new:self.set(comfy_confirmed=False,comfy_anchor=None)

    def _idle(self,c):
        # The common GPU lock handles running PE/SwinIR. Pending image jobs matter too.
        for index,worker in enumerate(self.workers):
            current=getattr(worker,'current',None)
            if current and (index>0 or self.store.job(current).get('local_machine',{}).get('backend')=='comfyui'):raise ValueError('アプリのGPU処理が実行中です。')
        if any(j['status'] in ('queued','sending') and j.get('local_machine',{}).get('backend')=='comfyui' for j in self.store.jobs()):
            raise ValueError('ComfyUIの生成ジョブが待機・実行中です。')
        if any(j['status']=='unknown' and j.get('local_machine',{}).get('backend')=='comfyui' for j in self.store.jobs()):raise ValueError('ComfyUIに結果不明のジョブがあります。先に中断ジョブを照合してください。')
        for worker in self.workers[1:]:
            if hasattr(worker,'jobs') and any(j.get('status') in ('queued','running','loading','upscaling','adjusting','saving','cancel_requested') for j in worker.jobs()):raise ValueError('GPUジョブが待機・実行中です。')
        running,pending=comfy.queue_ids(c)
        if running or pending:raise ValueError('ComfyUIの実行中・待機中キューがあります。他のジョブは取消しません。')

    def _observe(self,config,expected_revision=None):
        peer=strata.probe(config) if config['enabled'] else {'state':'disabled','message':'Strata連携無効'}
        devices=gpu_status.inventory();gpu=next((g for g in devices if not config['gpu_uuid'] or g['uuid']==config['gpu_uuid']),None) if len(devices)==1 else None
        with self.lock:
            if expected_revision is not None and (self.state['busy'] or self.state['revision']!=expected_revision):return None,None
            self.set(peer=peer,gpus=devices,gpu=gpu,observed_at=time.time())
        return peer,devices
    @staticmethod
    def _peer_signature(peer):return json.dumps(peer.get('vram') or {},sort_keys=True)
    def _still_unloaded(self,c,anchor):
        if not anchor:return False
        running,pending=comfy.queue_ids(c)
        history=comfy.get(c,'/history?max_items=1')
        return not running and not pending and list(history)==[anchor] and bool(history[anchor].get('status',{}).get('completed'))
    def _lease_matches(self,lease,config,peer,comfy_url):
        return lease['strata_url']==config['url'] and lease['comfy_url']==comfy_url and lease['target_mib']==strata.target_mib(config) and (not config['gpu_uuid'] or lease['gpu_uuid']==config['gpu_uuid']) and lease['peer_identity']==peer.get('identity') and lease['vram_signature']==self._peer_signature(peer)

    def status(self,refresh=True):
        config=self.config();s=self.snapshot()
        if refresh and not s['busy']:
            peer,devices=self._observe(config,s['revision'])
            if peer is None:return self.status(refresh=False)
            s=self.snapshot()
            # A saved operation may have completed after its HTTP acknowledgement was lost.
            pending=s.get('pending')
            if pending and pending['kind']=='free':
                try:
                    with comfy.client(comfy.configuration(self.store)) as c:
                        markers=pending.get('markers',[])
                        instance=gpu_status.local_server_instance(str(c.base_url))
                        if instance and pending.get('comfy_instance') and instance!=pending['comfy_instance']:
                            self.set(pending=None,lease=None,comfy_confirmed=False,comfy_anchor=None,phase='unconfirmed',message='ComfyUIの再起動を確認しました。再度解放してください。')
                        elif pending.get('free_accepted') and len(markers)>=3:
                            last=markers[-1];record=comfy.get(c,'/history/'+last).get(last,{})
                            if record.get('status',{}).get('completed'):
                                self.set(pending=None,lease=None,comfy_confirmed=True,comfy_anchor=last,phase='partially_released',message='ComfyUI解放完了を再確認しました。解放ボタンでStrata復帰を続行できます。')
                except (httpx.HTTPError,ValueError):pass
            if pending and pending['kind'] in ('reserve','resume') and config['enabled']:
                if peer.get('identity') and pending.get('peer_identity') and pending['peer_identity'][0]!=peer['identity'][0]:
                    self.set(pending=None,lease=None,resume_pending=False,comfy_confirmed=False,phase='unconfirmed',message='Strataの再起動・モデル変更を検出しました。確保状態は未確認です。')
                elif peer.get('identity')==pending.get('peer_identity') and self._peer_signature(peer)!=pending.get('before_signature'):
                    applied=peer.get('vram',{}).get('reserve_mib')
                    if pending['kind']=='reserve' and applied==pending['target_mib']:
                        with comfy.client(comfy.configuration(self.store)) as c:clean=self._still_unloaded(c,s['comfy_anchor'])
                        if not clean:self.set(pending=None,resume_pending=True,lease=None,comfy_confirmed=False,phase='unconfirmed',message='変更の適用後にComfyUIの処理を検出しました。一度解放してください。')
                        elif peer['state']!='loaded' or not s['gpu'] or s['gpu']['uuid']!=pending.get('gpu_uuid'):
                            self.set(pending=None,resume_pending=True,lease=None,phase='unconfirmed',message='変更の適用値を確認しましたが、Strata・対象GPUの状態を照合できません。一度解放してください。')
                        elif capacity_accepted(observed_free(s['gpu'],peer),pending['target_mib']):
                            self._reserved(config,peer,s['gpu'],pending['comfy_url'],observed_free(s['gpu'],peer))
                        else:self.set(pending=None,resume_pending=True,phase='insufficient',message='変更の適用を確認しましたが、実際の空きが不足しています。')
                    elif pending['kind']=='resume' and peer.get('vram',{}).get('reserve_mib')!=pending.get('previous_reserve'):
                        self.set(pending=None,resume_pending=False,phase='released',message='ComfyUI解放済み。Strata復帰要求の適用を状態APIで確認しました。')
            s=self.snapshot()
            if s['lease'] and config['enabled']:
                try:
                    with comfy.client(comfy.configuration(self.store)) as c:
                        marker=comfy.get(c,'/history/'+s['comfy_anchor']).get(s['comfy_anchor']) if s['comfy_anchor'] else None
                    if not marker or not self._lease_matches(s['lease'],config,peer,comfy.configuration(self.store)['url']):
                        self.set(lease=None,comfy_confirmed=False,phase='unconfirmed',message='再起動・再接続・Strata状態変更を検出しました。一度解放して確保し直してください。')
                except (httpx.HTTPError,ValueError):self.set(lease=None,comfy_confirmed=False,phase='unconfirmed',message='ComfyUIの確保状態を再確認できません。')
        s=self.snapshot();peer=s['peer']
        s.update(config=config,managed=self.managed(),ready_for_generation=not s['busy'] and not s['pending'] and (not config['enabled'] or peer['state'] in ('offline','unloaded') or s['lease'] is not None))
        s['operation_elapsed']=round(time.time()-s.get('operation_started',time.time()),1) if s['busy'] else None
        available=self.store.gpu_execution.acquire(blocking=False)
        s['gpu_jobs_busy']=not available
        if available:self.store.gpu_execution.release()
        try:s['key_set']=bool(strata_api_key())
        except ValueError:s['key_set']=False
        return s

    def registration_guard(self):
        self.busy_guard();c=self.config();s=self.snapshot()
        if c['enabled'] and s['peer']['state'] not in ('offline','unloaded') and not s['lease']:
            raise ValueError('生成前に「画像生成用VRAMを確保」を実行してください。初回・状態未確認時は先に解放してください。')
    def before_generation(self,comfy_config):
        self.busy_guard();c=self.config()
        if c['enabled']:
            s=self.status();self.registration_guard()
            if s['lease'] and not self._lease_matches(s['lease'],c,s['peer'],comfy_config['url']):raise ValueError('確保条件が変わりました。一度解放して確保し直してください。')
        with self.lock:self.state['comfy_confirmed']=False
    def start(self,action):
        config=self.config()
        if action=='acquire' and not config['enabled']:raise ValueError('Strata連携を設定で有効にしてください。')
        if not self.store.gpu_execution.acquire(blocking=False):raise ValueError('GPU処理中です。完了後に操作してください。')
        try:
            with comfy.client(comfy.configuration(self.store)) as c:self._idle(c)
            with self.store.lock,self.lock:
                if self.state['busy']:raise ValueError('VRAM操作は実行中です。')
                if self.state['pending']:raise ValueError('前回の結果が不明です。状態確認後に操作してください。')
                if any(j['status'] in ('queued','sending') and j.get('local_machine',{}).get('backend')=='comfyui' for j in self.store.jobs()):raise ValueError('生成ジョブが待機・実行中です。')
                self.state.update(busy=True,phase='acquiring' if action=='acquire' else 'comfy_releasing',operation_started=time.time(),message='LLM処理終了を待ってVRAMを確保します。' if action=='acquire' else 'ComfyUIのモデルを解放中です。')
                self.state['revision']+=1
        finally:self.store.gpu_execution.release()
        self.thread=threading.Thread(target=self._run,args=(action,config),daemon=True);self.thread.start()
        return self.status(refresh=False)

    def _reserved(self,config,peer,gpu,comfy_url,actual=None):
        target=strata.target_mib(config);actual=gpu['free_mib'] if actual is None else actual
        shortage=max(0,target-actual)
        lease={'strata_url':config['url'],'comfy_url':comfy_url,'gpu_uuid':gpu['uuid'],'target_mib':target,'peer_identity':peer.get('identity'),'vram_signature':self._peer_signature(peer),
               'free_mib_at_acquire':actual,'shortfall_mib':shortage,'tolerance_mib':SHORTFALL_TOLERANCE_MIB}
        message='画像生成用VRAM確保済み。'
        if shortage:message+=f'要求{target/1024:g}GiB／実際{actual/1024:.2f}GiB。不足{shortage/1024:.2f}GiBは許容1GiB以内です。'
        self.set(lease=lease,context=lease,pending=None,resume_pending=True,phase='acquired',message=message+'手動解放までComfyUIモデルを保持します。')
    def _run(self,action,config):
        started=time.monotonic();locked=False
        try:
            locked=self.store.gpu_execution.acquire(blocking=False)
            if not locked:raise ValueError('GPU処理が実行中です。完了後に操作してください。')
            with comfy.client(comfy.configuration(self.store)) as c:
                self._idle(c)
                if action=='acquire':self._acquire(c,config)
                else:self._release(c,config)
        except Exception as error:
            s=self.snapshot();message=strata.safe_error(error) if isinstance(error,httpx.HTTPError) else str(error)[:300]
            if s['pending'] and s['pending']['kind']=='free' and not s['pending'].get('free_submitted'):self.set(pending=None)
            if isinstance(error,httpx.HTTPStatusError) and error.response.status_code<500 and s['pending'] and s['pending']['kind']=='free' and not s['pending'].get('free_accepted'):self.set(pending=None)
            s=self.snapshot()
            if action=='release' and s['comfy_confirmed'] and not message.startswith('ComfyUI解放済み'):message='ComfyUI解放済み。Strata復帰失敗または未確認: '+message
            self.set(phase='unknown' if s['pending'] else 'partially_released' if s['comfy_confirmed'] and s['resume_pending'] else 'failed',message=message)
        finally:
            try:
                self.log(action,self.snapshot()['phase'],config,time.monotonic()-started)
            finally:
                if locked:self.store.gpu_execution.release()
                self.set(busy=False)

    def _acquire(self,c,config):
        peer,devices=self._observe(config);s=self.snapshot();target=strata.target_mib(config)
        if peer['state'] in ('offline','unloaded'):
            self.set(phase='not_needed',message='StrataのVRAM変更は不要です。通常生成を利用できます。');return
        if peer['state']!='loaded':raise ValueError(peer['message'])
        gpu=gpu_status.verified_device(config,devices,comfy.get(c,'/system_stats'),peer)
        if s['lease']:
            if self._lease_matches(s['lease'],config,peer,str(c.base_url).rstrip('/')):
                self.set(phase='acquired',message='確保済みです。同じ空き要求は送り直しません。');return
            raise ValueError('確保条件が変わりました。一度解放して確保し直してください。')
        marker=comfy.get(c,'/history/'+s['comfy_anchor']).get(s['comfy_anchor']) if s['comfy_anchor'] else None
        if not s['comfy_confirmed'] or not marker or not self._still_unloaded(c,s['comfy_anchor']):raise ValueError('ComfyUIに保持モデルが残っている可能性があります。先に「画像生成用VRAMを解放」を実行してください。')
        pending={'kind':'reserve','target_mib':target,'gpu_uuid':gpu['uuid'],'peer_identity':peer['identity'],'before_signature':self._peer_signature(peer),'comfy_url':str(c.base_url).rstrip('/')}
        self.set(pending=pending,context=pending,message='VRAM確保中。StrataのLLM処理終了を待っています。')
        try:result=strata.change(config,target)
        except httpx.HTTPStatusError as error:
            if error.response.status_code<500:self.set(pending=None)
            raise
        peer,devices=self._observe(config)
        if peer.get('identity')!=pending['peer_identity']:raise ValueError('操作中にStrataの状態が変わりました。')
        gpu=gpu_status.verified_device(config,devices,comfy.get(c,'/system_stats'),peer)
        actual=min(gpu['free_mib'],result.get('vram_free_mib',gpu['free_mib']))
        self.set(pending=None,resume_pending=True,gpu={**gpu,'free_mib':actual})
        if result['status']=='not loaded' or peer['state']=='unloaded':
            self.set(lease=None,phase='not_needed',message='Strataモデルは未ロードです。通常生成を利用できます。変更要求は次のロード時に適用されるため、終了後は手動解放してください。');return
        if not self._still_unloaded(c,s['comfy_anchor']):
            self.set(lease=None,comfy_confirmed=False,phase='unconfirmed',message='確保待ち中に別のComfyUI処理を検出しました。一度解放してください。');return
        if not capacity_accepted(actual,target):self.set(phase='insufficient',message=f'必要な空きを確保できませんでした。要求{target/1024:g}GiB／実際{actual/1024:.2f}GiB。不足の許容は1GiB以内です。');return
        self._reserved(config,peer,gpu,pending['comfy_url'],actual)

    def _release(self,c,config):
        s=self.snapshot()
        if not s['comfy_confirmed'] or s['lease'] or not self._still_unloaded(c,s['comfy_anchor']):
            operation={'kind':'free','comfy_url':str(c.base_url).rstrip('/'),'comfy_instance':gpu_status.local_server_instance(str(c.base_url))};self.set(pending=operation,comfy_confirmed=False)
            result=comfy.confirmed_free(c,operation,lambda:self.set(pending=operation))
            self.set(pending=None,lease=None,comfy_confirmed=True,comfy_anchor=result['anchor'],comfy_confirmation=result['method'])
        if not config['enabled']:
            self.set(phase='partially_released' if self.snapshot()['resume_pending'] else 'released',message='ComfyUI解放済み。Strata連携無効のため復帰要求は送信していません。');return
        peer,devices=self._observe(config)
        if peer['state']=='offline':self.set(phase='released',resume_pending=False,message='ComfyUI解放済み。Strata未起動です。');return
        if peer['state']=='unloaded' and not self.snapshot()['resume_pending']:
            self.set(phase='released',message='ComfyUI解放済み。Strataモデル未ロードです。');return
        if peer['state'] not in ('loaded','unloaded'):raise ValueError('ComfyUI解放済み。'+peer['message'])
        gpu_status.verified_device(config,devices,comfy.get(c,'/system_stats'),peer)
        pending={'kind':'resume','peer_identity':peer['identity'],'before_signature':self._peer_signature(peer),'previous_reserve':peer.get('vram',{}).get('reserve_mib')}
        self.set(pending=pending,resume_pending=True,phase='strata_resuming',message='ComfyUI解放済み。StrataのVRAM使用量を戻しています。')
        try:strata.change(config,None)
        except httpx.HTTPStatusError as error:
            if error.response.status_code<500:self.set(pending=None)
            raise
        self._observe(config);self.set(pending=None,resume_pending=False,phase='released',message='ComfyUI解放済み。Strata復帰要求完了（元の使用量・速度への完全復帰を保証するものではありません）。')
