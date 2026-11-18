// jsdom tests of the shipped viewer.html: Chinese tool summaries, edit statistics, file viewer, Git review dialog.
// The page talks to a scripted in-memory server (no sockets, no model calls).
const fs = require('fs'), path = require('path'), assert = require('assert/strict');
// jsdom is a devDependency (npm ci). A missing dependency is a failure, never a skipped pass.
const { JSDOM, VirtualConsole } = require('jsdom');
const assets = path.join(__dirname, '..', 'assets');
const html = fs.readFileSync(path.join(assets, 'viewer.html'), 'utf8');
const scripts = [...html.matchAll(/<script src="([^"]+)"><\/script>/g)].map(m => fs.readFileSync(path.join(assets, m[1].replace(/^\//, '')), 'utf8'));
const pageHtml = html.replace(/<script src="[^"]+"><\/script>\n?/g, '');
const tick = (ms = 0) => new Promise(r => setTimeout(r, ms));
const settle = async (n = 10) => { for (let i = 0; i < n; i++) await tick(5); };
const respond = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });

const upd = u => JSON.stringify({ type: 'sessionUpdate', data: { update: u } });
const call = (id, title, kind, input, extra = {}) => [upd({ sessionUpdate: 'tool_call', toolCallId: id, title, kind, rawInput: input, ...(extra.call || {}) }),
  ...(extra.done ? [upd({ sessionUpdate: 'tool_call_update', toolCallId: id, status: extra.status || 'completed', title, rawInput: input, ...extra.done })] : [])];
const cmd = (id, command, output = 'ok', status = 'completed') => call(id, 'Run Command', 'execute', { command, run_in_background: false },
  { done: { status, content: [{ type: 'content', content: { type: 'text', text: output } }], rawOutput: { output } } });

function conv(id, events, status = 'completed') {
  return { title: id, status, runs: [{ state: { id: 'r-' + id, agent_id: id, status, started_at: '2030-05-06T07:00:00+00:00', finished_at: '2030-05-06T07:00:05+00:00', exit_code: 0, prompt_forwarded: true },
    task: 'task ' + id, events: events.join('\n'), diagnostics: '', summary: null, temp: null }] };
}
function view(w, params) {
  const groups = [{ id: 'g', kind: 'codex', name: 'Proj', path: '/p', roots: ['/p'], exists: true, can_create: true, note: '',
    conversations: Object.entries(w.convs).map(([id, c]) => ({ key: 's1:' + id, source: 's1', id, title: c.title, status: c.status, created_at: '2030-05-06T07:00:00Z', activity: 1, cwd: '/p', turn_count: 1, deletable: true })) }];
  const agent = params.get('agent'), c = w.convs[agent];
  const selected = c ? { id: agent, title: c.title, status: c.status, created_at: '2030-05-06T07:00:00Z', cwd: '/p', source: 's1', group: 'g', key: 's1:' + agent, config: null, context: null } : null;
  return { groups, selected, turns: c ? c.runs : [], root: '/logs', source_groups: { s1: 'g' }, default: { group: 'g', source: 's1' }, catalog: { status: 'ok', message: '' },
    viewer: { active_tasks: 0, cleanup: {}, memory_mb: 1, account: null } };
}
function boot(w, options = {}) {
  const calls = [], errors = [], vc = new VirtualConsole();
  vc.on('jsdomError', e => errors.push(e));
  const server = (url, init) => {
    const u = new URL(url, 'http://127.0.0.1:47831'), q = u.searchParams;
    calls.push({ path: u.pathname, query: Object.fromEntries(q), post: !!(init && init.method === 'POST') });
    if (u.pathname === '/api') return respond(view(w, q));
    if (u.pathname === '/api/models') return respond({ status: 'unavailable', models: [], default: null });
    if (u.pathname.startsWith('/api/review')) {
      const echo = { group: q.get('group'), source: q.get('source'), agent: q.get('agent') };
      const handler = w.review[u.pathname];
      const finish = r => respond({ ...echo, ...r.body }, r.status || 200);
      const result = handler ? handler(Object.fromEntries(q), echo) : { status: 404, body: { error: 'x' } };
      return result && typeof result.then === 'function' ? result.then(finish) : finish(result);
    }
    return respond({ error: 'unexpected ' + url }, 404);
  };
  const dom = new JSDOM(pageHtml, { url: 'http://127.0.0.1:47831/', runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(window) {
      window.fetch = (url, init) => {
        const signal = init && init.signal;
        const result = Promise.resolve(server(String(url), init));
        if (!signal) return result;
        return new Promise((resolve, reject) => {
          const abort = () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' }));
          if (signal.aborted) return abort();
          signal.addEventListener('abort', abort);
          result.then(resolve, reject);
        });
      };
      window.confirm = () => true;
      window.navigator.clipboard = { writeText: async text => { ctx.copied.push(text); } };
    } });
  const { window } = dom, { document } = window;
  const ctx = { dom, window, document, calls, errors, w, copied: [], $: id => document.getElementById(id), ev: code => window.eval(code),
    click: el => el.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true })),
    key: (key, target) => (target || document).dispatchEvent(new window.KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })),
    poll: async () => { window.eval('clearTimeout(timer);refresh()'); await settle(); },
    openConv: async id => { ctx.click(document.querySelector('.run[data-id="' + id + '"]')); await settle(); },
    cards: () => [...document.querySelectorAll('.tool-row')],
    card: i => document.querySelectorAll('.tool-row')[i],
    title: i => document.querySelectorAll('.tool-row')[i].querySelector('.tool-name').textContent,
    stat: i => document.querySelectorAll('.tool-row')[i].querySelector('.tool-stat').textContent,
    btn: (root, label) => [...root.querySelectorAll('button')].find(b => b.textContent === label),
    ready: async () => { await new Promise(r => window.addEventListener('load', r)); for (const code of scripts) window.eval(code); await settle(20); return ctx; } };
  return ctx;
}
const newWorld = (convs, review = {}) => ({ convs, review });

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

