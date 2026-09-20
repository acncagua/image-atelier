from billing import usage_summary
import base64
import io
import json
import math
import os
import secrets
import sqlite3
import uuid
import httpx
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from core import ROOT, CAP, ROLES, Store, Worker, prompt_for, JobConflict, JobNotFound, AssetNotFound
from persistence import now, safe_id
from imaging import normalize, png
from PIL import Image, ImageOps
from local_config import api_key
from upscale_jobs import UpscaleJobs
from upscale_geometry import plan as upscale_plan

def create_app(data_path=None, run_worker=True, mock_gate=None, port=18791, gpu_runner=None):
    store=Store(data_path or ROOT/'data')
    token=secrets.token_urlsafe(32)
    worker=Worker(store,mock_gate=mock_gate)
    gpu=UpscaleJobs(store,runner=gpu_runner)
    @asynccontextmanager
    async def lifespan(app):
        lock_file=None
        if run_worker and os.name=='nt':
            import msvcrt
            lock_file=(store.path/'instance.lock').open('a+b')
            lock_file.seek(0); lock_file.write(b'0'); lock_file.flush(); lock_file.seek(0)
            try: msvcrt.locking(lock_file.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:
                lock_file.close()
                raise RuntimeError('Image Atelierは既に起動しています。二重起動を停止しました。')
        try:store.recover()
        except Exception as error:worker.fault(error)
        gpu.recover()
        if run_worker: worker.thread.start()
        if run_worker: gpu.thread.start()
        yield
        worker.stop.set()
        gpu.shutdown()
        if run_worker: worker.thread.join()
        if lock_file: lock_file.close()
    app=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.store=store
    app.state.token=token
    app.state.worker=worker
    app.state.gpu=gpu
    app.state.test_asset_fault=False

    @app.middleware('http')
    async def local_only(request, call_next):
        host=request.headers.get('host','')
        if host not in (f'127.0.0.1:{port}',f'localhost:{port}','testserver'):
            return JSONResponse({'detail':'ループバックの正規URLから開いてください。'},status_code=403)
        origin=request.headers.get('origin')
        if origin and origin not in (f'http://127.0.0.1:{port}',f'http://localhost:{port}'):
            return JSONResponse({'detail':'別サイトからのアクセスは許可されません。'},status_code=403)
        if request.method!='GET' and not secrets.compare_digest(request.headers.get('x-atelier-token',''),token):
            return JSONResponse({'detail':'ページを再読み込みしてください。'},status_code=403)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Cache-Control']='no-store'
        response.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        return response

    @app.exception_handler(JobConflict)
    async def conflict(request,e): return JSONResponse({'detail':str(e)},status_code=409)

    @app.exception_handler(JobNotFound)
    async def missing(request,e): return JSONResponse({'detail':str(e)},status_code=404)

    @app.exception_handler(AssetNotFound)
    async def missing_asset(request,e): return JSONResponse({'detail':str(e)},status_code=404)

    @app.exception_handler(sqlite3.Error)
    async def database_error(request,e):
        worker.fault(e)
        return JSONResponse({'detail':'履歴DBを読み書きできません。ワーカーを一時停止しました。'},status_code=503)

    @app.get('/api/worker/health')
    def worker_health(): return worker.health()

    @app.post('/api/worker/resume')
    def worker_resume(): return worker.resume()

    if mock_gate is not None:
        @app.post('/api/__test/mock-gate')
        async def test_gate(request:Request):
            p=await request.json()
            mock_gate.set() if p['open'] else mock_gate.clear()
            return {'open':mock_gate.is_set()}

        @app.post('/api/__test/asset-fault')
        async def test_asset_fault(request:Request):
            p=await request.json();app.state.test_asset_fault=p.get('enabled') is True
            return {'enabled':app.state.test_asset_fault}

    @app.exception_handler(ValueError)
    async def invalid(request,e): return JSONResponse({'detail':str(e)},status_code=400)

    @app.exception_handler(OSError)
    async def io_error(request,e): return JSONResponse({'detail':'ファイルの読み書きに失敗しました。保存先の権限・空き容量を確認してください。結果はPNGダウンロードでも回収できます。'},status_code=507)

    async def body(request,limit=30_000_000):
        data=bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data)>limit: raise HTTPException(413,'リクエストが大きすぎます。')
        try: return json.loads(data)
        except Exception: raise ValueError('JSONの形式が不正です。')

    @app.get('/api/bootstrap')
    def bootstrap():
        try:key_set=bool(api_key()) if mock_gate is None else False;config_error=None
        except ValueError:key_set=False;config_error='APIキー設定ファイルを読み込めません。ローカル設定を確認してください。'
        return {'token':token,'capabilities':CAP,'settings':store.settings(),'key_set':key_set,'config_error':config_error,'roles':ROLES,'test_mode':mock_gate is not None}

    @app.get('/evaluation')
    def evaluation():
        report=ROOT/'output'/'live-evaluation.html'
        if not report.exists(): raise HTTPException(404,'実API評価レポートはまだありません。')
        return FileResponse(report,media_type='text/html')

    @app.post('/api/assets')
    async def upload(request:Request):
        p=await body(request)
        try: raw=base64.b64decode(p['data'].split(',')[-1],validate=True)
        except Exception: raise ValueError('画像データが不正です。')
        return store.asset(raw,p.get('name','image'))

    @app.post('/api/connection-check')
    def connection_check():
        if mock_gate is not None:raise ValueError('隔離試験環境では外部通信を行いません。')
        key=api_key()
        if not key: raise ValueError('APIキーが未設定です。config.local.jsonを確認してください。')
        try:
            with httpx.Client(timeout=20,trust_env=False,follow_redirects=False) as client:
                response=client.get('https://api.openai.com/v1/models',headers={'Authorization':'Bearer '+key})
            if response.status_code!=200:
                raise ValueError(f'接続確認に失敗しました（HTTP {response.status_code}）。認証・権限・利用制限を確認してください。')
            available={item['id'] for item in response.json()['data']}
            return {'authenticated':True,'models':{model:model in available for model in CAP['models']},'message':'認証成功。モデル一覧の確認のみです。画像生成・編集の成功や残高は未確認です。'}
        except (httpx.HTTPError,KeyError,TypeError):
            raise ValueError('接続または応答の確認に失敗しました。ネットワーク設定を確認してください。キーは表示しません。') from None

    @app.get('/api/assets/{ident}')
    def meta(ident:str):
        if mock_gate is not None and app.state.test_asset_fault:raise HTTPException(503,'試験用の一時的な画像取得障害です。')
        result=store.meta(ident)
        if not store.file(ident).is_file():raise AssetNotFound('画像ファイルが見つかりません。')
        return result

    @app.get('/api/assets/{ident}/image')
    def image(ident:str): return FileResponse(store.file(ident),media_type='image/png')

    @app.get('/api/assets/{ident}/download')
    def download(ident:str): return FileResponse(store.file(ident),media_type='image/png',filename=f'atelier_{ident}.png')

    @app.get('/api/assets/{ident}/original')
    def original(ident:str):
        store.meta(ident)
        return FileResponse(store.path/'assets'/ident/'original.bin',media_type='application/octet-stream',filename=f'original_{ident}.bin')

    @app.post('/api/crop')
    async def crop(request:Request):
        p=await body(request); image=normalize(store.file(p['id']).read_bytes())
        x,y,w,h=[round(float(p[k])) for k in ('x','y','w','h')]
        if min(x,y)<0 or min(w,h)<=0 or x+w>image.width or y+h>image.height: raise ValueError('切り出し範囲が元画像の外です。')
        return store.asset(png(image.crop((x,y,x+w,y+h))),'顔の切り出し',p['id'],'crop')

    @app.post('/api/resize')
    async def resize(request:Request):
        p=await body(request); image=normalize(store.file(p['id']).read_bytes())
        w,h=int(p['width']),int(p['height'])
        if min(w,h)<=0 or w*h>40_000_000: raise ValueError('調整後の寸法が不正です。')
        if p['method']=='resize': result=image.resize((w,h),Image.Resampling.LANCZOS)
        elif p['method']=='crop':
            if w>image.width or h>image.height: raise ValueError('切り抜き寸法が画像を超えています。')
            x=(image.width-w)//2; y=(image.height-h)//2; result=image.crop((x,y,x+w,y+h))
        elif p['method']=='pad':
            if w<image.width or h<image.height: raise ValueError('余白追加では元画像より大きい寸法を指定してください。')
            result=Image.new('RGBA',(w,h),(0,0,0,0)); result.paste(image,((w-image.width)//2,(h-image.height)//2))
        else: raise ValueError('調整方法が不正です。')
        context=p.get('context')
        if context is not None:
            from core import canonical_input
            context=canonical_input(context)
        details={'source_size':[image.width,image.height]}
        if p['method']=='crop':details.update(x=x,y=y)
        if p['method']=='pad':details.update(x=(w-image.width)//2,y=(h-image.height)//2,color=[0,0,0,0])
        return store.asset(png(result),'明示的な寸法調整',p['id'],'adjusted',operation={
            'method':p['method'],'requested':[w,h],'details':details,'created':now(),'legacy':False,'context':context})

    @app.get('/api/local-edits')
    def local_edits():return store.local_edits()

    @app.post('/api/prompt')
    async def prompt(request:Request): return {'prompt':prompt_for(await body(request))}

    @app.get('/api/upscale/config')
    def upscale_config():return gpu.public_config()

    @app.post('/api/upscale/config')
    async def upscale_configure(request:Request):return gpu.configure(await body(request))

    @app.post('/api/upscale/plan')
    async def upscale_preview(request:Request):
        p=await body(request);source=store.meta(p['source_id'])
        return upscale_plan(source['width'],source['height'],p.get('options',{}))

    @app.get('/api/upscale/jobs')
    def upscale_jobs():return [gpu.public(j) for j in gpu.jobs()]

    @app.get('/api/upscale/jobs/{ident}')
    def upscale_job(ident:str):return gpu.public(gpu.get(ident))

    @app.post('/api/upscale/jobs')
    async def upscale_submit(request:Request):return await run_in_threadpool(gpu.submit,await body(request))

    @app.post('/api/upscale/jobs/{ident}/cancel')
    def upscale_cancel(ident:str):return gpu.cancel(ident)

    @app.post('/api/upscale/jobs/{ident}/save')
    def upscale_save(ident:str):return gpu.retry_save(ident)

    @app.post('/api/jobs')
    async def submit(request:Request):
        p=await body(request)
        try: uuid.UUID(p['id'])
        except Exception: raise ValueError('ジョブIDが不正です。')
        if worker.health()['state']=='fault':raise HTTPException(503,'ワーカー障害中です。復旧後に登録を確認してください。')
        if mock_gate is not None and p.get('provider')!='mock':raise HTTPException(403,'テスト環境はモックのみです。')
        return store.submit(p)

    def expose_job(job):
        return {**job,'recovery':store.response_file(job['id']) is not None}

    @app.get('/api/jobs')
    def jobs(): return [expose_job(job) for job in store.jobs()]

    @app.get('/api/jobs/{ident}')
    def job(ident:str):
        safe_id(ident)
        return expose_job(store.job(ident))

    @app.get('/api/jobs/{ident}/recovery')
    def recovery(ident:str):
        file=store.response_file(ident)
        if file is None:raise ValueError('回収用の応答記録がありません。')
        return FileResponse(file,media_type='application/octet-stream',filename='response_'+ident+'.json')

    @app.post('/api/jobs/{ident}/reprocess')
    def reprocess(ident:str):
        safe_id(ident)
        if worker.current is not None:raise HTTPException(409,'実行中の処理が完了してから再処理してください。')
        return worker.reprocess(ident)

    @app.post('/api/jobs/{ident}/cancel')
    def cancel(ident:str):
        job=store.update_queued(ident,{'status':'cancelled','reserved':0,'message':'待機取消'})
        if job is None:raise ValueError('取消できるのは待機中だけです。送信済み処理の課金は取り消せません。')
        return job

    @app.post('/api/output-folder/open')
    def open_output_folder():
        if os.name!='nt':raise HTTPException(400,'フォルダーを開く操作はWindowsで利用できます。')
        folder=Path(store.settings()['output']).expanduser().resolve()
        try:
            folder.mkdir(parents=True,exist_ok=True)
            os.startfile(str(folder),'explore')
        except OSError:
            raise HTTPException(400,'保存先フォルダーを開けません。設定の保存先とアクセス権を確認してください。') from None
        return {'path':str(folder)}

    @app.post('/api/export')
    async def export(request:Request): return {'path':store.export((await body(request))['id'])}

    @app.get('/api/usage-summary')
    def usage():return usage_summary(store.jobs(),store.settings())

    @app.post('/api/settings')
    async def settings(request:Request):
        p=await body(request)
        config={'limit_mode':p.get('limit_mode','notify'),'budget_period':p.get('budget_period','day'),'output':str(p['output']),'budget':float(p['budget']),'reservation':float(p['reservation']),'live':p['live'] is True}
        if any(not math.isfinite(config[k]) or config[k]<0 for k in ('budget','reservation')): raise ValueError('予算は0以上の数値で指定してください。')
        if config['limit_mode'] not in ('off','notify','stop') or config['budget_period'] not in ('day','month','all'):raise ValueError('料金管理の設定が不正です。')
        if config['limit_mode']=='stop' and (config['budget']<=0 or config['reservation']<=0):raise ValueError('停止上限と1枚の仮計上額は0より大きい値を指定してください。')
        if not Path(config['output']).is_absolute(): raise ValueError('保存先は絶対パスで指定してください。')
        store.set_settings(config); return config

    @app.get('/api/presets')
    def presets():
        with store.lock:
            row=store.db.execute("SELECT body FROM settings WHERE id='presets'").fetchone()
        return json.loads(row[0]) if row else []

    @app.post('/api/presets')
    async def save_preset(request:Request):
        p=await body(request)
        item={'name':str(p['name'])[:100], 'keep':str(p['keep'])[:32000], 'refs':p['refs']}
        if not item['name'].strip(): raise ValueError('プリセット名を入力してください。')
        for ref in item['refs']:
            store.meta(ref['id'])
            if ref['role'] not in ROLES: raise ValueError('資料の役割が不正です。')
        with store.lock:
            items=[x for x in presets() if x['name']!=item['name']]+[item]
            store.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('presets',json.dumps(items)))
            store.db.commit()
        return items

    if (ROOT/'dist').exists(): app.mount('/',StaticFiles(directory=ROOT/'dist',html=True),name='ui')
    return app

if __name__=='__main__':
    import uvicorn
    uvicorn.run(create_app(),host='127.0.0.1',port=18791,access_log=False)
