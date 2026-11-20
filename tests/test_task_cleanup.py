"""Unified Claude/Kiro task cleanup operates only on isolated task data."""
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import watch_kiro


class TaskCleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='task-cleanup-'); self.addCleanup(self.tmp.cleanup)
        self.cwd = Path(self.tmp.name).resolve()
        self.source = watch_kiro.Source(self.cwd, self.cwd / 'work/kiro-runs', create=True)
        self.panel = watch_kiro.Panel.__new__(watch_kiro.Panel)
        self.panel.catalog = mock.Mock(); self.panel.catalog.refresh.return_value = SimpleNamespace(projects=[])
        self.panel.all_sources = lambda snapshot: [self.source]
        self.panel.group_of = lambda source, snapshot: watch_kiro.OTHER_GROUP
        self.panel.temp_lock = threading.Lock()

    def run_record(self, ident, provider='claude', status='completed', agent=None, temp=False):
        directory = self.source.root / ident; directory.mkdir()
        state = {'id':ident,'agent_id':agent or ident,'provider':provider,'cwd':str(self.cwd),
                 'title':ident,'status':status,'started_at':'2026-11-19T01:00:00Z','exit_code':0 if status=='completed' else 1}
        if temp:
            path = self.cwd / 'work/tmp' / ident; path.mkdir(parents=True)
            (path / 'scratch.txt').write_text('temporary')
            state['temp'] = {'path':str(path),'status':'retained'}
        (directory / 'state.json').write_text(json.dumps(state))
        (directory / 'task.txt').write_text('task'); (directory / 'result.txt').write_text('result')
        (directory / 'events.jsonl').write_text('')
        (self.source.root / ('.launch-'+ident+'.log')).write_text('launch')
        return directory

    def test_all_includes_claude_and_failed_kiro_and_only_skips_active_turns(self):
        done=self.run_record('done',agent='shared')
        active=self.run_record('active',status='running',agent='shared')
        failed=self.run_record('failed','kiro','failed',temp=True)
        cancelled=self.run_record('cancelled','claude','cancelled')
        checking=self.run_record('checking',status='checking')
        tests=self.cwd/'tests';tests.mkdir();(tests/'test_feature.py').write_text('assert True')
        (self.source.root/'conversations.json').write_text(json.dumps({'shared':{'title':'keep'},'failed':{},'cancelled':{}}))
        view=self.panel.temp_overview()
        self.assertEqual(view['totals']['pending'],3);self.assertEqual(view['totals']['running'],2)
        status,result=self.panel.temp_bulk({'scope':'all'})
        self.assertEqual(status,200);self.assertEqual(result['cleaned'],3);self.assertEqual(result['skipped'],2)
        for directory in (done,failed,cancelled):self.assertFalse(directory.exists())
        self.assertTrue(active.exists());self.assertTrue(checking.exists())
        self.assertFalse((self.cwd/'work/tmp/failed').exists())
        self.assertTrue((tests/'test_feature.py').exists())
        self.assertEqual(json.loads((self.source.root/'conversations.json').read_text()),{'shared':{'title':'keep'}})
        self.assertEqual(self.panel.temp_bulk({'scope':'all'})[1]['cleaned'],0)

    def test_selected_only_cleans_chosen_and_rechecks_live_status(self):
        first=self.run_record('first');second=self.run_record('second')
        body={'scope':'selected','conversations':[{'group':watch_kiro.OTHER_GROUP,'source':self.source.id,'agent':'first'}]}
        self.assertEqual(self.panel.temp_bulk(body)[1]['cleaned'],1)
        self.assertFalse(first.exists());self.assertTrue(second.exists())
        state=json.loads((second/'state.json').read_text())
        stale=state.copy();state['status']='running';(second/'state.json').write_text(json.dumps(state))
        result=self.panel.temp_clean_turn(self.source,stale,'pending',{'run':'second','round':1})
        self.assertEqual(result['code'],'active');self.assertTrue(second.exists())

    def test_unknown_status_is_cleanable_without_acceptance_or_proc_checks(self):
        directory=self.run_record('unknown',status='legacy-state')
        with mock.patch.object(watch_kiro.kiro_temp,'associated_processes',side_effect=AssertionError('no proc scan')):
            result=self.panel.temp_bulk({'scope':'all'})[1]
        self.assertEqual(result['cleaned'],1);self.assertFalse(directory.exists())

    def test_outside_temp_and_symlink_targets_are_never_deleted(self):
        directory=self.run_record('unsafe','kiro',temp=True)
        outside=self.cwd/'tests';outside.mkdir();(outside/'keep.py').write_text('keep')
        state=json.loads((directory/'state.json').read_text());state['temp']['path']=str(outside)
        (directory/'state.json').write_text(json.dumps(state))
        result=self.panel.temp_bulk({'scope':'all'})[1]
        self.assertEqual(result['errors'],1);self.assertTrue(directory.exists());self.assertTrue((outside/'keep.py').exists())
        state['temp']['path']=str(self.cwd/'work/tmp/unsafe');(directory/'state.json').write_text(json.dumps(state))
        (directory/'external-link').symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.panel.temp_bulk({'scope':'all'})[1]['cleaned'],1)
        self.assertTrue((outside/'keep.py').exists())


if __name__=='__main__':unittest.main()
