# Codex / Claude 桌面适配

这两类宿主都只能把结果返回当前任务，不推断外部联系人，也不默认投递到 IM。四个宿主是独立安装项；用户只安装一个完全正常。

## Codex

安装完整 Plugin：

```bash
python3 skills/virtual-companion/scripts/companionctl.py install \
  --host codex \
  --apply
```

安装器会注册仓库内的 `companion-kit-preview` marketplace，再安装完整 Plugin。Plugin 包含安静的会话 Hook，不向 Codex 暴露独立 Skill。若发现旧版 `virtual-companion` Skill，安装器会先做可恢复备份；Persona 数据不参与迁移。

`0.7.0-dev.2` 在 Codex 中只使用宿主内置图片生成能力。内置能力使用 `gpt-image-2`，图片计入用户现有 Codex 方案的使用量或额度。

- 不读取、不检查也不索要 `OPENAI_API_KEY`。
- 不让用户选择 Provider、快速模式、严格模式或 API 计费方式。
- 人物原型、日常照片与参考图编辑都直接调用当前 Codex 图片工具。
- 候选经用户明确确认后先保存为私有主脸参考；一张主脸即可开始，不要求初始化时准备三视图。
- 用户明确想提高侧脸或全身稳定性时，可再各确认一张补充参考。后续任务由 Runtime 按画面从同一 Identity Pack 选择一至两张，且始终包含主脸。
- 主脸只锁定脸部身份，可选体型参考只帮助保持稳定体型特征；发型、表情、妆容、服饰、姿势和场景按本次需求变化。

检查状态：

```bash
python3 skills/virtual-companion/scripts/companionctl.py photo status
```

Codex 内置生图无需额外 Provider 配置。只有 OpenClaw 或 Hermes 的独立严格适配仍可能使用宿主进程中的 `OPENAI_API_KEY`，不得把这些要求带回 Codex 对话。

生成结果只返回当前任务。候选确认前不会成为固定形象。第一次照片可以从三种方式开始：上传有权使用的成年人物或虚构形象参考、描述后生成、或让 Persona 自己决定。候选 PNG 必须同时位于 Codex `generated_images`，并出现在当前任务图片工具的短期回执里；回执绑定任务、路径和内容哈希，两小时过期且只能消费一次。用户确认主脸后，下一任务的 Runtime 会取回同一私有 Identity Pack 并按画面选择参考；普通画面只用主脸，侧脸或全身画面最多加入一张对应补充。任一已登记成员损坏或缺失时整包停止，不生成替代脸。

当前版本已经完成上述本地回执、暂存、确认、跨任务取回和缺图失败关闭链路。真实 Codex 是否使用预期的图片工具名称、是否给出可识别的回执路径，以及参考图是否确实进入图片工具参数，仍要按开发者预览清单做端到端验收，不能只根据 Runtime 文本认定已经通过。

## Claude Code / Desktop

安装方式：

```bash
python3 skills/virtual-companion/scripts/companionctl.py install \
  --host claude \
  --apply
```

默认安装到用户级 `.claude/skills/virtual-companion`。开发态也可以用 `claude --plugin-dir .` 加载插件根目录；开发态命令为 `/companion-kit:virtual-companion`，独立 Skill 为 `/virtual-companion`。

Claude 适配仍停留在 `0.6.0-dev.1`，只输出图片计划。本轮 Codex 改动不得改变它的执行语义。即使环境存在图片工具，也不要复用 Codex 或事件型宿主的严格执行命令冒充 Claude 适配器；缺少明确的当前任务附件契约时返回 `image_generation_unavailable`。

## 共同规则

- 普通任务继续由 Codex 或 Claude 原有工具完成。
- 新装 Skill 后用新任务验证，避免旧上下文沿用缓存指令。
- 不展示内部图片提示词、路由、计时或本地路径。
- 不读取现有私人 persona、聊天、记忆或照片目录。