// ---- Chinese summaries -------------------------------------------------------------------------------
test('command summaries are specific and conservative', async () => {
  const t = await boot(newWorld({ c1: conv('c1', []) })).ready();
  const S = c => JSON.parse(JSON.stringify(t.ev('summarizeShell(' + JSON.stringify(c) + ')')));
  const title = c => S(c).title;
  const table = [
    ['python3 -m pytest -q tests', '运行Python测试'],
    ['cd /x/y && python3 -m unittest discover -s tests', '运行Python测试'],
    ['pytest', '运行Python测试'],
    ['/opt/venv/bin/python3.12 tests/test_review.py', '运行Python测试'],
    ['python3 scripts/build.py --fast', '执行Python脚本'],
    ['python3 - <<\'EOF\'\nimport os\nprint("rm -rf /; git push")\nEOF', '执行Python脚本'],
    ['python3 -c "print(1)"', '执行Python脚本'],
    ['ruff check .', '检查代码规范'],
    ['python3 -m flake8 src', '检查代码规范'],
    ['black --check .', '检查代码规范'],
    ['black .', '格式化代码'],
    ['mypy src', '检查类型'],
    ['git status --short', '检查Git改动'],
    ['git -C /repo diff --stat', '检查Git改动'],
    ['git log --oneline -5', '查看Git历史'],
    ['git push origin main', '推送Git提交'],
    ['git commit -m "x"', '提交Git改动'],
    ['cat README.md', '读取文件'],
    ['tail -30 independent-tests.log', '读取文件'],
    ['sed -n "1,20p" watch.py', '读取文件'],
    ['sed -i "s/a/b/" f.py', '就地修改文件'],
    ['grep -rn "foo" src | head -20', '搜索文本'],
    ['ls -la work', '列出目录'],
    ['find . -name "*.py"', '查找文件'],
    ['rm -rf /tmp/kiro-xyz', '清理临时文件'],
    ['rm -f work/tool-review/tmp/x.pyc', '清理临时文件'],
    ['rm -rf src/important', '删除文件'],
    ['mkdir -p a/b', '创建目录'],
    ['node tests/dom.cjs', '运行JS测试'],
    ['node build.js', '执行Node脚本'],
    ['npm test', '运行JS测试'],
    ['npm run lint', '检查代码规范'],
    ['npm install', '管理依赖'],
    ['curl -s http://127.0.0.1:1/x', '发送网络请求'],
    ['bash -c "pytest -q"', '运行Python测试'],
    ['bash run.sh', '执行shell脚本'],
    ['cd /tmp', '切换目录'],
    ['echo done', '输出文本'],
    ['cat > notes.txt <<\'EOF\'\nhello\nEOF', '写入文件'],
    ['echo hi > out.txt', '写入文件'],
  ];
  for (const [command, expected] of table) assert.equal(title(command), expected, command);
  // multi-action
  assert.equal(title('cd /w && cat a.txt; python3 -m pytest -q; git status'), '读取文件、运行Python测试、检查Git改动');
  assert.equal(title('cat task.txt | head -30; tail -5 run.log; grep -n "^def " w.py | head -200'), '读取文件、搜索文本');
  // unknown stays honest
  for (const c of ['frobnicate --all', 'my-tool run', '', '   ', 'cd /x && frobnicate']) assert.equal(title(c), c.includes('cd') ? '执行命令' : '执行命令', c);
  assert.equal(S('cat a.txt && frobnicate').unknown, true);
  assert.match(title('cat a.txt && frobnicate'), /^读取文件 等$/);
  assert.equal(S('echo hi > out.txt').mutates, true);
  assert.equal(S('cat README.md').mutates, false);
  assert.equal(S('python3 x.py').mutates, true); // arbitrary scripts may modify files
});

test('heredoc bodies, quotes and comments never create actions', async () => {
  const t = await boot(newWorld({ c1: conv('c1', []) })).ready();
  const S = c => JSON.parse(JSON.stringify(t.ev('summarizeShell(' + JSON.stringify(c) + ')')));
  const body = 'cat > /tmp/x.py <<\'EOF\'\nrm -rf /\ngit push --force\npytest\nEOF\necho after';
  assert.deepEqual(S(body).actions, ['写入文件']);
  assert.deepEqual(S('python3 - <<EOF\ngit commit -am x\nrm -rf /\nEOF').actions, ['执行Python脚本']);
  assert.deepEqual(S('echo "git push; rm -rf /"').actions, ['输出文本']);
  assert.deepEqual(S("echo 'a && pytest'").actions, ['输出文本']);
  assert.deepEqual(S('# rm -rf /\nls').actions, ['列出目录']);
  assert.deepEqual(S('cat <<-EOF\n\tgit push\n\tEOF\nls').actions, ['列出目录']);
  assert.deepEqual(S('python3 - <<\'PY\' | tail -3\nrm -rf /\nPY').actions, ['执行Python脚本']);
  assert.deepEqual(S('x=$(git push) && ls').actions, ['列出目录']); // substitution text is not a top-level action
  assert.equal(S('cat <<EOF\nunterminated rm -rf /\n').title, '输出文本');
});

test('non-command tools get Chinese titles and unknown ones keep theirs', async () => {
  const t = await boot(newWorld({ c1: conv('c1', []) })).ready();
  const T = tool => t.ev('toolSummary(' + JSON.stringify(tool) + ')');
  assert.equal(T({ title: 'Read File', kind: 'read', rawInput: { path: '/p/a' } }).title, '读取文件');
  assert.equal(T({ title: 'Write File', kind: 'edit', rawInput: { path: '/p/a', text: 'x' } }).title, '写入文件');
  assert.equal(T({ title: 'Replace in File', kind: 'edit', rawInput: {} }).title, '修改文件');
  assert.equal(T({ title: 'Mystery Tool', kind: 'other', rawInput: {} }).title, 'Mystery Tool');
  assert.equal(T({ kind: 'search', rawInput: {} }).title, '搜索内容');
  assert.equal(T({ title: 'Run Command', kind: 'execute', rawInput: {} }).title, '执行命令');
});

