from billing import usage_summary
import qwen_backend
import copy
import base64
import hashlib
import io
import json
import math
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from datetime import datetime, timezone
from PIL import Image, ImageDraw, ImageOps
import httpx
from local_config import api_key
from persistence import atomic_write, publish_new, migrate, now, safe_id
from imaging import normalize, png, mask_image, api_mask, composite

ROOT = Path(__file__).resolve().parent
CAP = json.loads((ROOT / 'capabilities.json').read_text('utf-8'))
ROLES = {'face':'顔立ちのみ。肌の塗り・光は編集対象に合わせる', 'body':'体型・髪型・全体の特徴', 'style':'色・肌・陰影・線・塗りの質感', 'outfit':'衣装・小物の形と配色'}

def usage_cost(usage):
    """Only calculate when the returned token breakdown is unambiguous."""
    try:
        details=usage['input_tokens_details']
        text_tokens=details['text_tokens']; image_tokens=details['image_tokens']; output=usage['output_tokens']
        if usage.get('cached_tokens',0) or details.get('cached_tokens',0): return None
        if any(type(v) is not int or v<0 for v in (text_tokens,image_tokens,output)): return None
        if text_tokens+image_tokens != usage['input_tokens']: return None
        price=CAP['price_per_million']
        return (text_tokens*price['text_input']+image_tokens*price['image_input']+output*price['image_output'])/1_000_000
    except (TypeError,KeyError): return None

def validate_size(w, h):
    d = CAP['dimensions']
    if (type(w) is not int or type(h) is not int or min(w,h) <= 0 or
        w % d['multiple'] or h % d['multiple'] or max(w,h) > d['max_edge'] or
        not d['min_pixels'] <= w*h <= d['max_pixels'] or max(w,h)/min(w,h) > d['max_ratio']):
        raise ValueError('未対応の寸法です。各辺16の倍数、比率1:3〜3:1、各辺3840以下、655,360〜8,294,400画素。候補: 1920×1088 / 2048×1152（16:9） / 1024×1024。自動変更はしません。')

def prompt_for(p):
    lines = ['モード: '+p['mode']]
    index = 1
    if p.get('target') and p['mode'] != 'generate':
        lines.append('画像1: 編集対象。構図・ポーズ・背景・現在の仕上がりの基準。')
        index += 1
    for ref in p.get('refs', []):
        lines.append(f"画像{index}: {ROLES[ref['role']]}。対象人物: {ref.get('person','指定なし')}")
        index += 1
    lines.extend(['変更すること:\n'+p.get('change',''), '維持すること:\n'+p.get('keep','')])
    if p.get('model')==qwen_backend.MODEL and p['mode']=='inpaint':lines.append(qwen_backend.mask_instruction(p))
    return '\n\n'.join(lines)

class JobConflict(ValueError):
    pass

class JobNotFound(ValueError):
    pass

class AssetNotFound(ValueError):
    pass

class PreflightError(ValueError):
    pass

class ResponsePersistenceError(OSError):
    def __init__(self,content,request_id):
        super().__init__('受信応答を保存できません。')
        self.content=content
        self.request_id=request_id


def canonical_input(p):
    # Only fixed user inputs; no server snapshots, timestamps or execution state.
    defaults={'provider':'mock','mode':'polish','target':None,'refs':[],'prompt':'','change':'','keep':'',
              'model':'','quality':'medium','width':1920,'height':1088,'format':'png','n':1,
              'strokes':[],'composite':False,'feather':0}
    result={k:copy.deepcopy(p.get(k,v)) for k,v in defaults.items()}
    result['refs']=[{'id':r['id'],'role':r['role'],'person':r.get('person','')} for r in result['refs']]
    result['strokes']=[{'width':float(r['width']),'erase':bool(r.get('erase',False)),
                       'points':[[float(x),float(y)] for x,y in r['points']]} for r in result['strokes']]
    if result['model']==qwen_backend.MODEL:
        result.update({k:copy.deepcopy(p.get(k,v)) for k,v in qwen_backend.DEFAULTS.items()})
    result['feather']=float(result['feather'])
    return result


def fingerprint(p):
    return hashlib.sha256(json.dumps(canonical_input(p),sort_keys=True,allow_nan=False).encode()).hexdigest()

