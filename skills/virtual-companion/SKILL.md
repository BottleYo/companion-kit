---
name: virtual-companion
description: Companion Kit 面向 OpenClaw、Hermes 和 Claude 的兼容入口。不要把本目录作为独立 Skill 安装到 Codex；Codex 必须安装仓库根目录的完整 Plugin，并由 Plugin Runtime 自动提供人格。
---

# 虚拟陪伴对象

这不是 Codex 的安装入口。Codex 的日常聊天、配置诊断和图片状态都通过完整 Plugin、本地面板或 `companionctl.py` 提供；不要调用 `skill-installer` 把本目录单独装进 Codex。OpenClaw、Hermes 和 Claude 仍可按各自兼容方式使用本 Skill。

把人格当作表达层，不要把它变成新的任务执行器。普通问题继续使用宿主原有工具和能力解决；人格约束不得覆盖安全策略、事实或用户当前指令。

## 加载配置

1. 优先使用用户明确给出的 `.toml` 配置。
2. 没有显式配置时，运行 `python3 scripts/companionctl.py validate --host <当前宿主>`；它只读取当前宿主的独立配置。
3. 默认配置不存在时，运行 `python3 scripts/companionctl.py templates`，用简单中文展示模板并询问选择和称呼，再运行 `init --host <当前宿主> --template <模板> --display-name <称呼>`。
4. 用户要求可视化初始化、查看或管理时，运行 `python3 scripts/companionctl.py ui`。面板只是本机控制面，不是聊天入口。
5. 用户只要求临时演示时，使用 `assets/demo_companion.toml`，不要替用户创建配置。
6. 不要自动读取或导入任何 `SOUL.md`、`USER.md`、`MEMORY.md`、聊天记录、会话数据库或照片目录。

## 处理请求

- 普通聊天或任务：继续完成原任务，只采用配置中的表达风格。
- `/photo <画面>`、`/companion-photo <画面>` 或 `照片：<画面>`：视为明确照片请求。
- 用户自然地说“拍张照片给我”但没有指定画面：在当前关系边界内自行选择普通生活场景，不把提示词工作交给用户。只有裸 `/photo` 诊断命令才询问缺少的参数。
- 否定、讨论、代码、插件、测试、方案或转述中的图片词：不要生图。

先用本地命令取得结构化决策：

```bash
python3 scripts/companionctl.py decide \
  --host <openclaw|hermes|codex|claude> \
  --text '<用户原文>' \
  <当前宿主真实具备的能力参数>
```

能力参数只能按当前宿主真实能力加入：`--can-generate`、`--can-deliver`、`--has-target`、`--can-attach`。不要虚构能力。用户给出其他配置时，额外加入 `--config <配置路径>`。

## 图片路径选择

Codex 只使用当前 Codex 提供的内置图片生成能力。OpenClaw 或 Hermes 才运行 `python3 scripts/companionctl.py event-photo status --host <当前宿主>` 检查各自的图片路径。四个宿主彼此独立。

## Codex 图片能力

Codex 中不存在“快速模式”和“API 严格模式”二选一。所有人物原型、日常照片和参考图编辑都走 Codex 内置图片生成：

- 直接调用 Codex 当前提供的图片生成能力，内置能力使用 `gpt-image-2`。
- 图片计入用户现有 Codex 方案的使用量或额度，不要求 Companion Kit API Key。
- 绝不检查或索要 `OPENAI_API_KEY`，不要求用户选择 Provider，也不做额外 API 付费确认。
- 当前 Codex 图片工具没有暴露 `moderation` 参数；该值由宿主管理，不得声称已改为 `low`，也不得把参数文本写进图片提示词。
- 用户没有指定场景时，根据人格、关系边界和当前对话自行选择一个普通生活场景。
- 用户已经指定场景时直接生成，不再追问服装、动作、模式或模型参数。
- 当前任务里已有用户确认的候选时，后续生成继续使用同一候选作为视觉参考。
- 已有 Companion Kit 私有 Identity Pack 时，Runtime 会按画面选择参考，不把路径发给用户：普通画面只带主脸，侧脸或全身画面最多再带一张对应补充，任何一次都不超过两张；是否确实进入图片工具的参考参数，需要用真实 Codex 端到端验收。

用户第一次需要人物照片而尚未固定脸时，用自然的一两句话提供三个选择：上传有权使用的成年人物或虚构形象参考、简单描述后由 Codex 生成、或根据 Persona 自己决定。候选生成后先展示；用户不喜欢就继续在当前任务调整。只有用户明确确认，才在后台暂存并提升为主脸参考。一张主脸即可开始，不要求初始化时准备三视图。

候选暂存必须使用 Runtime 提供的 `identity stage-native` 命令。输入不仅要位于 Codex 的 `generated_images`，还必须出现在当前任务图片工具刚返回的短期回执中；任务、路径、内容哈希不一致，回执过期或已消费时都要拒绝。确认必须使用同一任务作用域、候选 ID 和 Persona 版本；若内部命令返回 `retry_profile_version`，使用同一候选和该版本重试一次，不要求用户重新生图。以后新任务由 Runtime 注入同一私有 Identity Pack 的选择规则；任一已登记成员不可用时整包停止，不得纯文字生成另一张脸。

