"""Dedicated offline child process. JSON files only; no shell or web API."""
import json
import sys
import time
import hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from PIL import Image
from persistence import atomic_write,publish_new
from upscale_geometry import plan,starts,finish

class Cancelled(Exception):pass

def tiled_rgb(rgb,predict,window,tile,overlap,notify=lambda *a:None,cancel=lambda:False):
    import numpy as np
    height,width,_=rgb.shape;scale=4
    accum=np.zeros((height*scale,width*scale,3),dtype=np.float32)
    weights=np.zeros((height*scale,width*scale,1),dtype=np.float32)
    positions=[(y,x) for y in starts(height,tile,overlap) for x in starts(width,tile,overlap)]
    for index,(y,x) in enumerate(positions):
        if cancel():raise Cancelled()
        patch=rgb[y:min(y+tile,height),x:min(x+tile,width)]
        ph,pw=patch.shape[:2];dh=(-ph)%window;dw=(-pw)%window
        padded=np.pad(patch,((0,dh),(0,dw),(0,0)),mode='reflect' if min(ph,pw)>1 else 'edge')
        output=predict(padded)[:ph*scale,:pw*scale]
        if output.shape!=(ph*scale,pw*scale,3):raise ValueError('モデル出力の倍率・チャンネルが不正です。')
        if not np.isfinite(output).all():raise ValueError('モデル出力がNaN/Infです。')
        accum[y*scale:(y+ph)*scale,x*scale:(x+pw)*scale]+=output
        weights[y*scale:(y+ph)*scale,x*scale:(x+pw)*scale]+=1
        notify(index+1,len(positions))
    accum/=weights
    return np.rint(np.clip(accum,0,1)*255).astype(np.uint8)

def prepare_rgb(image):
    import numpy as np
    alpha=np.asarray(image.convert('RGBA').getchannel('A'),dtype=np.uint8)
    rgb=np.asarray(image.convert('RGB'),dtype=np.uint8).copy()
    if np.any(alpha==0) and np.any(alpha>0):
        from scipy.ndimage import distance_transform_edt
        indices=distance_transform_edt(alpha==0,return_distances=False,return_indices=True)
        transparent=alpha==0;rgb[transparent]=rgb[indices[0][transparent],indices[1][transparent]]
    elif np.all(alpha==0):rgb[:]=0
    return rgb.astype(np.float32)/255,alpha

