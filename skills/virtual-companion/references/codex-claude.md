# Codex / Claude 桌面适配

这两类宿主属于“桌面型宿主”：`0.3.0` 只规划；未来生成的图片也只能返回当前任务，不存在默认外部 IM 投递。

## Codex

最轻量安装方式是复制通用 Skill：

```bash
python3 skills/virtual-companion/scripts/companionctl.py install \
  --host codex \
  --apply
```

默认目标是用户级 `.agents/skills/virtual-companion`。仓库同时包含 `.codex-plugin/plugin.json`，可用于后续 marketplace 插件封装，但 manifest 本身不等于已经安装。只有当前 Codex 环境确实提供图片工具与附件返回时，才向决策命令传入 `--can-generate --can-attach`；这些参数只做能力探测，`0.3.0` 不调用图片工具。

## Claude Code / Desktop

最轻量安装方式：

```bash
python3 skills/virtual-companion/scripts/companionctl.py install \
  --host claude \
  --apply
```

默认目标是用户级 `.claude/skills/virtual-companion`。

开发态也可以直接加载插件根目录：

```bash
claude --plugin-dir .
```

仓库包含 `.claude-plugin/plugin.json`。开发态插件中的 Skill 使用 `/companion-kit:virtual-companion`；按默认方式安装为独立 Skill 后使用 `/virtual-companion`。正式 marketplace 发布不属于本阶段。如果当前 Claude 环境没有图片生成工具，就返回 `image_generation_unavailable`；能力齐备时也只展示计划，不要用占位链接或真实调用假装已经生成。

## 共同规则

- 普通任务继续由 Codex/Claude 原有工具完成。
- `0.3.0` 只展示图片计划，不产生附件或本地图片制品。
- 不推断 Telegram、Discord 或其他联系人。
- 新装 Skill 后使用新任务验证，避免旧上下文继续沿用缓存指令。
