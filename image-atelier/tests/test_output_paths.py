import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from fastapi.testclient import TestClient
from core import Store,Worker,ROOT
from imaging import png
from server import create_app
from upscale_jobs import UpscaleJobs
from output_paths import FIELDS

class OutputPaths(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.s=Store(self.root/'data');self.s.set_settings({'output':str(self.root/'legacy'),'budget':0,'reservation':1,'live':False,
            **{key:str(self.root/category) for category,key in FIELDS.items()}})
        self.asset=self.s.asset(png(Image.new('RGB',(1024,1024),'red')))
    def tearDown(self):self.s.db.close();self.temp.cleanup()
    def params(self,mode,**extra):return {'id':str(uuid.uuid4()),'model':'gpt-image-2.5-sunburst','provider':'mock','mode':mode,'target':None if mode=='generate' else self.asset['id'],'width':1024,'height':1024,'prompt':'test','refs':[],**extra}
    def run_job(self,mode,**extra):
        job=self.s.submit(self.params(mode,**extra));Worker(self.s).run(job['id']);job=self.s.job(job['id'])
        self.assertEqual(job['status'],'completed',job['message']);return job

    def test_generation_edit_and_inpaint_use_independent_paths(self):
        for mode,category in [('generate','t2i'),('polish','i2i'),('inpaint','i2i')]:
            with self.subTest(mode=mode):
                extra={'strokes':[{'width':40,'points':[[256,256]]}],'composite':True} if mode=='inpaint' else {}
                job=self.run_job(mode,**extra)
                for dest in job['saved_paths']:
                    self.assertEqual(Path(dest).parent,self.root/category);self.assertTrue(Path(dest).is_file())
                for asset in job['outputs']:
                    self.assertEqual(asset['output_category'],category)
                    self.assertEqual(Path(self.s.export(asset['id'])).parent,self.root/category)

    def test_old_untagged_assets_and_derived_images_follow_original_job(self):
        # Legacy results are found through history, rather than the current UI mode.
        original=self.s.asset(png(Image.new('RGB',(1024,1024),'blue')),kind='raw')
        job=self.s.submit(self.params('generate'));job.update(status='completed',outputs=[original]);self.s.save_job(job)
        child=self.s.asset(png(Image.new('RGB',(512,512),'blue')),parent=original['id'],kind='resized')
        self.assertEqual(Path(self.s.export(original['id'])).parent,self.root/'t2i')
        self.assertEqual(Path(self.s.export(child['id'])).parent,self.root/'t2i')
        upscaled=self.s.asset(png(Image.new('RGB',(512,512),'blue')),parent=original['id'],kind='upscaled')
        self.assertEqual(Path(self.s.export(upscaled['id'])).parent,self.root/'upscale')

    def test_upscale_final_save_and_retry_use_upscale_path(self):
        import sys
        model=self.root/'model.pth';model.write_text('normal')
        manager=UpscaleJobs(self.s,ROOT/'tests/fake_upscale_runner.py');manager.configure({'python':sys.executable,'model':str(model)})
        try:
            small=self.s.asset(png(Image.new('RGB',(31,25),'red')))
            job=manager.submit({'id':str(uuid.uuid4()),'source_id':small['id'],'options':{'factor':2}})
            with patch.object(self.s,'export',side_effect=PermissionError):manager.run(job['id'])
            self.assertEqual(manager.get(job['id'])['status'],'export_failed')
            job=manager.retry_save(job['id']);self.assertEqual(job['status'],'completed')
            self.assertEqual(Path(job['saved_path']).parent,self.root/'upscale')
            self.assertEqual(manager.retry_save(job['id'])['saved_path'],job['saved_path'])
        finally:manager.shutdown()

    def test_response_recovery_uses_current_path_for_the_same_category(self):
        job=self.s.submit(self.params('generate'))
        with patch.object(self.s,'export',side_effect=PermissionError):Worker(self.s).run(job['id'])
        self.assertEqual(self.s.job(job['id'])['status'],'local_error')
        self.s.set_settings({**self.s.settings(),'output_t2i':str(self.root/'new-t2i')})
        with patch('core.mock_request',side_effect=AssertionError('no regeneration')):Worker(self.s).reprocess(job['id'])
        result=self.s.job(job['id']);self.assertEqual(result['status'],'completed')
        self.assertEqual(Path(result['saved_path']).parent,self.root/'new-t2i')
        self.assertEqual(self.s.settings()['output_i2i'],str(self.root/'i2i'))

    def test_legacy_settings_keep_one_folder_and_api_updates_are_independent(self):
        self.s.set_settings({'output':str(self.root/'old-output')})
        for key in FIELDS.values():self.assertEqual(self.s.settings()[key],str(self.root/'old-output'))
        app=create_app(self.root/'api',False)
        try:
            with TestClient(app) as client:
                boot=client.get('/api/bootstrap').json();headers={'X-Atelier-Token':boot['token']}
                initial={**boot['settings'],**{key:str(self.root/category) for category,key in FIELDS.items()}}
                response=client.post('/api/settings',json=initial,headers=headers);self.assertEqual(response.status_code,200)
                changed={**response.json(),'output_i2i':str(self.root/'changed-edit')}
                response=client.post('/api/settings',json=changed,headers=headers);self.assertEqual(response.status_code,200)
                self.assertEqual(response.json()['output_t2i'],initial['output_t2i']);self.assertEqual(response.json()['output_upscale'],initial['output_upscale'])
                invalid={**changed,'output_upscale':'relative-folder'}
                self.assertEqual(client.post('/api/settings',json=invalid,headers=headers).status_code,400)
                self.assertEqual(client.get('/api/bootstrap').json()['settings']['output_upscale'],initial['output_upscale'])
                with patch('server.os.startfile') as opening:
                    response=client.post('/api/output-folder/open',json={'mode':'upscale'},headers=headers)
                    self.assertEqual(response.status_code,200);self.assertEqual(Path(response.json()['path']),self.root/'upscale');opening.assert_called_once()
        finally:app.state.store.db.close()

if __name__=='__main__':unittest.main()