// ---- Edit statistics -----------------------------------------------------------------------------------
const WRITE_NEW = (id, p, text) => call(id, 'Write File', 'edit', { path: p, text }, { call: { status: 'in_progress', locations: [{ path: p }] },
  done: { locations: [{ path: p }], content: [{ type: 'diff', path: 'file://' + p, oldText: '', newText: text }], rawOutput: { message: 'Created the ' + p + ' file.' } } });
const REPLACE = (id, p, oldFile, newFile, extra = {}) => call(id, 'Replace in File', 'edit', { path: p, oldStr: 'x', newStr: 'y', replace_all: false },
  { call: { status: 'in_progress', locations: [{ path: p }] },
    done: { locations: [{ path: p }], content: [{ type: 'diff', path: 'file://' + p, oldText: oldFile, newText: newFile }], rawOutput: { message: 'Replaced text in ' + p }, ...extra } });

test('edit cards show provable +/- and path; failed and unrecorded are not invented', async () => {
  const oldFile = 'a\nb\nc\nd\ne\n', newFile = 'a\nB\nB2\nc\nd\n';
  const events = [
    ...WRITE_NEW('w1', '/p/new.py', 'l1\nl2\nl3\n'),
    ...REPLACE('r1', '/p/f.py', oldFile, newFile),
    // explicit oldText '' is a trusted empty old file, so the count is computed from the pair
    ...call('w2', 'Write File', 'edit', { path: '/p/over.py', text: 'q\nq\n' }, { call: { locations: [{ path: '/p/over.py' }] },
      done: { locations: [{ path: '/p/over.py' }], content: [{ type: 'diff', path: 'file:///p/over.py', oldText: '', newText: 'q\nq\n' }], rawOutput: { message: 'Updated /p/over.py' } } }),
    // failed
    ...call('w3', 'Write File', 'edit', { path: '/p/bad.py', text: 'zzz' }, { status: 'failed', call: { locations: [{ path: '/p/bad.py' }] }, done: { status: 'failed', content: [{ type: 'content', content: { type: 'text', text: 'Permission denied' } }], rawOutput: { message: 'Permission denied' } } }),
    // in progress (empty diff placeholder as seen in real logs)
    ...call('w4', 'Replace in File', 'edit', { path: '/p/wip.py', oldStr: 'a', newStr: 'b' }, { call: { status: 'in_progress', locations: [{ path: '/p/wip.py' }] },
      done: { status: 'pending', locations: [{ path: '/p/wip.py' }], content: [{ type: 'diff', path: '', oldText: '', newText: '' }] } }),
    // shell modifying files
    ...cmd('s1', "sed -i 's/a/b/' /p/x.py"),
    ...cmd('s2', 'python3 fix.py'),
    ...cmd('s3', 'git status --short'),
    // patch style
    ...call('p1', 'Apply Patch', 'edit', { patch: '*** Begin Patch\n*** Update File: src/a.py\n@@\n-old1\n-old2\n+new1\n+new2\n+new3\n context\n*** Add File: src/b.py\n+x\n+y\n*** End Patch' }, { done: {} }),
    // identical text
    ...REPLACE('r2', '/p/same.py', 'a\n', 'a\n'),
  ];
  const t = await boot(newWorld({ c1: conv('c1', events) })).ready();
  const [w1, r1, w2, w3, w4, s1, s2, s3, p1, r2] = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9];
  assert.equal(t.cards().length, 10);
  assert.equal(t.title(w1), '写入文件');
  assert.equal(t.stat(w1), '+3−0');
  assert.match(t.card(w1).textContent, /新建/);
  assert.equal(t.title(r1), '修改文件');
  assert.equal(t.stat(r1), '+2−2'); // b->B,B2 ; e removed
  assert.equal(t.stat(w2), '+2−0');
  assert.equal(t.stat(w3), ''); assert.match(t.card(w3).textContent, /工具执行失败，不计入修改行数/);
  assert.doesNotMatch(t.card(w3).textContent, /已修改/);
  assert.equal(t.stat(w4), ''); assert.match(t.card(w4).textContent, /还没有执行结果/);
  assert.equal(t.title(s1), '就地修改文件');
  assert.equal(t.stat(s1), ''); assert.match(t.card(s1).textContent, /本次行数未记录/);
  assert.ok(t.btn(t.card(s1), '打开改动'));
  assert.match(t.card(s2).textContent, /本次行数未记录/);
  assert.doesNotMatch(t.card(s3).textContent, /本次行数未记录/);
  assert.equal(t.title(p1), '应用补丁');
  assert.equal(t.stat(p1), '+5−2');
  assert.equal(t.stat(r2), '+0−0');
  // the whole-repo Git diff is never consulted for a tool card
  assert.equal(t.calls.filter(c => c.path.startsWith('/api/review')).length, 0);
  // the original title and raw input/output stay available
  assert.match(t.card(w1).textContent, /工具原始标题：Write File/);
  assert.equal(t.card(w1).querySelector('.tool-name').title, '工具原始标题：Write File');
  const raw = [...t.card(r1).querySelectorAll('h3')].map(h => h.textContent);
  assert.ok(raw.includes('输入') && raw.includes('结果'));
  assert.deepEqual(t.errors, []);
});

test('line diff is exact and builds a readable unified diff', async () => {
  const t = await boot(newWorld({ c1: conv('c1', []) })).ready();
  const D = (a, b) => JSON.parse(t.ev('JSON.stringify(lineDiff(' + JSON.stringify(a) + ',' + JSON.stringify(b) + ',true))'));
  assert.deepEqual([D('a\nb\n', 'a\nb\n').added, D('a\nb\n', 'a\nb\n').deleted], [0, 0]);
  assert.deepEqual([D('', 'x\ny').added, D('', 'x\ny').deleted], [2, 0]);
  assert.deepEqual([D('x\ny\n', '').added, D('x\ny\n', '').deleted], [0, 2]);
  const d = D('1\n2\n3\n4\n5\n6\n7\n8\n9\n10\n', '1\n2\nX\n4\n5\n6\n7\n8\n9\n10\nEND\n');
  assert.deepEqual([d.added, d.deleted], [2, 1]);
  const unified = t.ev('unifiedFromOps("f.py", lineDiff("1\\n2\\n3\\n4\\n5\\n6\\n7\\n8\\n9\\n10\\n","1\\n2\\nX\\n4\\n5\\n6\\n7\\n8\\n9\\n10\\nEND\\n",true).ops)');
  assert.match(unified, /^--- a\/f.py\n\+\+\+ b\/f.py\n@@ -1,6 \+1,6 @@\n 1\n 2\n-3\n\+X\n 4\n 5\n 6\n@@ -8,3 \+8,4 @@\n 8\n/);
  assert.match(unified, /\+END\n$/);
  // too large to diff exactly: no number is invented
  const big = (n, tag) => Array.from({ length: n }, (_, i) => tag + i).join('\n');
  assert.equal(t.ev('lineDiff(' + JSON.stringify(big(3500, 'a')) + ',' + JSON.stringify(big(3500, 'b')) + ',false)'), null);
});

