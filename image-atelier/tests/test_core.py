import base64
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import httpx
from PIL import Image
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core import Store, Worker, validate_size, prompt_for, real_request, usage_cost
from imaging import png, normalize, mask_image, api_mask, composite
from server import create_app

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.s=Store(Path(self.temp.name)/'日本語 空白')
        self.s.set_settings({'output':str(Path(self.temp.name)/'保存 画像'),'budget':2,'reservation':1,'live':True})
        self.a=self.s.asset(png(Image.new('RGBA',(1024,1024),(15,44,78,255))),'元画像.png')
    def tearDown(self):
        self.s.db.close();self.temp.cleanup()
    def p(self,**kw):
        p=dict(id=str(uuid.uuid4()),mode='polish',provider='mock',target=self.a['id'],refs=[],width=1024,height=1024,model='gpt-image-2.5-sunburst',quality='medium',prompt='目の形を維持',change='光を調整',keep='顔立ち',strokes=[],feather=8)
        p.update(kw);return p
    def test_dimensions(self):
        for size in [(1920,1088),(2048,1152),(3840,2160)]:validate_size(*size)
        for size in [(1920,1080),(3840,2176),(0,0),(16,16),(3840,3840),(4096,1024)]:
            with self.assertRaises(ValueError):validate_size(*size)
    def test_concurrent_history_and_image_reads(self):
        job=self.s.submit(self.p())
        def read_and_write(index):
            self.assertEqual(self.s.meta(self.a['id'])['width'],1024)
            self.s.settings();self.s.jobs()
            current=self.s.job(job['id']);self.s.save_job(current)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(read_and_write,range(200)))
    def test_usage_cost_requires_breakdown(self):
        usage={'input_tokens':30,'input_tokens_details':{'text_tokens':10,'image_tokens':20},'output_tokens':100}
        self.assertAlmostEqual(usage_cost(usage),.00321)
        self.assertIsNone(usage_cost(None))
        self.assertIsNone(usage_cost({**usage,'input_tokens':40}))
        self.assertIsNone(usage_cost({**usage,'cached_tokens':5}))
    def test_roles_order(self):
        p=self.p(refs=[{'id':self.a['id'],'role':'face','person':'左の人物'},{'id':self.a['id'],'role':'style','person':''}])
        text=prompt_for(p)
        self.assertIn('画像1: 編集対象',text);self.assertIn('画像2: 顔立ちのみ',text);self.assertIn('画像3: 色',text)
        self.assertIn('画像1: 顔立ちのみ',prompt_for({**p,'mode':'generate'}))
    def test_duplicates_and_restart(self):
        p=self.p();j=self.s.submit(p);self.assertEqual(j['id'],self.s.submit(copy.deepcopy(p))['id'])
        self.assertEqual(j['id'],self.s.submit(self.p())['id'])
        j.update(status='sending',reserved=1);self.s.save_job(j);self.s.recover()
        self.assertEqual(self.s.job(j['id'])['status'],'unknown');self.assertEqual(self.s.job(j['id'])['reserved'],1)
    def test_queued_restart_cancels(self):
        j=self.s.submit(self.p());self.s.recover();self.assertEqual(self.s.job(j['id'])['status'],'cancelled')
    def test_settlement_migration_and_unknown_reservation(self):
        with patch('core.api_key',return_value='test-only'):
            complete=self.s.submit(self.p(provider='openai'))
            unknown=self.s.submit(self.p(provider='openai',change='other'))
        complete.update(status='completed',estimate=.04)
        # Simulate a pre-migration history row.
        self.s.db.execute('UPDATE jobs SET body=? WHERE id=?',(json.dumps(complete),complete['id']))
        self.s.db.commit()
        unknown.update(status='unknown',estimate=.02);self.s.save_job(unknown)
        self.s.recover();self.s.recover()
        settled=self.s.job(complete['id'])
        self.assertEqual(settled['reserved'],.04)
        self.assertEqual(settled['initial_reservation'],1)
        self.assertEqual(self.s.job(unknown['id'])['reserved'],1)
        self.s.set_settings({**self.s.settings(),'budget':3})
        with patch('core.api_key',return_value='test-only'):
            self.assertEqual(self.s.submit(self.p(provider='openai',change='new'))['status'],'queued')
    def test_unknown_cost_completion_keeps_reservation(self):
        with patch('core.api_key',return_value='test-only'):
            job=self.s.submit(self.p(provider='openai'))
        job.update(status='completed',estimate=None);self.s.save_job(job)
        self.assertEqual(self.s.job(job['id'])['reserved'],1)
        job.update(estimate=1.5);self.s.save_job(job)
        self.assertEqual(self.s.job(job['id'])['reserved'],1.5)
    def test_disabled_live_queue_never_sends(self):
        with patch('core.api_key',return_value='test-only'):
            j=self.s.submit(self.p(provider='openai'))
        self.s.set_settings({**self.s.settings(),'live':False})
        with patch('core.real_request') as call:
            Worker(self.s).run(j['id'])
            call.assert_not_called()
        self.assertEqual(self.s.job(j['id'])['status'],'cancelled')
        self.assertEqual(self.s.job(j['id'])['reserved'],0)
    def test_generated_output_not_subject_to_upload_limit(self):
        with patch('core.CAP',{**__import__('core').CAP,'max_file_bytes':10}):
            data=png(Image.new('RGB',(32,32)))
            with self.assertRaises(ValueError):self.s.asset(data)
            self.assertEqual(self.s.asset(data,kind='raw')['width'],32)
    def test_connection_check_hides_key(self):
        app=create_app(Path(self.temp.name)/'connection',False)
        with TestClient(app) as client,patch('server.api_key',return_value='secret-test-only'),patch('server.httpx.Client') as transport:
            transport.return_value.__enter__.return_value.get.return_value=httpx.Response(200,json={'data':[{'id':'gpt-image-2.5-sunburst'}]})
            r=client.post('/api/connection-check',json={},headers={'X-Atelier-Token':app.state.token})
            self.assertEqual(r.status_code,200)
            self.assertTrue(r.json()['models']['gpt-image-2.5-sunburst'])
            self.assertNotIn('secret-test-only',r.text)
        app.state.store.db.close()
    def test_mask_and_protection(self):
        mask=mask_image((100,100),[{'width':20,'points':[[40,40],[60,40]],'erase':False},{'width':4,'points':[[40,40]],'erase':True}])
        alpha=Image.open(io.BytesIO(api_mask(mask))).getchannel('A')
        self.assertEqual(alpha.getpixel((50,40)),0);self.assertEqual(alpha.getpixel((0,0)),255);self.assertEqual(alpha.getpixel((40,40)),255)
        original=Image.new('RGBA',(100,100),(20,40,70,180));other=Image.new('RGBA',(100,100),(255,0,0,255))
        merged=composite(original,other,mask,8)
        for y in range(100):
            for x in range(100):
                if not mask.getpixel((x,y)):self.assertEqual(original.getpixel((x,y)),merged.getpixel((x,y)))
        with self.assertRaises(ValueError):composite(original,Image.new('RGB',(90,100)),mask,8)
    def test_source_snapshots_and_non_overwrite(self):
        a=self.s.asset(png(Image.new('RGB',(16,32))),'日本語 source.png',self.a['id'])
        self.assertEqual(a['parent'],self.a['id']);self.assertTrue((self.s.path/'assets'/a['id']/'original.bin').exists())
        one=self.s.export(a['id']);two=self.s.export(a['id']);self.assertNotEqual(one,two)
    def test_mock_edit_and_save_failure(self):
        j=self.s.submit(self.p(width=1920,height=1088))
        with patch.object(self.s,'export',side_effect=PermissionError):Worker(self.s).run(j['id'])
        j=self.s.job(j['id']);self.assertEqual(j['status'],'completed');self.assertIn('要求寸法',j['message']);self.assertIn('回収',j['message'])
        self.assertEqual(j['outputs'][0]['width'],1024)
    def test_mock_generation(self):
        j=self.s.submit(self.p(mode='generate',target=None));Worker(self.s).run(j['id']);j=self.s.job(j['id'])
        self.assertEqual(j['status'],'completed');self.assertEqual(j['outputs'][0]['height'],1024)
    def test_multiple_results_and_formats(self):
        for fmt in ('png','jpeg','webp'):
            j=self.s.submit(self.p(mode='generate',target=None,n=2,format=fmt))
            Worker(self.s).run(j['id']);j=self.s.job(j['id'])
            self.assertEqual(j['status'],'completed');self.assertEqual(len(j['outputs']),2)
            self.assertEqual(len(j['saved_paths']),2)
            path=self.s.path/'assets'/j['outputs'][0]['id']/'original.bin'
            with Image.open(path) as im:self.assertEqual(im.format,fmt.upper())
    def test_multiple_outputs_reserve_per_image(self):
        with patch('core.api_key',return_value='test-only'):
            with self.assertRaises(ValueError):self.s.submit(self.p(provider='openai',n=3))
            j=self.s.submit(self.p(provider='openai',n=2))
            self.assertEqual(j['reserved'],2)
    def test_timeout_budget_no_retry(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test-not-a-secret'}):
            j=self.s.submit(self.p(provider='openai'))
            with patch('core.real_request',side_effect=httpx.ReadTimeout('secret must not leak')) as call:
                Worker(self.s).run(j['id']);Worker(self.s).run(j['id']);self.assertEqual(call.call_count,1)
            self.assertEqual(self.s.job(j['id'])['status'],'unknown');self.assertEqual(self.s.job(j['id'])['reserved'],1)
            self.s.submit(self.p(provider='openai',change='second'))
            with self.assertRaises(ValueError):self.s.submit(self.p(provider='openai',change='third'))
            self.assertNotIn('secret must not leak',json.dumps(self.s.jobs()))
    def test_auth_and_limit_errors(self):
        for code in [400,401,403,404,429,500]:
            j=self.s.submit(self.p(change=str(code)))
            error=httpx.HTTPStatusError('PRIVATE KEY',request=httpx.Request('POST','https://api.openai.com/v1/images/edits'),response=httpx.Response(code))
            with patch('core.mock_request',side_effect=error):Worker(self.s).run(j['id'])
            job=self.s.job(j['id']);self.assertEqual(job['status'],'unknown' if code==500 else 'failed');self.assertNotIn('PRIVATE KEY',json.dumps(job))
    def test_real_transport_contract_without_network(self):
        p=self.p(mode='inpaint',refs=[{'id':self.a['id'],'role':'face'}],strokes=[{'width':20,'points':[[20,20]]}]);j=self.s.submit(p)
        response=httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(self.s.file(self.a['id']).read_bytes()).decode()}],'usage':{'output_tokens':20}},headers={'x-request-id':'test-id'},request=httpx.Request('POST','https://api.openai.com'))
        with patch.dict(os.environ,{'OPENAI_API_KEY':'dummy-local'}),patch('core.httpx.Client') as cls:
            client=cls.return_value.__enter__.return_value;client.post.return_value=response
            raw,usage,rid=real_request(j['params'],self.s)
            args,kwargs=client.post.call_args
            self.assertEqual(args[0],'https://api.openai.com/v1/images/edits');self.assertEqual([f[0] for f in kwargs['files']],['image[]','image[]','mask']);self.assertNotIn('input_fidelity',kwargs['data']);self.assertEqual(rid,'test-id')
    def test_http_guards_and_restore(self):
        app=create_app(Path(self.temp.name)/'http',False)
        with TestClient(app) as client:
            token=client.get('/api/bootstrap').json()['token'];headers={'X-Atelier-Token':token}
            self.assertEqual(client.post('/api/settings',json={}).status_code,403)
            self.assertEqual(client.get('/api/bootstrap',headers={'Host':'evil.example'}).status_code,403)
            self.assertEqual(client.get('/api/bootstrap',headers={'Origin':'https://evil.example'}).status_code,403)
            r=client.post('/api/assets',json={'data':base64.b64encode(self.s.file(self.a['id']).read_bytes()).decode(),'name':'試験.png'},headers=headers)
            self.assertEqual(r.status_code,200);ident=r.json()['id']
            crop=client.post('/api/crop',json={'id':ident,'x':5,'y':7,'w':40,'h':60},headers=headers).json();self.assertEqual((crop['width'],crop['height']),(40,60))
            job=client.post('/api/jobs',json=self.p(target=ident),headers=headers).json()
            self.assertEqual(client.post('/api/jobs/'+job['id']+'/cancel',json={},headers=headers).json()['status'],'cancelled')
            self.assertEqual(client.get('/api/assets/'+ident+'/download').status_code,200)
        app.state.store.db.close()

if __name__=='__main__':unittest.main()
