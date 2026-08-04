# OpenClaw / Hermes 适配

这两类宿主属于“事件型宿主”：入站事件同时提供当前会话和回复目标。`0.3.0` 只规划；未来生成的图片也只能返回这个目标。

## OpenClaw

安装本地 Skill：

```bash
python3 skills/virtual-companion/scripts/companionctl.py install \
  --host openclaw \
  --apply
```

安装器会调用 OpenClaw 原生 Skill 安装机制，不猜测内部目录。`0.3.0` 的能力映射只用于判断计划是否可落地，不授权或执行真实调用：

- 图片生成能力探测：宿主 CLI 提供 `openclaw infer image generate` 时可声明可用，但本版不调用。
- 当前会话投递能力探测：只确认当前消息上下文存在；不要在 Skill 内保存联系人。
- 只有同时确认图片能力、媒体投递能力和当前回复目标时，才向决策命令传入 `--can-generate --can-deliver --has-target`。

如果缺少任一能力，保留 `blocked` 结果并自然说明原因。即使能力齐备，也只展示 `photo_plan`。

## Hermes

Hermes 的本地 Skill 目录通常位于当前 `HERMES_HOME/skills`。可以使用 Companion Kit 安装器复制公开 Skill：

```bash
python3 skills/virtual-companion/scripts/companionctl.py install \
  --host hermes \
  --apply
```

`0.3.0` 的能力映射同样只做探测：

- 图片生成：只有 `image_generate` 工具实际可用时，才声明 `--can-generate`；本版不调用该工具。
- 消息平台提供当前入站目标且支持图片附件时，才声明 `--can-deliver --has-target`。
- 后续执行适配器只能使用 Hermes 当前会话媒体流程，不得保存或猜测其他投递目标。

Skill 安装或修改后，新会话最稳妥；不要改写 Hermes 全局 `SOUL.md`。
