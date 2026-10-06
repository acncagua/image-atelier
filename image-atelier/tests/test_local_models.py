import json
import unittest
from unittest.mock import patch
import httpx
from PIL import Image
from core import Worker, canonical_input, prompt_for
from imaging import png
import comfy_backend as c
import local_models as models
from test_comfy import Comfy


FILES={
    'Qwen-Image-2.1':('qwen21.safetensors','qwen3vl.safetensors','qwen21vae.safetensors'),
    'FLUX.1-dev':('flux1-dev.safetensors','t5xxl_fp16.safetensors','ae.safetensors'),
    'FLUX.1-schnell':('flux1-schnell.safetensors','t5xxl_fp16.safetensors','ae.safetensors'),
    'FLUX.1-Kontext-dev':('flux1-kontext-dev.safetensors','t5xxl_fp16.safetensors','ae.safetensors'),
    'Anima':('anima-base-v1.0.safetensors','qwen_3_06b_base.safetensors','qwen_image_vae.safetensors'),
}

def object_info():
    nodes={k:{} for k in (*c.REQUIRED,'DualCLIPLoader','EmptySD3LatentImage','CLIPTextEncode','FluxGuidance','ReferenceLatent','ImageScale','VAEEncode','ImageToMask','SetLatentNoiseMask')}
    encoders=list({v[1] for v in FILES.values()})+['clip_l.safetensors','mmproj-qwen.gguf','unrelated.safetensors']
    for node,key,values in [('UNETLoader','unet_name',[v[0] for v in FILES.values()]+['flux2-dev.safetensors','flux1-fill-dev.safetensors']),('CLIPLoader','clip_name',encoders),('DualCLIPLoader','clip_name1',encoders),('VAELoader','vae_name',list({v[2] for v in FILES.values()}))]:
        nodes[node]={'input':{'required':{key:[values]}}}
    return nodes


