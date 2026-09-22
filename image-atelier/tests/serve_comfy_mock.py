"""Browser QA only: port 18792, isolated data, HTTP mock, no GPU or external API."""
import sys
import threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_comfy import Comfy
import comfy_backend
from server import create_app
import uvicorn

class Fake:
    handler=Comfy.handler
    client=Comfy.client
    calls=[]
    graph=None
    ident=None
    ambiguous=False
    cancel_upload=False

fake=Fake()
comfy_backend.client=fake.client
root=Path(__file__).resolve().parents[1]
app=create_app(root/'.tmp/comfy-browser',mock_gate=threading.Event(),port=18792,qwen_runner=root/'tests/fake_qwen_runner.py')
app.state.store.set_settings({'output':str(root/'.tmp/comfy-browser/exports'),'budget':0,'reservation':1,'live':False})
uvicorn.run(app,host='127.0.0.1',port=18792,access_log=False)
