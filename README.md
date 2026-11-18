# Coder-Agents

Coder-Agents 是一个本机浏览器面板，用来启动、查看和续聊 Kiro CLI 编码会话。它由原 Kiro Panel（`kiro-coding` skill 与 `kiro-panel` 插件）整理而来，保留 `scripts/` 与 `assets/` 布局。

- 平台：**仅 Linux**（本版本不承诺其他平台）。
- 无需 Codex：用浏览器即可使用。Codex 集成是可选的，见 [docs/codex-integration.md](docs/codex-integration.md)。
- 没有任何已注册项目时，仍可在“其他对话”中使用 Kiro。
- 无第三方 Python 依赖；只需 Python 3.9+、`kiro-cli`（已登录）和 `git`（审查功能）。
- 许可证：作者尚未确认，**本仓库目前不是开源项目**，见 [docs/licensing.md](docs/licensing.md)。

## 快速开始（源码克隆即可运行）

```bash
git clone <本仓库地址> Coder-Agents && cd Coder-Agents
python3 -m coder_agents --help
python3 -m coder_agents status          # 只读，显示路径、端口、活跃任务、服务状态
python3 -m coder_agents run --cwd /绝对路径/项目 --check   # 检查 Kiro 与固定模型是否可用
python3 -m coder_agents serve           # 前台启动面板，首次会创建配置
```

`serve` 首次运行会创建 `~/.local/state/kiro-panel/config.json`（权限 0600，含随机 token，固定回环端口 47831）。
面板启动后打印固定入口 `http://127.0.0.1:<端口>/`，直接用浏览器打开即可；token 不出现在页面和地址中。
若该端口已有服务在监听（例如旧版 Kiro Panel），`serve` 会拒绝启动，不会开第二个固定端口服务。

也可 `pip install .` 或 `pip install -e .` 得到 `coder-agents` 命令。`MANIFEST.in` 精确列出 sdist 内容（排除 work、缓存、个人配置），pip 从 sdist 构建 wheel 时 `setup.py` 会把 `scripts/`、`assets/`、`examples/` 打进 `coder_agents/_runtime/`，源码树缺这些文件时构建会直接失败；安装后用 `python3 -m coder_agents selfcheck` 验证。

## 命令

| 命令 | 作用 |
| --- | --- |
| `status` | 只读状态 |
| `serve [--port N]` | 前台启动面板（无需 systemd） |
| `run ...` | 运行一个 Kiro 任务，参数原样交给 `scripts/run_kiro.py` |
| `register --cwd DIR [--root DIR]` | 向运行中的面板注册项目 |
| `stop` | 关闭面板的 Python 服务（任务与历史保留） |
| `settings path\|init\|migrate-legacy [--apply]` | 用户设置；默认只预演 |
| `service status\|install\|uninstall [--apply]` | systemd 用户服务；默认只预演 |
| `legacy link\|unlink [--legacy DIR] [--apply]` | 把旧 kiro-coding 安装的 scripts/assets 指向本仓库（可回滚），见 [docs/legacy-link.md](docs/legacy-link.md) |
| `selfcheck` | 检查运行资源是否完整 |

## 设置（代理等）

设置文件不在仓库内。位置：`$CODER_AGENTS_SETTINGS`，否则 `${XDG_CONFIG_HOME:-~/.config}/coder-agents/settings.json`。
示例见 [examples/settings.example.json](examples/settings.example.json)（不预设任何代理）：

```json
{
  "proxy_defaults": { "HTTPS_PROXY": "http://proxy.example:8080" },
  "temp": { "min_free_mb": 512, "pytest_basetemp": false }
}
```

显式环境变量（`HTTPS_PROXY` 等）优先于设置文件。`python3 -m coder_agents settings init --apply` 复制示例；
从旧 kiro-coding 安装迁移用 `settings migrate-legacy --apply`（只复制，旧文件不动，不覆盖已有文件）。

## 服务安装（可选，显式执行）

```bash
python3 -m coder_agents service install            # 预演：打印将写入的配置、单元和 systemctl 步骤
python3 -m coder_agents service install --apply    # 真正安装 coder-agents.socket/.service
```

安装器：

- 单元文件使用当前 Python 与仓库的动态绝对路径；socket 只监听 `127.0.0.1` 的固定端口（沿用现有配置端口，否则 47831）。
- 沿用旧 `config.json` 与注册表，不修改；新建配置时写入 `auto_cleanup_enabled: false`。现有配置中该值不是 `false` 时拒绝安装。不安装清理定时器。
- 发现活跃任务（`checking`/`running`）时拒绝（退出码 3），不会停止任务。
- 发现旧版 `codex-kiro-panel.*` 单元或端口已被占用时拒绝；从旧服务切换是单独的后续迁移步骤，安装器不会停用或改动旧服务。

## 测试

```bash
python3 -m unittest discover -s tests -v   # 78 个：40 个后端 + 38 个 CLI/配置/安装器/旧安装链接/打包（含 sdist 解包后构建 wheel 并 selfcheck）
npm ci && npm test                          # 19 个 DOM 用例（jsdom 为 devDependency）
```

缺少 Node/npm 或 jsdom 时 DOM 测试会失败，而不是被跳过。

## 目录

```
coder_agents/   CLI 入口（python3 -m coder_agents）、服务安装器
scripts/        原 8 个 Python 模块（run_kiro、watch_kiro 等）
assets/         viewer.html 与 vendor/（marked、DOMPurify 及许可证）
tests/          unittest 与 jsdom 测试
docs/           架构、迁移、Codex 集成、许可证
integrations/   可选的 Codex skill 模板
examples/       示例设置
```

文档：[架构与迁移说明](docs/architecture.md) · [Codex 集成](docs/codex-integration.md) · [许可证状态](docs/licensing.md) · [第三方声明](THIRD_PARTY_NOTICES.md)
