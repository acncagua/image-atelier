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
    return '\n\n'.join(lines)

class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path/'assets').mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path/'history.sqlite3', check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, meta TEXT); CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, fingerprint TEXT, body TEXT); CREATE TABLE IF NOT EXISTS settings (id TEXT PRIMARY KEY, body TEXT);')
        self.db.commit()

    def settings(self):
        with self.lock:
            row = self.db.execute("SELECT body FROM settings WHERE id='config'").fetchone()
        return json.loads(row[0]) if row else {'output':str(ROOT/'output'), 'budget':0, 'reservation':1, 'live':False}

    def set_settings(self, data):
        with self.lock:
            self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('config',json.dumps(data)))
            self.db.commit()

    def asset(self, raw, name='image', parent=None, kind='input'):
        if kind == 'input' and len(raw) > CAP['max_file_bytes']:
            raise ValueError('ファイルは20MB以下にしてください（初期版の制限）。')
        image = normalize(raw)
        ident = uuid.uuid4().hex
        folder = self.path/'assets'/ident
        folder.mkdir()
        (folder/'original.bin').write_bytes(raw)
        (folder/'image.png').write_bytes(png(image))
        meta = {'id':ident,'name':name[:240], 'width':image.width,'height':image.height,'parent':parent,'kind':kind,'sha256':hashlib.sha256(raw).hexdigest()}
        with self.lock:
            self.db.execute('INSERT INTO assets VALUES (?,?)',(ident,json.dumps(meta)))
            self.db.commit()
        return meta

    def meta(self, ident):
        with self.lock:
            row = self.db.execute('SELECT meta FROM assets WHERE id=?',(ident,)).fetchone()
        if not row:
            raise ValueError('画像が見つかりません。')
        return json.loads(row[0])

    def file(self, ident):
        self.meta(ident)
        return self.path/'assets'/ident/'image.png'

    def job(self, ident):
        with self.lock:
            row = self.db.execute('SELECT body FROM jobs WHERE id=?',(ident,)).fetchone()
        if not row:
            raise ValueError('ジョブが見つかりません。')
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

    def recover(self):
        for job in self.jobs():
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
            existing = self.db.execute('SELECT body FROM jobs WHERE id=?',(p['id'],)).fetchone()
            if existing: return json.loads(existing[0])
            validate_size(p['width'],p['height'])
            if p['model'] not in CAP['models'] or p['quality'] not in CAP['models'][p['model']]['qualities']:
                raise ValueError('未対応モデル・品質です。別モデルへの自動切替はしません。')
            if p['mode'] not in ('generate','polish','inpaint') or p['provider'] not in ('mock','openai'):
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
            if len(ids)>CAP['max_images']: raise ValueError('画像は編集対象を含め8枚までです（初期版の制限）。')
            p['input_ids']=ids
            for ident in ids:
                if self.file(ident).stat().st_size>=50_000_000: raise ValueError('変換後の入力PNGが50MBを超えています。')
            p['input_snapshots']=[self.meta(ident) for ident in ids]
            if p['mode']=='inpaint':
                m=self.meta(p['target']); mask=mask_image((m['width'],m['height']),p.get('strokes',[]))
                if not mask.getbbox(): raise ValueError('変更したい範囲をマスクで塗ってください。')
            fingerprint=hashlib.sha256(json.dumps({k:v for k,v in p.items() if k!='id'},sort_keys=True).encode()).hexdigest()
            for job in self.jobs():
                if job.get('fingerprint')==fingerprint and job['status'] in ('queued','sending'):
                    return job
            settings=self.settings()
            reserved=0
            if p['provider']=='openai':
                if not settings['live'] or not api_key():
                    raise ValueError('APIキーのローカル設定と設定画面の実API有効化が必要です。')
                reserved=settings['reservation']*p.get('n',1)
                held=sum(j.get('reserved',0) for j in self.jobs())
                if reserved<=0 or held+reserved>settings['budget']:
                    raise ValueError('アプリ予算の残りが予約額を下回ります。予算と1回の予約額を確認してください。')
            job={'id':p['id'],'fingerprint':fingerprint,'params':p,'status':'queued','created':datetime.now(timezone.utc).isoformat(),'reserved':reserved,'estimate':None,'message':'待機中','outputs':[]}
            self.db.execute('INSERT INTO jobs VALUES (?,?,?)',(job['id'],fingerprint,json.dumps(job,ensure_ascii=False)))
            self.db.commit()
            return job

    def export(self, ident):
        source=self.file(ident)
        folder=Path(self.settings()['output']).expanduser()
        folder.mkdir(parents=True,exist_ok=True)
        dest=folder/f"atelier_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:12]}.png"
        with dest.open('xb') as f: f.write(source.read_bytes())
        return str(dest)

