"""Read-only Qwen checkpoint preflight. No model load or network access."""
import argparse
import json
import struct
from pathlib import Path


def inspect_checkpoint(root):
    root=Path(root).resolve();problems=[];weights=[]
    try:
        model=json.loads((root/'model_index.json').read_text('utf-8-sig'))
        if model.get('_class_name')!='QwenImage21Pipeline':problems.append('Unexpected pipeline class')
    except (OSError,ValueError) as error:
        return {'model':str(root),'ok':False,'problems':[str(error)]}
    for name in ('LICENSE','processor/tokenizer.json','processor/tokenizer_config.json','processor/preprocessor_config.json',
                 'scheduler/scheduler_config.json','text_encoder/config.json','transformer/config.json','vae/config.json'):
        file=root/name
        if not file.is_file():problems.append('Missing '+name)
        else:
            with file.open('rb') as stream:
                if stream.read(80).startswith(b'version https://git-lfs.github.com/spec/v1'):problems.append('Git LFS pointer: '+name)
    required=set()
    for component in ('text_encoder','transformer','vae'):
        folder=root/component;files=list(folder.glob('*.safetensors'))
        if not files:problems.append('No weights: '+component)
        for index in folder.glob('*.safetensors.index.json'):
            try:
                for name in json.loads(index.read_text('utf-8'))['weight_map'].values():
                    file=(folder/name).resolve()
                    if not file.is_relative_to(folder.resolve()):raise ValueError('Unsafe shard path')
                    required.add(file)
            except (OSError,ValueError,KeyError) as error:problems.append(f'Invalid index {index.name}: {error}')
        for file in files:
            try:
                size=file.stat().st_size
                with file.open('rb') as stream:
                    prefix=stream.read(80)
                    if prefix.startswith(b'version https://git-lfs.github.com/spec/v1'):raise ValueError('Git LFS pointer; weights not downloaded')
                    if len(prefix)<8:raise ValueError('Truncated header')
                    length=struct.unpack('<Q',prefix[:8])[0]
                    if length>min(100_000_000,size-8):raise ValueError('Invalid header length')
                    stream.seek(8);header=json.loads(stream.read(length))
                tensors=[v for k,v in header.items() if k!='__metadata__']
                if not tensors:raise ValueError('No tensors')
                offsets=sorted(v['data_offsets'] for v in tensors)
                end=0
                for start,stop in offsets:
                    if start!=end or stop<start:raise ValueError('Invalid tensor offsets')
                    end=stop
                if 8+length+end!=size:raise ValueError('Truncated or unexpected weight size')
                weights.append({'file':str(file.relative_to(root)),'bytes':size,'tensors':len(tensors)})
            except (OSError,ValueError,KeyError,TypeError) as error:problems.append(f'{file.name}: {error}')
    for file in required:
        if not file.is_file():problems.append('Missing shard: '+str(file.relative_to(root)))
    return {'model':str(root),'ok':not problems,'problems':problems,'weights':weights,'weight_bytes':sum(v['bytes'] for v in weights),
            'verification':'Headers, offsets and shard presence only; full-file hashes and GPU inference are not verified.'}


def environment():
    import importlib.metadata
    import inspect
    import torch
    from diffusers import QwenImage21Pipeline
    versions={name:importlib.metadata.version(name) for name in ('torch','torchvision','diffusers','transformers','accelerate','safetensors','pillow')}
    result={'versions':versions,'pipeline_signature':str(inspect.signature(QwenImage21Pipeline.__call__)),
            'cuda_available':torch.cuda.is_available(),'cuda':torch.version.cuda}
    if result['cuda_available']:
        free,total=torch.cuda.mem_get_info()
        result.update(gpu=torch.cuda.get_device_name(0),free_vram_bytes=free,total_vram_bytes=total,bf16_supported=torch.cuda.is_bf16_supported())
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--model',required=True);parser.add_argument('--environment',action='store_true');parser.add_argument('--report',type=Path)
    args=parser.parse_args();report=inspect_checkpoint(args.model)
    if args.environment:
        try:
            report['environment']=environment()
            if not report['environment'].get('cuda_available') or not report['environment'].get('bf16_supported'):
                report['problems'].append('CUDA BF16 is unavailable');report['ok']=False
        except Exception as error:report['environment_error']=f'{type(error).__name__}: {error}';report['ok']=False
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2));raise SystemExit(0 if report['ok'] else 1)
