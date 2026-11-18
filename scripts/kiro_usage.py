#!/usr/bin/env python3
"""Account credits for the panel, read from Kiro itself.

Source: the Kiro agent server's read-only ACP extension `_kiro/account/getUsage` (the same call behind the CLI's
`/usage`; it asks the Kiro control plane for GetUsageLimits). It is not a model prompt, creates no session and
is not billed. Field mapping (Kiro -> panel):

  data.planName                      -> plan
  data.usageBreakdowns[CREDIT].used  -> used
  data.usageBreakdowns[CREDIT].limit -> limit
  data.usageBreakdowns[].hasLimit    -> has_limit; with used/limit/currentOverages -> coverage
                                         covered | exceeded | unlimited | unknown   ("covered in plan")
  data.billingCycleReset (YYYY-MM-DD, UTC) -> next_reset, next_reset_md (MM-DD)

A field Kiro does not return is listed in `missing` and never invented. Panel turn credits are never added here.
"""
from datetime import date, datetime, timezone
import math
import threading
import time

import kiro_acp

TTL_SECONDS = 180          # cache lifetime (1-5 minutes)
MIN_INTERVAL = 20          # minimum gap between two upstream requests, manual refresh included
ERROR_INTERVAL = 60        # automatic retry gap after a failure
SOURCE = "Kiro agent: _kiro/account/getUsage"


class UsageError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, kiro_acp.scrub(message)


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return float(value)


def parse_usage(result):
    """Panel usage dict from a `_kiro/account/getUsage` result, or UsageError."""
    if not isinstance(result, dict):
        raise UsageError("parse_error", "Kiro 返回的额度数据格式无法识别")
    if result.get("success") is False:
        raise UsageError("usage_failed", "Kiro 未能返回额度：" + str(result.get("message") or "未知原因"))
    data = result.get("data")
    if data is None:
        raise UsageError("managed_by_admin", "账户套餐由管理员托管，Kiro 没有返回额度明细")
    if not isinstance(data, dict) or not isinstance(data.get("usageBreakdowns"), list):
        raise UsageError("parse_error", "Kiro 返回的额度数据格式无法识别")
    credit = None
    for item in data["usageBreakdowns"]:
        if not isinstance(item, dict):
            continue
        label = str(item.get("displayName") or "").strip().lower()
        if str(item.get("resourceType") or "").upper() == "CREDIT" or label in ("credit", "credits"):
            credit = item
            break
    missing = []
    used = limit = overage = None
    has_limit = None
    if credit is None:
        missing += ["used", "limit"]
    else:
        used, limit, overage = number(credit.get("used")), number(credit.get("limit")), number(credit.get("currentOverages"))
        has_limit = credit.get("hasLimit") if isinstance(credit.get("hasLimit"), bool) else None
        if used is None:
            missing.append("used")
        if limit is None or (has_limit and limit <= 0):
            missing.append("limit")
            limit = None
        if has_limit is False:
            limit = None  # Kiro reports a sentinel limit for plans without a cap; it is not a real total
    if used is None or has_limit is None:
        coverage = "unknown"
    elif has_limit is False:
        coverage = "unlimited"
    elif limit is None:
        coverage = "unknown"
    else:
        coverage = "exceeded" if used > limit or (overage or 0) > 0 else "covered"
    reset = None
    raw_reset = data.get("billingCycleReset")
    if isinstance(raw_reset, str):
        try:
            reset = date.fromisoformat(raw_reset.strip()[:10]).isoformat() if len(raw_reset.strip()) == 10 else None
        except ValueError:
            reset = None
    if reset is None:
        missing.append("next_reset")
    plan = data.get("planName") if isinstance(data.get("planName"), str) and data["planName"] != "Unknown" else None
    if plan is None:
        missing.append("plan")
    return {"plan": plan, "used": used, "limit": limit, "unit": "credits", "has_limit": has_limit, "coverage": coverage,
            "overage": overage, "overages_enabled": data.get("overagesEnabled") if isinstance(data.get("overagesEnabled"), bool) else None,
            "percent": round(used / limit * 100, 1) if used is not None and limit else None,
            "next_reset": reset, "next_reset_md": reset[5:] if reset else None, "missing": missing}


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat() if epoch else None


