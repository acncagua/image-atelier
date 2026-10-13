import copy,io,json,unittest
from unittest.mock import patch
import httpx
from PIL import Image,ImageChops
from core import Store,Worker,canonical_input,fingerprint
from imaging import png,normalize,mask_image
import sketch,sdxl_backend as s,comfy_backend as c
import test_sdxl as fixtures

class Painting(unittest.TestCase):
    def params(self,**extra):return {'model':'SDXL','mode':'polish','sketch':True,'sketch_color':'#ff0000','sketch_opacity':1,'strokes':[],**extra}
    def test_color_opacity_and_eraser_leave_the_source_unchanged(self):
        source=Image.new('RGBA',(128,128),'white');before=source.tobytes()
        strokes=[{'width':40,'points':[[30,30]],'color':'#ff0000','opacity':1},{'width':24,'points':[[80,30],[80,30]],'color':'#0000ff','opacity':.5},{'width':10,'points':[[30,30]],'erase':True}]
        result=sketch.render(source,self.params(strokes=strokes))
        self.assertEqual(source.tobytes(),before);self.assertEqual(result.getpixel((30,30)),(255,255,255,255))
        self.assertEqual(result.getpixel((44,30))[:3],(255,0,0));self.assertEqual(result.getpixel((80,30))[:3],(127,127,255))
        self.assertEqual(result.getpixel((120,120)),source.getpixel((120,120)))
        mask=mask_image(source.size,strokes);self.assertEqual(mask.getpixel((30,30)),0);self.assertEqual(mask.getpixel((80,30)),255)
    def test_existing_mask_strokes_use_the_selected_fallback_color(self):
        source=Image.new('RGBA',(64,64),'white');p=self.params(sketch_color='#00ff00',strokes=[{'width':20,'points':[[32,32]]}])
        self.assertEqual(sketch.render(source,p).getpixel((32,32))[:3],(0,255,0))
        for extra in ({'sketch':False},{'mode':'generate'},{'model':'Qwen-Image-2.1'}):self.assertEqual(sketch.render(source,{**p,**extra}).tobytes(),source.tobytes())
    def test_invalid_paint_is_rejected(self):
        for extra in ({'sketch':'yes'},{'sketch_color':'red'},{'sketch_opacity':float('nan')},{'sketch_opacity':2},{'strokes':[{'width':20,'points':[[1,1]],'color':'invalid'}]},{'strokes':[{'width':20,'points':[[1,1]],'opacity':-1}]}):
            with self.subTest(extra=extra),self.assertRaises(ValueError):sketch.validate(self.params(**extra))

class SDXLSketch(unittest.TestCase):
    setUp=fixtures.SDXL.setUp;tearDown=fixtures.SDXL.tearDown
    setUpBase=fixtures.SDXL.setUpBase;client=fixtures.SDXL.client;handler=fixtures.SDXL.handler
    params=fixtures.SDXL.params;run_job=fixtures.SDXL.run_job
    def test_colored_inputs_and_masks_use_standard_nodes_and_restore_original_pixels(self):
        source=self.s.asset(png(Image.new('RGBA',(512,512),'white')));original=self.s.file(source['id']).read_bytes()
        strokes=[{'width':96,'points':[[256,256]],'color':'#ff0000','opacity':1}]
        for mode,area in (('polish','whole'),('inpaint','whole'),('inpaint','masked')):
            with self.subTest(mode=mode,area=area):
                p=self.params(mode=mode,target=source['id'],sketch=True,strokes=strokes,inpaint_area=area,composite=True,feather=0)
                encoded=s.prepare_inputs(self.s,p);image=normalize(encoded[0]);self.assertEqual(image.getpixel((256,256))[:3],(255,0,0))
                if mode=='inpaint':self.assertEqual(normalize(encoded[1]).getpixel((256,256))[:3],(255,255,255))
                job=self.run_job(mode=mode,target=source['id'],sketch=True,strokes=strokes,inpaint_area=area,composite=True,feather=0)
                self.assertEqual(job['status'],'completed',job['message']);self.assertEqual(job['params']['strokes'][0]['color'],'#ff0000')
                self.assertIn('VAEEncode',[n['class_type'] for n in self.graph.values()]);self.assertNotIn('VAEEncodeForInpaint',[n['class_type'] for n in self.graph.values()])
                self.assertNotIn('41',self.graph)
                if mode=='inpaint':
                    result=normalize(self.s.file(job['outputs'][-1]['id']).read_bytes());self.assertEqual(result.getpixel((0,0))[:3],(255,255,255))
                self.assertEqual(self.s.file(source['id']).read_bytes(),original)
    def test_color_and_opacity_are_frozen_with_the_job(self):
        source=self.s.asset(png(Image.new('RGBA',(512,512),'white')))
        p=self.params(mode='polish',target=source['id'],sketch=True,strokes=[{'width':20,'points':[[50,50]],'color':'#00ff00','opacity':.5}])
        job=self.s.submit(p);p['strokes'][0]['color']='#0000ff';p['sketch_opacity']=.1
        stored=self.s.job(job['id'])['params'];self.assertEqual(stored['strokes'][0]['color'],'#00ff00');self.assertEqual(stored['sketch_opacity'],1)
        self.assertNotEqual(fingerprint(stored),fingerprint(p))
        self.assertFalse(canonical_input(self.params(mode='generate',sketch=True))['sketch'])