class LocalModels(unittest.TestCase):
    setUp=Comfy.setUp
    setUpBase=Comfy.setUpBase
    tearDown=Comfy.tearDown
    params=Comfy.params
    client=Comfy.client

    def handler(self,request):
        if request.url.path=='/object_info':return httpx.Response(200,json=object_info())
        return Comfy.handler(self,request)

    def configure(self,model):
        diffusion,encoder,vae=FILES[model]
        return c.configure(self.s,{'model':model,'diffusion':diffusion,'text_encoder':encoder,'text_encoder_2':'clip_l.safetensors','vae':vae})

    def test_model_filters_separate_shared_qwen_names_and_flux_variants(self):
        with self.client(self.config) as client:
            for model,(diffusion,encoder,vae) in FILES.items():
                with self.subTest(model=model):
                    result=c.inspect(client,model)
                    self.assertEqual(result['diffusion'],[diffusion])
                    self.assertEqual(result['text_encoder'],[encoder])
                    self.assertEqual(result['vae'],[vae])
                    self.assertEqual(result['missing'],[])

    def test_profiles_are_independent_and_jobs_freeze_the_selection(self):
        original=c.configuration(self.s)
        for model in FILES:self.configure(model)
        self.assertEqual(c.configuration(self.s)['diffusion'],original['diffusion'])
        p=self.params(model='Anima',provider='comfyui')
        job=self.s.submit(p)
        self.configure('FLUX.1-dev')
        self.assertEqual(job['local_machine']['model'],'Anima')
        self.assertEqual(job['local_machine']['diffusion'],FILES['Anima'][0])

    def test_old_flat_config_migrates_without_copying_into_other_models(self):
        (self.s.path/'comfy-settings.json').write_text(json.dumps(self.config),'utf-8')
        self.assertEqual(c.configuration(self.s,'Anima')['diffusion'],'')
        self.configure('Anima')
        self.assertEqual(c.configuration(self.s)['diffusion'],self.config['diffusion'])

    def test_every_model_generates_through_comfy_without_qwen_nodes(self):
        for model in FILES:
            with self.subTest(model=model):
                self.configure(model)
                job=self.s.submit(self.params(model=model,provider='comfyui'))
                with patch.object(c,'client',self.client):Worker(self.s).run(job['id'])
                result=self.s.job(job['id'])
                self.assertEqual(result['status'],'completed',result['message'])
                self.assertEqual(result['outputs'][0]['width'],512)
                if model!='Qwen-Image-2.1':self.assertNotIn('TextEncodeQwenImage21',[n['class_type'] for n in self.graph.values()])

    def test_inpaint_masks_latent_and_composite_protects_unpainted_pixels(self):
        base=self.s.asset(png(Image.new('RGB',(512,512),'red')))
        for model in ('Anima','FLUX.1-dev','FLUX.1-Kontext-dev'):
            with self.subTest(model=model):
                self.configure(model)
                job=self.s.submit(self.params(model=model,provider='comfyui',mode='inpaint',target=base['id'],composite=True,feather=0,strokes=[{'width':32,'points':[[256,256]]}]))
                with patch.object(c,'client',self.client):Worker(self.s).run(job['id'])
                result=self.s.job(job['id'])
                self.assertEqual(result['status'],'completed',result['message'])
                self.assertEqual(self.graph['24']['class_type'],'SetLatentNoiseMask')
                self.assertEqual(self.graph['6']['inputs']['latent_image'],['24',0])
                with Image.open(self.s.file(result['outputs'][-1]['id'])) as image:
                    self.assertEqual(image.getpixel((0,0))[:3],(255,0,0))
                    self.assertEqual(image.getpixel((256,256))[:3],(0,0,255))

    def test_kontext_reference_is_conditioning_not_initial_latent(self):
        config=self.configure('FLUX.1-Kontext-dev')
        p=self.params(model=config['model'],provider='comfyui')
        graph=c.workflow(p,config,42,['ref.png'],'test')
        self.assertEqual(graph['22']['class_type'],'ReferenceLatent')
        self.assertEqual(graph['6']['inputs']['positive'],['22',0])
        self.assertEqual(graph['6']['inputs']['latent_image'],['5',0])
        self.assertEqual(graph['6']['inputs']['denoise'],1)

    def test_unsupported_references_and_provider_mismatches_are_rejected(self):
        for model in ('Anima','FLUX.1-dev','FLUX.1-schnell'):
            with self.assertRaises(ValueError):self.s.submit(self.params(model=model,provider='comfyui',refs=[{'id':self.image['id'],'role':'face'}]))
            with self.assertRaises(ValueError):self.s.submit(self.params(model=model,provider='qwen'))

    def test_invalid_profile_or_missing_edit_nodes_never_submits(self):
        config=self.configure('Anima')
        with self.client(config) as client:
            with self.assertRaises(ValueError):c.validate_models(client,{**config,'vae':'qwen21vae.safetensors'})
        info=object_info();del info['SetLatentNoiseMask']
        with httpx.Client(base_url=config['url'],transport=httpx.MockTransport(lambda r:httpx.Response(200,json=info))) as client:
            with self.assertRaisesRegex(ValueError,'SetLatentNoiseMask'):c.validate_models(client,config,self.params(model='Anima',mode='inpaint',input_ids=['base']))
        self.assertFalse(any(path=='/prompt' for _,path,_ in self.calls))

    def test_defaults_and_prompts_come_from_selected_model(self):
        for model,spec in models.MODELS.items():
            p=canonical_input({'model':model,'provider':'comfyui','change':'a cup'})
            self.assertEqual(p['qwen_steps'],spec['steps'])
            self.assertEqual(p['qwen_cfg'],spec['cfg'])
            self.assertEqual(prompt_for(p),'a cup')

    def test_comfy_cancel_and_ambiguous_recovery_for_new_provider(self):
        self.configure('Anima')
        job=self.s.submit(self.params(model='Anima',provider='comfyui'))
        cancelled=c.cancel(self.s,job['id'])
        self.assertEqual(cancelled['status'],'cancelled')
        job=self.s.submit(self.params(model='Anima',provider='comfyui'))
        self.ambiguous=True
        with patch.object(c,'client',self.client):
            Worker(self.s).run(job['id'])
            self.assertEqual(self.s.job(job['id'])['status'],'unknown')
            self.ambiguous=False
            c.recover(self.s,Worker(self.s))
        self.assertEqual(self.s.job(job['id'])['status'],'completed')
        self.assertEqual(sum(path=='/prompt' for _,path,_ in self.calls),1)