class AcpUsageFetcher:
    def __init__(self, environment, command=None, token_provider=None, timeout=25):
        self.environment, self.command, self.token_provider, self.timeout = environment, command, token_provider, timeout

    def __call__(self):
        try:
            command = self.command or kiro_acp.server_command(self.environment)
            with kiro_acp.AcpClient(command, self.environment, self.token_provider) as client:
                client.initialize(self.timeout)
                return client.request("_kiro/account/getUsage", {}, self.timeout)
        except kiro_acp.AcpError as error:
            messages = {"timeout": "读取账户额度超时，请检查网络或代理后重试",
                        "server_exited": "Kiro 服务异常退出，未能读取账户额度"}
            raise UsageError(error.code, messages.get(error.code, error.message))


class UsageService:
    """Cached, rate-limited account usage. Errors keep the last good data and mark it stale."""

    def __init__(self, fetcher, clock=time.time, ttl=TTL_SECONDS, min_interval=MIN_INTERVAL, error_interval=ERROR_INTERVAL,
                 background=True):
        self.fetcher, self.clock, self.ttl = fetcher, clock, ttl
        self.min_interval, self.error_interval, self.background = min_interval, error_interval, background
        self.lock = threading.Lock()
        self.flight = threading.Lock()  # single flight: one upstream request at a time
        self.data = None
        self.fetched_at = None
        self.error = None
        self.last_attempt = None
        self.refreshing = False

    def snapshot(self, rate_limited=False):
        now = self.clock()
        with self.lock:
            data, error, fetched = self.data, self.error, self.fetched_at
            if data is None:
                status = "error" if error else "loading"
            else:
                status = "stale" if error else "ok"
            retry = 0
            if rate_limited and self.last_attempt is not None:
                retry = max(1, math.ceil(self.min_interval - (now - self.last_attempt)))
            return {"status": status, "data": dict(data) if data else None, "fetched_at": iso(fetched),
                    "age_seconds": int(now - fetched) if fetched else None, "stale": bool(data and error),
                    "error": dict(error) if error else None, "refreshing": self.refreshing,
                    "rate_limited": bool(rate_limited), "retry_after": retry, "ttl_seconds": self.ttl, "source": SOURCE}

    def due(self):
        now = self.clock()
        with self.lock:
            if self.refreshing:
                return False
            if self.last_attempt is not None:
                gap = self.error_interval if self.error else self.min_interval
                if now - self.last_attempt < gap:
                    return False
            return self.fetched_at is None or now - self.fetched_at >= self.ttl or bool(self.error)

    def poll(self):
        """Called on every panel poll: return the cache and start a background refresh when it is due."""
        if self.background and self.due():
            threading.Thread(target=self.fetch, daemon=True).start()
        return self.snapshot()

    def refresh(self):
        """Manual refresh (blocking). Too-frequent requests are answered from the cache with `rate_limited`."""
        now = self.clock()
        with self.lock:
            limited = self.last_attempt is not None and now - self.last_attempt < self.min_interval and not self.refreshing
        if limited:
            return self.snapshot(rate_limited=True)
        self.fetch()
        return self.snapshot()

    def fetch(self):
        with self.lock:
            if self.refreshing:
                waiting = True
            else:
                waiting = False
                self.refreshing = True
                self.last_attempt = self.clock()
        if waiting:
            with self.flight:  # another request is in flight: wait for it instead of starting a second one
                return
        with self.flight:
            try:
                data = parse_usage(self.fetcher())
                with self.lock:
                    self.data, self.fetched_at, self.error = data, self.clock(), None
            except UsageError as error:
                with self.lock:
                    self.error = {"code": error.code, "message": error.message, "at": iso(self.clock())}
            except Exception as error:  # a fetcher bug must never take the panel down
                with self.lock:
                    self.error = {"code": "internal", "message": kiro_acp.scrub("读取额度时出现内部错误：" + type(error).__name__),
                                  "at": iso(self.clock())}
            finally:
                with self.lock:
                    self.refreshing = False


def main():
    import json
    import run_kiro
    service = UsageService(AcpUsageFetcher(run_kiro.kiro_environment()), background=False)
    print(json.dumps(service.refresh(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
