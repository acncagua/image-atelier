"""Atelier-only estimates and optional limits; never a provider balance."""
import math
from datetime import datetime,timezone,timedelta

JST=timezone(timedelta(hours=9))

def usage_summary(jobs,settings,at=None):
    current=(at or datetime.now(JST)).astimezone(JST)
    totals={key:{'estimate':0.,'unknown':0,'pending':0,'jobs':0} for key in ('day','month','all')}
    held=0.
    for job in jobs:
        if job.get('params',{}).get('provider')!='openai':continue
        try:date=datetime.fromisoformat(job['created']).astimezone(JST)
        except (ValueError,KeyError):date=None
        periods=['all']
        if date and (date.year,date.month)==(current.year,current.month):periods.append('month')
        if date and date.date()==current.date():periods.append('day')
        estimate=job.get('estimate')
        known=type(estimate) in (int,float) and math.isfinite(estimate) and estimate>=0
        pending=job['status'] in ('queued','sending')
        unknown=not known and job['status'] in ('completed','unknown','local_error')
        for key in periods:
            row=totals[key];row['jobs']+=1
            if known:row['estimate']+=estimate
            if unknown:row['unknown']+=1
            if pending:row['pending']+=1
        # Keep unresolved jobs across date boundaries when the optional stop
        # policy is used. Estimates remain separate from these placeholders.
        if not known and (pending or unknown):
            held+=max(float(job.get('reserved',0)),float(settings['reservation'])*job['params'].get('n',1))
    period=settings['budget_period'];estimate=totals[period]['estimate']
    return {'totals':totals,'timezone':'Asia/Tokyo','period':period,'mode':settings['limit_mode'],
            'threshold':settings['budget'],'estimate':estimate,'pending_allowance':held,
            'limit_accounted':estimate+held,'notice':settings['limit_mode']=='notify' and settings['budget']>0 and estimate>=settings['budget']}

def api_error_message(response):
    try:
        error=response.json().get('error',{})
        code=error.get('code') or error.get('type') if isinstance(error,dict) else None
    except (ValueError,AttributeError):code=None
    if code=='credit_balance_exhausted':return 'OpenAI側のクレジット残高が不足しています。公式の請求画面を確認してください。自動再送しません。'
    if code=='insufficient_quota':return 'OpenAI側の利用枠または請求設定により拒否されました。公式の残高・利用上限を確認してください。自動再送しません。'
    return f'HTTP {response.status_code}。自動再送しません。'
