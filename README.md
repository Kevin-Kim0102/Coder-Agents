# Coder-Agents

Coder-Agents 是一个通用的本地 Agent 工作台：用 Python 标准库写的轻量服务，加一个本机网页面板，把你机器上已安装的编码 CLI Agent 串起来，用来启动、查看、续聊和审阅任务。

链路：**浏览器 → 本机 Python 服务（127.0.0.1）→ 本机 CLI Agent**。

| 后端 | 状态 |
| --- | --- |
| Kiro CLI | **当前已接入**，是唯一可用的后端 |
| Claude CLI 等其他本地 CLI | 规划中，**尚未支持**。目前没有 provider 抽象，面板里也没有选择后端的界面 |

本项目由原 Kiro Panel（`kiro-coding` skill 与 `kiro-panel` 插件）整理而来，保留 `scripts/` 与 `assets/` 布局。当前代码、数据目录名（`kiro-panel`）和部分命令仍带有 Kiro 命名，这是为兼容旧安装，不代表产品只服务 Kiro。

## 环境要求

- Linux（本版本不承诺其他平台）。
- Python 3.9 及以上。无第三方 Python 依赖。
- 当前后端需要 **已登录的 Kiro CLI**（`kiro-cli`）。登录凭据由 Kiro CLI 自己保存，不进入本仓库。
- Git：仅用于面板的改动审阅功能。
- 不需要构建前端，不需要 `pip install`、systemd、Codex 或全局 `AGENTS.md`。`npm` 只在开发、跑 DOM 测试时用到。

## 从 GitHub 拉取后使用

```bash
git clone https://github.com/Kevin-Kim0102/Coder-Agents.git
cd Coder-Agents
python3 -m coder_agents serve
```

然后用浏览器打开固定入口 **http://127.0.0.1:47831/**。

- 首次 `serve` 会创建 `~/.local/state/kiro-panel/config.json`（权限 0600，含随机 token）。端口默认 47831，仅在首次创建配置时可用 `--port` 指定；已有配置时不会改写。
- token 不出现在页面和地址里，浏览器只持有 HttpOnly cookie。
- 如果 47831 已有服务在监听（例如旧版 Kiro Panel 或你已启动的实例），`serve` 会拒绝启动，不会开第二个服务。此时直接打开上面的地址即可。

### 服务何时退出

`serve` 在前台运行，没有任务时空闲 120 秒（配置项 `idle_timeout`）后自动退出，并且无论如何最长运行 8 小时；有进行中的任务时不会因空闲退出。退出后固定入口没有 socket 可连，需要重新运行 `python3 -m coder_agents serve`。

`Ctrl+C` 或 `python3 -m coder_agents stop` 都只关闭 Python 服务，不会删除对话历史和任务日志。

## 项目与对话

- **其他对话**：不需要任何项目。新对话在 `~/Documents/Codex/<日期>` 下运行，日期按香港时区（`Asia/Hong_Kong`）计算；可用配置项 `daily_base` 改目录。目录名与时区目前是沿袭旧版的默认值，尚不能在面板里改。
- **Codex 项目自动发现**：面板会只读地读取 Codex 私有状态文件 `~/.codex/.codex-global-state.json` 来列出 Codex 项目。该文件不是公开接口，格式可能变；文件不存在时只显示“其他对话”和已注册项目。没有安装 Codex 完全不影响使用。
- **没有 Codex 时指定项目目录**：在另一个终端对该项目运行任务，任务日志会写到 `<项目>/work/kiro-runs/`：

  ```bash
  printf '%s\n' '阅读项目并总结目录结构，不要修改文件' | \
    python3 -m coder_agents run --cwd /绝对路径/项目 --agent-id demo --title "示例任务"
  ```

  面板正在运行时，再用 `register` 把这个项目的日志展示出来：

  ```bash
  python3 -m coder_agents register --cwd /绝对路径/项目
  ```

  `register` 只是让运行中的面板读取该项目的日志目录并打印页面 URL，**不是**“在界面里新建可选项目”的功能，这个界面目前不支持。

## 模型

