import copy
import json
import math
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
import comfy_backend as c
import strata_client as strata
import gpu_status
from core import Store,Worker
import test_comfy as fixtures

GPU={'index':0,'uuid':'GPU-test','name':'NVIDIA Test GPU','pci_bus':'0000:01:00.0','total_mib':32768,'free_mib':24576,'at':1,'source':'mock'}
REAL_STRATA_CLIENT=strata.client

class VRAM(unittest.TestCase):
    setUpBase=fixtures.Comfy.setUpBase
    tearDownBase=fixtures.Comfy.tearDown
    params=fixtures.Comfy.params
    def setUp(self):
        self.setUpBase();self.upscale_manager=self.manager;self.manager=self.s.vram
        self.config=c.configure(self.s,{'diffusion':'qwen21.safetensors','text_encoder':'qwen3vl.safetensors','vae':'qwen21vae.safetensors'})
        self.calls=[];self.histories={};self.queue=[];self.graph=None;self.ident=None;self.ambiguous=False;self.cancel_upload=False
        self.elastic=True;self.loaded=True;self.started=100;self.reserve=2048;self.return_code=200;self.free_result=24576;self.gpu=dict(GPU);self.pending_strata=False;self.comfy_free_error=False
        self.entered=threading.Event();self.finish=threading.Event();self.pause=False
        self.generation_error=False;self.cancel_after_post=False
        self.patches=[patch.object(c,'client',self.comfy_client),patch.object(strata,'client',self.strata_client),patch('gpu_status.inventory',lambda:[dict(self.gpu)]),patch('vram_manager.strata_api_key',return_value=''),patch('strata_client.strata_api_key',return_value='')]
        for p in self.patches:p.start()
    def tearDown(self):
        self.finish.set()
        if self.manager.thread:self.manager.thread.join(5)
        for p in reversed(self.patches):p.stop()
        self.manager=self.upscale_manager
        self.tearDownBase()
    def comfy_client(self,config):return httpx.Client(base_url=config['url'],transport=httpx.MockTransport(self.comfy_handler))
    def strata_client(self,config):return httpx.Client(base_url=config['url'],transport=httpx.MockTransport(self.strata_handler))
    def comfy_handler(self,r):
        path=r.url.path
        if path=='/system_stats':return httpx.Response(200,json={'system':{'comfyui_version':'0.38.0'},'devices':[{'type':'cuda','index':0,'name':'cuda:0 '+GPU['name'],'torch_vram_total':64*2**20}]})
        if path=='/queue' and r.method=='GET':return httpx.Response(200,json={'queue_running':self.queue,'queue_pending':[]})
        if path=='/object_info':
            info=fixtures.Comfy.handler(self,r).json();info.update(EmptyImage={},PreviewImage={});return httpx.Response(200,json=info)
        if path=='/prompt':
            body=json.loads(r.content);ident=body['prompt_id'];self.calls.append(('POST',path,r.content))
            if body['prompt']['1']['class_type']=='EmptyImage':
                self.histories[ident]={'status':{'completed':True,'status_str':'success'}}
                return httpx.Response(200,json={'prompt_id':ident})
            self.graph=body['prompt'];self.ident=ident
            self.histories[ident]={'status':{'completed':True,'status_str':'success'},'outputs':{'8':{'images':[{'filename':'out.png','type':'output','subfolder':''}]}}}
            if self.generation_error:self.histories[ident]={'status':{'completed':False,'status_str':'error'}}
            if self.cancel_after_post:c.cancel(self.s,ident)
            if self.ambiguous:raise httpx.ReadTimeout('lost acknowledgement')
            return httpx.Response(200,json={'prompt_id':ident})
        if path.startswith('/history/'):return httpx.Response(200,json={path.split('/')[-1]:self.histories[path.split('/')[-1]]} if path.split('/')[-1] in self.histories else {})
        if path=='/history':
            keys=list(self.histories)[-1:];return httpx.Response(200,json={k:self.histories[k] for k in keys})
        if path=='/free' and self.comfy_free_error:
            self.calls.append(('POST',path,r.content));return httpx.Response(400,json={'error':'not freed'})
        return fixtures.Comfy.handler(self,r)
    def strata_handler(self,r):
        self.calls.append((r.method,'strata'+r.url.path,r.content))
        if r.url.path=='/health':return httpx.Response(200,json={'service':'strata','loaded':self.loaded})
        if r.url.path=='/v1/status':return httpx.Response(200,json={'service':'strata','loaded':self.loaded,'model':'Test LLM','engine':'0.1.39','started':self.started,
            'vram':{'elastic':self.elastic,'reserve_mib':self.reserve,'expert_slots':100 if self.reserve==2048 else 50},'machine':{'gpu':{'name':GPU['name'],'total_mib':32768}},'activity':{'in_flight':0}})
        if r.url.path=='/v1/vram':
            self.entered.set()
            if self.pause:self.finish.wait(5)
            if self.return_code!=200:return httpx.Response(self.return_code,json={'error':{'message':'unsupported low-RAM mode'}})
            requested=json.loads(r.content)['reserve_mib']
            if self.pending_strata:raise httpx.ReadTimeout('may apply later')
            self.reserve=2048 if requested is None else requested
            return httpx.Response(200,json={'status':'ok','reserve_mib':self.reserve,'vram_free_mib':self.free_result,'expert_cache_mib':4000})
        return httpx.Response(404)
    def enable(self,**extra):self.s.set_settings({**self.s.settings(),'strata':{**strata.DEFAULTS,'enabled':True,**extra}})
    def action(self,kind):
        self.manager.start(kind);self.manager.thread.join(5);self.assertFalse(self.manager.thread.is_alive());return self.manager.status()
    def prepare(self):
        self.action('release');self.enable();self.manager.status();self.calls.clear()
    def posts(self,path):return [json.loads(body) for method,p,body in self.calls if method=='POST' and p==path]

    def test_disabled_generation_and_standalone_free_do_not_contact_strata(self):
        job=self.s.submit(self.params());Worker(self.s).run(job['id']);self.assertEqual(self.s.job(job['id'])['status'],'completed')
        self.assertTrue(self.posts('/free'));self.action('release')
        self.assertFalse(any(p.startswith('strata') for _,p,_ in self.calls))

    def test_reserve_two_generations_keep_models_and_manual_free_before_null(self):
        self.prepare();s=self.action('acquire');self.assertEqual(s['phase'],'acquired');self.assertEqual(self.posts('strata/v1/vram'),[{'reserve_mib':18432}])
        for _ in range(2):
            job=self.s.submit(self.params());Worker(self.s).run(job['id']);self.assertEqual(self.s.job(job['id'])['status'],'completed')
        c.select_session(self.s,False,False)
        with self.comfy_client(self.config) as client:c.release_models(self.s,client)
        c.release_for_other_gpu(self.s)
        self.assertEqual(self.posts('/free'),[]);self.assertEqual(len(self.posts('strata/v1/vram')),1)
        self.action('acquire');self.assertEqual(len(self.posts('strata/v1/vram')),1)
        self.calls.clear();s=self.action('release');self.assertEqual(s['phase'],'released')
        self.assertEqual(self.posts('/free'),[{'unload_models':True,'free_memory':True}]);self.assertEqual(self.posts('strata/v1/vram'),[{'reserve_mib':None}])
        resume=next(i for i,(_,p,_) in enumerate(self.calls) if p=='strata/v1/vram')
        self.assertGreater(resume,max(i for i,(_,p,_) in enumerate(self.calls) if p=='/prompt'))

    def test_startup_requires_free_and_gib_conversion_and_capacity_are_validated(self):
        self.enable();self.manager.status();s=self.action('acquire');self.assertEqual(s['phase'],'failed');self.assertIn('先に',s['message']);self.assertEqual(self.posts('strata/v1/vram'),[])
        self.assertEqual(strata.target_mib(strata.DEFAULTS),18432)
        self.assertEqual(strata.target_mib({**strata.DEFAULTS,'target_gib':18.001}),math.ceil(18.001*1024))
        for value in (-1,0,float('nan'),float('inf'),True):
            with self.assertRaises(ValueError):strata.validate_settings({'target_gib':value})
        with self.assertRaises(ValueError):strata.validate_settings({'change_timeout':600})
        config={**strata.DEFAULTS,'target_gib':40}
        with self.assertRaises(ValueError):gpu_status.verified_device(config,[self.gpu],{'devices':[{'type':'cuda','index':0,'name':'cuda:0 '+GPU['name']}]})
        with self.assertRaises(ValueError):gpu_status.verified_device(strata.DEFAULTS,[{**self.gpu,'free_mib':float('nan')}],{'devices':[]})

    def test_busy_llm_and_double_click_block_generation_until_response(self):
        self.prepare();self.pause=True;self.manager.start('acquire');self.assertTrue(self.entered.wait(2))
        self.assertTrue(self.manager.status()['busy'])
        with self.assertRaises(ValueError):self.manager.start('release')
        with self.assertRaises(ValueError):self.s.submit(self.params())
        self.finish.set();self.manager.thread.join(5);self.assertEqual(self.manager.status()['phase'],'acquired')

    def test_insufficient_free_does_not_become_acquired(self):
        self.prepare();self.free_result=14*1024;self.gpu['free_mib']=14*1024;s=self.action('acquire')
        self.assertEqual(s['phase'],'insufficient');self.assertIn('18GiB',s['message']);self.assertIn('14.00GiB',s['message']);self.assertFalse(s['ready_for_generation'])

    def test_shortfall_up_to_one_gib_is_allowed_but_more_is_rejected(self):
        for shortage in (156,1024,1025):
            with self.subTest(shortage=shortage):
                self.prepare();self.free_result=18432-shortage;self.gpu['free_mib']=24576
                s=self.action('acquire')
                self.assertEqual(s['phase'],'acquired' if shortage<=1024 else 'insufficient')
                self.assertEqual(s['ready_for_generation'],shortage<=1024)
                if shortage<=1024:
                    self.assertEqual(s['lease']['shortfall_mib'],shortage)
                    self.assertIn('許容1GiB以内',s['message'])
                self.action('release')
        self.prepare();self.free_result=24576;self.gpu['free_mib']=17407
        self.assertEqual(self.action('acquire')['phase'],'insufficient')

    def test_uncertain_reserve_reconciliation_uses_same_shortfall_tolerance(self):
        self.prepare();self.pending_strata=True
        self.assertEqual(self.action('acquire')['phase'],'unknown')
        self.reserve=18432;self.gpu['free_mib']=17408
        s=self.manager.status()
        self.assertEqual(s['phase'],'acquired');self.assertEqual(s['lease']['shortfall_mib'],1024)

    def test_queue_and_common_gpu_lock_refuse_ops_without_cancelling_others(self):
        self.enable();self.queue=[[0,'other-client',{}, {},[]]]
        for kind in ('acquire','release'):
            with self.assertRaises(ValueError):self.manager.start(kind)
        self.assertEqual(self.posts('/interrupt'),[]);self.assertEqual(self.posts('/queue'),[]);self.assertEqual(self.posts('/free'),[])
        self.queue=[];entered=threading.Event();release=threading.Event()
        def hold():
            with self.s.gpu_execution:entered.set();release.wait(5)
        thread=threading.Thread(target=hold);thread.start();entered.wait(2)
        try:
            with self.assertRaises(ValueError):self.manager.start('release')
        finally:release.set();thread.join(5)

    def test_free_failure_prevents_resume_and_resume_failure_is_retryable(self):
        self.prepare();self.action('acquire');self.calls.clear();self.comfy_free_error=True;s=self.action('release')
        self.assertEqual(self.posts('strata/v1/vram'),[]);self.assertFalse(s['comfy_confirmed'])
        self.comfy_free_error=False;self.return_code=400;s=self.action('release')
        self.assertEqual(s['phase'],'partially_released');self.assertTrue(s['comfy_confirmed'])
        self.calls.clear();self.return_code=200;s=self.action('release')
        self.assertEqual(s['phase'],'released');self.assertEqual(self.posts('/free'),[]);self.assertEqual(self.posts('strata/v1/vram'),[{'reserve_mib':None}])

    def test_timeout_blocks_opposite_request_until_status_confirms_application(self):
        self.prepare();self.pending_strata=True;s=self.action('acquire');self.assertEqual(s['phase'],'unknown')
        with self.assertRaises(ValueError):self.manager.start('release')
        self.assertEqual(self.posts('strata/v1/vram'),[{'reserve_mib':18432}])
        self.reserve=18432;s=self.manager.status();self.assertEqual(s['phase'],'acquired');self.assertIsNone(s['pending'])

    def test_restart_config_changes_disabling_and_missing_telemetry_are_conservative(self):
        self.prepare();self.action('acquire')
        with self.assertRaises(ValueError):self.manager.settings_guard({**self.manager.config(),'target_gib':20})
        with self.assertRaises(ValueError):self.manager.comfy_url_guard(self.config['url'],'http://127.0.0.1:8189')
        from vram_manager import VRAMManager
        restarted=VRAMManager(self.s);self.assertIsNone(restarted.snapshot()['lease']);self.assertEqual(restarted.snapshot()['phase'],'unconfirmed')
        self.started=200;s=self.manager.status();self.assertIsNone(s['lease']);self.assertFalse(s['ready_for_generation'])
        self.s.set_settings({**self.s.settings(),'strata':{**self.manager.config(),'enabled':False}});self.calls.clear();self.manager.status()
        self.assertFalse(any(p.startswith('strata') for _,p,_ in self.calls))
        with patch('gpu_status.inventory',return_value=[]):
            s=self.manager.status();self.assertIsNone(s['gpu'])

    def test_offline_unloaded_auth_and_elastic_states_are_not_confused(self):
        self.enable();self.loaded=False;s=self.manager.status();self.assertEqual(s['peer']['state'],'unloaded');self.assertTrue(s['ready_for_generation'])
        self.loaded=True;self.elastic=False;s=self.manager.status();self.assertEqual(s['peer']['state'],'unsupported');self.assertFalse(s['ready_for_generation'])
        def refused(r):raise httpx.ConnectError('[WinError 10061] actively refused')
        with patch.object(strata,'client',lambda cfg:httpx.Client(base_url=cfg['url'],transport=httpx.MockTransport(refused))):
            s=self.manager.status();self.assertEqual(s['peer']['state'],'offline');self.assertTrue(s['ready_for_generation'])
            job=self.s.submit(self.params());Worker(self.s).run(job['id']);self.assertEqual(self.s.job(job['id'])['status'],'completed')
            self.calls.clear();s=self.action('release');self.assertTrue(self.posts('/free'));self.assertEqual(self.posts('strata/v1/vram'),[])
        with patch.object(strata,'client',lambda cfg:httpx.Client(base_url=cfg['url'],transport=httpx.MockTransport(lambda r:httpx.Response(401)))):
            s=self.manager.status();self.assertEqual(s['peer']['state'],'auth_error');self.assertFalse(s['ready_for_generation'])

    def test_failure_and_cancellation_do_not_auto_unload_or_resume(self):
        self.prepare();self.action('acquire');self.calls.clear();self.generation_error=True
        job=self.s.submit(self.params());Worker(self.s).run(job['id']);self.assertEqual(self.s.job(job['id'])['status'],'unknown')
        self.assertEqual(self.posts('/free'),[]);self.assertEqual(self.posts('strata/v1/vram'),[])
        with self.assertRaises(ValueError):self.manager.start('release')
        c.recover(self.s,Worker(self.s));self.assertEqual(self.s.job(job['id'])['status'],'failed')
        self.generation_error=False;self.cancel_after_post=True
        job=self.s.submit(self.params());Worker(self.s).run(job['id']);self.assertEqual(self.s.job(job['id'])['status'],'cancelled')
        self.assertEqual(self.posts('/free'),[]);self.assertEqual(self.posts('strata/v1/vram'),[])

    def test_credentials_and_timeouts_and_malformed_probe_do_not_leak(self):
        config=strata.validate_settings({'url':'http://127.0.0.1:8080/v1'})
        self.assertEqual(config['url'],'http://127.0.0.1:8080')
        secret='dummy-strata-secret'
        with patch.object(strata,'client',REAL_STRATA_CLIENT),patch.object(strata,'strata_api_key',return_value=secret):
            with strata.client(config) as client:
                self.assertEqual(client.headers['Authorization'],'Bearer '+secret)
                self.assertEqual(client.timeout.read,900);self.assertEqual(client.timeout.connect,5)
            error=httpx.HTTPStatusError('no',request=httpx.Request('POST',config['url']+'/v1/vram'),response=httpx.Response(401,json={'error':{'message':secret}}))
            self.assertNotIn(secret,strata.safe_error(error))
        with patch.object(strata,'client',lambda cfg:httpx.Client(base_url=cfg['url'],transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'wrong':'server'})))):
            self.assertEqual(strata.probe(config)['state'],'unsupported')
        self.prepare();self.action('acquire')
        self.assertNotIn(secret,self.manager.file.read_text('utf-8'));self.assertNotIn(secret,self.manager.audit.read_text('utf-8'))

    def test_other_client_cached_work_requires_free_before_new_reserve(self):
        self.prepare();self.histories['foreign-completed']={'status':{'completed':True}}
        s=self.action('acquire');self.assertEqual(s['phase'],'failed');self.assertEqual(self.posts('strata/v1/vram'),[])
        self.histories.pop('foreign-completed');self.pause=True;self.manager.start('acquire');self.assertTrue(self.entered.wait(2))
        self.histories['foreign-during-reserve']={'status':{'completed':True}}
        self.finish.set();self.manager.thread.join(5);s=self.manager.status()
        self.assertEqual(s['phase'],'unconfirmed');self.assertIsNone(s['lease'])

if __name__=='__main__':unittest.main()
