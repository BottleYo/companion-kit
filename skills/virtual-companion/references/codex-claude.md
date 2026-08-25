# Codex / Claude 桌面适配

这两类宿主都只能把结果返回当前任务，不推断外部联系人，也不默认投递到 IM。四个宿主是独立安装项；用户只安装一个完全正常。

## Codex

安装完整 Plugin：

```bash
python3 skills/virtual-companion/scripts/companionctl.py install \
  --host codex \
  --apply
```

安装器会注册仓库内的 `companion-kit-preview` marketplace，再安装完整 Plugin。Plugin 包含安静的会话 Hook，不向 Codex 暴露独立 Skill，也不检查或改动用户级 Skill 目录。

已经安装过时，在原项目目录更新代码后可以先检查再确认升级：

```bash
python3 skills/virtual-companion/scripts/companionctl.py upgrade check
python3 skills/virtual-companion/scripts/companionctl.py upgrade apply --confirm
```

升级器先保存并校验 Codex Persona、关系数据库、Identity Pack 和最近照片配方，再另存旧 Plugin。切换后的即时健康检查失败时只恢复旧程序，不用恢复点覆盖当前用户数据。该流程只适用于本地 Codex Marketplace；Claude 和其他宿主不会跟着更新。更新后需要在 `/hooks` 重新审核当前版本的四个 Hook，再新建任务完成加载验证。

`0.9.0-dev.4` 在 Codex 中只使用宿主内置图片生成能力。内置能力使用 `gpt-image-2`，图片计入用户现有 Codex 方案的使用量或额度。Daily Look、短句续拍状态和面部动态配方都只在本地生成结构化控制，不额外调用图片模型。

- 不读取、不检查也不索要 `OPENAI_API_KEY`。
- 不让用户选择 Provider、快速模式、严格模式或 API 计费方式。
- 当前 Codex 图片工具不暴露 `moderation` 参数，因此审核级别由 Codex 管理；项目不伪造 `low` 设置，也不把它写进提示词。
- 人物原型、日常照片与参考图编辑都直接调用当前 Codex 图片工具。
- 候选经用户明确确认后先保存为私有主脸参考；一张主脸即可开始，不要求初始化时准备三视图。
- 已确认主脸可以随时从面板显式更换；确认前旧脸继续生效，确认后保留 Persona、关系、历史和旧身份包，新身份从单张主脸开始并在下一个 Codex 任务加载。
- 用户明确想提高侧脸或全身稳定性时，可再各确认一张补充参考。人物照片回合由图片保护 Hook 按画面从同一 Identity Pack 写入一至两张实际工具参考，且始终包含主脸。
- 主脸只锁定脸部身份，可选体型参考只帮助保持稳定体型特征；发型、表情、头部角度、视线、嘴角、妆容、服饰、姿势和场景按本次需求变化。身体可见时默认使用偏高挑但自然的成年人物比例，并避免大头与超广角畸变。

检查状态：

```bash
python3 skills/virtual-companion/scripts/companionctl.py photo status
```

Codex 内置生图无需额外 Provider 配置。只有 OpenClaw 或 Hermes 的独立严格适配仍可能使用宿主进程中的 `OPENAI_API_KEY`，不得把这些要求带回 Codex 对话。

生成结果只返回当前任务。候选确认前不会成为固定形象。已有参考图时，可以在面板选择 PNG、JPG 或 WebP，预览并确认使用权后进入候选槽；服务端不接收任意本地路径。也可以等第一次照片请求，再上传有权使用的参考、描述后生成，或让 Persona 自己决定。聊天入口的候选 PNG 必须同时位于 Codex `generated_images`，并出现在当前任务图片工具的短期回执里；回执绑定任务、路径和内容哈希，两小时过期且只能消费一次。用户确认主脸后，人物照片回合会取回同一私有 Identity Pack 并按画面选择参考；普通画面只用主脸，侧脸或全身画面最多加入一张对应补充。任一已登记成员损坏或缺失时整包停止，不生成替代脸。

当前版本已经完成上述本地回执、暂存、确认、跨任务取回、实际图片参数改写和缺图失败关闭链路。普通新拍会移除上一张图片链，只使用 Identity Pack；明确编辑上一张时，目标还必须是同一任务最近一次成功、路径与内容都能验证的人物照片。最近四次成功照片只保存结构化 PhotoMoment，用来减少造型和构图连拍，并让图片后的文字与当张画面相呼应。真实 Codex 是否保持预期的图片工具名称和回执结构，以及最终人物相似度，仍要按开发者预览清单做端到端验收。

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
- 新装或升级宿主适配后用新任务验证，避免旧上下文沿用缓存指令。
- 不展示内部图片提示词、路由、计时或本地路径。
- 不读取现有私人 persona、聊天、记忆或照片目录。