`run` 不带 `--model` 时，当前 Kiro runner 默认使用 `claude-sonnet-5.5` + `medium`，并由 `kiro_acp_relay.py` 确认实际选中后才转发任务，不会静默回退。在面板里，用户可以从自己 Kiro 账户的真实模型目录中选择其他模型与强度，后端会对照目录校验。这些是当前 Kiro runner 的行为，不是对未来后端的约定。

## 命令

| 命令 | 作用 |
| --- | --- |
| `serve [--port N]` | 前台启动面板 |
| `status` | 只读：路径、端口、活跃任务、服务单元状态 |
| `run ...` | 运行一个 Kiro 任务，任务文本从 stdin 读入，参数交给 `scripts/run_kiro.py`；`--check` 做可用性检查（会发一次模型请求） |
| `register --cwd DIR [--root DIR]` | 让运行中的面板读取项目的日志目录 |
| `stop` | 关闭面板服务（任务与历史保留） |
| `settings path\|init\|migrate-legacy [--apply]` | 用户设置；默认只预演 |
| `selfcheck` | 检查运行资源是否完整 |
| `service status\|install\|uninstall [--apply]` | 高级可选：systemd 用户服务，默认只预演 |
| `legacy link\|unlink [--legacy DIR] [--apply]` | 高级可选：把旧 kiro-coding 安装指向本仓库，见 [docs/legacy-link.md](docs/legacy-link.md) |

用 `python3 -m coder_agents --help` 与各子命令的 `--help` 查看完整参数。

## 设置、代理与数据

- 设置文件不在仓库内：`$CODER_AGENTS_SETTINGS`，否则 `${XDG_CONFIG_HOME:-~/.config}/coder-agents/settings.json`。示例见 [examples/settings.example.json](examples/settings.example.json)，不预设任何代理。`python3 -m coder_agents settings init --apply` 复制示例。
- 代理：`proxy_defaults` 里的 `HTTPS_PROXY` 等只作默认值，显式环境变量优先。
- 面板配置与项目注册表：`~/.local/state/kiro-panel/`。目录名为兼容旧安装暂时保留 `kiro-panel`，未来改为统一命名只是计划，目前没有实现。
- 对话历史与任务日志：各项目的 `work/kiro-runs/`；任务临时目录 `work/tmp/<id>/`，本仓库的命令不会主动删除它们。面板有“清理 7 天前不活跃对话”的功能，由配置里的 `auto_cleanup_enabled` 控制：`serve` 新建的配置写入 `false`（关闭）；但**缺少该键的旧配置会按开启处理**，所以沿用旧配置时请确认它是 `false`，不想自动清理就显式写 `false`。
- 登录凭据、token 和个人路径都不应提交进仓库。

## 可选：Codex skill

不用 Codex 也能完整使用面板。如果你在用 Codex，并希望它能委派任务，可参考 [docs/codex-integration.md](docs/codex-integration.md) 里的模板生成自己的本机 skill。**无需修改全局 `AGENTS.md`**，也没有 setup 命令。只有你希望所有项目都默认委派时，才需要自己手动往个人偏好里加规则。该模板对应当前的 Kiro 能力，适配其他 CLI 要等后续实现。

## 开发与测试

```bash
python3 -m pip install "setuptools>=61" wheel   # 仅开发/打包测试需要，运行时仍零依赖
python3 -m unittest discover -s tests -v   # 后端与 CLI 测试
npm ci && npm test                          # DOM 测试（jsdom 为 devDependency）
```

缺少 Node/npm 或 jsdom 时 DOM 测试会失败，不会被跳过。

## 目录

```
coder_agents/   CLI 入口（python3 -m coder_agents）、服务安装器
scripts/        run_kiro、watch_kiro 等 Python 模块
assets/         viewer.html 与 vendor/（marked、DOMPurify 及许可证）
tests/          unittest 与 jsdom 测试
docs/           架构、Codex 集成、旧安装链接、许可证
integrations/   可选的 Codex skill 模板
examples/       示例设置
```

## 许可证

作者尚未选定许可证，仓库中没有 `LICENSE` 文件，**目前不是开源项目**。详见 [docs/licensing.md](docs/licensing.md)、[第三方声明](THIRD_PARTY_NOTICES.md)。

文档：[架构](docs/architecture.md) · [Codex 集成](docs/codex-integration.md)
