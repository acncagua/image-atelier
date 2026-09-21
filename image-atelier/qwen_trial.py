"""Isolated Qwen trial harness; preparing a request never starts inference.

Pass --run explicitly to load the local model. Not connected to Atelier jobs.
"""
import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from persistence import atomic_write,publish_new
from qwen_probe import inspect_checkpoint

ROOT=Path(__file__).resolve().parent


def execute(request_path):
    request=json.loads(request_path.read_text('utf-8'));directory=request_path.parent
    def report(**values):atomic_write(directory/'status.json',json.dumps(values,ensure_ascii=False).encode('utf-8'))
    started=time.monotonic()
    try:
        # All model components must already be local. No implicit download or API.
        import socket
        def offline(*args,**kwargs):raise RuntimeError('Network is disabled in the Qwen trial worker.')
        socket.socket.connect=offline;socket.socket.connect_ex=offline
        import torch
        from PIL import Image
        from diffusers import QwenImage21Pipeline
        from qwen_probe import environment
        if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():raise RuntimeError('CUDA BF16 is required.')
        report(state='loading')
        pipe=QwenImage21Pipeline.from_pretrained(request['model'],torch_dtype=torch.bfloat16,local_files_only=True)
        if request['offload']=='sequential':pipe.enable_sequential_cpu_offload()
        else:pipe.enable_model_cpu_offload()
        torch.cuda.reset_peak_memory_stats()
        def progress(pipeline,step,timestep,kwargs):
            report(state='generating',step=step+1,total=request['steps'])
            return kwargs
        options={'prompt':request['prompt'],'width':request['width'],'height':request['height'],
                 'num_inference_steps':request['steps'],'generator':torch.Generator('cuda').manual_seed(request['seed']),
                 'num_images_per_prompt':1,'true_cfg_scale':1.0,'callback_on_step_end':progress}
        if request.get('input'):
            with Image.open(request['input']) as image:options['image']=image.convert('RGBA')
        result=pipe(**options).images[0]
        report(state='saving')
        import io
        buffer=io.BytesIO();result.save(buffer,format='PNG');publish_new(Path(request['output']),buffer.getvalue())
        report(state='completed',output=request['output'],size=list(result.size),mode=result.mode,
               elapsed_seconds=round(time.monotonic()-started,3),peak_cuda_allocated_bytes=torch.cuda.max_memory_allocated(),
               environment=environment())
        return 0
    except Exception as error:
        report(state='failed',error_type=type(error).__name__,message=str(error),elapsed_seconds=round(time.monotonic()-started,3))
        return 1


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--model',type=Path);parser.add_argument('--input',type=Path)
    parser.add_argument('--prompt-file',type=Path);parser.add_argument('--width',type=int,default=512);parser.add_argument('--height',type=int,default=512)
    parser.add_argument('--steps',type=int,default=8);parser.add_argument('--seed',type=int,default=42)
    parser.add_argument('--offload',choices=['model','sequential'],default='model')
    parser.add_argument('--timeout',type=int,default=1800);parser.add_argument('--run',action='store_true')
    parser.add_argument('--worker',type=Path,help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.worker:return execute(args.worker)
    if not args.model:parser.error('--model is required')
    if min(args.width,args.height)<=0 or max(args.width,args.height)>2048 or args.width%32 or args.height%32:parser.error('Trial dimensions must be multiples of 32, up to 2048 per edge.')
    if not 1<=args.steps<=50 or args.timeout<=0:parser.error('Use 1..50 steps and a positive timeout.')
    checkpoint=inspect_checkpoint(args.model)
    if not checkpoint['ok']:print(json.dumps(checkpoint,ensure_ascii=False));return 1
    if args.input and not args.input.is_file():parser.error('Input image not found')
    prompt=args.prompt_file.read_text('utf-8-sig') if args.prompt_file else ('Change the background to a plain light gray studio wall. Keep the subject unchanged.' if args.input else 'A ceramic teapot on a wooden table, soft daylight, no text.')
    if not prompt.strip():parser.error('Prompt is empty')
    ident=uuid.uuid4().hex;directory=ROOT/'.tmp'/'qwen'/'trials'/ident;directory.mkdir(parents=True)
    output=ROOT/'qwen-test-output';output.mkdir(exist_ok=True)
    request={'model':str(args.model.resolve()),'input':str(args.input.resolve()) if args.input else None,'prompt':prompt,
             'width':args.width,'height':args.height,'steps':args.steps,'seed':args.seed,'offload':args.offload,'output':str(output/(ident+'.png'))}
    file=directory/'request.json';atomic_write(file,json.dumps(request,ensure_ascii=False).encode('utf-8'))
    print('Prepared request:',file,flush=True)
    if not args.run:print('No inference performed. Add --run to execute a new trial.');return 0
    from managed_child import launch
    env={k:v for k,v in os.environ.items() if not any(x in k.upper() for x in ('API_KEY','TOKEN','SECRET'))}
    env.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',PYTHONUTF8='1')
    child=launch([sys.executable,'-I',str(Path(__file__).resolve()),'--worker',str(file)],directory,ROOT,env)
    last=None;deadline=time.monotonic()+args.timeout
    try:
        while child.poll() is None:
            if time.monotonic()>deadline:raise TimeoutError('Qwen trial timed out')
            try:
                state=json.loads((directory/'status.json').read_text('utf-8'));key=(state.get('state'),state.get('step'))
                if key!=last:print(key,flush=True);last=key
            except (OSError,ValueError):pass
            time.sleep(.5)
        print('Worker exit:',child.returncode,'Report:',directory/'status.json')
        return child.returncode
    except (KeyboardInterrupt,TimeoutError) as error:
        child.kill();child.wait(timeout=10)
        atomic_write(directory/'supervisor.json',json.dumps({'state':'cancelled' if isinstance(error,KeyboardInterrupt) else 'timeout'}).encode())
        return 130
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=10)
        if hasattr(child,'close'):child.close()


if __name__=='__main__':raise SystemExit(main())