// ---- Viewer -----------------------------------------------------------------------------------------------
const review = (extra = {}) => ({
  '/api/review': () => ({ body: { kind: 'git', message: '', cwd: '/p', repo: { root: '/p', scope: '', branch: 'main', detached: false, unborn: false, head: 'abc1234' },
    areas: {
      staged: { entries: [{ path: 'a.txt', old_path: null, code: 'M', added: 2, deleted: 1, binary: false }, { path: 'new_name.txt', old_path: 'old_name.txt', code: 'R', added: 0, deleted: 0, binary: false }], truncated: false, hidden: 0, error: null },
      unstaged: { entries: [{ path: 'a.txt', old_path: null, code: 'M', added: 1, deleted: 0, binary: false }, { path: 'bin.dat', old_path: null, code: 'M', added: null, deleted: null, binary: true }, { path: 'gone.txt', old_path: null, code: 'D', added: 0, deleted: 3, binary: false }], truncated: false, hidden: 1, error: null },
      untracked: { entries: [{ path: '<img src=x onerror=alert(1)>.txt', old_path: null, code: '?', added: 1, deleted: 0, binary: false }], truncated: false, hidden: 0, error: null } } } }),
  '/api/review/diff': q => ({ body: { area: q.area, path: q.path, state: 'ok', code: 'M', diff: q.area === 'staged' ? 'diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1,2 +1,3 @@\n one\n-two\n+TWO\n+four\n' : 'diff --git a/a.txt b/a.txt\n@@ -1 +1,2 @@\n x\n+<script>window.pwned=1</script>\n', added: 2, deleted: 1, binary: q.path === 'bin.dat', truncated: false } }),
  '/api/review/file': q => q.path === 'outside' ? { status: 403, body: { error: '这个文件不在对话的工作目录内，面板不读取' } } : ({ body: q.path === 'missing.txt' ? { path: q.path, name: 'missing.txt', state: 'missing', message: '文件不存在（可能已删除或尚未创建）' }
    : q.path === 'big.bin' ? { path: q.path, name: 'big.bin', state: 'too_large', message: '文件过大', size: 5000000 }
    : { path: q.path, name: q.path.split('/').pop(), state: 'ok', text: 'line1\n<b>line2</b>\n\nline4', lines: 4, size: 24 } }),
  ...extra });
const simple = () => conv('c1', [...WRITE_NEW('w1', '/p/new.py', 'l1\nl2\n'), ...REPLACE('r1', '/p/f.py', 'a\nb\n', 'a\nc\n'), ...cmd('s1', 'python3 - <<\'EOF\'\nprint("hi")\nEOF'),
  ...call('rd', 'Read File', 'read', { path: '/p/a.txt' }, { call: { locations: [{ path: '/p/a.txt' }] }, done: { locations: [{ path: '/p/a.txt' }], rawOutput: { message: 'x' } } })]);

test('view file from a card: lines, safe text, copy, missing/too-large/outside states', async () => {
  const t = await boot(newWorld({ c1: simple() }, review())).ready();
  t.card(0).querySelector('details').open = true;
  t.click(t.btn(t.card(0), '查看文件')); await settle();
  assert.equal(t.$('review').hidden, false);
  const q = t.calls.filter(c => c.path === '/api/review/file').pop().query;
  assert.deepEqual([q.group, q.source, q.agent, q.path], ['g', 's1', 'c1', '/p/new.py']);
  assert.equal(t.$('rv-name').textContent, 'new.py');
  const rows = [...t.document.querySelectorAll('#rv-code .ln')];
  assert.equal(rows.length, 4);
  assert.equal(rows[0].querySelector('.no').textContent, '1');
  assert.equal(rows[1].querySelector('.tx').textContent, '<b>line2</b>');
  assert.equal(t.document.querySelector('#rv-code b'), null); // text, never markup
  assert.equal(t.$('rv-meta').textContent, '4 行 · 24 B');
  t.click(t.$('rv-copy')); await settle(); t.click(t.$('rv-copy-path')); await settle();
  assert.deepEqual(t.copied, ['line1\n<b>line2</b>\n\nline4', '/p/new.py']);
  // read-only: no editing controls
  assert.equal(t.document.querySelector('#review textarea,#review input,#review [contenteditable]'), null);
  t.key('Escape'); assert.equal(t.$('review').hidden, true);
  t.ev("openFileView('missing.txt')"); await settle();
  assert.match(t.$('rv-status').textContent, /文件不存在/);
  assert.equal(t.document.querySelectorAll('#rv-code .ln').length, 0);
  t.ev("openFileView('big.bin')"); await settle();
  assert.match(t.$('rv-status').textContent, /文件过大/);
  t.ev("openFileView('outside')"); await settle();
  assert.match(t.$('rv-status').textContent, /不在对话的工作目录内/);
  t.key('Escape');
  assert.deepEqual(t.errors, []);
});

