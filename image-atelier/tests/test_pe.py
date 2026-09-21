import json
import sys
import threading
import time
import uuid
import unittest
from unittest.mock import patch
import test_upscale as fixture
from core import ROOT,JobConflict,Store
from pe_jobs import PEJobs,read_record,directory
from pe_runner import parse_result

class PE(unittest.TestCase):
    def setUp(self):
        fixture.Jobs.setUp(self)
        self.pe=PEJobs(self.s,ROOT/'tests/fake_pe_runner.py')
        self.pe_model=self.path/'pe-model';self.pe_model.mkdir();(self.pe_model/'system_prompt.txt').write_text('test')
        self.pe.configure({'python':sys.executable,'model':str(self.pe_model)})
    def tearDown(self):self.pe.shutdown();fixture.Jobs.tearDown(self)
    def body(self,**extra):return {'id':uuid.uuid4().hex,'model':'Qwen-Image-2.1','mode':'generate','refs':[],'prompt':'宮殿の人物',**extra}
    def test_success_is_not_image_generation(self):
        body=self.body();self.pe.submit(body)
        with patch('core.real_request',side_effect=AssertionError('no API')):self.pe.run(body['id'])
        result=self.pe.get(body['id']);self.assertEqual(result['status'],'completed');self.assertEqual(result['result']['wh_ratio'],'2:3')
        self.assertEqual(self.s.jobs(),[]);self.assertEqual(result['params']['prompt'],body['prompt'])
    def test_idempotency_and_registration_write_failure(self):
        p=self.body()
        with patch.object(self.pe,'save',side_effect=OSError('full')):
            with self.assertRaises(OSError):self.pe.submit(p)
        self.pe.submit(p);self.pe.submit(p)
        with self.assertRaises(JobConflict):self.pe.submit({**p,'prompt':'different'})
    def test_rejects_images_and_edit_modes(self):
        for extra in ({'mode':'polish'},{'refs':[{'id':'ref'}]},{'model':'other'},{'max_new_tokens':0}):
            with self.assertRaises(ValueError):self.pe.submit(self.body(**extra))
    def test_cancel_inference_preserves_original(self):
        (self.pe_model/'wait').write_text('wait');p=self.body();self.pe.submit(p)
        t=threading.Thread(target=self.pe.run,args=(p['id'],));t.start()
        end=time.time()+5
        while self.pe.get(p['id'])['status']=='queued' and time.time()<end:time.sleep(.02)
        self.pe.cancel(p['id']);t.join(10)
        self.assertFalse(t.is_alive());self.assertEqual(self.pe.get(p['id'])['status'],'cancelled')
        self.assertEqual(self.pe.get(p['id'])['params']['prompt'],p['prompt'])
    def test_gpu_wait_can_be_cancelled_without_launch(self):
        p=self.body();self.pe.submit(p)
        with self.s.gpu_execution:
            t=threading.Thread(target=self.pe.run,args=(p['id'],));t.start();time.sleep(.05);self.pe.cancel(p['id'])
        t.join(3);self.assertEqual(self.pe.get(p['id'])['status'],'cancelled');self.assertFalse(directory(self.s,p['id']).exists())
    def test_recovery_preserves_complete_result_without_rerun(self):
        p=self.body();self.pe.submit(p);j=read_record(self.s,p['id']);j['status']='running';self.pe.save(j)
        folder=directory(self.s,p['id']);folder.mkdir();(folder/'result.json').write_text(json.dumps({'rewritten_prompt':'final','wh_ratio':'1:1'}))
        with patch('pe_jobs.launch',side_effect=AssertionError('no inference')):self.pe.recover()
        self.assertEqual(self.pe.get(p['id'])['status'],'completed')
    def test_parser_validates_final_object_and_does_not_use_thinking(self):
        self.assertEqual(parse_result('private thinking</think>{"rewritten_prompt":"final","wh_ratio":"1:1"}')['rewritten_prompt'],'final')
        for text in ('[]','{"rewritten_prompt":"","wh_ratio":"1:1"}','{"rewritten_prompt":"final","wh_ratio":"bogus"}','unfinished thoughts'):
            with self.assertRaises(ValueError):parse_result(text)
    def test_adopted_result_retains_original_and_final_prompt(self):
        import qwen_backend as q
        p=self.body();self.pe.submit(p);self.pe.run(p['id'])
        model=self.path/'qwen';model.mkdir();(model/'model_index.json').write_text('{}')
        q.configure(self.s,{'python':sys.executable,'model':str(model)})
        params={'id':uuid.uuid4().hex,'model':q.MODEL,'provider':'qwen','mode':'generate','width':512,'height':512,'n':1,'format':'png','prompt':'manually adjusted final text','pe_job_id':p['id'],**q.DEFAULTS}
        job=self.s.submit(params)
        self.assertEqual(job['prompt_enhancement']['source_prompt'],p['prompt'])
        self.assertEqual(job['params']['prompt'],'manually adjusted final text')
        self.assertEqual(job['params']['width'],512)
        with self.assertRaises(JobConflict):self.s.submit({**params,'pe_job_id':uuid.uuid4().hex})
    def test_cancelled_result_is_not_recovered_as_success(self):
        p=self.body();self.pe.submit(p);j=read_record(self.s,p['id']);j['status']='cancel_requested';self.pe.save(j)
        folder=directory(self.s,p['id']);folder.mkdir();(folder/'result.json').write_text(json.dumps({'rewritten_prompt':'discarded','wh_ratio':'1:1'}))
        self.pe.recover();self.assertEqual(self.pe.get(p['id'])['status'],'cancelled')

if __name__=='__main__':unittest.main()