def real_request(p, store):
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
            response=client.post('https://api.openai.com/v1/images/edits',data=data,files=files,headers=headers)
        else:
            response=client.post('https://api.openai.com/v1/images/generations',json=data,headers=headers)
        response.raise_for_status()
        # Preserve successful HTTP bytes before decoding so malformed responses are recoverable.
        (store.path/('http_response_'+p['id']+'.json')).write_bytes(response.content)
        body=response.json()
        return [base64.b64decode(x['b64_json'],validate=True) for x in body['data']],body.get('usage'),response.headers.get('x-request-id')

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

class Worker:
    def __init__(self, store):
        self.store=store; self.stop=threading.Event()
        self.thread=threading.Thread(target=self.loop,daemon=True)

    def loop(self):
        while not self.stop.wait(0.2):
            queued=[j for j in self.store.jobs() if j['status']=='queued']
            if queued: self.run(queued[-1]['id'])

    def run(self, ident):
        s=self.store
        with s.lock:
            job=s.job(ident)
            if job['status']!='queued': return
            if job['params']['provider']=='openai' and not s.settings()['live']:
                job.update(status='cancelled',reserved=0,message='実APIが無効になったため送信前に取り消しました。')
                s.save_job(job)
                return
            job.update(status='sending',started=time.time(),message='送信中・生成中（進捗率は取得できません）')
            s.save_job(job)
        p=job['params']
        try:
            if p['mode']=='inpaint':
                meta=s.meta(p['target']); mask=mask_image((meta['width'],meta['height']),p['strokes'])
                job['mask']=s.asset(api_mask(mask),'APIマスク',p['target'],'mask')
                s.save_job(job)
            raw,usage,request_id=(real_request if p['provider']=='openai' else mock_request)(p,s)
            # Recovery bytes reach disk before image validation/composition/export.
            recovery=s.path/('response_'+ident+'.json')
            recovery.write_text(json.dumps({'images':[base64.b64encode(b).decode() for b in raw],'usage':usage,'request_id':request_id}),encoding='utf-8')
            job.update(usage=usage,request_id=request_id,recovery=True)
            job['estimate']=usage_cost(usage)
            job['price_checked']=CAP['checked']
            if job['estimate'] is not None: job['reserved']=max(job['reserved'],job['estimate'])
            s.save_job(job)
            for content in raw:
                result=s.asset(content,'API生出力' if p['provider']=='openai' else 'モック出力',p.get('target'),'raw')
                job['outputs'].append(result)
                s.save_job(job)
            messages=([('モック新規生成は検査用パターンです。' if p['mode']=='generate' else 'モック編集は元画像のコピーです。')+'画質の評価には使えません。'] if p['provider']=='mock' else [])
            if len(job['outputs'])!=p.get('n',1): messages.append('要求枚数と取得枚数が異なります。取得できた結果を保存しました。')
            for result in list(job['outputs']):
                if (result['width'],result['height'])!=(p['width'],p['height']): messages.append('要求寸法と実寸法が異なります。自動調整していません。')
                if p['mode']=='inpaint' and p.get('composite'):
                    try:
                        original=normalize(s.file(p['target']).read_bytes()); output=normalize(s.file(result['id']).read_bytes())
                        merged=composite(original,output,mask,float(p.get('feather',0)))
                        merged_asset=s.asset(png(merged),'局所合成',p['target'],'composite')
                        merged_asset['raw_output_id']=result['id']
                        job['outputs'].append(merged_asset)
                    except ValueError as e: messages.append(str(e))
            job.update(status='completed',message=' / '.join(messages) or '完了')
            job['saved_paths']=[]
            for output in job['outputs']:
                try:
                    job['saved_path']=s.export(output['id'])
                    job['saved_paths'].append(job['saved_path'])
                except OSError: job['message']+=' / 保存先に書き込めません。結果はアプリ内に保持しています。PNGダウンロードで回収できます。'
        except httpx.HTTPStatusError as e:
            status=e.response.status_code
            job['status']='unknown' if status>=500 or status==408 else 'failed'
            labels={400:'入力またはポリシー',401:'認証',403:'権限またはポリシー',404:'モデルまたはAPI未対応',429:'利用制限・残高'}
            job['message']=f"HTTP {status}: {labels.get(status,'APIエラー')}。自動再送なし。"
            job['request_id']=e.response.headers.get('x-request-id')
            if job['status']=='failed': job['reserved']=0
        except Exception as error:
            job['error_type']=type(error).__name__
            job.update(status='unknown',message='通信・応答処理・保存中に結果を確定できませんでした。自動再送はしません。再実行は追加課金の可能性があります。')
        finally:
            job['elapsed']=round(time.time()-job['started'],2)
            s.save_job(job)