test('tool diff and command script open locally without any server read', async () => {
  const t = await boot(newWorld({ c1: simple() }, review())).ready();
  const before = t.calls.length;
  t.click(t.btn(t.card(1), '查看本次修改')); await settle();
  const rows = [...t.document.querySelectorAll('#rv-code .ln')].map(r => r.className.replace('ln ', '') + ':' + r.querySelector('.tx').textContent);
  assert.ok(rows.includes('del:-b') && rows.includes('add:+c'), rows.join('|'));
  assert.equal(t.$('rv-meta').textContent, '+1 −1');
  t.key('Escape');
  t.click(t.btn(t.card(2), '查看脚本')); await settle();
  assert.equal(t.$('review-title').textContent, '命令脚本');
  assert.deepEqual([...t.document.querySelectorAll('#rv-code .tx')].map(n => n.textContent), ["python3 - <<'EOF'", 'print("hi")', 'EOF']);
  t.click(t.$('rv-copy')); await settle();
  assert.equal(t.copied.pop(), "python3 - <<'EOF'\nprint(\"hi\")\nEOF");
  t.key('Escape');
  assert.equal(t.calls.length, before);
});

test('Git review: categories, stats, branch, disclaimer, per-category diff, current text, XSS', async () => {
  const t = await boot(newWorld({ c1: simple() }, review())).ready();
  assert.equal(t.$('review-open').disabled, false);
  t.$('prompt').value = 'half typed'; t.$('prompt').dispatchEvent(new t.window.Event('input', { bubbles: true }));
  const scrollBefore = t.$('scroll').scrollTop;
  t.$('review-open').focus();
  t.click(t.$('review-open')); await settle();
  assert.equal(t.$('review').hidden, false);
  assert.equal(t.$('review-open').getAttribute('aria-expanded'), 'true');
  assert.match(t.$('review-note').textContent, /可能包含其他任务或你自己已有的修改，不代表这条对话做过的修改/);
  assert.match(t.$('rv-list').textContent, /分支：main/);
  const groups = [...t.document.querySelectorAll('.cl-group')].map(g => g.querySelector('h3').textContent);
  assert.match(groups[0], /已暂存（index） · 2\+2−1/);
  assert.match(groups[1], /未暂存（工作区） · 3\+1−3/);
  assert.match(groups[2], /未跟踪 · 1/);
  assert.match(t.$('rv-list').textContent, /old_name\.txt → new_name\.txt/);
  assert.match(t.$('rv-list').textContent, /二进制/);
  assert.match(t.$('rv-list').textContent, /已隐藏 1 个凭据\/敏感路径/);
  assert.equal(t.document.querySelector('#rv-list img'), null); // hostile file name stays text
  assert.match(t.$('rv-list').textContent, /<img src=x onerror=alert\(1\)>\.txt/);
  // staged a.txt and unstaged a.txt are different requests
  const rows = [...t.document.querySelectorAll('.cl-row')];
  t.click(rows[0]); await settle();
  let q = t.calls.filter(c => c.path === '/api/review/diff').pop().query;
  assert.deepEqual([q.area, q.path], ['staged', 'a.txt']);
  assert.ok([...t.document.querySelectorAll('#rv-code .ln.add')].some(r => r.textContent.includes('+TWO')));
  t.click(t.document.querySelectorAll('.cl-row')[2]); await settle();
  q = t.calls.filter(c => c.path === '/api/review/diff').pop().query;
  assert.deepEqual([q.area, q.path], ['unstaged', 'a.txt']);
  assert.equal(t.document.querySelector('#rv-code script'), null);
  assert.ok([...t.document.querySelectorAll('#rv-code .tx')].some(n => n.textContent === '+<script>window.pwned=1</script>'));
  assert.equal(t.window.pwned, undefined);
  // binary: no diff, no counts
  t.click(t.document.querySelectorAll('.cl-row')[3]); await settle();
  assert.match(t.$('rv-status').textContent, /二进制文件，不显示 diff，也不统计行数/);
  // current text tab
  t.click(t.$('rv-tab-text')); await settle();
  q = t.calls.filter(c => c.path === '/api/review/file').pop().query;
  assert.equal(q.path, 'bin.dat');
  t.click(t.$('rv-tab-diff')); await settle();
  // composer draft, selection and scroll are untouched; focus returns to the opener
  assert.equal(t.$('prompt').value, 'half typed');
  t.key('Escape');
  assert.equal(t.$('review').hidden, true);
  assert.equal(t.document.activeElement, t.$('review-open'));
  assert.equal(t.$('prompt').value, 'half typed');
  assert.equal(t.$('scroll').scrollTop, scrollBefore);
  assert.equal(t.document.querySelector('main').hasAttribute('inert'), false);
  assert.equal(t.$('sidebar').hasAttribute('inert'), false);
  assert.deepEqual(t.errors, []);
});

test('non-Git directory shows an empty state and file viewing still works', async () => {
  const w = newWorld({ c1: simple() }, review({ '/api/review': () => ({ body: { kind: 'nogit', message: '这个目录不在 Git 仓库中', cwd: '/p', repo: null, areas: {} } }) }));
  const t = await boot(w).ready();
  t.click(t.$('review-open')); await settle();
  assert.match(t.$('rv-list').textContent, /这个目录不是 Git 仓库/);
  assert.match(t.$('rv-list').textContent, /仍可在工具卡片里点“查看文件”/);
  t.key('Escape');
  t.click(t.btn(t.card(0), '查看文件')); await settle();
  assert.equal(t.document.querySelectorAll('#rv-code .ln').length, 4);
  t.key('Escape');
});

test('detached HEAD, unborn branch, subdirectory scope and area errors are explained', async () => {
  const base = { kind: 'git', message: '', cwd: '/p/sub', areas: { staged: { entries: [], truncated: false, hidden: 0, error: null }, unstaged: { entries: [], truncated: false, hidden: 0, error: 'Git 读取超时，已停止' }, untracked: { entries: [], truncated: false, hidden: 0, error: null } } };
  const w = newWorld({ c1: simple() }, review({ '/api/review': () => ({ body: { ...base, repo: { root: '/p', scope: 'sub', branch: '', detached: true, unborn: false, head: 'deadbeef1234' } } }) }));
  const t = await boot(w).ready();
  t.click(t.$('review-open')); await settle();
  assert.match(t.$('rv-list').textContent, /游离 HEAD（deadbeef1234）/);
  assert.match(t.$('rv-list').textContent, /只显示子目录 sub 内的改动/);
  assert.match(t.$('rv-list').textContent, /Git 读取超时，已停止/);
  t.key('Escape');
  w.review['/api/review'] = () => ({ body: { ...base, areas: { ...base.areas, unstaged: base.areas.staged }, repo: { root: '/p', scope: '', branch: 'main', detached: false, unborn: true, head: '' } } });
  t.click(t.$('review-open')); await settle();
  assert.match(t.$('rv-list').textContent, /main（尚无提交）/);
  assert.match(t.$('rv-list').textContent, /当前工作区没有未提交的改动/);
  t.key('Escape');
});

