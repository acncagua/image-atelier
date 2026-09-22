import json
import sys
import threading
import time
import unittest
from unittest.mock import patch
import test_upscale as fixtures
from core import Worker,JobConflict,ROOT,canonical_input,prompt_for
import qwen_backend as q

class QwenJobs(unittest.TestCase):
    setUp=fixtures.Jobs.setUp
    tearDown=fixtures.Jobs.tearDown
    def params(self,**changes):
        import uuid
        folder=self.path/'qwen';folder.mkdir(exist_ok=True);(folder/'model_index.json').write_text('{}')
        q.configure(self.s,{'python':sys.executable,'model':str(folder)})
        self.s.qwen_runner=ROOT/'tests/fake_qwen_runner.py'
        return {'id':str(uuid.uuid4()),'model':q.MODEL,'provider':'qwen','mode':'generate','width':512,'height':512,'n':1,'format':'png','prompt':'変更すること: 青色の背景','refs':[],**q.DEFAULTS,**changes}
    def test_generate_keeps_history_without_api_or_budget(self):
        p=self.params();job=self.s.submit(p)
        with patch('core.real_request',side_effect=AssertionError('no API')),patch('core.mock_request',side_effect=AssertionError('not API mock')):Worker(self.s).run(job['id'])
        job=self.s.job(job['id']);self.assertEqual(job['status'],'completed');self.assertEqual(job['reserved'],0)
        self.assertEqual(job['outputs'][0]['name'],'Qwen出力');self.assertEqual(job['outputs'][0]['width'],512)
        self.assertIn('Qwen',job['message'])
    def test_reference_generation_is_not_source_editing(self):
        from PIL import Image
        from imaging import png
        base=self.s.asset(png(Image.new('RGB',(512,512),'green')))
        p=self.params(target=base['id'],refs=[{'id':self.image['id'],'role':'face','person':'主人公'}],change='庭園で座る',keep='髪色を維持')
        p['prompt']=prompt_for(p)
        self.assertEqual(p['prompt'],'庭園で座る')
        job=self.s.submit(p);self.assertEqual(job['params']['input_ids'],[self.image['id']])
        Worker(self.s).run(job['id'])
        request=json.loads((q.directory(self.s,job['id'])/'request.json').read_text('utf-8'))
        self.assertEqual(request['inputs'],[str(self.s.file(self.image['id']))])
    def test_random_seed_resolves_once_per_job_and_preserves_minus_one(self):
        p=self.params(qwen_seed=-1);job=self.s.submit(p)
        with patch('qwen_backend.secrets.randbits',side_effect=[101,202]) as random:
            Worker(self.s).run(job['id'])
            same=self.s.submit(p);Worker(self.s).run(same['id'])
            other=self.s.submit(self.params(qwen_seed=-1));Worker(self.s).run(other['id'])
            self.assertEqual(random.call_count,2)
        first=self.s.job(job['id']);second=self.s.job(other['id'])
        self.assertEqual(first['params']['qwen_seed'],-1);self.assertEqual(second['params']['qwen_seed'],-1)
        self.assertEqual(first['qwen_seed_used'],101);self.assertEqual(second['qwen_seed_used'],202)
        request=json.loads((q.directory(self.s,job['id'])/'request.json').read_text('utf-8'))
        self.assertEqual(request['seed'],101)
        with self.assertRaises(ValueError):self.s.submit(self.params(qwen_seed=-2))
    def test_cfg_negative_and_plain_prompt(self):
        p=self.params(change='選択プリセット\n二人がお茶会をしている',keep='顔を維持',qwen_cfg=2.5,qwen_negative='collage, text',refs=[{'id':self.image['id'],'role':'body','person':'説明は送らない'}])
        for mode in ('generate','polish','inpaint'):
            self.assertEqual(prompt_for({**p,'mode':mode}),p['change'])
        p['prompt']=prompt_for(p)
        job=self.s.submit(p);Worker(self.s).run(job['id'])
        request=json.loads((q.directory(self.s,job['id'])/'request.json').read_text('utf-8'))
        self.assertEqual(request['prompt'],p['change'])
        self.assertEqual(request['negative_prompt'],'collage, text')
        self.assertEqual(request['true_cfg_scale'],2.5)
        for key,value in [('qwen_cfg',3),('qwen_negative','different')]:
            with self.assertRaises(JobConflict):self.s.submit({**p,key:value})
        for value in (0,5.1,float('inf'),True):
            with self.assertRaises(ValueError):self.s.submit(self.params(qwen_cfg=value))
        self.assertNotIn('qwen_negative',canonical_input({'model':'gpt-image-2.5-sunburst','qwen_negative':'x'}))

    def test_each_generation_exits_and_does_not_retain_model(self):
        from managed_child import tree_exited
        worker=Worker(self.s)
        with patch('qwen_backend.launch',wraps=q.launch) as launch:
            for _ in range(2):
                job=self.s.submit(self.params());worker.run(job['id'])
                self.assertEqual(self.s.job(job['id'])['status'],'completed')
                self.assertFalse(self.s.job(job['id'])['qwen_model_reused'])
                self.assertTrue(tree_exited(q.directory(self.s,job['id'])))
                self.assertIsNone(self.s.qwen_session.process)
            self.assertEqual(launch.call_count,2)

    def test_old_browser_cannot_enable_model_retention(self):
        from fastapi.testclient import TestClient
        from server import create_app
        app=create_app(self.path/'endpoint',run_worker=False)
        with TestClient(app) as client:
            token=client.get('/api/bootstrap').json()['token']
            response=client.post('/api/qwen/session',json={'selected':True},headers={'x-atelier-token':token})
            self.assertEqual(response.json(),{'selected':False})
            self.assertFalse(app.state.store.qwen_session.selected)
        app.state.store.db.close()

    def test_timing_option_and_record_survive_worker_boundary(self):
        job=self.s.submit(self.params(qwen_timing=True));Worker(self.s).run(job['id'])
        request=json.loads((q.directory(self.s,job['id'])/'request.json').read_text('utf-8'))
        self.assertTrue(request['timing'])
        self.assertEqual(self.s.job(job['id'])['qwen_timing']['steps'],[{'step':1,'ms':125}])

    def test_parameters_are_part_of_idempotency(self):
        p=self.params();self.s.submit(p);self.s.submit(p)
        with self.assertRaises(JobConflict):self.s.submit({**p,'qwen_steps':8})
        self.assertEqual(len(self.s.jobs()),1)
    def test_api_canonical_contract_is_unchanged(self):
        p={'model':'gpt-image-2.5-sunburst','qwen_steps':7}
        self.assertNotIn('qwen_steps',canonical_input(p))
    def test_edit_preserves_prompt_and_reference_order(self):
        p=self.params(mode='polish',target=self.image['id'],refs=[{'id':self.image['id'],'role':'style','person':'A'}],change='preset\nmanual',keep='顔を維持')
        p['prompt']=prompt_for(p);job=self.s.submit(p);Worker(self.s).run(job['id'])
        r=json.loads((q.directory(self.s,job['id'])/'request.json').read_text('utf-8'))
        self.assertEqual(r['prompt'],p['prompt']);self.assertEqual(len(r['inputs']),2)
        self.assertIn('preset\nmanual',r['prompt'])
    def test_invalid_modes_dimensions_and_options_are_rejected(self):
        for changes in ({'mode':'upscale'},{'width':513},{'n':2},{'format':'jpeg'},{'qwen_steps':0},{'qwen_stride':512},{'provider':'openai'}):
            with self.assertRaises(ValueError):self.s.submit(self.params(**changes))
    def test_cancel_while_waiting_for_gpu_never_launches(self):
        job=self.s.submit(self.params());worker=Worker(self.s)
        with self.s.gpu_execution:
            thread=threading.Thread(target=worker.run,args=(job['id'],));thread.start();time.sleep(.05)
            q.cancel(self.s,job['id'])
        thread.join(5);self.assertFalse(thread.is_alive());self.assertEqual(self.s.job(job['id'])['status'],'cancelled')
        self.assertFalse((q.directory(self.s,job['id'])/'request.json').exists())
    def test_running_cancel_terminates_owned_child(self):
        job=self.s.submit(self.params());(self.path/'qwen/wait').write_text('wait');worker=Worker(self.s)
        thread=threading.Thread(target=worker.run,args=(job['id'],));thread.start()
        try:
            deadline=time.time()+5
            while self.s.job(job['id'])['status']=='queued' and time.time()<deadline:time.sleep(.02)
            q.cancel(self.s,job['id'])
        finally:thread.join(10)
        self.assertFalse(thread.is_alive());self.assertEqual(self.s.job(job['id'])['status'],'cancelled')
    def test_saved_output_reprocess_does_not_infer(self):
        job=self.s.submit(self.params());worker=Worker(self.s)
        with patch.object(self.s,'export',side_effect=OSError('full')):worker.run(job['id'])
        self.assertEqual(self.s.job(job['id'])['status'],'local_error')
        with patch('qwen_backend.launch',side_effect=AssertionError('no inference')):worker.reprocess(job['id'])
        self.assertEqual(self.s.job(job['id'])['status'],'completed')
    def test_restart_recovers_complete_png_without_a_completed_marker(self):
        from PIL import Image
        from imaging import png
        job=self.s.submit(self.params());folder=q.directory(self.s,job['id']);folder.mkdir(parents=True)
        (folder/'result.png').write_bytes(png(Image.new('RGBA',(512,512),'blue')))
        (folder/'status.json').write_text('{"state":"saving"}')
        job['status']='sending';self.s.save_job(job);q.recover(self.s)
        self.assertEqual(self.s.job(job['id'])['status'],'local_error')
        with patch('qwen_backend.launch',side_effect=AssertionError('no inference')):Worker(self.s).reprocess(job['id'])
        self.assertEqual(self.s.job(job['id'])['status'],'completed')
    def test_swinir_waits_for_qwen_on_shared_gpu(self):
        job=self.s.submit(self.params());(self.path/'qwen/wait').write_text('wait')
        worker=Worker(self.s);qthread=threading.Thread(target=worker.run,args=(job['id'],));qthread.start()
        deadline=time.time()+5
        while self.s.job(job['id'])['status']=='queued' and time.time()<deadline:time.sleep(.02)
        other=self.manager.submit(fixtures.Jobs.body(self));sthread=threading.Thread(target=self.manager.run,args=(other['id'],));sthread.start()
        try:
            time.sleep(.1);self.assertEqual(self.manager.get(other['id'])['status'],'queued')
        finally:q.cancel(self.s,job['id']);qthread.join(10);sthread.join(10)
        self.assertFalse(sthread.is_alive());self.assertEqual(self.manager.get(other['id'])['status'],'completed')

    def test_mask_is_last_and_composite_preserves_unpainted_pixels(self):
        from PIL import Image
        from imaging import png
        source=self.s.asset(png(Image.new('RGBA',(512,512),(255,0,0,128))))
        strokes=[{'width':80,'points':[[256,256]],'erase':False},{'width':16,'points':[[256,256]],'erase':True}]
        p=self.params(mode='inpaint',change='青くする',target=source['id'],strokes=strokes,composite=True,feather=8,
                      refs=[{'id':self.image['id'],'role':'outfit','person':'色見本'}])
        p['prompt']=prompt_for(p);job=self.s.submit(p);Worker(self.s).run(job['id'])
        result=self.s.job(job['id']);self.assertEqual(result['status'],'completed')
        request=json.loads((q.directory(self.s,job['id'])/'request.json').read_text('utf-8'))
        self.assertEqual(len(request['inputs']),3);self.assertEqual(request['prompt'],p['change'])
        with Image.open(request['inputs'][-1]) as mask:
            self.assertEqual(mask.convert('RGB').getpixel((280,256)),(255,255,255))
            self.assertEqual(mask.convert('RGB').getpixel((256,256)),(0,0,0))
        merged=result['outputs'][-1];self.assertEqual(merged['kind'],'composite')
        with Image.open(self.s.file(merged['id'])) as out:
            self.assertEqual(out.getpixel((0,0)),(255,0,0,128));self.assertEqual(out.getpixel((256,256)),(255,0,0,128))
            self.assertNotEqual(out.getpixel((280,256)),(255,0,0,128))
    def test_reference_limits_include_target_and_mask(self):
        from PIL import Image
        from imaging import png
        source=self.s.asset(png(Image.new('RGB',(512,512),'red')))
        for mode,count in [('generate',10),('polish',9),('inpaint',8)]:
            refs=[{'id':source['id'],'role':'style'}]*count
            p=self.params(mode=mode,target=source['id'],refs=refs,strokes=[{'width':20,'points':[[50,50]]}])
            job=self.s.submit(p);Worker(self.s).run(job['id'])
            request=json.loads((q.directory(self.s,job['id'])/'request.json').read_text('utf-8'))
            self.assertEqual(len(request['inputs']),10)
            with self.assertRaisesRegex(ValueError,'合計10枚'):self.s.submit({**p,'id':__import__('uuid').uuid4().hex,'refs':refs+[refs[0]]})
    def test_blank_mask_and_size_mismatch_are_rejected(self):
        from PIL import Image
        from imaging import png
        source=self.s.asset(png(Image.new('RGB',(512,512))))
        with self.assertRaisesRegex(ValueError,'マスク'):self.s.submit(self.params(mode='inpaint',target=source['id']))
        with self.assertRaisesRegex(ValueError,'同じ'):self.s.submit(self.params(mode='inpaint',target=source['id'],width=1024,strokes=[{'width':20,'points':[[50,50]]}]))

if __name__=='__main__':unittest.main()
