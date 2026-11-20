"""Global role config and delegation, isolated from personal config and real model calls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import agent_roles
import run_agent


class RoleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="coder-roles-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.env = {"HOME": str(self.base), "XDG_CONFIG_HOME": str(self.base / "config"),
                    "PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"}
        patcher = mock.patch.dict(os.environ, self.env, clear=True)
        patcher.start(); self.addCleanup(patcher.stop)
        self.store = agent_roles.RoleStore()

    def custom(self):
        return {**self.store.get("coder"), "id": "docs", "name": "文档", "description": "维护文档"}

    def test_continuation_uses_latest_role_and_refuses_disabled_or_changed_provider(self):
        role = self.store.get('coder')
        role.update(name='新版编码', instructions='仅修改授权文件', model='opus', effort='high')
        self.store.mutate({'action': 'save', 'role': role, 'revision': self.store.snapshot()['revision']})
        actual, prompt = agent_roles.continuation('coder', 'claude', '修复问题', store=self.store)
        self.assertEqual(actual['model'], 'opus')
        self.assertIn('仅修改授权文件', prompt)
        with self.assertRaisesRegex(ValueError, '新建'):
            agent_roles.continuation('coder', 'kiro', '修复问题', store=self.store)
        role['enabled'] = False
        self.store.mutate({'action': 'save', 'role': role, 'revision': self.store.snapshot()['revision']})
        with self.assertRaisesRegex(ValueError, '停用'):
            agent_roles.continuation('coder', 'claude', '修复问题', store=self.store)

    def test_dispatch_sets_identity_only_and_direct_calls_clear_inherited_identity(self):
        class Executed(Exception): pass
        captured = []
        def execute(*args):
            captured.append((os.environ.get('CODER_AGENTS_ROLE_ID'), os.environ.get('CODER_AGENTS_ROLE_NAME')))
            raise Executed
        with mock.patch.object(run_agent.sys, 'stdin', mock.Mock(read=lambda: 'task')), \
                mock.patch.object(run_agent.os, 'dup2'), mock.patch.object(run_agent.os, 'execv', side_effect=execute):
            with self.assertRaises(Executed): run_agent.main(['--role', 'coder', '--cwd', str(self.base)])
            self.assertEqual(captured[-1], ('coder', '编码'))
            with self.assertRaises(Executed): run_agent.main(['--provider', 'claude', '--cwd', str(self.base)])
            self.assertEqual(captured[-1], (None, None))

    def test_direct_claude_uses_saved_defaults_but_preserves_explicit_selection_and_roles(self):
        class Executed(Exception): pass
        calls = []
        def execute(binary, command):
            calls.append(command)
            raise Executed
        with mock.patch.object(run_agent.Connections, 'defaults', return_value={'model': 'custom-model', 'effort': 'high'}), \
                mock.patch.object(run_agent.os, 'execv', side_effect=execute):
            for extra, expected in [([], ['--model', 'custom-model', '--effort', 'high']),
                    (['--effort', 'low'], ['--effort', 'low', '--model', 'custom-model']),
                    (['--no-effort'], ['--no-effort', '--model', 'custom-model']),
                    (['--model=opus'], ['--model=opus']),
                    (['--model', 'opus', '--effort', 'low'], ['--model', 'opus', '--effort', 'low'])]:
                with self.assertRaises(Executed): run_agent.main(['--provider', 'claude', *extra])
                self.assertEqual(calls[-1][2:], expected)
            with mock.patch.object(run_agent.sys, 'stdin', mock.Mock(read=lambda: 'task')), mock.patch.object(run_agent.os, 'dup2'):
                with self.assertRaises(Executed): run_agent.main(['--role', 'coder'])
            self.assertIn('sonnet', calls[-1]); self.assertNotIn('custom-model', calls[-1])

    def test_direct_resume_prefers_original_session_pair_in_same_workdir(self):
        import agent_tasks
        class Executed(Exception): pass
        calls = []
        def execute(binary, command): calls.append(command); raise Executed
        previous = {'provider':'claude', 'session_id':'session', 'cwd':str(self.base),
                    'model':'original-model', 'effort':'low'}
        with mock.patch.object(agent_tasks, 'list_tasks', return_value=[previous]), \
                mock.patch.object(run_agent.Connections, 'defaults', return_value={'model':'new-default', 'effort':'high'}), \
                mock.patch.object(run_agent.os, 'execv', side_effect=execute):
            with self.assertRaises(Executed): run_agent.main(['--provider','claude','--resume-id','session','--cwd',str(self.base)])
            self.assertEqual(calls[-1][-4:], ['--model','original-model','--effort','low'])
            with self.assertRaises(Executed): run_agent.main(['--provider','claude','--resume-id','session','--cwd','/different'])
            self.assertEqual(calls[-1][-4:], ['--model','new-default','--effort','high'])

    def test_default_read_is_global_and_has_no_write_or_process_side_effect(self):
        with mock.patch("subprocess.run", side_effect=AssertionError("process")):
            data = self.store.snapshot()
        self.assertEqual(self.store.path, self.base / "config/coder-agents/roles.json")
        self.assertEqual([r["id"] for r in data["roles"]], ["coder", "tester"])
        self.assertFalse(self.store.path.exists())
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": ""}):
            self.assertEqual(agent_roles.roles_path(), self.base / ".config/coder-agents/roles.json")

    def test_crud_atomic_private_and_disabled_role_is_not_executable(self):
        first = self.store.snapshot()
        saved = self.store.mutate({"action": "save", "role": self.custom(), "revision": first["revision"]})
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(agent_roles.RoleStore().get("docs")["name"], "文档")
        role = self.store.get("docs"); role["enabled"] = False
        saved = self.store.mutate({"action": "save", "role": role, "revision": saved["revision"]})
        with self.assertRaisesRegex(ValueError, "停用"):
            self.store.get("docs", executable=True)
        self.store.mutate({"action": "delete", "id": "docs", "revision": saved["revision"]})
        self.assertEqual(len(self.store.snapshot()["roles"]), 2)
        self.assertEqual(set(json.loads(self.store.path.read_text())), {"version", "roles"})

    def test_invalid_and_stale_updates_never_replace_saved_data(self):
        first = self.store.snapshot()
        self.store.mutate({"action": "save", "role": self.custom(), "revision": first["revision"]})
        before = self.store.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "更新"):
            self.store.mutate({"action": "save", "role": self.custom(), "revision": first["revision"]})
        for changed in ({"id": "../escape"}, {"provider": "unknown"}, {"enabled": "yes"},
                        {"model": "--evil"}, {"effort": "unknown"}, {"name": ""},
                        {"permissions": {"bypass": True}}, {"constraints": "not-array"}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.store.mutate({"action": "save", "role": {**self.custom(), **changed},
                                   "revision": self.store.snapshot()["revision"]})
        self.assertEqual(before, self.store.path.read_bytes())
        with self.assertRaisesRegex(ValueError, "内置"):
            self.store.mutate({"action": "delete", "id": "coder", "revision": self.store.snapshot()["revision"]})

    def test_corrupt_config_is_not_reset(self):
        self.store.path.parent.mkdir(parents=True)
        self.store.path.write_text('{broken')
        with self.assertRaises(ValueError): self.store.snapshot()
        with self.assertRaises(ValueError):
            self.store.mutate({"action": "save", "role": {"id": "x"}, "revision": "missing"})
        self.assertEqual(self.store.path.read_text(), '{broken')

    def test_cli_json_is_latest_and_disabled_hidden_by_default(self):
        def cli(*args):
            return subprocess.run([sys.executable, "-m", "coder_agents", "roles", *args],
                                  cwd=ROOT, capture_output=True, text=True, env=self.env, timeout=10)
        self.assertEqual([r["id"] for r in json.loads(cli("list").stdout)["roles"]], ["coder", "tester"])
        role = self.store.get("tester"); role["enabled"] = False
        self.store.mutate({"action": "save", "role": role, "revision": self.store.snapshot()["revision"]})
        self.assertEqual(len(json.loads(cli("list").stdout)["roles"]), 1)
        self.assertEqual(len(json.loads(cli("list", "--all").stdout)["roles"]), 2)
        self.assertFalse(json.loads(cli("show", "tester").stdout)["enabled"])
        self.assertNotEqual(cli("show", "missing").returncode, 0)

    def test_role_dispatch_applies_provider_model_and_prompt(self):
        role = self.custom(); role.update(provider="kiro", model="claude-sonnet-5.5", effort=None)
        self.store.mutate({"action": "save", "role": role, "revision": self.store.snapshot()["revision"]})
        provider, args, prompt = run_agent.role_invocation("docs", ["--cwd", "/project"], "本次任务")
        self.assertEqual(provider, "kiro")
        self.assertEqual(args, ["--cwd", "/project", "--model", "claude-sonnet-5.5", "--no-effort"])
        self.assertIn("维护文档", prompt); self.assertIn("本次任务", prompt)
        for args in (["--model=sonnet"], ["--effort", "high"], ["--check"], ["--cli-path", "/other"]):
            with self.assertRaises(ValueError): run_agent.role_invocation("coder", args, "task")
        with self.assertRaises(ValueError): run_agent.role_invocation("missing", [], "task")
        with self.assertRaises(ValueError): run_agent.role_invocation("coder", [], " ")

    def test_role_dispatch_reaches_real_runner_with_fake_cli(self):
        from tests.test_claude import FAKE
        binary = self.base / "claude"; binary.write_text(FAKE); binary.chmod(0o700)
        capture = self.base / "capture.json"
        env = {**self.env, "PATH": str(self.base) + os.pathsep + self.env["PATH"], "CAPTURE": str(capture),
               "CODER_AGENTS_CONFIG": str(self.base / "state/config.json")}
        directory = self.base / "run"
        done = subprocess.run([sys.executable, "-m", "coder_agents", "run", "--role", "tester",
                               "--cwd", str(self.base), "--run-dir", str(directory)], input="准备阶段：编写登录测试，不运行。",
                              capture_output=True, text=True, env=env, cwd=ROOT, timeout=15)
        self.assertEqual(done.returncode, 0, done.stderr)
        actual = json.loads(capture.read_text())
        self.assertIn("准备阶段：编写登录测试，不运行。", actual["prompt"])
        self.assertIn("不修改业务实现", actual["prompt"])
        self.assertEqual(actual["args"][actual["args"].index("--model") + 1], "sonnet")
        self.assertEqual(json.loads((directory / "state.json").read_text())["status"], "completed")
        self.assertFalse(any("snapshot" in p.name or "role" in p.name for p in directory.iterdir()))

    def test_role_api_requires_auth_and_same_origin_and_preserves_store_on_failure(self):
        import http.client
        from http.server import ThreadingHTTPServer
        from types import SimpleNamespace
        import threading
        import watch_kiro
        panel = SimpleNamespace(port=0, prefix="/secret/", last_seen=0, cookie_name="panel", cookie_value="value", roles=self.store)
        server = ThreadingHTTPServer(("127.0.0.1", 0), watch_kiro.make_handler(panel))
        panel.port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        conn = http.client.HTTPConnection("127.0.0.1", panel.port, timeout=5); self.addCleanup(conn.close)
        headers = {"Cookie": "panel=value", "Origin": "http://127.0.0.1:" + str(panel.port), "Content-Type": "application/json"}
        def request(method, body=None, hdrs=None):
            conn.request(method, "/api/roles", json.dumps(body) if body else None, headers=hdrs or {})
            response = conn.getresponse(); raw = response.read()
            return response.status, json.loads(raw) if raw.startswith(b'{') else None
        self.assertEqual(request("GET")[0], 401)
        status, data = request("GET", hdrs=headers)
        self.assertEqual(status, 200); self.assertEqual(len(data["roles"]), 2)
        payload = {"action": "save", "role": self.custom(), "revision": data["revision"]}
        self.assertEqual(request("POST", payload)[0], 401)
        self.assertEqual(request("POST", payload, {"Cookie": "panel=value", "Content-Type": "application/json"})[0], 403)
        self.assertFalse(self.store.path.exists())
        status, data = request("POST", payload, headers)
        self.assertEqual(status, 200); self.assertEqual(len(data["roles"]), 3)
        before = self.store.path.read_bytes()
        self.assertEqual(request("POST", payload, headers)[0], 400)
        self.assertEqual(self.store.path.read_bytes(), before)


if __name__ == "__main__": unittest.main()
