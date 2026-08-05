# Codex / Claude 桌面适配

这两类宿主都只能把结果返回当前任务，不推断外部联系人，也不默认投递到 IM。四个宿主是独立安装项；用户只安装一个完全正常。

## Codex

安装完整 Plugin：

```bash
python3 skills/virtual-companion/scripts/companionctl.py install \
  --host codex \
  --apply
```

安装器会注册仓库内的 `companion-kit-preview` marketplace，再安装完整 Plugin。Plugin 包含安静的会话 Hook；Skill 只保留为显式诊断入口。

`0.6.0-dev.1` 在 Codex 中只使用宿主内置图片生成能力。内置能力使用 `gpt-image-2`，图片计入用户现有 Codex 方案的使用量或额度。

- 不读取、不检查也不索要 `OPENAI_API_KEY`。
- 不让用户选择 Provider、快速模式、严格模式或 API 计费方式。
- 人物原型、日常照片与参考图编辑都直接调用当前 Codex 图片工具。
- 当前任务已有确认原型时，使用最近对话图片作为参考；跨任务私有参考图桥接完成后，使用本地参考路径。

检查状态：

```bash
python3 skills/virtual-companion/scripts/companionctl.py photo status
```

Codex 内置生图无需额外 Provider 配置。只有 OpenClaw 或 Hermes 的独立严格适配仍可能使用宿主进程中的 `OPENAI_API_KEY`，不得把这些要求带回 Codex 对话。

生成结果只返回当前任务。候选确认前不会成为固定形象。当前版本不允许导入任意外部真人照片；跨任务参考图自动保存仍在开发。

## Claude Code / Desktop

安装方式：

```bash
python3 skills/virtual-companion/scripts/companionctl.py install \
  --host claude \
  --apply
```

默认安装到用户级 `.claude/skills/virtual-companion`。开发态也可以用 `claude --plugin-dir .` 加载插件根目录；开发态命令为 `/companion-kit:virtual-companion`，独立 Skill 为 `/virtual-companion`。

Claude 在 `0.6.0-dev.1` 仍只输出图片计划。即使环境存在图片工具，也不要复用 Codex 或事件型宿主的严格执行命令冒充 Claude 适配器；缺少明确的当前任务附件契约时返回 `image_generation_unavailable`。

## 共同规则

- 普通任务继续由 Codex 或 Claude 原有工具完成。
- 新装 Skill 后用新任务验证，避免旧上下文沿用缓存指令。
- 不展示内部图片提示词、路由、计时或本地路径。
- 不读取现有私人 persona、聊天、记忆或照片目录。
