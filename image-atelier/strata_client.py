"""Local Strata metadata and elastic VRAM API; never load a model or send chat."""
import math
from urllib.parse import urlsplit,urlunsplit
import httpx
from local_config import strata_api_key

DEFAULTS={'enabled':False,'url':'http://127.0.0.1:8080','gpu_uuid':'','target_gib':18,'probe_timeout':5,'change_timeout':900}

def validate_settings(value):
    if not isinstance(value,dict):raise ValueError('Strata設定の形式が不正です。')
    c={**DEFAULTS,**value}
    if type(c['enabled']) is not bool:raise ValueError('Strata連携はON/OFFで指定してください。')
    parsed=urlsplit(str(c['url']).rstrip('/'))
    if parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','localhost','::1') or parsed.username or parsed.password or parsed.path not in ('','/v1') or parsed.query or parsed.fragment or not parsed.port:
        raise ValueError('Strataは同じPCのhttp://127.0.0.1:ポート番号で指定してください。')
    c['url']=urlunsplit((parsed.scheme,parsed.netloc,'','',''))
    if not isinstance(c['gpu_uuid'],str):raise ValueError('対象GPUが不正です。')
    for key,low,high in [('target_gib',0,1024),('probe_timeout',1,30),('change_timeout',750,7200)]:
        if type(c[key]) not in (int,float) or not math.isfinite(c[key]) or (c[key]<=0 if key=='target_gib' else c[key]<low) or c[key]>high:
            raise ValueError({'target_gib':'目標VRAMは正の有限値で指定してください。','probe_timeout':'疎通待ちは1〜30秒です。','change_timeout':'VRAM変更待ちは750〜7200秒です。'}[key])
    return {k:c[k] for k in DEFAULTS}

def target_mib(config):return math.ceil(config['target_gib']*1024)

def client(config):
    key=strata_api_key()
    return httpx.Client(base_url=config['url'],headers={'Authorization':'Bearer '+key} if key else {},timeout=httpx.Timeout(config['change_timeout'],connect=config['probe_timeout']),trust_env=False,follow_redirects=False)

def safe_error(error):
    if isinstance(error,httpx.HTTPStatusError):
        text='HTTP '+str(error.response.status_code)
        try:
            message=error.response.json().get('error',{})
            if isinstance(message,dict):text+=': '+str(message.get('message',''))[:240]
        except (ValueError,AttributeError):pass
    else:text=type(error).__name__
    try:
        secret=strata_api_key()
        if secret:text=text.replace(secret,'[redacted]')
    except ValueError:pass
    return text

def probe(config):
    try:
        with client(config) as c:
            r=c.get('/health',timeout=config['probe_timeout']);r.raise_for_status();health=r.json()
            if not isinstance(health,dict) or health.get('service')!='strata' or type(health.get('loaded')) is not bool:
                return {'state':'unsupported','message':'接続先のStrata状態APIを確認できません。'}
            r=c.get('/v1/status',timeout=config['probe_timeout']);r.raise_for_status();s=r.json()
            if not isinstance(s,dict) or s.get('service')!='strata' or type(s.get('loaded')) is not bool or type(s.get('started')) not in (int,float) or not math.isfinite(s['started']) or s['started']<=0:
                raise ValueError('invalid Strata status')
            gpu=(s.get('machine') or {}).get('gpu') or {}
            vram=s.get('vram') or {}
            state='unloaded' if not s['loaded'] else 'loaded' if vram.get('elastic') is True else 'unsupported'
            return {'state':state,'model':s.get('model'),'version':s.get('engine'),'started':s['started'],'identity':[s['started'],s.get('model'),s.get('engine')],
                    'gpu_name':gpu.get('name'),'gpu_total_mib':gpu.get('total_mib'),'vram':vram,'in_flight':(s.get('activity') or {}).get('in_flight'),
                    'message':'vram_elasticが有効ではありません。Strata設定を変更して再起動してください。' if state=='unsupported' else 'モデル未ロード' if state=='unloaded' else 'モデル稼働中'}
    except httpx.ConnectError as error:
        reason=error
        while reason:
            if getattr(reason,'errno',None) in (61,111,10061) or any(token in str(reason) for token in ('10061','Connection refused','actively refused')):
                return {'state':'offline','message':'Strata未起動（接続拒否）'}
            reason=reason.__cause__
        return {'state':'unknown','message':'Strataの接続状態を確認できません。'}
    except httpx.HTTPStatusError as error:
        code=error.response.status_code
        return {'state':'auth_error' if code in (401,403) else 'unsupported' if code in (404,405) else 'unknown','message':safe_error(error)}
    except (httpx.HTTPError,ValueError,TypeError,AttributeError) as error:
        return {'state':'unknown','message':safe_error(error)}

def change(config,reserve_mib):
    with client(config) as c:
        r=c.post('/v1/vram',json={'reserve_mib':reserve_mib});r.raise_for_status();result=r.json()
        if not isinstance(result,dict) or result.get('status') not in ('ok','not loaded'):raise ValueError('StrataのVRAM変更応答が不正です。')
        if result['status']=='ok' and (type(result.get('vram_free_mib')) not in (int,float) or not math.isfinite(result['vram_free_mib']) or result['vram_free_mib']<0):raise ValueError('Strataの空きVRAMを確認できません。')
        return result