test('server refusals and failures are shown, never swallowed', async () => {
  const w = newWorld({ c1: simple() }, review({ '/api/review': () => ({ status: 409, body: { error: '对话所属项目已变化，请刷新页面' } }) }));
  const t = await boot(w).ready();
  t.click(t.$('review-open')); await settle();
  assert.match(t.$('rv-list').textContent, /对话所属项目已变化/);
  t.key('Escape');
});

test('polling neither rebuilds an open diff nor the cards; buttons keep working', async () => {
  const t = await boot(newWorld({ c1: simple() }, review())).ready();
  t.card(0).querySelector('details').open = true;
  const cardNode = t.card(0), button = t.btn(cardNode, '查看文件');
  t.click(t.$('review-open')); await settle();
  t.click(t.document.querySelectorAll('.cl-row')[0]); await settle();
  const code = t.$('rv-code'), firstRow = code.querySelector('.ln'), listRow = t.document.querySelector('.cl-row');
  for (let i = 0; i < 3; i++) await t.poll();
  assert.equal(t.$('rv-code'), code);
  assert.equal(code.querySelector('.ln'), firstRow);
  assert.equal(t.document.querySelector('.cl-row'), listRow);
  t.key('Escape');
  assert.equal(t.card(0), cardNode);
  assert.equal(t.card(0).querySelector('details').open, true);
  assert.equal(t.btn(t.card(0), '查看文件'), button);
  t.click(button); await settle();
  assert.equal(t.$('review').hidden, false);
  t.key('Escape');
  // a changed tool record rebuilds its body, and the fresh buttons still work
  t.w.convs.c1 = conv('c1', [...WRITE_NEW('w1', '/p/new.py', 'l1\nl2\nl3\n'), ...REPLACE('r1', '/p/f.py', 'a\nb\n', 'a\nc\n')]);
  await t.poll();
  assert.equal(t.stat(0), '+3−0');
  t.click(t.btn(t.card(0), '查看文件')); await settle();
  assert.equal(t.$('review').hidden, false);
  t.key('Escape');
  assert.deepEqual(t.errors, []);
});

test('Escape closes only the review, Tab stays inside it, background is inert', async () => {
  const t = await boot(newWorld({ c1: simple() }, review())).ready();
  t.click(t.$('review-open')); await settle();
  assert.equal(t.document.activeElement, t.$('review-close'));
  assert.ok(t.document.querySelector('main').hasAttribute('inert') && t.$('sidebar').hasAttribute('inert'));
  const focusables = [...t.$('review').querySelectorAll('button,[tabindex="0"]')].filter(n => !n.disabled && !n.hidden && !n.closest('[hidden]'));
  focusables[focusables.length - 1].focus();
  t.key('Tab', focusables[focusables.length - 1]);
  assert.equal(t.document.activeElement, focusables[0]);
  assert.equal(t.$('settings').hidden, true);
  t.key('Escape');
  assert.equal(t.$('review').hidden, true);
  assert.equal(t.$('settings').hidden, true);
  // settings still open and close normally afterwards, and cannot be stacked on the review
  t.click(t.$('settings-open')); assert.equal(t.$('settings').hidden, false);
  t.ev("openReview('changes')"); assert.equal(t.$('review').hidden, true);
  t.key('Escape'); assert.equal(t.$('settings').hidden, true);
  // the button is disabled without a conversation
  t.click(t.document.querySelector('.group-new')); await settle();
  assert.equal(t.$('review-open').disabled, true);
});

test('switching conversations while a request is in flight never shows the old answer', async () => {
  const slow = {};
  const w = newWorld({ c1: simple(), c2: conv('c2', []) }, review({
    '/api/review': (q, echo) => echo.agent === 'c1' ? new Promise(resolve => { slow.resolve = () => resolve({ body: { kind: 'git', message: '', cwd: '/p', repo: { root: '/c1-secret', scope: '', branch: 'c1-branch', detached: false, unborn: false, head: '' }, areas: { staged: { entries: [], truncated: false, hidden: 0, error: null }, unstaged: { entries: [], truncated: false, hidden: 0, error: null }, untracked: { entries: [], truncated: false, hidden: 0, error: null } } } }); })
      : { body: { kind: 'git', message: '', cwd: '/p2', repo: { root: '/c2', scope: '', branch: 'c2-branch', detached: false, unborn: false, head: '' }, areas: { staged: { entries: [], truncated: false, hidden: 0, error: null }, unstaged: { entries: [], truncated: false, hidden: 0, error: null }, untracked: { entries: [], truncated: false, hidden: 0, error: null } } } } }));
  const t = await boot(w).ready();
  t.click(t.$('review-open')); await settle();
  assert.match(t.$('rv-list').textContent, /正在读取/);
  // the conversation changes underneath (as when it is deleted or auto-cleaned): the dialog closes
  t.ev("select({group:'g',source:'s1',agent:'c2'})"); await settle();
  assert.equal(t.$('review').hidden, true);
  slow.resolve(); await settle();
  assert.equal(t.$('rv-list').textContent, '');
  assert.equal(t.$('review').hidden, true);
  // re-open on c2: only c2 data
  await settle(); t.click(t.$('review-open')); await settle();
  assert.match(t.$('rv-list').textContent, /c2-branch/);
  assert.doesNotMatch(t.$('rv-list').textContent, /c1/);
  t.key('Escape');
  assert.deepEqual(t.errors, []);
});

