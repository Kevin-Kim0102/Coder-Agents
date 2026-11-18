# Codex 集成（可选）

Coder-Agents 不依赖 Codex。需要时，用模板生成**你自己的**本机 skill；渲染结果含个人绝对路径，不要提交到仓库。

```bash
ROOT="$(pwd)"                                  # Coder-Agents 源码目录
DEST="$HOME/.codex/skills/coder-agents"
mkdir -p "$DEST/agents"
sed "s#{{CODER_AGENTS_ROOT}}#$ROOT#g" integrations/codex/SKILL.md.template > "$DEST/SKILL.md"
cp integrations/codex/openai.yaml.template "$DEST/agents/openai.yaml"
```

- 模板中的命令统一为 `PYTHONPATH=<ROOT> python3 -m coder_agents ...`。
- 面板读取 Codex 项目列表是只读的，缺少 Codex 时自动降级为“其他对话”。
- 不要在启用本 skill 的同时让旧 `kiro-coding`/`kiro-panel` skill 去操作同一端口的另一份服务；切换前先完成 [迁移](architecture.md#从旧安装迁移)。
- 模板保留固定模型门禁和“不自动清理”的约束，请勿删除这些规则。
