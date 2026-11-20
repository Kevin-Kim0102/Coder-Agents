"""Bounded, credential-redacted error summaries shared by runners and panel.

Categories describe evidence in the CLI response; unknown errors remain unknown.
Never pass the complete user's settings or authentication documents to this module.
"""
import json
import os
import re

_SECRET_NAME = r'(?:[\w-]{0,64}(?:api[_-]?key|auth[_-]?token|access[_-]?token|refresh[_-]?token|password|secret|authorization|credential)[\w-]{0,64}|token)'
_ENV_SECRET = re.compile(_SECRET_NAME + r'$', re.I)
_ASSIGN = re.compile(r'(?i)(?<![\w-])([\"\']?' + _SECRET_NAME + r'[\"\']?\s*[:=]\s*)(?:"[^"\n]*"|\'[^\'\n]*\'|[^\s,;&}\]\n]+)')
_HEADER = re.compile(r'(?im)((?:authorization|proxy-authorization|x-api-key|api-key)\s*:\s*)[^\r\n]+')


def redact(text, limit=4000, secrets=()):
    """Scrub before bounding so truncation cannot leave half a credential visible."""
    if not isinstance(text, str):
        text = str(text or '')
    known = list(secrets) + [value for key, value in os.environ.items() if _ENV_SECRET.fullmatch(key)]
    for secret in sorted((v for v in known if isinstance(v, str) and len(v) >= 4), key=len, reverse=True):
        text = text.replace(secret, '[REDACTED]')
    text = _HEADER.sub(lambda match: match[1] + '[REDACTED]', text)
    text = re.sub(r'(?i)\b(Bearer|Basic)\s+[^\s,;"\']+', r'\1 [REDACTED]', text)
    text = _ASSIGN.sub(lambda match: match[1] + '[REDACTED]', text)
    text = re.sub(r'(?i)(--(?:api-key|auth-token|access-token|password|secret)\s+)[^\s]+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)(https?://)[^/@\s]+:[^/@\s]+@', r'\1[REDACTED]@', text)
    text = re.sub(r'\bsk-(?:ant-)?[A-Za-z0-9_-]+', '[REDACTED]', text)
    return text[-max(0, min(int(limit), 131072)):]


_MESSAGES = {
    'auth': ('CLI 登录或 API 认证失败', '重新登录 CLI，或检查当前请求地址的认证配置。'),
    'quota': ('请求受到额度或速率限制', '检查账户额度和限流信息，确认后再由主 Agent 决定重试。'),
    'network': ('无法完成网络请求', '检查网络、代理和当前 API 地址；保留已有改动，不自动重试任务。'),
    'model': ('当前请求地址拒绝了模型选择', '重新读取该地址的模型列表，并检查角色或对话所选模型。'),
    'permission': ('模型已调用，但任务所需工具权限不足', '查看被拒绝的工具；由主 Agent 在本次授权范围内配置权限后继续，不绕过权限。'),
    'timeout': ('任务或请求已超时', '先检查任务状态、日志和已有改动，再决定继续或调整超时。'),
    'interrupted': ('任务已中断', '检查已有改动与测试结果，由主 Agent 决定续聊或创建新任务。'),
    'unknown': ('执行失败，尚无法确定原因', '复制诊断交给主 Agent，结合任务日志检查；不要自动重新提交。'),
}


def diagnose(text='', state=None):
    state = state if isinstance(state, dict) else {}
    denials = state.get('permission_denials')
    extra = ''
    if isinstance(denials, list) and denials:
        extra = '\n被拒绝工具：' + ', '.join(str(item.get('tool_name', '未知工具'))[:80]
                                        for item in denials[:20] if isinstance(item, dict))
    detail = redact(str(text or '') + extra)
    evidence = detail.lower()
    if extra:
        category = 'permission'
    elif re.search(r'\b401\b|unauthori[sz]ed|authentication_error|invalid.{0,12}(?:api.?key|token)|not.logged.in|登录.{0,4}(?:失效|失败|过期)', evidence):
        category = 'auth'
    elif re.search(r'\b429\b|rate.?limit|insufficient.{0,12}(?:quota|credit)|quota|credit.balance|billing|额度不足', evidence):
        category = 'quota'
    elif re.search(r'permission.{0,15}(?:denied|refused)|not.allowed|tool.{0,12}denied|权限.{0,5}(?:不足|拒绝)', evidence):
        category = 'permission'
    elif re.search(r'timed?.?out|timeout|超时', evidence):
        category = 'timeout'
    elif re.search(r'model[_ -]not[_ -]found|unknown.model|model.{0,30}(?:unavailable|not.supported|does.not.exist|not.available)|invalid.model', evidence):
        category = 'model'
    elif re.search(r'econn|enotfound|eai_again|connection.{0,12}(?:error|reset|refused)|network|dns|ssl|tls|证书|网络', evidence):
        category = 'network'
    elif state.get('status') == 'cancelled' or re.search(r'cancelled|canceled|interrupted|中断', evidence):
        category = 'interrupted'
    else:
        category = 'unknown'
    message, next_step = _MESSAGES[category]
    return {'category': category, 'message': message, 'next_step': next_step, 'detail': detail}
