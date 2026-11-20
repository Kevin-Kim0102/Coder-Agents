# Global Roles Implementation Plan

**Goal:** 实现已确认的用户全局编码、测试及自定义角色管理与委派。

**Architecture:** scripts/agent_roles.py 负责 JSON 路径、校验、原子写入和角色查询。面板与 CLI 共用存储；run_agent 在进入原有 runner 前读取配置并组合任务；Skill 指导 Codex 调度测试准备与执行。

**Tech Stack:** Python stdlib、现有 HTML/CSS/JS 面板、unittest、jsdom。

- [x] 写 tests/test_roles.py：验证默认值、XDG、CRUD、损坏文件、并发版本和角色委派；运行确认缺少模块失败。
- [x] 实现 scripts/agent_roles.py，添加 coder_agents.cli 的 roles list/show 和 scripts/run_agent.py 的 --role；运行角色测试到绿。
- [x] 写角色 API 与 DOM 测试，确认未实现接口/页面失败；添加 watch_kiro 的认证同源角色 API 和 viewer 的角色管理页。
- [x] 更新 Skill 模板、配置提示词、Codex 集成文档，明确两阶段测试与反馈流程；加入运行资源清单。
- [x] 修复上轮导航调整遗留的窄屏测试顺序，运行完整 Python/DOM 检查；重启面板验证真实页面。

本次在当前对话执行；不创建额外线程，不自动提交已有未提交工作。

验证结果：126 项 Python 测试、32 项 DOM 测试通过，运行资源 selfcheck 与渲染后的 Skill 校验通过。真实面板已确认角色列表与测试角色编辑表单。
