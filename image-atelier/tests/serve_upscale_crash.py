"""Real Uvicorn owner for the Windows forced-exit regression; mock inference only."""
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import uvicorn
from server import create_app
from persistence import atomic_write

root=Path(sys.argv[1]);body=json.loads(Path(sys.argv[2]).read_text('utf-8'))
if len(sys.argv)>3 and sys.argv[3]=='pause-before-pid':
    import upscale_jobs
    original_launch=upscale_jobs.launch
    def launch(*args,**kwargs):
        child=original_launch(*args,**kwargs)
        atomic_write(root/'child-start.json',json.dumps({'pid':child.pid}).encode())
        time.sleep(60)  # fault-injection window, before job.process_id is saved
        return child
    upscale_jobs.launch=launch
app=create_app(root,gpu_runner=Path(__file__).with_name('fake_upscale_runner.py'))
original=app.router.lifespan_context

@asynccontextmanager
async def lifetime(app):
    async with original(app):
        job=app.state.gpu.submit(body)
        atomic_write(root/'owner-ready.json',json.dumps({'pid':os.getpid(),'job':job['id']}).encode())
        yield

app.router.lifespan_context=lifetime
uvicorn.run(app,host='127.0.0.1',port=0,log_level='critical')
