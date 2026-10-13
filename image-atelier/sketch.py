"""Color guidance on an immutable SDXL source; masks stay separate from RGB paint."""
import math
import re
from PIL import Image,ImageChops,ImageOps
from imaging import mask_image

def enabled(p):return p.get('model')=='SDXL' and p.get('mode') in ('polish','inpaint') and p.get('sketch') is True
def valid_color(value):return isinstance(value,str) and re.fullmatch(r'#[0-9a-fA-F]{6}',value) is not None

def validate(p):
    if type(p.get('sketch',False)) is not bool:raise ValueError('SketchはON/OFFで指定してください。')
    if not enabled(p):return
    if not valid_color(p.get('sketch_color')):raise ValueError('描画色は#RRGGBBで指定してください。')
    opacity=p.get('sketch_opacity')
    if type(opacity) not in (int,float) or not math.isfinite(opacity) or not 0<=opacity<=1:raise ValueError('描画の不透明度は0〜1です。')
    if len(p['strokes'])>2000:raise ValueError('描画ストロークが多すぎます。')
    for s in p['strokes']:
        if not math.isfinite(s['width']) or s['width']<=0 or not s['points'] or len(s['points'])>10000 or any(not math.isfinite(v) for pt in s['points'] for v in pt):raise ValueError('描画の座標・ブラシ径が不正です。')
        if 'color' in s and not valid_color(s['color']):raise ValueError('描画ストロークの色が不正です。')
        if 'opacity' in s and (type(s['opacity']) not in (int,float) or not math.isfinite(s['opacity']) or not 0<=s['opacity']<=1):raise ValueError('描画ストロークの不透明度が不正です。')

def render(original,p):
    if not enabled(p):return original.copy()
    validate(p);layer=Image.new('RGBA',original.size,(0,0,0,0));w,h=original.size
    for s in p['strokes']:
        radius=max(1,min(s['width'],1000))/2
        xs,ys=zip(*s['points'])
        box=(max(0,math.floor(min(xs)-radius-1)),max(0,math.floor(min(ys)-radius-1)),min(w,math.ceil(max(xs)+radius+2)),min(h,math.ceil(max(ys)+radius+2)))
        if box[2]<=box[0] or box[3]<=box[1]:continue
        size=(box[2]-box[0],box[3]-box[1]);points=[[x-box[0],y-box[1]] for x,y in s['points']]
        coverage=mask_image(size,[{**s,'erase':False,'points':points}]);patch=layer.crop(box)
        if s.get('erase'):
            patch.putalpha(ImageChops.multiply(patch.getchannel('A'),ImageOps.invert(coverage)))
        else:
            color=s.get('color',p['sketch_color']);rgb=tuple(int(color[i:i+2],16) for i in (1,3,5))
            paint=Image.new('RGBA',size,rgb+(255,));opacity=s.get('opacity',p['sketch_opacity'])
            paint.putalpha(coverage.point(lambda v:round(v*opacity)))
            patch=Image.alpha_composite(patch,paint)
        layer.paste(patch,box)
    return Image.alpha_composite(original.convert('RGBA'),layer)
