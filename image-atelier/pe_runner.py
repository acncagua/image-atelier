"""Offline PE-T2I worker. Outputs only validated final JSON, never an image."""
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from persistence import atomic_write,publish_new

RATIOS={'1:1','4:3','3:4','3:2','2:3','16:9','9:16'}
def parse_result(text):
    answer=text.rsplit('</think>',1)[-1].strip()
    if answer.startswith('```json') and answer.endswith('```'):answer=answer[7:-3].strip()
    elif answer.startswith('```') and answer.endswith('```'):answer=answer[3:-3].strip()
    result=json.loads(answer)
    if not isinstance(result,dict):raise ValueError('補強結果がJSONオブジェクトではありません。')
    prompt=result.get('rewritten_prompt') or result.get('rewrited_prompt');ratio=result.get('wh_ratio');follow=result.get('ratio_follow','')
    valid_ratio=(ratio in RATIOS and follow=='') if isinstance(ratio,str) else False
    valid_follow=isinstance(follow,str) and bool(re.fullmatch(r'<image(?:[1-9]|10)>',follow)) and ratio==''
    if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>32000 or not isinstance(ratio,str) or not (valid_ratio or valid_follow):
        raise ValueError('補強結果の形式・長さ・縦横比が不正です。元の指示を保持しています。')
    return {'rewritten_prompt':prompt.strip(),'wh_ratio':ratio,**({'ratio_follow':follow} if follow else {})}

def load_edit_image(path):
    from PIL import Image
    with Image.open(path) as source:image=source.convert('RGB')
    w,h=image.size
    if w*h>1024*1024:
        scale=(1024*1024/(w*h))**.5
        image=image.resize((max(1,int(w*scale)),max(1,int(h*scale))),Image.Resampling.LANCZOS)
    return image


def run(file):
    p=json.loads(file.read_text('utf-8'));folder=file.parent;started=time.monotonic()
    def report(**state):atomic_write(folder/'status.json',json.dumps(state,ensure_ascii=False).encode('utf-8'))
    try:
        import socket
        def offline(*a,**kw):raise RuntimeError('PE worker networking is disabled')
        socket.socket.connect=offline;socket.socket.connect_ex=offline
        report(state='loading')
        import torch
        from transformers import AutoModelForCausalLM,AutoTokenizer
        if not torch.cuda.is_available():raise RuntimeError('CUDAが利用できません。')
        free,total=torch.cuda.mem_get_info();budget=min(24*1024**3,free-2*1024**3)
        if budget<4*1024**3:raise RuntimeError('GPUの空き容量が不足しています。他のGPU処理の終了後に再試行してください。')
        root=Path(p['model']);system=(root/'system_prompt.txt').read_text('utf-8-sig').strip()
        if p.get('inputs'):
            from PIL import Image
            from transformers import AutoModelForImageTextToText,AutoProcessor
            processor=AutoProcessor.from_pretrained(str(root),local_files_only=True)
            model=AutoModelForImageTextToText.from_pretrained(str(root),dtype=torch.bfloat16,device_map='auto',max_memory={0:budget,'cpu':48*1024**3},local_files_only=True).eval()
            images=[]
            for path in p['inputs']:
                images.append(load_edit_image(path))
            messages=[{'role':'system','content':[{'type':'text','text':system}]},
                      {'role':'user','content':[{'type':'image','image':image} for image in images]+[{'type':'text','text':p['prompt']}]}]
            inputs=processor.apply_chat_template(messages,add_generation_prompt=True,tokenize=True,return_dict=True,return_tensors='pt',enable_thinking=True).to(model.device)
            if 'mm_token_type_ids' not in inputs and hasattr(processor,'create_mm_token_type_ids'):
                inputs['mm_token_type_ids']=processor.create_mm_token_type_ids(inputs['input_ids'])
            tokenizer=processor.tokenizer
        else:
            tokenizer=AutoTokenizer.from_pretrained(str(root),local_files_only=True)
            model=AutoModelForCausalLM.from_pretrained(str(root),dtype=torch.bfloat16,device_map='auto',max_memory={0:budget,'cpu':48*1024**3},local_files_only=True).eval()
            text=tokenizer.apply_chat_template([{'role':'system','content':system},{'role':'user','content':p['prompt']}],tokenize=False,add_generation_prompt=True,enable_thinking=True)
            inputs=tokenizer(text,return_tensors='pt').to(model.device)
        torch.manual_seed(p['seed']);torch.cuda.reset_peak_memory_stats();report(state='rewriting')
        with torch.inference_mode():
            out=model.generate(**inputs,max_new_tokens=p['max_new_tokens'],do_sample=True,temperature=1.0,top_p=.95,top_k=20)
        tokens=out[0,inputs['input_ids'].shape[1]:]
        result=parse_result(tokenizer.decode(tokens,skip_special_tokens=True))
        result.update(elapsed_seconds=round(time.monotonic()-started,3),generated_tokens=len(tokens),peak_cuda_allocated_bytes=torch.cuda.max_memory_allocated())
        publish_new(folder/'result.json',json.dumps(result,ensure_ascii=False).encode('utf-8'));report(state='completed')
        return 0
    except Exception as error:
        atomic_write(folder/'error.txt',traceback.format_exc().encode('utf-8'))
        report(state='failed',error_type=type(error).__name__,message=str(error) or repr(error));return 1

if __name__=='__main__':raise SystemExit(run(Path(sys.argv[1])))
