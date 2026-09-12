import json
import os
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
from PIL import Image
import test_upscale as fixtures
from core import JobConflict,ROOT
from imaging import png
from upscale_jobs import UpscaleJobs
from managed_child import tree_exited


class Recovery(unittest.TestCase):
    setUp=fixtures.Jobs.setUp
    tearDown=fixtures.Jobs.tearDown
    body=fixtures.Jobs.body

    def test_failed_registration_same_id_survives_restart(self):
        body=self.body()
        with patch.object(self.manager,'save',side_effect=OSError('disk full')):
            for _ in range(2):
                with self.assertRaises(OSError):self.manager.submit(body)
        self.assertTrue((self.manager.path/body['id']).is_dir())
        self.assertFalse((self.manager.path/body['id']/'job.json').exists())
        restored=UpscaleJobs(self.s,ROOT/'tests/fake_upscale_runner.py')
        with self.assertRaises(JobConflict):restored.submit({**body,'options':{'factor':3}})
        job=restored.submit(body)
        self.assertEqual(job['status'],'queued')
        before=(restored.path/body['id']/'job.json').read_bytes()
        restored.submit(body)
        self.assertEqual((restored.path/body['id']/'job.json').read_bytes(),before)
        restored.run(job['id']);self.assertEqual(restored.get(job['id'])['status'],'completed')

    def test_legacy_empty_directory_is_reusable_but_artifacts_are_protected(self):
        body=self.body();directory=self.manager.path/body['id'];directory.mkdir()
        self.assertEqual(self.manager.submit(body)['id'],body['id'])
        other=self.body();directory=self.manager.path/other['id'];directory.mkdir()
        (directory/'result.png').write_bytes(b'preserve')
        with self.assertRaises(JobConflict):self.manager.submit(other)
        self.assertEqual((directory/'result.png').read_bytes(),b'preserve')

    def test_initial_intent_save_failure_creates_no_job_directory(self):
        body=self.body()
        with patch('upscale_jobs.atomic_write',side_effect=OSError('full')):
            with self.assertRaises(OSError):self.manager.submit(body)
        self.assertFalse((self.manager.path/body['id']).exists())
        self.assertEqual(self.manager.submit(body)['status'],'queued')

    def test_crash_after_png_is_recovered_without_inference(self):
        self.model.write_text('crash-after-png')
        job=self.manager.submit(self.body());self.manager.run(job['id'])
        failed=self.manager.get(job['id']);self.assertEqual(failed['status'],'failed')
        status=json.loads((self.manager.path/job['id']/'status.json').read_text())
        self.assertEqual(status['state'],'saving')
        self.assertTrue(self.manager.public(failed)['result_available'])
        with patch('upscale_jobs.launch',side_effect=AssertionError('must not infer')):
            recovered=self.manager.retry_save(job['id'])
        self.assertEqual(recovered['status'],'completed')
        self.assertEqual(recovered['output']['width'],62)

    def finished_fixture(self):
        job=self.manager.submit(self.body());job.update(status='saving');self.manager.save(job)
        directory=self.manager.path/job['id']
        (directory/'result.png').write_bytes(png(Image.new('RGBA',(62,50),(3,4,5,64))))
        (directory/'status.json').write_text('{"state":"saving"}')
        self.manager.recover()
        return job['id'],directory

    def test_missing_or_malformed_status_does_not_lose_complete_png(self):
        for state in (None,'{broken','{"state":"failed"}'):
            ident,directory=self.finished_fixture()
            if state is None:(directory/'status.json').unlink()
            else:(directory/'status.json').write_text(state)
            with patch('upscale_jobs.launch',side_effect=AssertionError('must not infer')):
                result=self.manager.retry_save(ident)
            self.assertEqual(result['status'],'completed')
            self.assertFalse(result['native_scale_verified'])

    def test_incomplete_wrong_size_non_png_and_cancelled_outputs_rejected(self):
        for mode in ('truncated','size','jpeg','cancelled','cancel-marker','cancel-status','alive'):
            ident,directory=self.finished_fixture()
            if mode=='truncated':(directory/'result.png').write_bytes((directory/'result.png').read_bytes()[:40])
            elif mode=='size':(directory/'result.png').write_bytes(png(Image.new('RGB',(12,12))))
            elif mode=='jpeg':Image.new('RGB',(62,50)).save(directory/'result.png',format='JPEG')
            elif mode=='cancelled':
                job=self.manager.get(ident);job['status']='cancelled';self.manager.save(job)
            elif mode=='cancel-marker':(directory/'cancel').write_text('cancel')
            elif mode=='cancel-status':(directory/'status.json').write_text('{"state":"cancelled"}')
            with patch('upscale_jobs.tree_exited',return_value=mode!='alive'):
                self.assertFalse(self.manager.public(self.manager.get(ident))['result_available'],mode)
                with self.assertRaises((ValueError,JobConflict)):self.manager.retry_save(ident)
            self.assertIsNone(self.manager.get(ident)['output'])

    def test_live_legacy_pid_blocks_restart_without_terminating_it(self):
        job=self.manager.submit(self.body());job.update(status='loading',process_id=os.getpid());self.manager.save(job)
        with self.assertRaises(RuntimeError):self.manager.recover()
        self.assertEqual(self.manager.get(job['id'])['status'],'loading')

    @unittest.skipUnless(os.name=='nt','Windows Job Object integration')
    def test_forced_uvicorn_exit_kills_tree_then_restart_runs_new_job(self):
        self.forced_restart(False)

    @unittest.skipUnless(os.name=='nt','Windows Job Object integration')
    def test_forced_exit_before_pid_persistence_also_kills_tree(self):
        self.forced_restart(True)

    def forced_restart(self,pause):
        import ctypes as c
        from ctypes import wintypes as w
        from managed_child import open_process,wait_handle,close,dll,checked
        terminate=dll.TerminateProcess;terminate.argtypes=[w.HANDLE,w.UINT];terminate.restype=w.BOOL
        self.model.write_text('wait');body=self.body()
        request=self.path/'launch.json';request.write_text(json.dumps(body))
        unrelated=subprocess.Popen([sys._base_executable,'-c','import time;time.sleep(60)'],creationflags=subprocess.CREATE_NO_WINDOW)
        owner=subprocess.Popen([sys.executable,str(ROOT/'tests/serve_upscale_crash.py'),str(self.s.path),str(request)]+(['pause-before-pid'] if pause else []),
                               cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        handles=[];owner_handle=None
        try:
            deadline=time.monotonic()+12
            while time.monotonic()<deadline:
                try:
                    ready=json.loads((self.s.path/'owner-ready.json').read_text())
                    job=self.manager.get(body['id'])
                    status=json.loads((self.manager.path/body['id']/'status.json').read_text())
                    launch_pid=json.loads((self.s.path/'child-start.json').read_text())['pid'] if pause else job.get('process_id')
                    if launch_pid and status.get('pid'):break
                except (OSError,ValueError,LookupError):pass
                time.sleep(.03)
            else:self.fail('mock inference did not start')
            owner_handle=open_process(0x00100001,False,ready['pid']);checked(owner_handle)
            if pause:self.assertNotIn('process_id',job)
            for pid in {launch_pid,status['pid']}:
                handle=open_process(0x00100000,False,pid);checked(handle);handles.append(handle)
            checked(terminate(owner_handle,91))  # kill only the server, never /T
            self.assertEqual(wait_handle(owner_handle,10000),0)
            for handle in handles:self.assertEqual(wait_handle(handle,10000),0,'orphaned Python child')
            self.assertTrue(tree_exited(self.manager.path/body['id']))
            self.assertIsNone(unrelated.poll(),'unrelated process was terminated')
            self.model.write_text('normal');new=self.body();request.write_text(json.dumps(new))
            restarted=subprocess.Popen([sys.executable,str(ROOT/'tests/serve_upscale_crash.py'),str(self.s.path),str(request)],
                                       cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
            restart_handle=None
            try:
                deadline=time.monotonic()+12
                while time.monotonic()<deadline:
                    ready2=json.loads((self.s.path/'owner-ready.json').read_text())
                    if ready2['job']==new['id']:
                        if restart_handle is None:restart_handle=open_process(0x00100001,False,ready2['pid']);checked(restart_handle)
                        if self.manager.get(new['id'])['status']=='completed':break
                    time.sleep(.03)
                else:self.fail('restarted Uvicorn did not complete the new job')
                self.assertEqual(self.manager.get(body['id'])['status'],'interrupted')
                self.assertTrue(tree_exited(self.manager.path/new['id']))
            finally:
                if restart_handle:terminate(restart_handle,93);wait_handle(restart_handle,10000);close(restart_handle)
                else:restarted.terminate()
                restarted.wait(timeout=10)
        finally:
            if owner_handle:
                if wait_handle(owner_handle,0)!=0:terminate(owner_handle,92)
                close(owner_handle)
            for handle in handles:close(handle)
            owner.wait(timeout=10)
            # Test-owned unrelated sentinel only; no production process is touched.
            unrelated.terminate();unrelated.wait(timeout=10)


if __name__=='__main__':unittest.main()
