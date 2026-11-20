import sys
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

class ClaudeUsageTests(unittest.TestCase):
    def service(self, response, base='https://custom.invalid/prefix/v1', key='synthetic-api-secret'):
        import claude_usage
        config={'base_url':base,'headers':{'x-api-key':key},'cache_key':key,'proxies':{}}
        patch=mock.patch('claude_usage.claude_catalog.resolve_config',return_value=config)
        patch.start();self.addCleanup(patch.stop)
        fetch=mock.Mock(return_value=response)
        return claude_usage.UsageService(fetch=fetch), fetch, config

    def test_live_endpoint_daily_quota_and_no_invented_reset(self):
        service,fetch,_=self.service({'unit':'USD','subscription':{'daily_usage_usd':400,'daily_limit_usd':500,'expires_at':'2026-12-01T00:00:00Z'}})
        self.assertEqual(service.snapshot()['status'],'idle');fetch.assert_not_called()
        result=service.refresh()
        self.assertEqual(result['data']['used'],400)
        self.assertEqual(result['data']['limit'],500)
        self.assertEqual(result['data']['period'],'daily')
        self.assertIsNone(result['data']['next_reset_md'])
        request=fetch.call_args.args[0]
        self.assertEqual(request.full_url,'https://custom.invalid/prefix/v1/usage')
        self.assertEqual(request.get_header('Authorization'),'Bearer synthetic-api-secret')
        self.assertNotIn('synthetic-api-secret',str(result))
        self.assertEqual(service.refresh()['rate_limited'],True)
        self.assertEqual(fetch.call_count,1)

    def test_quota_reset_and_cache_isolation(self):
        service,fetch,config=self.service({'quota':{'used':20,'limit':100,'reset_at':'2026-11-21T00:00:00Z'},'unit':'USD'})
        self.assertEqual(service.refresh()['data']['next_reset_at'],'2026-11-21T00:00:00+00:00')
        config['cache_key']='different';config['base_url']='https://another.invalid'
        self.assertEqual(service.snapshot()['status'],'idle')
        service.refresh()
        self.assertEqual(fetch.call_args.args[0].full_url,'https://another.invalid/v1/usage')

    def test_balance_and_malformed_values(self):
        service,_,_=self.service({'balance':12.5,'unit':'USD'})
        self.assertEqual(service.refresh()['data']['balance'],12.5)
        for response in [{'quota':{'used':float('nan'),'limit':100}}, {'subscription':{'daily_usage_usd':True,'daily_limit_usd':500}}, {'html':'not usage'}]:
            service,_,_=self.service(response)
            self.assertEqual(service.refresh()['status'],'error')

    def test_failed_refresh_keeps_only_same_endpoint_safe_data(self):
        service,fetch,_=self.service({'quota':{'used':1,'limit':5}})
        service.refresh()
        fetch.side_effect=HTTPError('https://private.invalid/secret',401,'secret',{},None)
        result=service.refresh(force=True)
        self.assertTrue(result['stale']);self.assertEqual(result['data']['used'],1)
        self.assertNotIn('secret',str(result));self.assertNotIn('private.invalid',str(result))

class ClaudeUsagePanelTests(unittest.TestCase):
    def test_panel_passes_project_context_to_usage_service(self):
        import watch_kiro
        panel=watch_kiro.Panel.__new__(watch_kiro.Panel)
        panel.injected_services=True
        panel.default_provider='claude'
        panel.claude_usage=mock.Mock()
        panel.claude_usage.snapshot.return_value={'status':'idle'}
        panel.claude_usage.refresh.return_value={'status':'ok'}
        self.assertEqual(panel.account_view('claude',cwd='/project'),{'status':'idle'})
        panel.claude_usage.snapshot.assert_called_once_with(cwd='/project')
        self.assertEqual(panel.usage_refresh('claude',cwd='/project'),(200,{'status':'ok'}))
        panel.claude_usage.refresh.assert_called_once_with(cwd='/project')

    def test_context_rejects_source_from_another_project(self):
        import watch_kiro
        panel=watch_kiro.Panel.__new__(watch_kiro.Panel)
        panel.catalog=mock.Mock()
        panel.source_by_id=mock.Mock(return_value=mock.Mock(cwd=Path('/private/project')))
        panel.group_of=mock.Mock(return_value='other')
        with self.assertRaises(ValueError):panel.context_cwd('requested','source')
        self.assertIsNone(panel.context_cwd('requested','source',strict=False))

if __name__=='__main__':unittest.main()