test('a slower earlier request cannot overwrite a newer one (refresh while loading)', async () => {
  const pending = [];
  const entry = name => ({ path: name, old_path: null, code: 'M', added: 1, deleted: 0, binary: false });
  const area = names => ({ entries: names.map(entry), truncated: false, hidden: 0, error: null });
  const w = newWorld({ c1: simple() }, review({ '/api/review': () => new Promise(resolve => pending.push(names => resolve({ body: { kind: 'git', message: '', cwd: '/p', repo: { root: '/p', scope: '', branch: 'main', detached: false, unborn: false, head: '' }, areas: { staged: area(names), unstaged: area([]), untracked: area([]) } } }))) }));
  const t = await boot(w).ready();
  t.click(t.$('review-open')); await settle();
  t.click(t.$('review-refresh')); await settle();
  assert.equal(pending.length, 2);
  pending[1](['new.txt']); await settle();
  pending[0](['old.txt']); await settle();
  assert.match(t.$('rv-list').textContent, /new\.txt/);
  assert.doesNotMatch(t.$('rv-list').textContent, /old\.txt/);
  t.key('Escape');
});


// ---- review round 2 ----------------------------------------------------------------------------------------
const E = (t, tool) => JSON.parse(t.ev('JSON.stringify(editInfo(' + JSON.stringify({ status: 'completed', ...tool }) + ',true))'));
const P = (t, text) => JSON.parse(t.ev('JSON.stringify(parsePatchText(' + JSON.stringify(text) + '))'));
const diffItem = (extra, msg) => ({ title: 'Write File', kind: 'edit', locations: [{ path: '/p/a' }], content: [{ type: 'diff', path: 'file:///p/a', ...extra }], rawOutput: { message: msg || 'Updated /p/a' } });

test('ACP diff items: missing fields are not empty text; only explicit pairs (or a reliable create) are counted', async () => {
  const t = await boot(newWorld({ c1: conv('c1', []) })).ready();
  const f = tool => E(t, tool).files[0];
  assert.deepEqual([f(diffItem({ oldText: 'a\nb\n', newText: 'a\nc\n' })).exact, E(t, diffItem({ oldText: 'a\nb\n', newText: 'a\nc\n' })).added], [true, 1]);
  // explicit empty old text is a trusted empty file
  let r = E(t, diffItem({ oldText: '', newText: 'x\ny\n' })); assert.deepEqual([r.exact, r.added, r.deleted], [true, 2, 0]);
  // explicit empty new text really deletes the content
  r = E(t, diffItem({ oldText: 'x\ny\n', newText: '' })); assert.deepEqual([r.exact, r.added, r.deleted], [true, 0, 2]);
  // missing newText is NOT a deletion
  r = E(t, diffItem({ oldText: 'x\ny\n' })); assert.equal(r.exact, false); assert.equal(r.some, false); assert.equal(r.files[0].added, undefined);
  r = E(t, diffItem({ oldText: 'x\ny\n', newText: null })); assert.equal(r.some, false);
  // missing oldText without a reliable "Created" message is not a creation
  r = E(t, diffItem({ newText: 'x\ny\n' })); assert.equal(r.some, false);
  r = E(t, diffItem({ oldText: null, newText: 'x\ny\n' })); assert.equal(r.some, false);
  // missing oldText + explicit Created + newText: a new file
  r = E(t, diffItem({ newText: 'x\ny\nz\n' }, 'Created the /p/a file.')); assert.deepEqual([r.exact, r.added, r.deleted, r.files[0].created], [true, 3, 0, true]);
  // Created message but no newText: nothing is invented
  r = E(t, diffItem({}, 'Created the /p/a file.')); assert.equal(r.some, false);
  // failed tools count nothing
  r = E(t, { ...diffItem({ oldText: 'a', newText: 'b' }), status: 'failed' }); assert.equal(r.some, undefined); assert.equal(r.added, 0);
});

test('patch parsing: only real hunk lines; incomplete or unprovable records stay unknown', async () => {
  const t = await boot(newWorld({ c1: conv('c1', []) })).ready();
  const known = f => [f.known, f.added, f.deleted];
  // unified diff: content that looks like headers inside a hunk is content
  let r = P(t, 'diff --git a/x.txt b/x.txt\n--- a/x.txt\n+++ b/x.txt\n@@ -1,3 +1,3 @@\n keep\n--- old dashes\n+++ new pluses\n tail\n');
  assert.equal(r.length, 1); assert.equal(r[0].path, 'x.txt'); assert.deepEqual(known(r[0]), [true, 1, 1]);
  // pure ---/+++ multi-file without diff --git, second file named like a header line
  r = P(t, '--- a/one.txt\n+++ b/one.txt\n@@ -1 +1,2 @@\n a\n+b\n--- a/two.txt\n+++ b/two.txt\n@@ -1,2 +1 @@\n a\n-b\n');
  assert.deepEqual(r.map(f => [f.path, ...known(f)]), [['one.txt', true, 1, 0], ['two.txt', true, 0, 1]]);
  // new / deleted file via /dev/null
  r = P(t, '--- /dev/null\n+++ b/n.txt\n@@ -0,0 +1,2 @@\n+a\n+b\n--- a/d.txt\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-a\n-b\n');
  assert.deepEqual(r.map(f => [f.path, ...known(f)]), [['n.txt', true, 2, 0], ['d.txt', true, 0, 2]]);
  // no hunk, binary, rename-only: unknown, no numbers
  for (const text of ['diff --git a/x b/x\nindex 1..2 100644\n--- a/x\n+++ b/x\n',
    'diff --git a/b.bin b/b.bin\nBinary files a/b.bin and b/b.bin differ\n',
    'diff --git a/o b/n\nsimilarity index 100%\nrename from o\nrename to n\n',
    'diff --git a/m b/m\nold mode 100644\nnew mode 100755\n']) {
    const files = P(t, text); assert.equal(files.length, 1); assert.deepEqual(known(files[0]), [false, null, null]);
  }
  // truncated hunk (declares 5 old lines, only 2 present): unknown, not a short count
  r = P(t, '--- a/t.txt\n+++ b/t.txt\n@@ -1,5 +1,5 @@\n a\n-b\n'); assert.deepEqual(known(r[0]), [false, null, null]);
  // a good file followed by a truncated one: only the truncated one is unknown
  r = P(t, '--- a/g.txt\n+++ b/g.txt\n@@ -1 +1 @@\n-a\n+b\n--- a/t.txt\n+++ b/t.txt\n@@ -1,4 +1,4 @@\n-a\n');
  assert.deepEqual(r.map(f => f.known), [true, false]);
  // "\ No newline" is not a line
  r = P(t, '--- a/n\n+++ b/n\n@@ -1 +1 @@\n-a\n\\ No newline at end of file\n+b\n\\ No newline at end of file\n'); assert.deepEqual(known(r[0]), [true, 1, 1]);
  // apply_patch: Add File needs no @@; Delete File with only a path is unknown; missing End Patch is unknown
  r = P(t, '*** Begin Patch\n*** Add File: a.py\n+x\n+y\n*** Delete File: gone.py\n*** Update File: u.py\n@@\n-o\n+n\n+n2\n*** End Patch');
  assert.deepEqual(r.map(f => [f.path, ...known(f)]), [['a.py', true, 2, 0], ['gone.py', false, null, null], ['u.py', true, 2, 1]]);
  r = P(t, '*** Begin Patch\n*** Update File: u.py\n@@\n-o\n+n\n'); assert.deepEqual(known(r[0]), [false, null, null]);
  r = P(t, '*** Begin Patch\n*** Update File: u.py\n*** Move to: v.py\n*** End Patch'); assert.deepEqual([r[0].path, ...known(r[0])], ['v.py', false, null, null]);
  // an added line whose text starts with "++ " or "-- " is counted as content
  r = P(t, '*** Begin Patch\n*** Add File: h.txt\n+++ not a header\n+--- also content\n*** End Patch'); assert.deepEqual(known(r[0]), [true, 2, 0]);
  assert.equal(P(t, ''), null); assert.equal(P(t, 'just text'), null);
});

