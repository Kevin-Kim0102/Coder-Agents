"""Cross-provider lifecycle and registration, isolated from personal task state."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import agent_tasks


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve(); self.directory = self.base / 'run'; self.directory.mkdir()
        self.patch = mock.patch.dict(os.environ, {'CODER_AGENTS_CONFIG': str(self.base / 'state/config.json')})
        self.patch.start(); self.addCleanup(self.patch.stop)

    def state(self, **changes):
        state = {'id': 'run', 'status': 'running', 'runner_identity': agent_tasks.process_identity(os.getpid()), **changes}
        (self.directory / 'state.json').write_text(json.dumps(state)); return state

    def test_live_task_is_not_recovered_or_interrupted(self):
        state = self.state()
        self.assertEqual(agent_tasks.recover(self.directory, state)['status'], 'running')
        self.assertTrue(agent_tasks.process_identity(os.getpid())['start'])

    def test_dead_runner_recovers_to_cancelled_and_persists(self):
        state = self.state(runner_identity={'pid': 99999999, 'start': 'gone', 'boot': agent_tasks.boot_identity()})
        result = agent_tasks.recover(self.directory, state)
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(result['interruption_reason'], 'runner_exited')
        self.assertEqual(json.loads((self.directory / 'state.json').read_text())['status'], 'cancelled')
        self.assertIn('中断', (self.directory / 'stderr.log').read_text())
        self.assertEqual(agent_tasks.stop(self.directory)['status'], 'cancelled')

    def test_reused_pid_is_recovered_never_signalled(self):
        identity = agent_tasks.process_identity(os.getpid()); identity['start'] = 'different'
        state = self.state(runner_identity=identity)
        with mock.patch.object(os, 'kill', wraps=os.kill) as kill:
            self.assertEqual(agent_tasks.stop(self.directory)['status'], 'cancelled')
            self.assertFalse(any(call.args[1] != 0 for call in kill.call_args_list))

    def test_unknown_legacy_identity_remains_conservative(self):
        state = self.state(runner_identity=None)
        self.assertEqual(agent_tasks.recover(self.directory, state)['status'], 'running')
        with self.assertRaisesRegex(ValueError, '身份'):
            agent_tasks.stop(self.directory)

    def test_dead_runner_with_live_child_does_not_claim_task_finished(self):
        state = self.state(runner_identity={'pid': 99999999, 'start': 'gone', 'boot': agent_tasks.boot_identity()},
                           child_identity=agent_tasks.process_identity(os.getpid()))
        self.assertEqual(agent_tasks.recover(self.directory, state)['status'], 'running')

    def test_index_keeps_concurrent_sources_and_deduplicates(self):
        processes = []
        script = str(Path(agent_tasks.__file__).resolve().parent)
        for i in range(5):
            code = "import sys;sys.path.insert(0,sys.argv[1]);import agent_tasks;agent_tasks.register(sys.argv[2],sys.argv[3])"
            processes.append(subprocess.Popen([sys.executable, '-c', code, script, str(self.base / ('p'+str(i))), str(self.base / ('r'+str(i)))], env=os.environ.copy()))
        self.assertTrue(all(p.wait(timeout=10) == 0 for p in processes))
        agent_tasks.register(self.base/'p0', self.base/'r0')
        self.assertEqual(len(agent_tasks.registered_sources()), 5)
        self.assertEqual(agent_tasks.index_path().stat().st_mode & 0o777, 0o600)

    def test_stop_verified_child_process_and_is_idempotent(self):
        process = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'], start_new_session=True)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        self.state(runner_identity=agent_tasks.process_identity(process.pid))
        result = agent_tasks.stop(self.directory, timeout=.3)
        process.wait(timeout=5)
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(agent_tasks.stop(self.directory)['status'], 'cancelled')

    def test_previous_boot_is_interrupted_without_signalling(self):
        identity = agent_tasks.process_identity(os.getpid()); identity['boot'] = 'old-boot'
        state = self.state(runner_identity=identity)
        self.assertEqual(agent_tasks.recover(self.directory, state)['interruption_reason'], 'previous_boot')

    def test_legacy_definitely_absent_pid_can_recover_but_live_unverified_pid_cannot(self):
        state = self.state(runner_identity=None, runner_pid=99999999)
        self.assertEqual(agent_tasks.recover(self.directory, state)['status'], 'cancelled')
        state = self.state(runner_identity=None, runner_pid=os.getpid())
        self.assertEqual(agent_tasks.recover(self.directory, state)['status'], 'running')

    def test_show_returns_final_result_and_kiro_final_event(self):
        self.state(status='completed')
        (self.directory / 'result.txt').write_text('Claude result')
        self.assertEqual(agent_tasks.read(self.directory)['result'], 'Claude result')
        (self.directory / 'result.txt').unlink()
        (self.directory / 'events.jsonl').write_text(json.dumps({'type':'runFinished','data':{'finalText':'Kiro result'}}))
        self.assertEqual(agent_tasks.read(self.directory)['result'], 'Kiro result')

    def test_kiro_deadline_stops_real_cli_and_its_process_group(self):
        import run_kiro
        binary = self.base / 'fake-kiro'
        binary.write_text('#!' + sys.executable + '\nimport json,sys,time\n'
                          'if "--list-models" in sys.argv:\n print(json.dumps({"models":[{"model_id":"claude-sonnet-5.5"}]}));sys.exit(0)\n'
                          'print("{}",flush=True)\ntime.sleep(30)\n')
        binary.chmod(0o700)
        directory = self.base / 'kiro-run'
        record = run_kiro.RunRecord(self.base, 'task', directory)
        def environment(env, *args, **kwargs): return env
        with mock.patch.object(run_kiro, 'relay_environment', side_effect=environment), mock.patch.object(run_kiro, 'read_status', return_value={}):
            with self.assertRaisesRegex(TimeoutError, 'timeout'):
                try: run_kiro.execute(str(binary), self.base, 'task', os.environ.copy(), False, record, timeout=2)
                finally: record.finish(1)
        identity = record.state['child_identity']
        self.assertNotEqual(agent_tasks.identity_status(identity), 'alive')

    def test_dead_leaders_with_orphan_group_members_remain_active(self):
        child = {'pid': 40001, 'pgid': 40001, 'start': 'old', 'boot': 'boot'}
        state = self.state(runner_identity={'pid': 40002, 'start':'old','boot':'boot'}, child_identity=child)
        with mock.patch.object(agent_tasks, 'identity_status', return_value='dead'), \
             mock.patch.object(agent_tasks, 'group_members', return_value=[{'pid':40003,'start':'tool','boot':'boot','pgid':40001}]):
            self.assertEqual(agent_tasks.recover(self.directory, state)['status'], 'running')

    def test_stop_kills_verified_surviving_members_after_leader_exits(self):
        runner = {'pid': 50001, 'start':'r','boot':'boot'}
        child = {'pid': 50002, 'pgid':50002, 'start':'c','boot':'boot'}
        member = {'pid':50003, 'pgid':50002, 'start':'m','boot':'boot'}
        self.state(runner_identity=runner, child_identity=child)
        states = {50001:'alive',50002:'alive',50003:'alive'}
        sent = []
        def status(identity): return states.get((identity or {}).get('pid'), 'unknown')
        def kill(pid, sig):
            sent.append((pid,sig))
            if sig: states[pid] = 'dead'
        def killpg(pid, sig):
            sent.append(('group',pid,sig));states[pid] = 'dead'
        def members(pgid): return [value for value in [child,member] if states[value['pid']] == 'alive']
        with mock.patch.object(agent_tasks, 'identity_status', side_effect=status), \
             mock.patch.object(agent_tasks, 'group_members', side_effect=members), \
             mock.patch.object(os, 'kill', side_effect=kill), mock.patch.object(os, 'killpg', side_effect=killpg):
            result = agent_tasks.stop(self.directory, timeout=.01)
        self.assertIn((50003, signal.SIGKILL), sent)
        self.assertEqual(result['status'], 'cancelled')

    def test_real_stop_reaps_tool_ignoring_sigterm_after_leader_exit(self):
        import time
        marker = self.base / 'tool.pid'
        child_code = 'import os,signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path(' + repr(str(marker)) + ').write_text(str(os.getpid()));time.sleep(30)'
        code = 'import subprocess,sys,time;subprocess.Popen([sys.executable,"-c",' + repr(child_code) + ']);time.sleep(30)'
        leader = subprocess.Popen([sys.executable, '-c', code], start_new_session=True)
        self.addCleanup(lambda: leader.poll() is None and leader.kill())
        deadline = time.monotonic() + 5
        while not marker.is_file() and time.monotonic() < deadline: time.sleep(.02)
        self.assertTrue(marker.is_file())
        tool = agent_tasks.process_identity(int(marker.read_text()))
        self.addCleanup(lambda: agent_tasks.identity_status(tool) == 'alive' and os.kill(tool['pid'], signal.SIGKILL))
        identity = agent_tasks.process_identity(leader.pid)
        self.state(runner_identity=identity, child_identity=identity)
        result = agent_tasks.stop(self.directory, timeout=.2)
        leader.wait(timeout=5)
        self.assertEqual(result['status'], 'cancelled')
        self.assertNotEqual(agent_tasks.identity_status(tool), 'alive')
