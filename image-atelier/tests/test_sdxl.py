import json
import unittest
from unittest.mock import patch
import httpx
from PIL import Image,ImageChops
from core import Worker,canonical_input
import comfy_backend as c
import sdxl_backend as s
from imaging import png,mask_image
import test_comfy as fixtures

def object_info():
    info={n:{} for n in (*s.REQUIRED,'VAELoader','LoadImage','ImageScale','VAEEncode','ImageToMask','SetLatentNoiseMask','LatentUpscale','UpscaleModelLoader','ImageUpscaleWithModel')}
    for node,key,options in [('CheckpointLoaderSimple','ckpt_name',['XL.safetensors','OtherXL.safetensors']),('VAELoader','vae_name',['sdxl_vae.safetensors']),('KSampler','sampler_name',['euler','dpmpp_2m']),('KSampler','scheduler',['normal','karras']),('LatentUpscale','upscale_method',['bislerp','nearest-exact']),('ImageScale','upscale_method',['lanczos','nearest-exact'])]:
        info[node].setdefault('input',{}).setdefault('required',{})[key]=[options]
    # v3 nodes expose combos differently from legacy nodes.
    info['UpscaleModelLoader']={'input':{'required':{'model_name':['COMBO',{'options':['4x-UltraSharp.pth']}]}}}
    return info

