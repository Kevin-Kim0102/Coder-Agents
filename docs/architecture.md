# 产品架构与迁移说明

## 架构

```
浏览器 ──HTTP(127.0.0.1:固定端口)──> scripts/watch_kiro.py（面板服务，Python 标准库）
                                         │ 读取 assets/viewer.html、assets/vendor/*
                                         │ 注册表/配置在状态目录
                                         ▼
                              scripts/run_kiro.py（runner，每个任务一个进程）
                                         │ 固定模型 claude-sonnet-5.5 + medium + V3
                                         ▼
                      scripts/kiro_acp_relay.py（模型/强度门禁）──> kiro-cli
```

| 模块 | 职责 |
| --- | --- |
| `watch_kiro.py` | 本机面板服务、HTTP API、项目注册表、可选的 systemd socket 激活 |
| `run_kiro.py` | 启动 Kiro 任务，写入 `work/kiro-runs/<id>/{task.txt,events.jsonl,stderr.log,state.json}` |
| `kiro_acp_relay.py` | 转发 ACP 流量，确认“本次所选”模型与强度后才转发任务（门禁，未改动） |
| `kiro_acp.py` | ACP 客户端、kiro-cli/Node 定位 |
| `kiro_catalog.py` | 账户模型目录与强度缓存 |
| `kiro_usage.py` | 只读账户额度 |
| `kiro_review.py` | 只读文件/Git 审查 |
| `kiro_temp.py` | 任务临时目录的归属校验与显式清理 |
| `coder_agents/` | 新增：统一 CLI 与服务安装器，不改变上述模块的行为边界 |

安全约束保持不变：只监听回环；浏览器只拿 HttpOnly cookie，token 不出现在页面；模型与强度由后端对照真实目录校验；
自动清理由 `auto_cleanup_enabled` 控制且默认关闭；没有明确请求不会删除任何对话或日志。

## 数据位置

- 面板配置与注册表：`~/.local/state/kiro-panel/`（沿用旧位置，`CODER_AGENTS_CONFIG` 可指定其他配置文件，注册表与缓存在其同目录）。
- 用户设置：`${XDG_CONFIG_HOME:-~/.config}/coder-agents/settings.json`（`CODER_AGENTS_SETTINGS` 可覆盖）。
- 任务日志：各项目 `work/kiro-runs/`，临时目录 `work/tmp/`。
- 个人历史位置不会被本仓库的任何命令移动或删除。

## 相对原 Kiro Panel 的改动

1. 目录：`scripts/*.py`（8 个）与 `assets/`（viewer.html、vendor 及许可证）原样迁入，只做下列最小改动。
2. `run_kiro.py`：设置文件不再取自 skill 目录旁的 `settings.json`，改为用户配置目录或 `CODER_AGENTS_SETTINGS`；`proxy_defaults` 缺省时视为空。
3. `watch_kiro.py`：配置路径可由 `CODER_AGENTS_CONFIG` 指定；没有已注册项目且配置无 `default_project` 时不再报错，只提供“其他对话”；缺配置的提示改为指向 `service install`/`serve`。
4. 测试迁入 `tests/`，去除本机路径；DOM 测试改用 npm 的 jsdom。
5. 新增 `coder_agents` CLI、服务安装器、文档与 CI。

## 从旧安装迁移

旧安装由 `~/.codex/skills/kiro-coding` 与 `codex-kiro-panel.*` 用户单元组成。本阶段**不**改动它们：

1. 并行使用：旧服务继续占用 47831，新仓库可用 `python3 -m coder_agents status`、`run`、`settings` 等。
2. 设置迁移（可选）：`python3 -m coder_agents settings migrate-legacy --apply`。
3. 代码来源统一（无需改 systemd）：`python3 -m coder_agents legacy link --apply`，见 [legacy-link.md](legacy-link.md)。
4. 服务切换（后续阶段，需人工执行）：在没有活跃任务时，确认旧服务已按计划停用后再运行 `service install --apply`。
   安装器沿用旧 `config.json`/注册表，所以历史、端口和 token 保持不变；旧单元文件与清理定时器由使用者自行处理。
5. `~/.local/state/kiro-panel` 及其中的历史、`work/kiro-runs` 不会被迁移或删除。

## 已知限制

- 仅 Linux；服务安装依赖 systemd 用户实例。
- 支持源码克隆、`pip install -e .` 和普通安装：wheel 内含 `coder_agents/_runtime/{scripts,assets,examples}`，源码树缺文件时构建失败；`MANIFEST.in` 保证 sdist 带齐这些文件，测试会从 sdist 解包构建 wheel 并运行 selfcheck。
- 面板读取 Codex 私有状态文件 `~/.codex/.codex-global-state.json` 来列出 Codex 项目；文件缺失时只显示“其他对话”和已注册项目，不影响使用。
- “其他对话”的日期目录沿用 `~/Documents/Codex/<日期>`（日期按香港时区），可用配置项 `daily_base` 修改。
