import json
import uuid
import unittest
from unittest.mock import patch
import httpx
from PIL import Image
import test_upscale as fixtures
from imaging import png
from core import Worker
import comfy_backend as c
import qwen_backend as q


class Comfy(unittest.TestCase):
    setUpBase=fixtures.Jobs.setUp
    tearDown=fixtures.Jobs.tearDown

    def setUp(self):
        self.setUpBase()
        self.config=c.configure(self.s,{'url':'http://127.0.0.1:8188','diffusion':'qwen21.safetensors','text_encoder':'qwen3vl.safetensors','vae':'qwen21vae.safetensors'})
        self.calls=[];self.graph=None;self.ident=None;self.ambiguous=False;self.cancel_upload=False

    def params(self,**extra):
        return {'id':str(uuid.uuid4()),'model':q.MODEL,'provider':'qwen','mode':'generate','width':512,'height':512,'n':1,'format':'png','prompt':'A blue cup','refs':[],**q.DEFAULTS,**extra}

    def handler(self,request):
        path=request.url.path;self.calls.append((request.method,path,request.content))
        if path=='/object_info':
            nodes={k:{} for k in c.REQUIRED}
            for node,key,value in [('UNETLoader','unet_name','qwen21.safetensors'),('CLIPLoader','clip_name','qwen3vl.safetensors'),('VAELoader','vae_name','qwen21vae.safetensors')]:
                nodes[node]={'input':{'required':{key:[[value]]}}}
            return httpx.Response(200,json=nodes)
        if path=='/queue' and request.method=='GET':return httpx.Response(200,json={'queue_running':[],'queue_pending':[]})
        if path=='/upload/image':
            if self.cancel_upload:q.cancel(self.s,self.ident)
            return httpx.Response(200,json={'name':'uploaded.png','subfolder':'','type':'input'})
        if path=='/prompt':
            body=json.loads(request.content);self.graph=body['prompt'];self.ident=body['prompt_id']
            if self.ambiguous:raise httpx.ReadTimeout('lost acknowledgement')
            return httpx.Response(200,json={'prompt_id':self.ident})
        if path.startswith('/history/'):
            return httpx.Response(200,json={self.ident:{'status':{'completed':True,'status_str':'success'},'outputs':{'8':{'images':[{'filename':'out.png','type':'output','subfolder':''}]}}}})
        if path=='/view':return httpx.Response(200,content=png(Image.new('RGBA',(512,512),'blue')))
        return httpx.Response(200,json={})

    def client(self,config):return httpx.Client(base_url=config['url'],transport=httpx.MockTransport(self.handler))

    def test_generation_uses_comfy_and_records_result_without_local_inference(self):
        p=self.params(qwen_seed=-1,qwen_cfg=2,qwen_negative='text')
        j=self.s.submit(p)
        with patch.object(c,'client',self.client),patch('qwen_backend.launch',side_effect=AssertionError('no Diffusers')):
            Worker(self.s).run(j['id'])
        result=self.s.job(j['id']);self.assertEqual(result['status'],'completed')
        self.assertEqual(self.graph['4']['inputs']['prompt'],'A blue cup')
        self.assertEqual(self.graph['4']['inputs']['negative_prompt'],'text')
        self.assertGreaterEqual(self.graph['6']['inputs']['seed'],0)
        self.assertEqual(self.graph['6']['inputs']['cfg'],2)
        self.assertTrue(any(path=='/free' for _,path,_ in self.calls))

    def test_references_and_mask_order_and_requested_size(self):
        graph=c.workflow(self.params(),self.config,42,['base','ref','mask'],'job')
        self.assertEqual(graph['4']['inputs']['images.image_1'],['101',0])
        self.assertEqual(graph['103']['inputs']['image'],'mask')
        self.assertEqual(graph['6']['inputs']['latent_image'],['5',0])
        self.assertEqual(graph['5']['inputs']['width'],512)

    def test_cancel_during_upload_never_submits(self):
        p=self.params(refs=[{'id':self.image['id'],'role':'face'}]);self.ident=p['id'];self.cancel_upload=True
        j=self.s.submit(p)
        with patch.object(c,'client',self.client):Worker(self.s).run(j['id'])
        self.assertEqual(self.s.job(j['id'])['status'],'cancelled')
        self.assertFalse(any(path=='/prompt' for _,path,_ in self.calls))

    def test_ambiguous_submission_is_not_retried_and_recovered_without_generation(self):
        j=self.s.submit(self.params());self.ambiguous=True
        with patch.object(c,'client',self.client):
            Worker(self.s).run(j['id'])
            self.assertEqual(self.s.job(j['id'])['status'],'unknown')
            with self.assertRaises(ValueError):c.guard(self.s)
            self.ambiguous=False;c.recover(self.s,Worker(self.s))
        self.assertEqual(self.s.job(j['id'])['status'],'completed')
        self.assertEqual(sum(path=='/prompt' for _,path,_ in self.calls),1)

    def test_cancel_uses_only_targeted_endpoints(self):
        with self.client(self.config) as client:c.cancel_remote(client,'owned-id')
        bodies=[json.loads(body) for method,path,body in self.calls if method=='POST']
        self.assertEqual(bodies,[{'delete':['owned-id']},{'prompt_id':'owned-id'}])

    def test_urls_are_local_only(self):
        for url in ['https://example.com:8188','http://127.0.0.1:8188/path','http://user:pass@localhost:8188','http://example.com:8188']:
            with self.assertRaises(ValueError):c.validate_url(url)

    def test_missing_models_fail_before_transmission(self):
        config={**self.config,'vae':'missing'}
        with self.client(config) as client:
            with self.assertRaises(ValueError):c.validate_models(client,config)
        self.assertFalse(any(path=='/prompt' for _,path,_ in self.calls))

    def test_error_history_without_completed_flag_is_not_polled_forever(self):
        job={'comfy_prompt_id':'id'}
        with self.assertRaises(RuntimeError):
            c.collect(self.s,job,None,{'id':{'status':{'completed':False,'status_str':'error'}}})
