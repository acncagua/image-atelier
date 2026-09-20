"""Regression cases from CODEX_IMAGE_ATELIER_FIXES_01_03 (no external API)."""
import base64
import copy
import json
import tempfile
import unittest
import uuid
import threading
import sqlite3
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import httpx
from PIL import Image
from fastapi.testclient import TestClient
from core import Store, Worker
from imaging import png
from server import create_app

class Fixes(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.app=create_app(Path(self.tmp.name)/'日本語 data',False)
        self.s=self.app.state.store
        self.s.set_settings({'output':str(Path(self.tmp.name)/'exports'),'budget':10,'reservation':1,'live':True,'limit_mode':'stop','budget_period':'all'})
        self.a=self.s.asset(png(Image.new('RGB',(1024,1024),'navy')))
        self.client=TestClient(self.app)
        self.client.headers['X-Atelier-Token']=self.app.state.token
    def tearDown(self):
        self.client.close();self.s.db.close();self.tmp.cleanup()
    def params(self,**extra):
        p=dict(id=str(uuid.uuid4()),provider='mock',mode='polish',target=self.a['id'],refs=[],prompt='最終指示',change='変更',keep='維持',model='gpt-image-2.5-sunburst',quality='medium',width=1024,height=1024,n=1,format='png',strokes=[],composite=False,feather=8)
        p.update(extra);return p
    def test_same_id_different_content_is_409(self):
        p=self.params();self.client.post('/api/jobs',json=p)
        response=self.client.post('/api/jobs',json={**p,'prompt':'different'})
        self.assertEqual(response.status_code,409)
        self.assertEqual(self.s.job(p['id'])['params']['prompt'],'最終指示')
    def test_different_ids_same_content_are_distinct(self):
        p=self.params();a=self.s.submit(p);b=self.s.submit({**p,'id':str(uuid.uuid4())})
        self.assertNotEqual(a['id'],b['id'])
    def test_local_edit_is_discoverable(self):
        out=self.client.post('/api/resize',json={'id':self.a['id'],'width':1040,'height':1040,'method':'pad'})
        self.assertEqual(out.status_code,200)
        edits=self.client.get('/api/local-edits')
        self.assertEqual(edits.status_code,200)
        self.assertEqual(edits.json()[0]['result']['id'],out.json()['id'])
    def test_legacy_response_without_flag_is_downloadable(self):
        j=self.s.submit(self.params());j.update(status='unknown');self.s.save_job(j)
        (self.s.path/('http_response_'+j['id']+'.json')).write_text('{broken response','utf-8')
        response=self.client.get('/api/jobs/'+j['id']+'/recovery')
        self.assertEqual(response.status_code,200)
        self.assertIn('attachment',response.headers['content-disposition'])
    def test_worker_failure_is_visible(self):
        worker=Worker(self.s)
        with patch.object(self.s,'jobs',side_effect=OSError('private diagnostic')):
            worker.tick()
        self.assertEqual(worker.health()['state'],'fault')
        self.assertNotIn('private diagnostic',json.dumps(worker.health()))
    def response(self,job,items=None,legacy=False):
        image=base64.b64encode(self.s.file(self.a['id']).read_bytes()).decode()
        payload={'data':[{'b64_json':x} for x in (items or [image])],'usage':{'input_tokens':30,'input_tokens_details':{'text_tokens':10,'image_tokens':20},'output_tokens':100}}
        if legacy:payload={'images':items or [image],'usage':payload['usage']}
        prefix='response_' if legacy else 'http_response_'
        (self.s.path/(prefix+job['id']+'.json')).write_text(json.dumps(payload),'utf-8')
        return payload
    def test_parallel_same_id_one_reservation_and_call(self):
        p=self.params(provider='openai')
        with patch('core.api_key',return_value='test'):
            with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(lambda _:self.s.submit(copy.deepcopy(p)),range(16)))
            self.assertEqual(len(self.s.jobs()),1);self.assertEqual(self.s.jobs()[0]['reserved'],1)
            worker=Worker(self.s)
            with patch('core.real_request',return_value=([self.s.file(self.a['id']).read_bytes()],None,'test')) as call:
                with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(lambda _:worker.run(p['id']),range(4)))
                self.assertEqual(call.call_count,1)
    def test_canonical_comparison_ignores_server_fields(self):
        p=self.params();self.s.submit(p)
        self.assertEqual(self.s.submit({**p,'input_snapshots':[{'changed':True}],'started':1})['id'],p['id'])
        for field,value in [('quality','high'),('strokes',[{'width':4,'points':[[1,2]]}]),('format','webp'),('n',2)]:
            self.assertEqual(self.client.post('/api/jobs',json={**p,field:value}).status_code,409)
    def test_two_jobs_fifo_while_first_running(self):
        a=self.s.submit(self.params());gate=threading.Event();entered=threading.Event();seen=[]
        def provider(p,store):
            seen.append(p['id']);entered.set()
            if p['id']==a['id']:gate.wait(3)
            return [self.s.file(self.a['id']).read_bytes()],None,None
        worker=Worker(self.s)
        with patch('core.mock_request',side_effect=provider):
            thread=threading.Thread(target=worker.tick);thread.start();self.assertTrue(entered.wait(2))
            b=self.s.submit(self.params(prompt='B'))
            self.assertEqual(b['status'],'queued');gate.set();thread.join(3);worker.tick()
        self.assertEqual(seen,[a['id'],b['id']])
    def test_deliberate_same_input_runs_twice(self):
        a=self.s.submit(self.params());b=self.s.submit({**a['params'],'id':str(uuid.uuid4())});worker=Worker(self.s)
        with patch('core.mock_request',return_value=([self.s.file(self.a['id']).read_bytes()],None,None)) as call:
            worker.tick();worker.tick();self.assertEqual(call.call_count,2)
    def test_accepted_registration_can_be_queried_after_lost_reply(self):
        p=self.params();self.client.post('/api/jobs',json=p)
        worker=Worker(self.s)
        with patch('core.mock_request',return_value=([self.s.file(self.a['id']).read_bytes()],None,None)) as call:
            worker.tick();found=self.client.get('/api/jobs/'+p['id']);self.client.post('/api/jobs',json=p);worker.tick()
            self.assertEqual(found.status_code,200);self.assertEqual(call.call_count,1)
    def test_resize_history_chain_survives_restart(self):
        original=self.s.meta(self.a['id'])['sha256']
        out=self.client.post('/api/resize',json={'id':self.a['id'],'width':1040,'height':1040,'method':'pad','context':self.params(strokes=[{'width':3,'points':[[10,10]]}])}).json()
        result=self.client.post('/api/resize',json={'id':out['id'],'width':1024,'height':1024,'method':'crop'}).json()
        reopened=Store(self.s.path)
        try:
            edits=reopened.local_edits();self.assertEqual(edits[0]['source_id'],out['id']);self.assertEqual(edits[0]['details']['x'],8)
            self.assertEqual(edits[1]['details']['color'],[0,0,0,0]);self.assertTrue(edits[1]['context']['strokes'])
            self.assertEqual(reopened.meta(self.a['id'])['sha256'],original);self.assertEqual(reopened.jobs(),[])
            self.assertEqual(reopened.meta(result['id'])['parent'],out['id'])
        finally:reopened.db.close()
    def test_local_history_transaction_failure_rolls_back_asset(self):
        before=self.s.db.execute('SELECT count(*) FROM assets').fetchone()[0]
        self.s.db.execute("CREATE TRIGGER fail_edit BEFORE INSERT ON local_edits BEGIN SELECT RAISE(ABORT, 'test'); END")
        response=self.client.post('/api/resize',json={'id':self.a['id'],'width':1040,'height':1040,'method':'pad'})
        self.assertEqual(response.status_code,503)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM assets').fetchone()[0],before)
    def test_migration_backup_and_legacy_adjusted(self):
        old=self.s.asset(png(Image.new('RGB',(100,100))),'old',self.a['id'],'adjusted')
        self.s.db.execute('PRAGMA user_version=0');self.s.db.commit()
        reopened=Store(self.s.path)
        try:
            e=next(e for e in reopened.local_edits() if e['id']==old['id'])
            self.assertIsNone(e['method']);self.assertIsNone(e['created']);self.assertEqual(e['source_id'],self.a['id'])
            backups=list((self.s.path/'backups').glob('*/history.sqlite3'));self.assertEqual(len(backups),1)
            backup=sqlite3.connect(backups[0])
            try:self.assertEqual(backup.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            finally:backup.close()
        finally:reopened.db.close()
        again=Store(self.s.path);again.db.close();self.assertEqual(len(list((self.s.path/'backups').glob('*/history.sqlite3'))),1)
    def test_sending_state_save_failure_prevents_provider(self):
        self.s.submit(self.params());worker=Worker(self.s)
        with patch.object(self.s,'save_job',side_effect=OSError('private')),patch('core.mock_request') as provider:
            worker.tick();provider.assert_not_called()
        self.assertEqual(worker.health()['state'],'fault')
    def test_final_save_failure_pauses_without_repeat(self):
        j=self.s.submit(self.params());worker=Worker(self.s);original=self.s.save_job
        def fail_complete(job):
            if job.get('status')=='completed':raise OSError('private')
            original(job)
        with patch.object(self.s,'save_job',side_effect=fail_complete),patch('core.mock_request',return_value=([self.s.file(self.a['id']).read_bytes()],None,None)) as provider:
            worker.tick();worker.tick();self.assertEqual(provider.call_count,1)
        self.assertEqual(worker.health()['state'],'fault')
        worker.resume();self.assertNotEqual(self.s.job(j['id'])['status'],'queued')
    def test_local_preflight_failure_vs_timeout(self):
        with patch('core.api_key',return_value='test'):j=self.s.submit(self.params(provider='openai'))
        worker=Worker(self.s)
        with patch('core.api_key',side_effect=ValueError('private')),patch('core.real_request') as provider:
            worker.run(j['id']);provider.assert_not_called()
        self.assertEqual(self.s.job(j['id'])['phase'],'preflight_failed');self.assertEqual(self.s.job(j['id'])['reserved'],0)
        with patch('core.api_key',return_value='test'):
            other=self.s.submit(self.params(provider='openai'))
            with patch('core.real_request',side_effect=httpx.ReadTimeout('private')):worker.run(other['id'])
        self.assertEqual(self.s.job(other['id'])['status'],'unknown');self.assertEqual(self.s.job(other['id'])['reserved'],1)
    def test_reprocess_is_idempotent_and_offline(self):
        j=self.s.submit(self.params(provider='mock'));j.update(status='unknown');self.s.save_job(j);self.response(j)
        with patch('core.httpx.Client',side_effect=AssertionError('No HTTP')),patch('core.real_request',side_effect=AssertionError('No API')):
            worker=Worker(self.s);first=worker.reprocess(j['id']);second=worker.reprocess(j['id'])
        self.assertEqual(first['outputs'],second['outputs']);self.assertEqual(first['saved_paths'],second['saved_paths'])
        self.assertEqual(len(list(Path(self.s.settings()['output']).glob('*.png'))),1)
    def test_partial_decode_retains_good_item_and_usage(self):
        j=self.s.submit(self.params(n=2));j.update(status='unknown');self.s.save_job(j)
        good=base64.b64encode(self.s.file(self.a['id']).read_bytes()).decode();self.response(j,['not base64',good])
        worker=Worker(self.s);first=worker.reprocess(j['id']);second=worker.reprocess(j['id'])
        self.assertEqual(first['status'],'local_error');self.assertEqual(len(second['outputs']),1)
        self.assertEqual(first['outputs'],second['outputs']);self.assertEqual(second['usage']['output_tokens'],100)
        self.assertIn('error',second['processing_items']['0'])
    def test_export_retry_only_retries_local_step(self):
        j=self.s.submit(self.params());j.update(status='unknown');self.s.save_job(j);self.response(j,legacy=True)
        worker=Worker(self.s)
        with patch.object(self.s,'export',side_effect=PermissionError):first=worker.reprocess(j['id'])
        with patch('core.real_request',side_effect=AssertionError):second=worker.reprocess(j['id'])
        self.assertEqual(first['outputs'],second['outputs']);self.assertEqual(second['status'],'completed')
    def test_composite_retry_preserves_raw(self):
        p=self.params(mode='inpaint',strokes=[{'width':20,'points':[[40,40]]}],composite=True)
        j=self.s.submit(p);j.update(status='unknown');self.s.save_job(j);self.response(j)
        worker=Worker(self.s)
        with patch('worker.composite',side_effect=ValueError):first=worker.reprocess(j['id'])
        second=worker.reprocess(j['id'])
        self.assertEqual(first['outputs'][0],second['outputs'][0]);self.assertEqual(len(second['outputs']),2)
    def test_response_write_failure_keeps_volatile_and_pauses(self):
        j=self.s.submit(self.params());worker=Worker(self.s)
        from core import ResponsePersistenceError
        with patch('core.mock_request',side_effect=ResponsePersistenceError(b'{"data":[]}',None)):worker.run(j['id'])
        self.assertEqual(worker.health()['state'],'fault');self.assertIn(j['id'],worker.health()['volatile_response_ids'])
    def test_response_path_rejects_arbitrary_files(self):
        self.assertEqual(self.client.get('/api/jobs/invalid-id/recovery').status_code,400)
        self.assertEqual(self.client.get('/api/jobs/'+str(uuid.uuid4())+'/recovery').status_code,404)
    def test_http_saved_decode_failure_keeps_usage_and_recovers_good_image(self):
        with patch('core.api_key',return_value='test'):
            j=self.s.submit(self.params(provider='openai',n=2))
            good=base64.b64encode(self.s.file(self.a['id']).read_bytes()).decode()
            response=httpx.Response(200,json={'data':[{'b64_json':good},{'b64_json':'invalid!'}],
                'usage':{'input_tokens':30,'input_tokens_details':{'text_tokens':10,'image_tokens':20},'output_tokens':100}},
                headers={'x-request-id':'test-request'},request=httpx.Request('POST','https://api.openai.com'))
            with patch('core.httpx.Client') as cls:
                cls.return_value.__enter__.return_value.post.return_value=response
                worker=Worker(self.s);worker.run(j['id'])
        job=self.s.job(j['id']);self.assertEqual(job['status'],'local_error')
        self.assertEqual(job['usage']['output_tokens'],100);self.assertEqual(job['request_id'],'test-request')
        with patch('core.httpx.Client',side_effect=AssertionError):recovered=worker.reprocess(j['id'])
        self.assertEqual(len(recovered['outputs']),1)
    def test_health_endpoint_works_when_database_reads_fail(self):
        with patch.object(self.s,'jobs',side_effect=sqlite3.OperationalError('private')):
            self.assertEqual(self.client.get('/api/jobs').status_code,503)
            health=self.client.get('/api/worker/health')
            self.assertEqual(health.status_code,200);self.assertEqual(health.json()['state'],'fault')
            self.assertNotIn('private',health.text)
    def test_settings_read_failure_stops_external_send(self):
        with patch('core.api_key',return_value='test'):j=self.s.submit(self.params(provider='openai'))
        worker=Worker(self.s)
        with patch.object(self.s,'settings',side_effect=sqlite3.OperationalError('private')),patch('core.real_request') as provider:
            worker.run(j['id']);provider.assert_not_called()
        self.assertEqual(worker.health()['state'],'fault')
    def test_resume_preserves_queued_jobs_without_requeueing_unknown(self):
        unknown=self.s.submit(self.params());unknown['status']='unknown';self.s.save_job(unknown)
        queued=self.s.submit(self.params())
        worker=Worker(self.s);worker.fault(OSError())
        self.assertEqual(worker.resume()['state'],'running')
        self.assertEqual(self.s.job(unknown['id'])['status'],'unknown')
        self.assertEqual(self.s.job(queued['id'])['status'],'queued')
    def test_cancel_during_preflight_never_dispatches(self):
        with patch('core.api_key',return_value='test'):
            job=self.s.submit(self.params(provider='openai'))
        entered=threading.Event();release=threading.Event();original=self.s.file
        def paused_file(ident):
            entered.set();release.wait(3);return original(ident)
        worker=Worker(self.s)
        with patch('core.api_key',return_value='test'),patch.object(self.s,'file',side_effect=paused_file),patch('core.real_request') as provider:
            thread=threading.Thread(target=worker.run,args=(job['id'],));thread.start()
            try:
                self.assertTrue(entered.wait(2))
                response=self.client.post('/api/jobs/'+job['id']+'/cancel',json={})
                self.assertEqual(response.status_code,200)
            finally:release.set();thread.join(5)
            provider.assert_not_called()
        self.assertEqual(self.s.job(job['id'])['status'],'cancelled')
        self.assertEqual(self.s.job(job['id'])['reserved'],0)
    def test_partial_existing_export_recovers_without_overwriting(self):
        expected=self.s.file(self.a['id']).read_bytes()
        folder=Path(self.s.settings()['output']);folder.mkdir()
        broken=folder/'atelier_partial.png';broken.write_bytes(expected[:70])
        recovered=Path(self.s.export(self.a['id'],operation='partial'))
        self.assertEqual(recovered.read_bytes(),expected)
        if recovered!=broken:self.assertEqual(broken.read_bytes(),expected[:70])
        self.assertEqual(self.s.export(self.a['id'],operation='partial'),str(recovered))
    def test_export_failure_never_publishes_partial_final(self):
        with patch('persistence.os.fsync',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.s.export(self.a['id'],operation='interrupted')
        folder=Path(self.s.settings()['output'])
        self.assertFalse((folder/'atelier_interrupted.png').exists())
        self.assertEqual(list(folder.glob('*.tmp')),[])
        result=Path(self.s.export(self.a['id'],operation='interrupted'))
        self.assertEqual(result.read_bytes(),self.s.file(self.a['id']).read_bytes())
    def test_conflicting_valid_export_is_never_overwritten(self):
        folder=Path(self.s.settings()['output']);folder.mkdir()
        original=png(Image.new('RGB',(32,32),'red'))
        existing=folder/'atelier_collision.png';existing.write_bytes(original)
        result=Path(self.s.export(self.a['id'],operation='collision'))
        self.assertNotEqual(existing,result);self.assertEqual(existing.read_bytes(),original)
        self.assertEqual(result.read_bytes(),self.s.file(self.a['id']).read_bytes())
    def test_parallel_export_publish_is_idempotent(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            files=list(pool.map(lambda _:self.s.export(self.a['id'],operation='parallel'),range(8)))
        self.assertEqual(len(set(files)),1)
        self.assertEqual(len(list(Path(self.s.settings()['output']).glob('*.png'))),1)
    def test_dispatch_winner_rejects_late_cancellation(self):
        entered=threading.Event();release=threading.Event();content=self.s.file(self.a['id']).read_bytes()
        with patch('core.api_key',return_value='test'):job=self.s.submit(self.params(provider='openai'))
        def provider(*args):entered.set();release.wait(3);return [content],None,None
        worker=Worker(self.s)
        with patch('core.api_key',return_value='test'),patch('core.real_request',side_effect=provider) as send:
            thread=threading.Thread(target=worker.run,args=(job['id'],));thread.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(self.client.post('/api/jobs/'+job['id']+'/cancel',json={}).status_code,400)
            finally:release.set();thread.join(5)
            self.assertEqual(send.call_count,1)
    def test_missing_asset_is_404_but_db_failure_is_503(self):
        self.assertEqual(self.client.get('/api/assets/'+uuid.uuid4().hex).status_code,404)
        with patch.object(self.s,'meta',side_effect=sqlite3.OperationalError('temporary')):
            self.assertEqual(self.client.get('/api/assets/'+self.a['id']).status_code,503)
    def test_reprocess_repairs_partial_file_even_with_cached_export_path(self):
        job=self.s.submit(self.params());job['status']='unknown';self.s.save_job(job);self.response(job)
        worker=Worker(self.s);first=worker.reprocess(job['id'])
        old=Path(first['saved_paths'][0]);complete=old.read_bytes();old.write_bytes(complete[:70])
        with patch('core.real_request',side_effect=AssertionError('no API')):
            second=worker.reprocess(job['id']);third=worker.reprocess(job['id'])
        self.assertEqual(second['status'],'completed');self.assertEqual(second['outputs'],first['outputs'])
        self.assertEqual(Path(second['saved_paths'][0]).read_bytes(),complete)
        self.assertEqual(old.read_bytes(),complete[:70]);self.assertEqual(third['saved_paths'],second['saved_paths'])

if __name__=='__main__':unittest.main()
