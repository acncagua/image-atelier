"""Isolated Qwen trial harness; preparing a request never starts inference.

Pass --run explicitly to load the local model. Not connected to Atelier jobs.
"""
import argparse
import json
import os
import sys
import time
import uuid
import traceback
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from persistence import atomic_write,publish_new
from qwen_probe import inspect_checkpoint
from qwen_diagnostics import memory_snapshot

ROOT=Path(__file__).resolve().parent


def execute(request_path, cache=None):
    request=json.loads(request_path.read_text('utf-8'));directory=request_path.parent
    from qwen_timing import Timing
    timing=Timing() if request.get('timing') else None
    timing_hook=None
    phase='initializing'
    def report(**values):
        nonlocal phase
        phase=values.get('state',phase)
        if timing:
            timing.enter(phase);values['timing']=timing.snapshot()
        if request.get('diagnose'):
            values['memory']=memory_snapshot()
            with (directory/'phases.jsonl').open('a',encoding='utf-8') as log:log.write(json.dumps(values,ensure_ascii=False)+'\n')
        atomic_write(directory/'status.json',json.dumps(values,ensure_ascii=False).encode('utf-8'))
    started=time.monotonic()
    try:
        report(state='initializing')
        # All model components must already be local. No implicit download or API.
        import socket
        def offline(*args,**kwargs):raise RuntimeError('Network is disabled in the Qwen trial worker.')
        socket.socket.connect=offline;socket.socket.connect_ex=offline
        import torch
        from PIL import Image
        from diffusers import QwenImage21Pipeline
        from qwen_probe import environment
        if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():raise RuntimeError('CUDA BF16 is required.')
        key=tuple(request.get(k) for k in ('model','offload','vae_tiling','vae_tile_size','vae_tile_stride'))
        reused=cache is not None and cache.get('key')==key
        if reused:
            pipe=cache['pipe'];report(state='reusing_model')
        else:
            report(state='loading')
            pipe=QwenImage21Pipeline.from_pretrained(request['model'],torch_dtype=torch.bfloat16,local_files_only=True)
            if request.get('vae_tiling'):
                pipe.vae.enable_tiling(tile_sample_min_height=request.get('vae_tile_size',256),tile_sample_min_width=request.get('vae_tile_size',256),tile_sample_stride_height=request.get('vae_tile_stride',192),tile_sample_stride_width=request.get('vae_tile_stride',192))
            report(state='configuring_offload')
            if request['offload']=='sequential':pipe.enable_sequential_cpu_offload()
            else:pipe.enable_model_cpu_offload()
            if cache is not None:cache.update(key=key,pipe=pipe)
        original_decode=pipe.vae.decode
        def decode(*args,**kwargs):
            if timing:torch.cuda.synchronize()
            report(state='decoding',vae_tiling=request.get('vae_tiling',False))
            return original_decode(*args,**kwargs)
        pipe.vae.decode=decode
        torch.cuda.reset_peak_memory_stats()
        def progress(pipeline,step,timestep,kwargs):
            if timing:
                torch.cuda.synchronize();timing.step(step)
            report(state='generating',step=step+1,total=request['steps'])
            return kwargs
        options={'prompt':request['prompt'],'width':request['width'],'height':request['height'],
                 'num_inference_steps':request['steps'],'generator':torch.Generator('cuda').manual_seed(request['seed']),
                 'num_images_per_prompt':1,'true_cfg_scale':request.get('true_cfg_scale',1.0),'negative_prompt':request.get('negative_prompt',''),'callback_on_step_end':progress}
        if request.get('input'):
            report(state='reading_input')
            with Image.open(request['input']) as image:options['image']=image.convert('RGBA')
        if request.get('inputs'):
            inputs=[]
            for file in request['inputs']:
                with Image.open(file) as image:inputs.append(image.convert('RGBA'))
            options['image']=inputs[0] if len(inputs)==1 else inputs
        if timing:
            def first_forward(module,args):
                if timing.step_mark is None:
                    torch.cuda.synchronize();timing.first_forward()
            timing_hook=pipe.transformer.register_forward_pre_hook(first_forward)
            torch.cuda.synchronize()
        report(state='inference_start')
        result=pipe(**options).images[0]
        if timing:torch.cuda.synchronize()
        report(state='saving')
        import io
        buffer=io.BytesIO();result.save(buffer,format='PNG');publish_new(Path(request['output']),buffer.getvalue())
        report(state='completed',model_reused=reused,output=request['output'],size=list(result.size),mode=result.mode,
               elapsed_seconds=round(time.monotonic()-started,3),peak_cuda_allocated_bytes=torch.cuda.max_memory_allocated(),
               environment=environment())
        return 0
    except Exception as error:
        detail=traceback.format_exc()
        atomic_write(directory/'error.txt',detail.encode('utf-8'))
        report(state='failed',failed_at=phase,error_type=type(error).__name__,message=str(error) or repr(error),traceback=detail,elapsed_seconds=round(time.monotonic()-started,3))
        return 1
    finally:
        if timing_hook is not None:timing_hook.remove()
        if 'original_decode' in locals():pipe.vae.decode=original_decode


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--model',type=Path);parser.add_argument('--input',type=Path)
    parser.add_argument('--prompt-file',type=Path);parser.add_argument('--width',type=int,default=512);parser.add_argument('--height',type=int,default=512)
    parser.add_argument('--steps',type=int,default=8);parser.add_argument('--seed',type=int,default=42)
    parser.add_argument('--offload',choices=['model','sequential'],default='model')
    parser.add_argument('--timeout',type=int,default=1800);parser.add_argument('--run',action='store_true')
    parser.add_argument('--vae-tiling',action='store_true',help='Use official tiled VAE encoding/decoding to reduce memory')
    parser.add_argument('--vae-tile-size',type=int,default=256)
    parser.add_argument('--vae-tile-stride',type=int,default=192)
    parser.add_argument('--diagnose',action='store_true',help='Record memory/commit samples and detailed failure stage')
    parser.add_argument('--worker',type=Path,help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.worker:return execute(args.worker)
    if not args.model:parser.error('--model is required')
    if min(args.width,args.height)<=0 or max(args.width,args.height)>2048 or args.width%32 or args.height%32:parser.error('Trial dimensions must be multiples of 32, up to 2048 per edge.')
    if not 1<=args.steps<=50 or args.timeout<=0:parser.error('Use 1..50 steps and a positive timeout.')
    if args.vae_tile_size<128 or args.vae_tile_size>1024 or args.vae_tile_size%32 or args.vae_tile_stride<=0 or args.vae_tile_stride>=args.vae_tile_size or args.vae_tile_stride%32:parser.error('VAE tile: 128..1024, stride smaller than tile; both multiples of 32.')
    checkpoint=inspect_checkpoint(args.model)
    if not checkpoint['ok']:print(json.dumps(checkpoint,ensure_ascii=False));return 1
    if args.input and not args.input.is_file():parser.error('Input image not found')
    prompt=args.prompt_file.read_text('utf-8-sig') if args.prompt_file else ('Change the background to a plain light gray studio wall. Keep the subject unchanged.' if args.input else 'A ceramic teapot on a wooden table, soft daylight, no text.')
    if not prompt.strip():parser.error('Prompt is empty')
    ident=uuid.uuid4().hex;directory=ROOT/'.tmp'/'qwen'/'trials'/ident;directory.mkdir(parents=True)
    output=ROOT/'qwen-test-output';output.mkdir(exist_ok=True)
    request={'model':str(args.model.resolve()),'input':str(args.input.resolve()) if args.input else None,'prompt':prompt,
             'width':args.width,'height':args.height,'steps':args.steps,'seed':args.seed,'offload':args.offload,'diagnose':args.diagnose,'vae_tiling':args.vae_tiling,'vae_tile_size':args.vae_tile_size,'vae_tile_stride':args.vae_tile_stride,'output':str(output/(ident+'.png'))}
    file=directory/'request.json';atomic_write(file,json.dumps(request,ensure_ascii=False).encode('utf-8'))
    print('Prepared request:',file,flush=True)
    print('Trial directory:',directory,flush=True)
    if not args.run:print('No inference performed. Add --run to execute a new trial.');return 0
    from managed_child import launch
    env={k:v for k,v in os.environ.items() if not any(x in k.upper() for x in ('API_KEY','TOKEN','SECRET'))}
    env.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',PYTHONUTF8='1')
    child=launch([sys.executable,'-I',str(Path(__file__).resolve()),'--worker',str(file)],directory,ROOT,env)
    last=None;deadline=time.monotonic()+args.timeout;last_sample=0
    try:
        while child.poll() is None:
            if time.monotonic()>deadline:raise TimeoutError('Qwen trial timed out')
            if args.diagnose and time.monotonic()-last_sample>=1:
                with (directory/'memory.jsonl').open('a',encoding='utf-8') as log:log.write(json.dumps(memory_snapshot(child.pid))+'\n')
                last_sample=time.monotonic()
            try:
                state=json.loads((directory/'status.json').read_text('utf-8'));key=(state.get('state'),state.get('step'))
                if key!=last:print(key,flush=True);last=key
            except (OSError,ValueError):pass
            time.sleep(.5)
        if child.returncode!=0:
            try:previous=json.loads((directory/'status.json').read_text('utf-8'))
            except (OSError,ValueError):previous={}
            if previous.get('state') not in ('failed','completed'):
                atomic_write(directory/'status.json',json.dumps({'state':'interrupted','last_state':previous.get('state'),'worker_exit':child.returncode,'message':'Worker exited before completion; no automatic retry.'}).encode())
        print('Worker exit:',child.returncode,'Report:',directory/'status.json')
        if (directory/'error.txt').exists():print((directory/'error.txt').read_text('utf-8'),flush=True)
        return child.returncode
    except (KeyboardInterrupt,TimeoutError) as error:
        child.kill();child.wait(timeout=10)
        atomic_write(directory/'supervisor.json',json.dumps({'state':'cancelled' if isinstance(error,KeyboardInterrupt) else 'timeout'}).encode())
        return 130
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=10)
        if hasattr(child,'close'):child.close()


if __name__=='__main__':raise SystemExit(main())
