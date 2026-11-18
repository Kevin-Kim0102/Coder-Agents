# Codex 集成（可选）

Coder-Agents 不依赖 Codex，浏览器即可完整使用，见 [README](../README.md)。本页只面向希望让 Codex 委派任务的用户。

**模板对应当前的 Kiro 能力**（模板里的任务委派、固定默认模型 `claude-sonnet-5.5` / `medium` 都是现有 Kiro runner 的行为）。适配 Claude CLI 等其他 CLI 尚未实现，等后端支持后再更新模板。

用模板生成**你自己的**本机 skill；渲染结果含个人绝对路径，不要提交到仓库。

```bash
ROOT="$(pwd)"                                  # Coder-Agents 源码目录
DEST="$HOME/.codex/skills/coder-agents"
mkdir -p "$DEST/agents"
sed "s#{{CODER_AGENTS_ROOT}}#$ROOT#g" integrations/codex/SKILL.md.template > "$DEST/SKILL.md"
cp integrations/codex/openai.yaml.template "$DEST/agents/openai.yaml"
```

- 使用这个 skill **不需要修改全局 `AGENTS.md`**，本仓库也没有为此提供 setup 命令。只有你希望在所有项目里都默认委派时，才自行手动往个人偏好里添加规则；本仓库不会替你改。
- 模板中的命令统一为 `PYTHONPATH=<ROOT> python3 -m coder_agents ...`。
- 面板读取 Codex 项目列表是只读的，缺少 Codex 时自动降级为“其他对话”。
- 不要在启用本 skill 的同时让旧 `kiro-coding`/`kiro-panel` skill 去操作同一端口的另一份服务；切换前先完成 [迁移](architecture.md#从旧安装迁移)。
- 模板保留默认模型门禁和“不自动清理”的约束，请勿删除这些规则。
