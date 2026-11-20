"""Read-only Sub2API-compatible usage from the effective Claude API endpoint."""
from copy import deepcopy
from datetime import datetime, timezone
import math
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request
import claude_catalog


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def reset_time(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.isoformat() if parsed.tzinfo is not None else None
    except ValueError:
        return None


def normalize(raw):
    if not isinstance(raw, dict):
        raise ValueError('invalid usage')
    unit = {'usd': 'USD', 'credits': 'Credits', 'requests': '请求'}.get(str(raw.get('unit', 'USD')).lower(), '')
    result = {'unit': unit, 'next_reset_at': None, 'next_reset_md': None, 'coverage': None}
    subscription = raw.get('subscription')
    if isinstance(subscription, dict):
        for period in ('daily', 'weekly', 'monthly'):
            used, limit = subscription.get(period + '_usage_usd'), subscription.get(period + '_limit_usd')
            if number(limit) and limit > 0 and number(used):
                return {**result, 'unit': 'USD', 'used': used, 'limit': limit, 'period': period,
                        'next_reset_at': reset_time(subscription.get(period + '_reset_at'))}
    quota = raw.get('quota')
    if isinstance(quota, dict) and number(quota.get('used')) and number(quota.get('limit')):
        return {**result, 'used': quota['used'], 'limit': quota['limit'], 'period': 'total',
                'next_reset_at': reset_time(quota.get('reset_at') or raw.get('reset_at'))}
    if number(raw.get('balance')):
        return {**result, 'balance': raw['balance'], 'period': 'balance'}
    raise ValueError('invalid usage')


class UsageService:
    def __init__(self, fetch=None):
        self.fetch = fetch or claude_catalog.fetch_json
        self.cache = {}
        self.lock = threading.Lock()

    def snapshot(self, cwd=None):
        return self._get(cwd, False, False)

    def refresh(self, cwd=None, force=False):
        return self._get(cwd, True, force)

    def _get(self, cwd, refresh, force):
        with self.lock:
            try:
                config = claude_catalog.resolve_config(cwd)
            except ValueError:
                return {'provider': 'claude', 'status': 'unsupported', 'data': None,
                        'message': '当前 Claude 认证方式暂不支持 API 额度查询'}
            key = config['cache_key']
            old = self.cache.get(key)
            if not refresh:
                return deepcopy(old or {'provider': 'claude', 'status': 'idle', 'data': None,
                                        'message': '点击刷新查询当前 Claude API 额度'})
            now = time.time()
            if old and not force and now - old.get('attempted_at', 0) < 15:
                return {**deepcopy(old), 'rate_limited': True, 'retry_after': max(1, math.ceil(15 - (now - old['attempted_at'])))}
            result = {'provider': 'claude', 'status': 'error', 'data': old.get('data') if old else None,
                      'fetched_at': old.get('fetched_at') if old else None, 'attempted_at': now}
            try:
                base = config['base_url'].rstrip('/')
                url = base + ('/usage' if base.endswith('/v1') else '/v1/usage')
                auth = config['headers'].get('Authorization') or 'Bearer ' + config['headers'].get('x-api-key', '')
                raw = self.fetch(Request(url, headers={'Accept': 'application/json', 'Authorization': auth}),
                                 timeout=10, proxies=config['proxies'])
                result.update(status='ok', data=normalize(raw), fetched_at=datetime.now(timezone.utc).isoformat())
            except HTTPError as error:
                message = ('API 额度查询认证失败' if error.code in (401, 403) else
                           '当前 API 不支持此额度查询协议' if error.code in (404, 405) else 'API 额度查询失败')
                result['error'] = {'message': message}
            except (ValueError, TypeError, KeyError):
                result['error'] = {'message': '当前 API 未返回支持的额度字段'}
            except Exception:
                result['error'] = {'message': 'API 额度查询失败，请检查网络或服务状态'}
            result['stale'] = result['status'] != 'ok' and result['data'] is not None
            self.cache[key] = result
            while len(self.cache) > 20:
                self.cache.pop(next(iter(self.cache)))
            return deepcopy(result)