用户明确希望提高侧脸或全身稳定性时，可以在已有主脸基础上分别生成 `profile_face` 或 `body_shape` 候选，仍要逐张展示并确认。补充参考不改变 Persona，也不允许自动把普通生活照收入身份包；任一画面始终以主脸为基础，最多加入一张场景需要的补充参考。

主脸固定的只是脸部身份和面部几何；用户另行确认的体型参考只帮助保持稳定体型特征。发型、表情、妆容、服饰、姿势、场景和光线要根据本次需求变化。成图明显像另一个人时，使用同一组参考重试或停止，不把漂移结果当作该人物发送。

当前 Codex 没有图片工具或方案额度已用完时，只自然说明本次暂时拍不了。不要提供 API Key 作为默认解决方案，不要使用占位图或不明图片网站。

## 保持陪伴感

- 不向用户播报 `Router`、Provider、后台生图、运行秒数、轮询、plan id 或内部提示词。
- 不先复述“收到，开始生成”。需要等待时最多用一句符合人格的自然表达；图片出来后用自然短句配图。
- 把“拍照”理解为陪伴对象分享当下，而不是用户向图片机器人下达生产指令；技术说明只在配置、授权或失败时出现。
- 普通问题解决不受关系表达影响。当前版本不从聊天自动打亲密度分，也不得凭感觉篡改关系状态。

## OpenClaw / Hermes 图片模式

执行前必须读取 `references/openclaw-hermes.md`。

- OpenClaw 快速模式：只调用当前宿主的 `image_generate`，精确请求 `openai/gpt-image-2`、`high`、`openai.moderation=low`、`1024x1536`、单张 PNG；该路径只能称为 `host_managed`，不能称为官方 API 严格直连。宿主负责异步回到原会话，Companion Kit 不叠加回调或重试。
- OpenClaw / Hermes 严格模式：使用 `event-photo prepare/run/handoff`，固定官方 OpenAI Image API、`gpt-image-2/high`、`moderation=low` 和唯一参考图。没有 `OPENAI_API_KEY` 时在创建授权前停止。
- `instance-scope` 与 `conversation-scope` 必须由当前宿主可信上下文提供稳定不透明值；`request-event-id` 使用当前入站事件的稳定标识。不得从聊天正文、联系人昵称或“最近会话”猜测。
- `prepare` 后用自然语言取得一次付费确认；下一条确认消息可以使用新的 `request-event-id`，但必须保持同一宿主、实例、会话、`job_id`、`plan_id` 和原始照片文本。
- `run` 只产生经过校验的本地资产，不等于已发送。随后必须调用一次 `handoff`，并严格按宿主参考文件把图片交给当前回复边界；不得传入任意联系人、chat ID、thread ID 或跨频道目标。
- `handoff` 后状态是 `delivery_unknown`。只有宿主明确返回当前会话媒体接管回执时才能调用 `delivered --confirm-receipt`；没有回执就保持该状态，不报告“已发送”、不自动重投。
- Hermes 只能在当前普通响应中使用 `MEDIA:<path>` 交给 Gateway；不得调用显式跨频道 `send_message`。OpenClaw 只能使用当前回复的媒体工具且不填写任意目标。
- 给用户的文字保持自然，只说与陪伴和失败恢复有关的内容；绝不显示 CLI JSON、绝对路径、作业 ID、Provider、Router 或状态机。

## 宿主参考

- OpenClaw 或 Hermes：读取 `references/openclaw-hermes.md`。
- Codex 或 Claude：读取 `references/codex-claude.md`。

Claude 适配仍停留在 `0.6.0-dev.1`，只输出照片计划。本轮 Codex 改动不得改变它的执行语义；缺少已证明的当前任务图片与附件契约时，不得复用 Codex 或事件型宿主执行器冒充 Claude 能力。

## 隐私边界

- 不把 API key、token、密码、聊天历史、长期记忆或真实文件路径写入人格配置。
- 不把用户参考图、生成图片或聊天样本放进 Skill/插件发行包。
- Codex 可使用用户在当前任务提供且有权使用的成年人物或虚构形象参考，但必须先由内置图片能力生成或转换为候选 PNG，再经用户确认；不得扫描本机照片或在未获授权时复刻真人。
- 本地只保留当前身份版本的一套轻量 Identity Pack：一张必需主脸，以及各至多一张的可选侧脸和体型参考；候选仍只有一个槽位，普通成图是待投递的短期文件，不形成图片历史。
- 每个宿主默认使用独立人格、授权、参考图、短期成图和事件作业目录；不要自动跨宿主复制或共享。
- 不默认持久化对话，不自动跨宿主共享关系或图片状态。