class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path/'assets').mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.gpu_execution = threading.RLock()
        self.asset_lock = threading.RLock()
        self.db = sqlite3.connect(self.path/'history.sqlite3', check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, meta TEXT); CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, fingerprint TEXT, body TEXT); CREATE TABLE IF NOT EXISTS settings (id TEXT PRIMARY KEY, body TEXT);')
        self.db.commit()
        migrate(self)

    def settings(self):
        with self.lock:
            row = self.db.execute("SELECT body FROM settings WHERE id='config'").fetchone()
        return {'output':str(ROOT/'output'),'budget':5,'reservation':0.5,'live':False,'limit_mode':'notify','budget_period':'day',**(json.loads(row[0]) if row else {})}

    def set_settings(self, data):
        with self.lock:
            self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('config',json.dumps(data)))
            self.db.commit()

    def asset(self, raw, name='image', parent=None, kind='input', identity=None, operation=None):
        if kind == 'input' and len(raw) > CAP['max_file_bytes']:
            raise ValueError('ファイルは20MB以下にしてください。')
        ident=uuid.uuid5(uuid.NAMESPACE_URL,identity).hex if identity else uuid.uuid4().hex
        with self.asset_lock:
            with self.lock:
                row=self.db.execute('SELECT meta FROM assets WHERE id=?',(ident,)).fetchone()
            if row:return json.loads(row[0])
            image=normalize(raw)
            folder=self.path/'assets'/ident
            folder.mkdir(exist_ok=True)
            atomic_write(folder/'original.bin',raw)
            atomic_write(folder/'image.png',png(image))
            meta={'id':ident,'name':name[:240],'width':image.width,'height':image.height,
                  'parent':parent,'kind':kind,'sha256':hashlib.sha256(raw).hexdigest()}
            with self.lock, self.db:
                self.db.execute('INSERT INTO assets VALUES (?,?)',(ident,json.dumps(meta)))
                if operation is not None:
                    edit={**operation,'id':ident,'source_id':parent,'result':meta}
                    self.db.execute('INSERT INTO local_edits VALUES (?,?)',(ident,json.dumps(edit,ensure_ascii=False)))
            return meta

    def local_edits(self):
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute('SELECT body FROM local_edits ORDER BY rowid DESC')]

    def response_file(self, ident):
        safe_id(ident);self.job(ident)
        for prefix in ('response_','http_response_'):
            file=self.path/(prefix+ident+'.json')
            if file.is_file():return file
        return None

    def write_response(self,ident,content,request_id=None,http=False):
        safe_id(ident);self.job(ident)
        prefix='http_response_' if http else 'response_'
        atomic_write(self.path/(prefix+ident+'.json'),content)
        atomic_write(self.path/('response_meta_'+ident+'.json'),json.dumps({'request_id':request_id,'saved':now()}).encode())

    def meta(self, ident):
        with self.lock:
            row = self.db.execute('SELECT meta FROM assets WHERE id=?',(ident,)).fetchone()
        if not row:
            raise AssetNotFound('画像が見つかりません。')
        return json.loads(row[0])

    def file(self, ident):
        self.meta(ident)
        return self.path/'assets'/ident/'image.png'

    def job(self, ident):
        with self.lock:
            row = self.db.execute('SELECT body FROM jobs WHERE id=?',(ident,)).fetchone()
        if not row:
            raise JobNotFound('ジョブが見つかりません。')
        return json.loads(row[0])

    def jobs(self):
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute('SELECT body FROM jobs ORDER BY rowid DESC')]

    def save_job(self, job):
        with self.lock:
            estimate=job.get('estimate')
            if (job['status']=='completed' and job['params']['provider']=='openai'
                and type(estimate) in (int,float) and math.isfinite(estimate) and estimate>=0):
                job.setdefault('initial_reservation',job.get('reserved',0))
                job['reserved']=estimate
                job['budget_status']='settled_estimate'
            self.db.execute('UPDATE jobs SET body=? WHERE id=?',(json.dumps(job,ensure_ascii=False), job['id']))
            self.db.commit()

    def update_queued(self,ident,changes):
        """Linearization point shared by cancellation and dispatch admission."""
        with self.lock:
            job=self.job(ident)
            if job['status']!='queued':return None
            job.update(changes)
            self.save_job(job)
            return job

    def recover(self,cancel_queued=True):
        for job in self.jobs():
            if job['status']=='queued' and not cancel_queued:continue
            if job['status'] in ('sending','queued'):
                job['status'] = 'unknown' if job['status']=='sending' else 'cancelled'
                job['message'] = '再起動を検出。自動再送していません。'
                if job['status']=='cancelled': job['reserved']=0
                self.save_job(job)
            elif job['status']=='completed':
                # Migrate old completed jobs using their recorded estimate, not today's prices.
                self.save_job(job)

    def submit(self, p):
        with self.lock:
            safe_id(p['id'])
            p={**canonical_input(p),'id':p['id']}
            existing = self.db.execute('SELECT body FROM jobs WHERE id=?',(p['id'],)).fetchone()
            if existing:
                job=json.loads(existing[0])
                if fingerprint(job['params'])!=fingerprint(p):
                    raise JobConflict('同じジョブIDで異なる入力は登録できません。新しく実行する場合は新しいIDが必要です。')
                return job
            is_qwen=p['model']==qwen_backend.MODEL or p['provider']=='qwen'
            if is_qwen:qwen_backend.validate(p)
            else:validate_size(p['width'],p['height'])
            if not is_qwen and (p['model'] not in CAP['models'] or p['quality'] not in CAP['models'][p['model']]['qualities']):
                raise ValueError('未対応モデル・品質です。別モデルへの自動切替はしません。')
            if p['mode'] not in ('generate','polish','inpaint') or p['provider'] not in ('mock','openai','qwen'):
                raise ValueError('モード・接続方式が不正です。')
            if type(p.get('n',1)) is not int or not 1<=p.get('n',1)<=4 or p.get('format','png') not in ('png','jpeg','webp'):
                raise ValueError('生成枚数は1〜4枚、出力形式はPNG・JPEG・WebPから選んでください。')
            if not p.get('prompt','').strip(): raise ValueError('送信指示文を確認してください。')
            if len(p['prompt'])>32000: raise ValueError('指示文は32,000文字以下にしてください。')
            feather=float(p.get('feather',0))
            if not math.isfinite(feather) or not 0<=feather<=100: raise ValueError('境界ぼかしは0〜100pxにしてください。')
            ids = []
            if p['mode']!='generate':
                self.meta(p.get('target',''))
                ids.append(p['target'])
            for ref in p.get('refs',[]):
                if ref['role'] not in ROLES: raise ValueError('資料の役割が不正です。')
                self.meta(ref['id']); ids.append(ref['id'])
            if is_qwen:
                if len(ids)+(1 if p['mode']=='inpaint' else 0)>10:raise ValueError('Qwenの画像入力は元画像・参照資料・部分修正マスクの合計10枚までです。')
            elif len(ids)>CAP['max_images']:raise ValueError('画像は編集対象を含め8枚までです（初期版の制限）。')
            p['input_ids']=ids
            for ident in ids:
                if self.file(ident).stat().st_size>=50_000_000: raise ValueError('変換後の入力PNGが50MBを超えています。')
            p['input_snapshots']=[self.meta(ident) for ident in ids]
            if p['mode']=='inpaint':
                m=self.meta(p['target']); mask=mask_image((m['width'],m['height']),p.get('strokes',[]))
                if not mask.getbbox(): raise ValueError('変更したい範囲をマスクで塗ってください。')
                if is_qwen and (p['width'],p['height'])!=(m['width'],m['height']):raise ValueError('Qwen部分修正では出力寸法を元画像と同じにしてください。32の倍数でない元画像は先に余白追加で調整してください。')
            digest=fingerprint(p)
            settings=self.settings()
            reserved=0
            if p['provider']=='openai':
                if not settings['live'] or not api_key():
                    raise ValueError('APIキーのローカル設定と設定画面の実API有効化が必要です。')
                if settings['limit_mode']=='stop':
                    reserved=settings['reservation']*p.get('n',1)
                    accounted=usage_summary(self.jobs(),settings)['limit_accounted']
                    if reserved<=0 or accounted+reserved>settings['budget']:
                        raise ValueError('Atelierで設定した利用上限に達するため登録できません。設定の制限方法・金額を確認してください。OpenAIの残高不足ではありません。')
            job={'id':p['id'],'fingerprint':digest,'params':p,'status':'queued','created':datetime.now(timezone.utc).isoformat(),'reserved':reserved,'estimate':None,'message':'待機中','outputs':[]}
            if is_qwen:
                machine=qwen_backend.configuration(self)
                if not Path(machine['python']).is_file() or not (Path(machine['model'])/'model_index.json').is_file():raise ValueError('Qwen専用Pythonまたはモデルが未設定です。Qwen環境設定を確認してください。')
                job['local_machine']=machine
            self.db.execute('INSERT INTO jobs VALUES (?,?,?)',(job['id'],digest,json.dumps(job,ensure_ascii=False)))
            self.db.commit()
            return job

    def export(self, ident, operation=None):
        source=self.file(ident)
        content=source.read_bytes()
        folder=Path(self.settings()['output']).expanduser()
        folder.mkdir(parents=True,exist_ok=True)
        dest=folder/(f"atelier_{operation}.png" if operation else f"atelier_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:12]}.png")
        digest=hashlib.sha256(content).hexdigest()[:16]
        candidate=dest
        for attempt in range(100):
            if candidate.exists():
                if candidate.read_bytes()==content:return str(candidate)
                # Preserve both valid user files and legacy partial exports. Recover
                # into a stable sibling so subsequent reprocessing is idempotent.
                suffix='' if attempt==0 else f'_{attempt}'
                candidate=dest.with_name(f'{dest.stem}_recovered_{digest}{suffix}.png')
                continue
            try:
                publish_new(candidate,content)
                return str(candidate)
            except FileExistsError:
                continue  # A concurrent publisher won; compare its complete file.
        raise OSError('書き出し先で名前の競合が続いています。保存先を確認してください。')

