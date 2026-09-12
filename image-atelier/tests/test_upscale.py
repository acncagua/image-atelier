import unittest,tempfile,sys,uuid,time,threading
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from PIL import Image
from fastapi.testclient import TestClient
from core import Store,JobConflict,ROOT
from imaging import png
from upscale_geometry import plan,finish,resize_alpha_aware
from upscale_jobs import UpscaleJobs
from server import create_app

class Geometry(unittest.TestCase):
    def test_representative_sizes(self):
        for factor,size in [(2,(3840,2176)),(1.5,(2880,1632)),(4,(7680,4352))]:
            p=plan(1920,1088,{'factor':factor});self.assertEqual((p['width'],p['height']),size)
        self.assertEqual(plan(11,13,{'factor':1.5})['width'],17)
    def test_invalid_inputs(self):
        for options in [{'factor':1},{'factor':4.01},{'factor':float('nan')},{'factor':float('inf')},{'mode':'size','width':100.5,'height':120},{'mode':'size','width':40,'height':40},{'tile':193},{'overlap':192},{'overlap':-1}]:
            with self.assertRaises(ValueError):plan(100,100,options)
        with self.assertRaises(ValueError):plan(2048,2048,{})
        with self.assertRaises(ValueError):plan(1920,1088,{'tile':32,'overlap':31})
    def test_pad_crop_stretch_geometry(self):
        image=Image.new('RGB',(16,8),'red')
        padded,info=finish(image,(11,11),'pad');self.assertEqual(info['offset'],[0,2]);self.assertEqual(padded.getpixel((0,0))[3],0);self.assertEqual(padded.getpixel((0,2)),(255,0,0,255))
        cropped,info=finish(image,(7,7),'crop');self.assertEqual(cropped.size,(7,7));self.assertEqual(info['offset'],[3,0])
        stretched,_=finish(image,(7,9),'stretch');self.assertEqual(stretched.size,(7,9));self.assertEqual(stretched.mode,'RGB')
    def test_premultiplied_alpha_no_green_bleed(self):
        image=Image.new('RGBA',(16,16),(0,255,0,0))
        for y in range(4,12):
            for x in range(4,12):image.putpixel((x,y),(255,0,0,255))
        result=resize_alpha_aware(image,(8,8))
        for r,g,b,a in result.getdata():
            if a>0:self.assertEqual(g,0)

