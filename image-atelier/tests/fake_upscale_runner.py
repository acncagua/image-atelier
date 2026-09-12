"""Structured child-process fixture; NEVER a fallback for real SwinIR."""
import sys,json,time,os
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from persistence import atomic_write,publish_new
from upscale_geometry import finish,plan

request=json.loads(Path(sys.argv[1]).read_text('utf-8'))
status=Path(request['status']);cancel=Path(request['cancel']);mode=Path(request['model']).read_text('utf-8')
def report(**value):atomic_write(status,json.dumps(value).encode())
if mode=='crash':os._exit(17)
if mode in ('oom','no-cuda','bad-model'):
    report(state='failed',error_type=mode,message='模擬エラー: '+mode);sys.exit(1)
report(state='upscaling',done=0,total=1)
while mode=='wait' and not (Path(request['model']).parent/'release').exists():
    if cancel.exists():report(state='cancelled');sys.exit(0)
    time.sleep(.03)
if cancel.exists():report(state='cancelled');sys.exit(0)
environment={'mock':True,'gpu':'MOCK (no GPU inference)','native_scale':4,'precision':'FP32','torch':'not used'}
if request.get('diagnose'):report(state='completed',environment=environment);sys.exit(0)
with Image.open(request['input']) as im:
    p=plan(im.width,im.height,request['options']);native=im.resize((im.width*4,im.height*4))
    result,geometry=finish(native,(p['width'],p['height']),p['fit'])
import io
buffer=io.BytesIO();result.save(buffer,format='PNG');publish_new(Path(request['output']),buffer.getvalue())
report(state='completed',environment=environment,geometry=geometry,actual_size=list(result.size))