test('patch tools: unknown files add nothing, partial application output is conservative', async () => {
  const t = await boot(newWorld({ c1: conv('c1', []) })).ready();
  const patch = '*** Begin Patch\n*** Update File: a.py\n@@\n-o\n+n\n*** Delete File: gone.py\n*** End Patch';
  let r = E(t, { title: 'Apply Patch', kind: 'edit', rawInput: { patch }, rawOutput: { message: 'Done' } });
  assert.deepEqual(r.files.map(f => [f.path, f.exact]), [['a.py', true], ['gone.py', false]]);
  assert.equal(r.exact, false); assert.equal(r.some, true); assert.deepEqual([r.added, r.deleted], [1, 1]);   // delete-file is never "-0"
  assert.match(r.files[1].note, /未记录/);
  for (const message of ['Patch partially applied', 'Hunk #2 FAILED at 10', 'applied 1 of 2 hunks; rejected 1', '部分应用成功']) {
    r = E(t, { title: 'Apply Patch', kind: 'edit', rawInput: { patch }, rawOutput: { message } });
    assert.equal(r.some, false, message); assert.equal(r.added, 0, message);
    assert.match(r.files[0].note, /部分应用/);
  }
  r = E(t, { title: 'Apply Patch', kind: 'edit', rawInput: { patch: 'diff --git a/x b/x\nBinary files a/x and b/x differ\n' }, rawOutput: { message: 'ok' } });
  assert.equal(r.some, false);
});

test('an existing specific Chinese title is kept; generic ones fall back to the category; shell is not guessed', async () => {
  const t = await boot(newWorld({ c1: conv('c1', []) })).ready();
  const T = tool => t.ev('toolSummary(' + JSON.stringify(tool) + ')');
  assert.equal(T({ title: '修改配置文件 config.yaml', kind: 'edit', rawInput: { path: '/p/config.yaml' } }).title, '修改配置文件 config.yaml');
  assert.equal(T({ title: '读取 README 说明', kind: 'read', rawInput: {} }).title, '读取 README 说明');
  assert.equal(T({ title: '检索项目中的登录逻辑', kind: 'search', rawInput: {} }).title, '检索项目中的登录逻辑');
  assert.equal(T({ title: '删除旧的构建产物', kind: 'delete', rawInput: {} }).title, '删除旧的构建产物');
  assert.equal(T({ title: '修改文件', kind: 'edit', rawInput: {} }).title, '修改文件');
  assert.equal(T({ title: '工具调用', kind: 'search', rawInput: {} }).title, '搜索内容');
  // unknown shell keeps the accurate Chinese summary; the command text is never interpreted
  const sh = T({ title: '运行单元测试并汇总失败用例', kind: 'execute', rawInput: { command: 'mytool --go' } });
  assert.equal(sh.title, '运行单元测试并汇总失败用例'); assert.equal(sh.unknown, true); assert.equal(sh.actions.length, 0);
  assert.equal(T({ title: '执行命令', kind: 'execute', rawInput: { command: 'mytool --go' } }).title, '执行命令');
  assert.equal(T({ title: 'Run Command', kind: 'execute', rawInput: { command: 'mytool --go' } }).title, '执行命令');
  // a kept title does not hide that the command may modify files
  assert.equal(T({ title: '整理一下目录', kind: 'execute', rawInput: { command: 'rm -rf build' } }).mutates, true);
  // rendered card: title kept, note does not claim "执行命令"
  const events = cmd('k1', 'mytool --go').map(l => l.replace(/"title":"Run Command"/g, '"title":"运行单元测试并汇总失败用例"'));
  const page = await boot(newWorld({ c1: conv('c1', events) })).ready();
  assert.equal(page.title(0), '运行单元测试并汇总失败用例');
  assert.match(page.card(0).textContent, /沿用工具给出的中文描述/);
  assert.doesNotMatch(page.card(0).textContent, /按“执行命令”显示/);
});

// ---- run ----------------------------------------------------------------------------------------------------
(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log('ok   ' + name); }
    catch (error) { failed++; console.log('FAIL ' + name + '\n' + (error && error.stack || error)); }
  }
  console.log(failed ? failed + ' failed' : 'all ' + tests.length + ' passed');
  process.exit(failed ? 1 : 0);
})();