class Jobs(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        self.s=Store(self.path/'日本語 データ');self.s.set_settings({'output':str(self.path/'exports'),'budget':5,'reservation':1,'live':False})
        self.image=self.s.asset(png(Image.new('RGB',(31,25),'red')))
        self.model=self.path/'test.pth';self.model.write_text('normal')
        self.manager=UpscaleJobs(self.s,ROOT/'tests/fake_upscale_runner.py')
        self.manager.configure({'python':sys.executable,'model':str(self.model)})
    def tearDown(self):self.manager.shutdown();self.s.db.close();self.temp.cleanup()
    def body(self,**extra):return {'id':str(uuid.uuid4()),'source_id':self.image['id'],'options':{'mode':'factor','factor':2},**extra}
    def test_dedup_and_budget_isolation(self):
        body=self.body();before=self.s.settings()
        with ThreadPoolExecutor(max_workers=4) as pool:jobs=list(pool.map(lambda _:self.manager.submit(body),range(4)))
        self.assertEqual(len(self.manager.jobs()),1)
        with patch('core.real_request',side_effect=AssertionError('no API')):self.manager.run(jobs[0]['id'])
        job=self.manager.get(jobs[0]['id']);self.assertEqual(job['status'],'completed');self.assertEqual(job['output']['width'],62)
        self.assertEqual(self.s.jobs(),[]);self.assertEqual(self.s.settings(),before)
        with self.assertRaises(JobConflict):self.manager.submit({**body,'options':{'factor':3}})
    def test_queued_cancel_never_launches(self):
        job=self.manager.submit(self.body());self.manager.cancel(job['id'])
        with patch('upscale_jobs.launch') as launch:self.manager.run(job['id']);launch.assert_not_called()
    def test_running_cancel_waits_for_process_exit(self):
        self.model.write_text('wait');job=self.manager.submit(self.body())
        thread=threading.Thread(target=self.manager.run,args=(job['id'],));thread.start()
        deadline=time.time()+5
        while self.manager.get(job['id'])['status']=='queued' and time.time()<deadline:time.sleep(.01)
        result=self.manager.cancel(job['id']);self.assertEqual(result['status'],'cancel_requested')
        thread.join(8);self.assertFalse(thread.is_alive());self.assertEqual(self.manager.get(job['id'])['status'],'cancelled');self.assertIsNone(self.manager.process)
    def test_child_failures_never_return_original(self):
        for kind in ('bad-model','no-cuda','oom','crash'):
            self.model.write_text(kind);job=self.manager.submit(self.body());self.manager.run(job['id'])
            result=self.manager.get(job['id']);self.assertEqual(result['status'],'failed');self.assertIsNone(result['output'])
    def test_save_retry_does_not_infer_again(self):
        job=self.manager.submit(self.body())
        with patch.object(self.s,'export',side_effect=PermissionError):self.manager.run(job['id'])
        before=self.manager.get(job['id']);self.assertEqual(before['status'],'export_failed')
        with patch('upscale_jobs.launch',side_effect=AssertionError('no inference')):
            after=self.manager.retry_save(job['id']);again=self.manager.retry_save(job['id'])
        self.assertEqual(after['status'],'completed');self.assertEqual(before['output'],after['output']);self.assertEqual(after['saved_path'],again['saved_path'])
    def test_restart_does_not_requeue(self):
        job=self.manager.submit(self.body());self.manager.recover();self.assertEqual(self.manager.get(job['id'])['status'],'interrupted')
    def test_missing_configuration_keeps_existing_app_usable(self):
        self.manager.configure({'python':'','model':''})
        with self.assertRaises(ValueError):self.manager.submit(self.body())
        self.assertFalse(self.manager.public_config()['model_exists'])
        self.assertEqual(self.s.jobs(),[])
    def test_commit_wins_cancel_rejected(self):
        job=self.manager.submit(self.body());self.manager.run(job['id'])
        with self.assertRaises(JobConflict):self.manager.cancel(job['id'])
    def test_settings_and_routes_without_gpu_import(self):
        app=create_app(self.path/'api',False,gpu_runner=ROOT/'tests/fake_upscale_runner.py')
        with TestClient(app) as c:
            self.assertEqual(c.get('/api/upscale/config').status_code,200)
            self.assertEqual(c.post('/api/upscale/jobs',json={}).status_code,403)
            self.assertEqual(c.get('/api/jobs').status_code,200)
        app.state.store.db.close()
    def test_ui_api_stays_responsive_while_child_runs(self):
        app=create_app(self.path/'responsive',False,gpu_runner=ROOT/'tests/fake_upscale_runner.py')
        source=app.state.store.asset(png(Image.new('RGB',(32,32))))
        self.model.write_text('wait');app.state.gpu.configure({'python':sys.executable,'model':str(self.model)})
        try:
            with TestClient(app) as c:
                job=app.state.gpu.submit({'id':str(uuid.uuid4()),'source_id':source['id'],'options':{'factor':2}})
                t=threading.Thread(target=app.state.gpu.run,args=(job['id'],));t.start()
                try:
                    deadline=time.time()+5
                    while app.state.gpu.get(job['id'])['status']=='queued' and time.time()<deadline:time.sleep(.01)
                    self.assertEqual(c.get('/api/jobs').status_code,200);self.assertEqual(c.get('/api/worker/health').status_code,200)
                finally:
                    app.state.gpu.cancel(job['id']);t.join(8)
                self.assertFalse(t.is_alive())
        finally:app.state.store.db.close()
    def test_model_hash_is_fixed_at_registration(self):
        job=self.manager.submit(self.body());digest=job['model_sha256'];self.model.write_text('changed')
        self.assertEqual(self.manager.get(job['id'])['model_sha256'],digest)
    def test_large_image_encoding_does_not_hold_database_lock(self):
        entered=threading.Event();release=threading.Event()
        from core import normalize
        def delayed(raw):entered.set();release.wait(3);return normalize(raw)
        with patch('core.normalize',side_effect=delayed):
            t=threading.Thread(target=self.s.asset,args=(png(Image.new('RGB',(64,64))),));t.start()
            try:
                self.assertTrue(entered.wait(2))
                with ThreadPoolExecutor(max_workers=1) as pool:self.assertEqual(pool.submit(self.s.jobs).result(timeout=1),[])
            finally:release.set();t.join(3)
    def test_cancel_races_with_final_save_commit(self):
        entered=threading.Event();release=threading.Event();original=self.s.export
        def delayed(*a,**kw):entered.set();release.wait(4);return original(*a,**kw)
        job=self.manager.submit(self.body())
        with patch.object(self.s,'export',side_effect=delayed),ThreadPoolExecutor(max_workers=2) as pool:
            running=pool.submit(self.manager.run,job['id']);self.assertTrue(entered.wait(3))
            cancelling=pool.submit(self.manager.cancel,job['id'])
            release.set();running.result(timeout=6)
            with self.assertRaises(JobConflict):cancelling.result(timeout=3)
        self.assertEqual(self.manager.get(job['id'])['status'],'completed')
    def test_cancel_after_inference_before_commit_registers_no_output(self):
        entered=threading.Event();release=threading.Event();original=self.manager.progress
        def delayed(ident):
            original(ident)
            if self.manager.process and self.manager.process.poll() is not None:entered.set();release.wait(4)
        job=self.manager.submit(self.body())
        with patch.object(self.manager,'progress',side_effect=delayed):
            t=threading.Thread(target=self.manager.run,args=(job['id'],));t.start()
            try:self.assertTrue(entered.wait(4));self.assertEqual(self.manager.cancel(job['id'])['status'],'cancel_requested')
            finally:release.set();t.join(6)
        self.assertEqual(self.manager.get(job['id'])['status'],'cancelled');self.assertIsNone(self.manager.get(job['id'])['output'])
    def test_two_gpu_jobs_are_serial(self):
        self.model.write_text('wait');a=self.manager.submit(self.body());b=self.manager.submit(self.body())
        self.manager.thread.start();deadline=time.time()+5
        while self.manager.get(a['id'])['status']=='queued' and time.time()<deadline:time.sleep(.02)
        self.assertEqual(self.manager.get(b['id'])['status'],'queued')
        (self.path/'release').write_text('release');deadline=time.time()+8
        while self.manager.get(b['id'])['status']!='completed' and time.time()<deadline:time.sleep(.03)
        self.assertEqual(self.manager.get(b['id'])['status'],'completed')
        self.assertLess(self.manager.get(a['id'])['started_epoch'],self.manager.get(b['id'])['started_epoch'])
    def test_transient_windows_replace_is_local_retry(self):
        import persistence
        original=persistence.os.replace;calls=[]
        def once(*a):
            calls.append(a)
            if len(calls)==1:raise PermissionError('sharing')
            return original(*a)
        with patch('persistence.os.replace',side_effect=once),patch('persistence.time.sleep'):
            persistence.atomic_write(self.path/'state.json',b'complete')
        self.assertEqual((self.path/'state.json').read_bytes(),b'complete');self.assertEqual(len(calls),2)

if __name__=='__main__':unittest.main()