def real_request(p, store):
    attempted=False
    try:
        data={k:p[k] for k in ('model','quality','prompt')}
        data.update(size=f"{p['width']}x{p['height']}", n=p.get('n',1), output_format=p.get('format','png'))
        headers={'Authorization':'Bearer '+api_key()}
        # No SDK retry loop. POST is attempted once; ambiguous failures stay unknown.
        with httpx.Client(timeout=httpx.Timeout(300,connect=20), follow_redirects=False, trust_env=False) as client:
            if p['input_ids']:
                files=[('image[]',(f'image_{i+1}.png',store.file(ident).read_bytes(),'image/png')) for i,ident in enumerate(p['input_ids'])]
                if p['mode']=='inpaint':
                    m=store.meta(p['target']); mask=mask_image((m['width'],m['height']),p['strokes'])
                    files.append(('mask',('mask.png',api_mask(mask),'image/png')))
                attempted=True
                response=client.post('https://api.openai.com/v1/images/edits',data=data,files=files,headers=headers)
            else:
                attempted=True
                response=client.post('https://api.openai.com/v1/images/generations',json=data,headers=headers)
            response.raise_for_status()
            # Preserve successful HTTP bytes before decoding so malformed responses are recoverable.
            try:store.write_response(p['id'],response.content,response.headers.get('x-request-id'),http=True)
            except OSError:raise ResponsePersistenceError(response.content,response.headers.get('x-request-id')) from None
            body=response.json()
            job=store.job(p['id'])
            job.update(usage=body.get('usage'),estimate=usage_cost(body.get('usage')),
                       request_id=response.headers.get('x-request-id'),phase='response_saved',recovery=True)
            store.save_job(job)
            return [base64.b64decode(x['b64_json'],validate=True) for x in body['data']],body.get('usage'),response.headers.get('x-request-id')
    except Exception as error:
        if not attempted:raise PreflightError('外部送信前の準備に失敗しました。') from None
        raise

def mock_request(p, store):
    time.sleep(0.4)
    def encode(image):
        if p.get('format','png')=='png': return png(image)
        buffer=io.BytesIO()
        image.convert('RGB').save(buffer,format=p['format'].upper())
        return buffer.getvalue()
    if p['mode']!='generate':
        # Identity mock: never pretend to have improved the picture.
        return [encode(normalize(store.file(p['target']).read_bytes()))]*p.get('n',1),None,'mock-'+uuid.uuid4().hex
    image=Image.new('RGB',(p['width'],p['height']),'#e5eeee')
    draw=ImageDraw.Draw(image)
    for x in range(0,image.width,64): draw.line((x,0,x,image.height),fill='#c7d9d9')
    for y in range(0,image.height,64): draw.line((0,y,image.width,y),fill='#c7d9d9')
    draw.text((32,32),'MOCK - TEST PATTERN / NO AI GENERATION',fill='#204c50',font_size=24)
    return [encode(image)]*p.get('n',1),None,'mock-'+uuid.uuid4().hex

from worker import Worker
