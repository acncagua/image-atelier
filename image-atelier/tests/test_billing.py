import unittest
from unittest.mock import patch
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import httpx
import test_core as fixtures
from billing import usage_summary,api_error_message

class Billing(unittest.TestCase):
    setUp=fixtures.Tests.setUp
    tearDown=fixtures.Tests.tearDown
    p=fixtures.Tests.p

    def test_legacy_settings_default_to_notification_without_losing_history(self):
        self.s.set_settings({'output':self.s.settings()['output'],'budget':0,'reservation':1,'live':True})
        self.assertEqual(self.s.settings()['limit_mode'],'notify')
        with patch('core.api_key',return_value='test-only'):
            job=self.s.submit(self.p(provider='openai'))
        self.assertEqual(job['reserved'],0);self.assertEqual(len(self.s.jobs()),1)

    def test_off_and_notification_never_block_on_allowance(self):
        for mode in ('off','notify'):
            self.s.set_settings({**self.s.settings(),'limit_mode':mode,'budget':0})
            with patch('core.api_key',return_value='test-only'):
                job=self.s.submit(self.p(provider='openai',n=4))
            self.assertEqual(job['status'],'queued');self.assertEqual(job['reserved'],0)

    def test_opt_in_stop_is_atomic_and_counts_all_images(self):
        self.s.set_settings({**self.s.settings(),'limit_mode':'stop','budget':1,'reservation':.6})
        def submit(_):
            try:return self.s.submit(self.p(provider='openai'))['status']
            except ValueError:return 'blocked'
        with patch('core.api_key',return_value='test-only'),ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(submit,range(2))),['blocked','queued'])

    def test_local_calendar_and_unknown_are_separate_from_estimates(self):
        settings={**self.s.settings(),'limit_mode':'notify','budget_period':'day','budget':.1}
        def job(date,status,estimate,provider='openai'):
            return {'created':date,'status':status,'estimate':estimate,'reserved':0,'params':{'provider':provider,'n':1}}
        jobs=[job('2026-09-19T15:00:00+00:00','completed',.2),job('2026-09-19T14:59:59+00:00','completed',.3),
              job('2026-08-30T00:00:00+00:00','completed',.4),job('2026-09-20T00:00:00+00:00','completed',None),
              job('2026-09-20T00:00:00+00:00','cancelled',None),job('2026-09-20T00:00:00+00:00','completed',50,'mock')]
        summary=usage_summary(jobs,settings,datetime.fromisoformat('2026-09-20T10:00:00+09:00'))
        self.assertAlmostEqual(summary['totals']['day']['estimate'],.2)
        self.assertAlmostEqual(summary['totals']['month']['estimate'],.5)
        self.assertAlmostEqual(summary['totals']['all']['estimate'],.9)
        self.assertEqual(summary['totals']['day']['unknown'],1);self.assertTrue(summary['notice'])
        self.assertEqual(summary['pending_allowance'],1)

    def test_switching_to_stop_accounts_for_previous_unreserved_unknown_jobs(self):
        self.s.set_settings({**self.s.settings(),'limit_mode':'off'})
        with patch('core.api_key',return_value='test-only'):
            j=self.s.submit(self.p(provider='openai'));j['status']='unknown';self.s.save_job(j)
            self.s.set_settings({**self.s.settings(),'limit_mode':'stop','budget':1,'reservation':1})
            with self.assertRaisesRegex(ValueError,'Atelier'):
                self.s.submit(self.p(provider='openai'))

    def test_provider_errors_are_not_confused_with_local_limits(self):
        response=httpx.Response(429,json={'error':{'code':'credit_balance_exhausted','message':'PRIVATE'}})
        self.assertIn('OpenAI側のクレジット残高',api_error_message(response))
        self.assertNotIn('PRIVATE',api_error_message(response))
        self.assertIn('利用枠',api_error_message(httpx.Response(429,json={'error':{'code':'insufficient_quota'}})))
        self.assertIn('HTTP 429',api_error_message(httpx.Response(429)))

if __name__=='__main__':unittest.main()