class SDXL(unittest.TestCase):
    setUpBase=fixtures.Comfy.setUpBase
    tearDown=fixtures.Comfy.tearDown
    client=fixtures.Comfy.client
    def setUp(self):
        self.setUpBase();self.config=c.configure(self.s,{'model':'SDXL','checkpoint':'XL.safetensors'})
        self.calls=[];self.ident=None;self.graph=None;self.ambiguous=False;self.cancel_upload=False
    def params(self,**extra):
        body={**fixtures.Comfy.params(self),'model':'SDXL','provider':'comfyui','qwen_steps':30,'qwen_cfg':7,**extra}
        return canonical_input(body)|{'id':body['id']}
    def handler(self,request):
        if request.url.path=='/object_info':return httpx.Response(200,json=object_info())
        if request.url.path=='/view':
            size=s.sampling_size(self.s.job(self.ident)['params'])
            return httpx.Response(200,content=png(Image.new('RGB',size,'blue')))
        return fixtures.Comfy.handler(self,request)
    def run_job(self,**extra):
        job=self.s.submit(self.params(**extra))
        with patch.object(c,'client',self.client):Worker(self.s).run(job['id'])
        return self.s.job(job['id'])

    def test_defaults_and_embedded_vae_require_only_checkpoint(self):
        p=self.params();self.assertEqual(p['denoise'],.7);self.assertEqual(p['clip_skip'],2);self.assertEqual(p['qwen_cfg'],7)
        job=self.run_job();self.assertEqual(job['status'],'completed',job['message'])
        self.assertNotIn('3',self.graph);self.assertEqual(self.graph['7']['inputs']['vae'],['1',2])
        self.assertEqual(self.graph['2']['inputs']['stop_at_clip_layer'],-2)
        self.assertEqual(self.graph['6']['inputs']['denoise'],1)

    def test_optional_vae_and_alternate_hires_checkpoint(self):
        for vae in ('','sdxl_vae.safetensors'):
            with self.subTest(vae=vae):
                c.configure(self.s,{'model':'SDXL','checkpoint':'XL.safetensors','vae':vae,'hires_checkpoint':'OtherXL.safetensors'})
                job=self.run_job(hires_fix=True,hires_scale=1.5,hires_steps=12)
                self.assertEqual(job['status'],'completed',job['message'])
                self.assertEqual(job['outputs'][0]['width'],768)
                self.assertNotIn('要求寸法',job['message'])
                self.assertEqual(self.graph['30']['inputs']['ckpt_name'],'OtherXL.safetensors')
                self.assertEqual(self.graph['41']['inputs']['steps'],12)
                self.assertEqual(self.graph['41']['inputs']['denoise'],.7)
                self.assertEqual(self.graph['42']['inputs']['vae'],['3',0] if vae else ['30',2])
                if not vae:self.assertEqual(self.graph['32']['class_type'],'VAEEncode')

    def test_hires_inherits_checkpoint_steps_and_supports_all_upscaler_routes(self):
        for upscaler,node in [('latent:bislerp','LatentUpscale'),('pixel:lanczos','ImageScale'),('model:4x-UltraSharp.pth','ImageUpscaleWithModel')]:
            with self.subTest(upscaler=upscaler):
                job=self.run_job(hires_fix=True,hires_upscaler=upscaler,qwen_steps=8)
                self.assertEqual(job['status'],'completed',job['message'])
                self.assertEqual(job['outputs'][0]['width'],1024)
                self.assertEqual(self.graph['41']['inputs']['model'],['1',0])
                self.assertEqual(self.graph['41']['inputs']['steps'],8)
                self.assertIn(node,[n['class_type'] for n in self.graph.values()])
                self.assertNotIn('30',self.graph)

    def test_img2img_and_whole_inpaint_ignore_hires_and_keep_composite_geometry(self):
        base=self.s.asset(png(Image.new('RGB',(640,480),'red')))
        job=self.run_job(mode='polish',target=base['id'],denoise=.45)
        self.assertEqual(job['status'],'completed',job['message']);self.assertEqual(self.graph['6']['inputs']['denoise'],.45)
        job=self.run_job(mode='inpaint',target=base['id'],hires_fix=True,composite=True,feather=0,strokes=[{'width':48,'points':[[320,240]]}])
        self.assertEqual(job['status'],'completed',job['message'])
        self.assertFalse(job['params']['hires_fix']);self.assertNotIn('41',self.graph)
        self.assertEqual(self.graph['24']['class_type'],'SetLatentNoiseMask')
        with Image.open(self.s.file(job['outputs'][-1]['id'])) as im:
            self.assertEqual(im.size,(512,512));self.assertEqual(im.getpixel((0,0))[:3],(255,0,0));self.assertEqual(im.getpixel((256,256))[:3],(0,0,255))

    def test_legacy_hires_flags_cannot_enable_a_second_pass_in_edit_modes(self):
        for mode in ('polish','inpaint'):
            with self.subTest(mode=mode):
                p=self.params(mode=mode,hires_fix=True)
                self.assertFalse(p['hires_fix'])
                # Defend the graph builder too, including previously saved parameters.
                legacy={**p,'hires_fix':True,'hires_scale':4,'hires_upscaler':'model:missing'}
                graph=c.workflow(legacy,{**self.config,'hires_checkpoint':'missing'},42,['base.png','mask.png'] if mode=='inpaint' else ['base.png'],'old')
                self.assertNotIn('41',graph);self.assertNotIn('30',graph)
                self.assertEqual(s.sampling_size(legacy),(512,512))
                with self.client(self.config) as client:c.validate_models(client,{**self.config,'hires_checkpoint':'missing'},legacy)

    def test_only_masked_preserves_canvas_and_unpainted_pixels_at_edges(self):
        base=self.s.asset(png(Image.new('RGBA',(641,479),(255,0,0,180))))
        for center in ((320,240),(2,2),(639,477)):
            with self.subTest(center=center):
                job=self.run_job(mode='inpaint',target=base['id'],inpaint_area='masked',hires_fix=True,composite=False,feather=8,strokes=[{'width':40,'points':[center]}])
                self.assertEqual(job['status'],'completed',job['message'])
                self.assertEqual((job['outputs'][0]['width'],job['outputs'][0]['height']),(641,479))
                with Image.open(self.s.file(job['outputs'][0]['id'])) as im:
                    diff=ImageChops.difference(im.convert('RGBA'),Image.open(self.s.file(base['id'])).convert('RGBA'))
                    protected=ImageChops.invert(mask_image(im.size,job['params']['strokes']))
                    for band in diff.split():self.assertIsNone(ImageChops.multiply(band,protected).getbbox())
                    self.assertNotEqual(im.getpixel(center)[:3],(255,0,0))

    def test_dynamic_options_and_invalid_choices_fail_before_submission(self):
        with self.client(self.config) as client:
            available=c.inspect(client,'SDXL');self.assertIn('model:4x-UltraSharp.pth',available['upscalers']);self.assertNotIn('model:C',available['upscalers'])
            for p in (self.params(sampler='missing'),self.params(scheduler='missing'),self.params(hires_fix=True,hires_upscaler='model:missing')):
                with self.assertRaises(ValueError):c.validate_models(client,self.config,p)
            with self.assertRaises(ValueError):c.validate_models(client,{**self.config,'hires_checkpoint':'missing'},self.params(hires_fix=True))
        self.assertFalse(any(path=='/prompt' for _,path,_ in self.calls))

    def test_only_masked_feathering_is_applied_once_with_composite_checked(self):
        base=self.s.asset(png(Image.new('RGB',(512,512),'red')))
        args=dict(mode='inpaint',target=base['id'],inpaint_area='masked',feather=8,strokes=[{'width':40,'points':[[256,256]]}])
        without=self.run_job(**args,composite=False);with_composite=self.run_job(**args,composite=True)
        self.assertEqual(with_composite['status'],'completed')
        self.assertEqual(len(with_composite['outputs']),1)
        self.assertEqual(self.s.file(without['outputs'][0]['id']).read_bytes(),self.s.file(with_composite['outputs'][0]['id']).read_bytes())

    def test_validation_snapshot_and_no_resend_recovery(self):
        for overrides in ({'clip_skip':0},{'hires_scale':float('nan')},{'hires_scale':4,'hires_fix':True,'width':2048},{'qwen_cfg':31},{'denoise':-1},{'inpaint_area':'bad'},{'hires_fix':'yes'},{'hires_steps':151}):
            with self.subTest(overrides=overrides),self.assertRaises(ValueError):self.s.submit(self.params(**overrides))
        job=self.s.submit(self.params(hires_fix=True));c.configure(self.s,{'model':'SDXL','checkpoint':'OtherXL.safetensors'})
        self.assertEqual(job['local_machine']['checkpoint'],'XL.safetensors')
        self.ambiguous=True
        with patch.object(c,'client',self.client):
            Worker(self.s).run(job['id']);self.assertEqual(self.s.job(job['id'])['status'],'unknown')
            self.ambiguous=False;c.recover(self.s,Worker(self.s))
        self.assertEqual(self.s.job(job['id'])['status'],'completed')
        self.assertEqual(sum(path=='/prompt' for _,path,_ in self.calls),1)

if __name__=='__main__':unittest.main()
