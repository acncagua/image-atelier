"""Shared planning contract; independent of torch, CUDA and the model loader."""
import math
import json
from pathlib import Path
from PIL import Image

LIMITS=json.loads((Path(__file__).parent/'upscale_limits.json').read_text('utf-8'))

def rounded(value):return math.floor(value+.5)

def starts(length,tile,overlap):
    end=max(0,length-tile)
    return list(range(0,end,tile-overlap))+[end]

def plan(width,height,options):
    if type(width) is not int or type(height) is not int or min(width,height)<=0:
        raise ValueError('入力寸法が不正です。')
    if max(width,height)>LIMITS['max_input_edge'] or width*height>LIMITS['max_input_pixels']:
        raise ValueError('入力は各辺2048px以下・4,194,304画素以下です。通常リサイズで調整してください。')
    mode=options.get('mode','factor');policy=options.get('fit','pad')
    if policy not in ('pad','crop','stretch'):raise ValueError('縦横比の処理方法が不正です。')
    if mode=='factor':
        factor=options.get('factor',2)
        if type(factor) not in (float,int) or not math.isfinite(factor) or not 1<factor<=4:
            raise ValueError('倍率は1倍超〜4倍で指定してください。')
        target=(rounded(width*factor),rounded(height*factor))
    elif mode=='size':
        target=(options.get('width'),options.get('height'));factor=None
        if any(type(v) is not int or v<=0 for v in target):raise ValueError('解像度は正の整数で指定してください。')
        if target[0]<=width and target[1]<=height:raise ValueError('縮小のみの操作は通常リサイズを使ってください。')
        if target[0]>width*4 or target[1]>height*4:raise ValueError('各辺の解像度は元画像の4倍以下です。')
    else:raise ValueError('指定方式が不正です。')
    if max(target)>LIMITS['max_output_edge'] or target[0]*target[1]>LIMITS['max_output_pixels']:
        raise ValueError('出力上限を超えています。')
    tile=options.get('tile',192);overlap=options.get('overlap',8)
    if type(tile) is not int or not LIMITS['tile_min']<=tile<=LIMITS['tile_max'] or tile%LIMITS['tile_multiple']:
        raise ValueError('タイルは32〜512px、8の倍数で指定してください。')
    if type(overlap) is not int or not 0<=overlap<tile:raise ValueError('重なりは0以上・タイル未満の整数です。')
    count=len(starts(width,tile,overlap))*len(starts(height,tile,overlap))
    if count>LIMITS['max_tiles']:raise ValueError('タイル数が多すぎます。重なりを減らすかタイルを大きくしてください。')
    return {'mode':mode,'factor':factor,'width':target[0],'height':target[1],'fit':policy,
            'tile':tile,'overlap':overlap,'native_scale':4,'native_size':[width*4,height*4],
            'tiles':count,'cpu_memory_estimate_bytes':width*height*16*48+width*height*64}

def resize_alpha_aware(image,size):
    if image.size==size:return image.copy()
    if image.mode=='RGBA':
        return image.convert('RGBa').resize(size,Image.Resampling.LANCZOS).convert('RGBA')
    return image.resize(size,Image.Resampling.LANCZOS)

def finish(image,target,policy):
    """Extra odd pixel goes to right/bottom; no implicit stretch."""
    width,height=target
    if policy=='stretch' or image.width*height==image.height*width:
        return resize_alpha_aware(image,target),{'resized':list(target),'offset':[0,0],
            'resampler':'none' if image.size==target else 'Lanczos','alpha_resize':'premultiplied' if image.mode=='RGBA' else 'opaque'}
    if policy=='pad':
        ratio=min(width/image.width,height/image.height)
        size=(max(1,min(width,rounded(image.width*ratio))),max(1,min(height,rounded(image.height*ratio))))
        small=resize_alpha_aware(image,size).convert('RGBA')
        result=Image.new('RGBA',target,(0,0,0,0));offset=((width-size[0])//2,(height-size[1])//2)
        result.paste(small,offset)
    elif policy=='crop':
        ratio=max(width/image.width,height/image.height)
        size=(max(width,rounded(image.width*ratio)),max(height,rounded(image.height*ratio)))
        small=resize_alpha_aware(image,size);offset=((size[0]-width)//2,(size[1]-height)//2)
        result=small.crop((offset[0],offset[1],offset[0]+width,offset[1]+height))
    else:raise ValueError('縦横比の処理方法が不正です。')
    return result,{'resized':list(size),'offset':list(offset),'policy':policy,'resampler':'Lanczos','alpha_resize':'premultiplied' if image.mode=='RGBA' else 'opaque'}
