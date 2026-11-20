# Coder-Agents

让 Codex 调用本机 Claude Code 或 Kiro CLI 完成编码和测试，并通过网页面板查看任务、对话和执行结果。

**本项目定位为 Subagent 工作台，仅用于执行主 Agent 委派的任务。** 建议由 Codex 等主 Agent 拆解需求、派发任务、协调编码与测试，并审核和最终验收；不建议直接把本项目作为主 Agent 独立完成开发。

## 1. 启动面板

需要 macOS 或 Linux、Python 3.9+，以及 Claude Code 或 Kiro CLI。CLI 的安装方法也可在面板的“CLI 连接”中查看。

```bash
git clone https://github.com/Kevin-Kim0102/Coder-Agents.git
cd Coder-Agents
python3 -m coder_agents start
```

打开命令返回的地址，默认是 [http://127.0.0.1:47831/](http://127.0.0.1:47831/)。服务在后台运行，可以关闭终端；已启动时会复用现有面板。

## 2. 连接 CLI

1. 打开 **设置 → CLI 连接**，选择 Claude Code 或 Kiro CLI。
2. 点击 **连接**，未登录时按提示完成登录。找不到 CLI 时填写可执行文件的绝对路径。
3. 连接成功后会读取模型列表，也可以点击 **读取模型目录** 重试。

已连接的 CLI 可以暂停、启动或断开。**测试调用** 会消耗少量模型额度，按需使用。

Claude 的模型列表读取当前 CLI 配置的 API 地址（包括代理/中转），可在此保存默认模型和思考强度；Kiro 继续读取账户模型目录。若 API 不支持模型目录或只使用订阅登录，页面会说明限制，不用固定列表替代。思考强度是 CLI 选项，实际支持情况由执行时验证。

Claude 额度栏可点击“刷新”，从当前项目生效的 CLI API 地址查询 Sub2API 兼容的 `/v1/usage`。显示接口提供的已用量、上限或余额，以及更新时间；只有明确返回重置时间才显示“几h 几m 后重置”的倒计时，缺失时显示“重置时间未知”。切换 API 地址后查询随之切换，不固定服务商域名。不支持该协议或无法复用认证时会显示原因，普通页面轮询不请求外部额度接口。

## 3. 配置角色

在 **设置 → Subagent 角色** 中编辑内置角色，或新增自定义角色。每个角色可以选择 CLI、模型和思考强度，并设置职责与交付要求。

- **编码**：实现业务代码，根据测试反馈修复问题。
- **测试**：编写测试脚本，编码完成后执行测试、反馈并复测。

角色配置全局生效，保存后在下次委派时使用。Codex 负责派遣、安排执行顺序、审核和最终验收。

## 4. 让 Codex 使用

1. 在 **设置 → Codex 技能** 中复制安装提示词。
2. 在本项目的 Codex 对话中发送提示词，完成一次全局 Skill 安装。
3. 安装后新建 Codex 对话，即可在其他项目中使用。

例如，对 Codex 说：

> 使用 $coder-agents，让编码和测试角色完成登录功能，由你协调并验收。

面板中的直接消息用于补充任务指令、排查问题和查看执行结果；完整开发任务建议交给主 Agent 统一协调。

界面会显示任务角色。角色续聊使用最新角色配置；已停用或更换 CLI 时会提示处理。角色任务使用角色模型，普通新对话使用 Claude 默认配置，已有普通对话保留原选择。

执行中的任务可单独停止，已修改的项目文件保留；异常退出的任务在确认进程已结束后标记中断。失败时可打开诊断并复制给 Codex，权限不足不会自动扩权或重新提交。

Codex 也可以通过统一命令管理任务：

```bash
python3 -m coder_agents tasks list
python3 -m coder_agents tasks show --run-dir /path/to/run
python3 -m coder_agents tasks stop --run-dir /path/to/run
```

任务启动时自动登记项目和日志目录，因此独立 worktree 或自定义日志位置的任务也能在面板中查看。

## 5. 关闭与重新打开

安装 Skill 后，可以直接对 Codex 说：

> 关闭 Coder-Agents 面板。

> 启动 Coder-Agents，打开面板。

也可在 **设置 → 服务与资源** 中关闭服务。

也可以在项目目录终端中手动执行：

```bash
python3 -m coder_agents start   # 后台启动
python3 -m coder_agents stop    # 关闭服务
```

关闭面板服务会保留 CLI 连接、角色配置和历史记录，正在运行的 CLI 任务继续执行。

更多说明：[Codex 集成](docs/codex-integration.md) · [技术文档](docs/architecture.md) · [许可证说明](docs/licensing.md)
