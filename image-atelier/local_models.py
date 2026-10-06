"""Model selection and legacy compatibility, shared with the browser registry."""
import json
import math
from pathlib import Path
import qwen_backend
import sdxl_backend

MODELS=json.loads(Path(__file__).with_name('local_models.json').read_text('utf-8'))
DEFAULT_MODEL=next(iter(MODELS))
# Retain persisted parameter names so old drafts/history remain replayable.
DEFAULTS={**qwen_backend.DEFAULTS,'denoise':0.65,'guidance':3.5}

def is_local(model):return model in MODELS

def defaults_for(model):
    spec=MODELS[model]
    return {**DEFAULTS,'qwen_steps':spec['steps'],'qwen_cfg':spec['cfg'],'guidance':spec.get('guidance',3.5),**(sdxl_backend.DEFAULTS if spec['family']=='sdxl' else {})}

def validate(p):
    spec=MODELS.get(p['model'])
    if not spec or p['provider'] not in ('comfyui','qwen') or (p['provider']=='qwen' and spec['family']!='qwen'):
        raise ValueError('選択したモデルはComfyUI接続で実行してください。')
    if p['mode'] not in ('generate','polish','inpaint'):raise ValueError('未対応の生成モードです。')
    if p['n']!=1 or p['format']!='png':raise ValueError('ローカル生成は1枚・PNGで実行してください。')
    if any(type(p[k]) is not int or not 128<=p[k]<=2048 or p[k]%32 for k in ('width','height')):raise ValueError('出力寸法は各辺128〜2048px、32の倍数で指定してください。')
    if type(p['qwen_timing']) is not bool:raise ValueError('時間計測の設定が不正です。')
    if not isinstance(p['qwen_negative'],str) or len(p['qwen_negative'])>32000:raise ValueError('ネガティブプロンプトは32,000文字以下です。')
    sdxl=spec['family']=='sdxl'
    for key,low,high in [('qwen_steps',1,150 if sdxl else 50),('qwen_seed',-1,4294967295),('qwen_tile',128,1024)]:
        if type(p[key]) is not int or not low<=p[key]<=high:raise ValueError('パラメータが範囲外です: '+key)
    if p['qwen_offload'] not in ('model','sequential'):raise ValueError('オフロード設定が不正です。')
    if p['qwen_tile']%32 or type(p['qwen_stride']) is not int or not 0<p['qwen_stride']<p['qwen_tile'] or p['qwen_stride']%32:raise ValueError('タイルとstrideの設定が不正です。')
    for key,low,high in [('denoise',0 if sdxl else 0.01,1),('guidance',0,100),('qwen_cfg',1,30 if sdxl else 5)]:
        if type(p[key]) not in (int,float) or not math.isfinite(p[key]) or not low<=p[key]<=high:raise ValueError(key+' が範囲外です。')
    if p.get('refs') and not spec['references']:raise ValueError('このモデルは参照資料に対応していません。')
    if p.get('pe_job_id') and not spec.get('prompt_enhancement'):raise ValueError('このモデルは指示補強に対応していません。')
    if sdxl:sdxl_backend.validate(p)

def configuration(store,model):
    if MODELS[model]['family']=='qwen' and getattr(store,'_legacy_qwen_test',False):return qwen_backend.configuration(store)
    import comfy_backend
    return comfy_backend.configuration(store,model)

def required_files(model):
    if MODELS[model]['family']=='sdxl':return ('checkpoint',)
    return ('diffusion','text_encoder','text_encoder_2','vae') if MODELS[model]['family']=='flux' else ('diffusion','text_encoder','vae')

def ready_configuration(store,p):
    machine=configuration(store,p['model'])
    if machine.get('backend')=='comfyui':
        if any(not machine.get(k) for k in required_files(p['model'])):raise ValueError('ComfyUIの生成モデル・テキストエンコーダー・VAEを選択してください。')
        if p.get('qwen_timing'):raise ValueError('ComfyUI版のステップ別時間計測は未対応です。時間計測を解除してください。')
    elif not Path(machine['python']).is_file() or not (Path(machine['model'])/'model_index.json').is_file():raise ValueError('ローカルPythonまたはモデルが未設定です。環境設定を確認してください。')
    return machine

def output_size(p):
    return sdxl_backend.output_size(p) if p.get('model')=='SDXL' else (p['width'],p['height'])

def validate_inputs(p,ids):
    spec=MODELS[p['model']]
    count=len(ids)+(1 if p['mode']=='inpaint' and spec['family']=='qwen' else 0)
    if count>spec['max_images']:raise ValueError(f"{p['model']}の画像入力は合計{spec['max_images']}枚までです。")
