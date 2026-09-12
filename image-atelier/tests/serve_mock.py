"""Manual/browser QA server. Separate data + port; no OpenAI traffic allowed."""
import base64
import json
import sys
import threading
import uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import create_app
from imaging import png
from PIL import Image,ImageDraw
import uvicorn

folder=Path(__file__).resolve().parents[1]/'.tmp'/'fixes-browser'
gate=threading.Event()
app=create_app(folder,True,mock_gate=gate,port=18792,gpu_runner=Path(__file__).parent/'fake_upscale_runner.py')
fake_model=folder/'mock-model.pth'
if not fake_model.exists():fake_model.write_text('normal','utf-8')
app.state.gpu.configure({'python':sys.executable,'model':str(fake_model.resolve())})
s=app.state.store
s.set_settings({'output':str(folder/'exports'),'budget':0,'reservation':1,'live':False})
if not s.jobs():
    im=Image.new('RGB',(1001,777),'#baceda');ImageDraw.Draw(im).rectangle((200,200,500,400),fill='#078b95')
    (folder/'input-1001x777.png').write_bytes(png(im))
    asset=s.asset(png(im),'試験入力1001×777')
    job=s.submit({'id':str(uuid.uuid4()),'provider':'mock','mode':'polish','target':asset['id'],'refs':[],'prompt':'保存応答の回収試験','change':'回収試験','keep':'配置を維持','model':'gpt-image-2.5-sunburst','quality':'medium','width':1024,'height':1024})
    job['status']='unknown';s.save_job(job)
    s.write_response(job['id'],json.dumps({'images':[base64.b64encode(png(im)).decode()]}).encode())
uvicorn.run(app,host='127.0.0.1',port=18792,access_log=False)