def execute(request,report):
    started=time.time();cancel_path=Path(request['cancel'])
    def check():
        if cancel_path.exists():raise Cancelled()
    check()
    report(state='loading',message='推論環境・モデルを読み込み中')
    import torch
    import numpy as np
    import importlib.metadata
    base_info={'torch':torch.__version__,'cuda':torch.version.cuda,'cuda_available':torch.cuda.is_available()}
    report(state='loading',environment=base_info)
    from spandrel import ModelLoader,ImageModelDescriptor
    if not base_info['cuda_available']:raise RuntimeError('CUDAが利用できません。CPUへは切り替えません。')
    torch.set_num_threads(4)
    torch.backends.cuda.matmul.allow_tf32=False
    torch.backends.cudnn.allow_tf32=False
    model_path=Path(request['model'])
    if not model_path.is_file():raise ValueError('モデルファイルが見つかりません。')
    with model_path.open('rb') as file:digest=hashlib.file_digest(file,'sha256').hexdigest()
    if request.get('model_sha256') and digest!=request['model_sha256']:raise ValueError('モデルが登録時から変更されています。')
    descriptor=ModelLoader().load_from_file(model_path)
    if not isinstance(descriptor,ImageModelDescriptor) or descriptor.architecture.id!='SwinIR' or descriptor.scale!=4 or descriptor.input_channels!=3 or descriptor.output_channels!=3:
        raise ValueError('RGB入出力・ネイティブ4倍のSwinIRモデルではありません。')
    window=int(getattr(descriptor.model,'window_size',8))
    info={**base_info,'gpu':torch.cuda.get_device_name(0),
          'spandrel':importlib.metadata.version('spandrel'),'pillow':importlib.metadata.version('pillow'),
          'model_name':model_path.name,'model_sha256':digest,'architecture':descriptor.architecture.id,
          'native_scale':descriptor.scale,'window':window,'precision':'FP32','autocast':False,'compile':False}
    info.update(input_channels=descriptor.input_channels,output_channels=descriptor.output_channels,
                parameters=sum(p.numel() for p in descriptor.model.parameters()))
    check()
    descriptor.model.float().eval().to('cuda')
    torch.cuda.reset_peak_memory_stats()
    if request.get('diagnose'):
        with torch.inference_mode(),torch.autocast('cuda',enabled=False):
            sample=descriptor(torch.zeros((1,3,window,window),device='cuda',dtype=torch.float32))
        if tuple(sample.shape)!=(1,3,window*4,window*4):raise ValueError('実モデルの出力形状が不正です。')
        info['peak_vram_bytes']=torch.cuda.max_memory_allocated();info['seconds']=time.time()-started
        return {'state':'completed','environment':info}
    with Image.open(request['input']) as original:
        original.load();image=original.copy();icc=original.info.get('icc_profile')
    params=plan(image.width,image.height,request['options'])
    if params['tile']%window:raise ValueError('タイルサイズがモデルのwindowの倍数ではありません。')
    rgb,alpha=prepare_rgb(image)
    def predict(patch):
        tensor=torch.from_numpy(np.ascontiguousarray(patch.transpose(2,0,1))).unsqueeze(0).to('cuda',dtype=torch.float32)
        with torch.inference_mode(),torch.autocast('cuda',enabled=False):
            output=descriptor(tensor).float()
        result=output[0].permute(1,2,0).cpu().numpy().copy()
        del tensor,output
        return result
    output=tiled_rgb(rgb,predict,window,params['tile'],params['overlap'],
        notify=lambda done,total:report(state='upscaling',done=done,total=total,environment=info),cancel=cancel_path.exists)
    check();report(state='adjusting',environment=info)
    native=Image.fromarray(output,'RGB')
    if np.any(alpha<255):
        native=native.convert('RGBA');native.putalpha(Image.fromarray(alpha,'L').resize(native.size,Image.Resampling.LANCZOS))
    result,geometry=finish(native,(params['width'],params['height']),params['fit'])
    check();report(state='saving',environment=info)
    import io
    buffer=io.BytesIO();kwargs={'icc_profile':icc} if icc else {}
    result.save(buffer,format='PNG',**kwargs)
    check();publish_new(Path(request['output']),buffer.getvalue())
    torch.cuda.synchronize();info['peak_vram_bytes']=torch.cuda.max_memory_allocated();info['seconds']=time.time()-started
    del descriptor,output,native,result,rgb;torch.cuda.empty_cache()
    return {'state':'completed','environment':info,'geometry':geometry,'actual_size':[params['width'],params['height']]}

def main():
    # No Python networking in inference, including any accidental loader fallback.
    import socket
    def no_network(*args,**kwargs):raise RuntimeError('推論中の外部通信は禁止されています。')
    socket.socket.connect=no_network
    socket.socket.connect_ex=no_network
    request=json.loads(Path(sys.argv[1]).read_text('utf-8'))
    status=Path(request['status'])
    latest={}
    def report(**data):
        if data.get('state')!=latest.get('state') and 'message' not in data:latest.pop('message',None)
        latest.update(data)
        atomic_write(status,json.dumps(latest,ensure_ascii=False).encode('utf-8'))
    try:report(**execute(request,report))
    except Cancelled:report(state='cancelled',message='取消しました。')
    except Exception as error:
        text=str(error)
        if 'out of memory' in text.lower():message='GPUメモリ不足です。タイルサイズを小さくしてください。自動再試行はしません。'
        elif isinstance(error,(ValueError,RuntimeError)) and ('CUDA' in text or 'モデル' in text or 'タイル' in text):message=text[:300]
        else:message='ローカル推論に失敗しました。環境診断・モデル・空き容量を確認してください。'
        report(state='failed',error_type=type(error).__name__,message=message)
        return 1
    return 0

if __name__=='__main__':sys.exit(main())
