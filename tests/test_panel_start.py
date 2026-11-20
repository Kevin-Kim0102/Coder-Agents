"""Panel background lifecycle; isolated config, no browser launches or model calls."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from coder_agents import cli, panel_lifecycle, service


class PanelStartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="coder-panel-start-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.config_path = self.base / "state/config.json"
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.base), "CODER_AGENTS_CONFIG": str(self.config_path)}, clear=False)
        patcher.start(); self.addCleanup(patcher.stop)
        self.config = service.new_config(47831)

    def test_first_start_creates_config_and_background_start_returns_public_url(self):
        with mock.patch.object(panel_lifecycle, "start_panel", return_value="http://127.0.0.1:47831/") as start:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(["start"]), 0)
        config = json.loads(self.config_path.read_text())
        self.assertIs(config["auto_cleanup_enabled"], False)
        self.assertEqual(self.config_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(start.call_args.args[0], self.config_path.resolve())
        self.assertNotIn(config["token"], output.getvalue())
        self.assertIn("http://127.0.0.1:47831/", output.getvalue())

    def test_reuses_only_authenticated_panel_and_refuses_foreign_listener(self):
        with mock.patch.object(panel_lifecycle, "panel_ready", return_value=True), mock.patch("subprocess.Popen") as spawn:
            self.assertEqual(panel_lifecycle.start_panel(self.config_path, self.config), "http://127.0.0.1:47831/")
            spawn.assert_not_called()
        with mock.patch.object(panel_lifecycle, "panel_ready", return_value=False), \
             mock.patch.object(service, "port_in_use", return_value=True), mock.patch("subprocess.Popen") as spawn:
            with self.assertRaisesRegex(ValueError, "占用"):
                panel_lifecycle.start_panel(self.config_path, self.config)
            spawn.assert_not_called()

    def test_detached_process_private_log_and_readiness_check(self):
        process = mock.Mock(); process.poll.return_value = None
        with mock.patch.object(panel_lifecycle, "panel_ready", side_effect=[False, False, True]), \
             mock.patch.object(service, "port_in_use", return_value=False), \
             mock.patch("subprocess.Popen", return_value=process) as spawn, mock.patch("time.sleep"):
            url = panel_lifecycle.start_panel(self.config_path, self.config)
        self.assertEqual(url, "http://127.0.0.1:47831/")
        command = spawn.call_args.args[0]; options = spawn.call_args.kwargs
        self.assertIn("--serve", command); self.assertIn(str(self.config_path), command)
        self.assertEqual(options["stdin"], subprocess.DEVNULL)
        self.assertTrue(options["start_new_session"])
        self.assertEqual(options["stderr"], subprocess.STDOUT)
        self.assertEqual((self.config_path.parent / "panel.log").stat().st_mode & 0o777, 0o600)
        self.assertNotIn(self.config["token"], " ".join(command))

    def test_failed_child_and_timeout_never_report_success(self):
        for exit_code in (2, None):
            with self.subTest(exit_code=exit_code), \
                 mock.patch.object(panel_lifecycle, "panel_ready", return_value=False), \
                 mock.patch.object(service, "port_in_use", return_value=False), \
                 mock.patch("subprocess.Popen") as spawn, mock.patch("time.sleep"):
                spawn.return_value.poll.return_value = exit_code
                with self.assertRaisesRegex(ValueError, "日志|就绪"):
                    panel_lifecycle.start_panel(self.config_path, self.config, timeout=0.01)
                if exit_code is None:
                    spawn.return_value.terminate.assert_called_once()

    def test_start_rejects_bad_port_without_writing_or_spawning(self):
        with mock.patch("subprocess.Popen") as spawn, contextlib.redirect_stderr(io.StringIO()):
            self.assertNotEqual(cli.main(["start", "--port", "1"]), 0)
        self.assertFalse(self.config_path.exists()); spawn.assert_not_called()

    def test_background_panel_survives_cli_exit_reuses_then_stops(self):
        import socket
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
        env = {**os.environ, "HOME": str(self.base), "XDG_CONFIG_HOME": str(self.base / "cfg"),
               "CODER_AGENTS_CONFIG": str(self.config_path), "PYTHONDONTWRITEBYTECODE": "1"}
        def command(*args):
            return subprocess.run([sys.executable, "-m", "coder_agents", *args], cwd=ROOT, env=env,
                                  capture_output=True, text=True, timeout=20)
        try:
            first = command("start", "--port", str(port))
            self.assertEqual(first.returncode, 0, first.stderr)
            config = json.loads(self.config_path.read_text())
            self.assertTrue(panel_lifecycle.panel_ready(config))
            self.assertNotIn(config["token"], first.stdout + first.stderr)
            before = self.config_path.read_bytes()
            second = command("start")
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(second.stdout.strip(), f"http://127.0.0.1:{port}/")
            self.assertEqual(self.config_path.read_bytes(), before)
            wrong = {**config, "token": "wrong" * 10}
            self.assertFalse(panel_lifecycle.panel_ready(wrong))
            stopped = command("stop")
            self.assertEqual(stopped.returncode, 0, stopped.stderr)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and service.port_in_use(port): time.sleep(0.05)
            self.assertFalse(service.port_in_use(port))
            self.assertTrue(self.config_path.exists())
            self.assertEqual((self.config_path.parent / "panel.log").stat().st_mode & 0o777, 0o600)
        finally:
            if self.config_path.exists(): command("stop")


if __name__ == "__main__": unittest.main()
