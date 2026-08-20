# 图片 Provider 能力契约（v6）

## Codex 先走宿主能力

Codex 不进入 Companion Kit Provider 选择。人物原型、日常照片和参考图编辑都直接调用 Codex 内置图片生成能力。内置能力使用 `gpt-image-2`，消耗用户现有 Codex 方案的使用量或额度。

Codex 路径不得读取或索要 `OPENAI_API_KEY`，不得展示 Provider 选择，也不得把固定人物包装成另一套 API 严格模式。跨任务参考由已确认的私有 Identity Pack 提供；Runtime 按画面要求图片工具使用主脸以及至多一张对应补充，但实际参考参数仍需端到端验收。任一已登记成员不可用时应暂停该人物生图，不能引导用户改走独立 API，也不能静默用纯文字重建另一张脸。

当前 Codex 图片工具契约没有暴露 `moderation` 参数，因此该值由 Codex 宿主管理。Companion Kit 不伪造“已设为 low”，也不把参数文本塞进提示词；以后只有宿主正式暴露该字段时才接入。

## 质量基线

OpenClaw / Hermes 的独立严格模式固定：

- 官方 OpenAI Image API；
- 实际请求模型 `gpt-image-2`；
- 请求画质 `high`；
- 内容审核级别 `low`；
- 新身份原型使用 `images/generations`；
- 固定身份后的照片使用 `images/edits` 与唯一参考图；
- 结果只能返回当前任务或当前入站会话。

缺少任一项时，严格照片能力关闭；聊天和原有问题解决继续工作。不得静默改用其他模型、兼容网关、占位图片或不明图片网站。

`moderation=low` 使用 OpenAI 官方提供的较低限制级别，并不关闭内容安全审核。`quality=high` 是请求参数证明，不是审美质量或像素级人物一致性的保证。参数定义见 [OpenAI Images API Reference](https://developers.openai.com/api/reference/resources/images/)。

## 两种证据不能混写

图片路由的证明级别分为：

- `direct_request`：执行器固定直连官方端点，并把 `gpt-image-2`、`high` 与 `moderation=low` 写入不可变请求；这是当前严格 API 路径使用的请求侧证明。
- `receipt_verified`：只有 Provider 响应确实回显模型与画质时才使用；不能用本地请求值伪造回执字段。
- `host_managed`：宿主负责模型、上游或交付，项目无法取得同等级证明；Codex 内置生图和 OpenClaw 原生快速模式属于此类。
- `unverified`：兼容协议但无法独立验证实际上游，只能在普通模式由用户明确开启，永远不能进入严格模式。

公开 OpenAI Image API 成功响应不保证回显模型与画质，因此直接 API 路由以请求侧证明为准，并把响应的请求 ID、成功或错误状态作为响应侧回执。两类证据必须分开记录。

## 路由声明与选择

每次选择必须传入不能被聊天内容覆盖的 `expected_host`。候选列表包含其他宿主、重复路由 ID 或不匹配的 `kind/proof` 时整次失败。

每条路由声明：

- `host`、稳定 `id` 与 `kind`；
- `available`、`auth_ready`；
- 请求模型、实际上游模型与请求画质；
- 是否支持参考图编辑、是否能返回结果；
- 证明级别与仅在真实存在时填写的回执字段。

Codex 固定使用宿主内置图片能力，不运行这套路由排序。其他宿主的普通模式优先顺序是合格宿主原生、已证明 API、用户明确开启的兼容网关。严格模式只接受合格的 `direct_request` 或真实 `receipt_verified` 路由。没有合格路由时返回 `no_eligible_gpt_image_2_route`，不自动降级。

## 四个宿主独立

- Codex：只使用内置 `gpt-image-2`，无需 Companion Kit API Key；主脸确认后可通过私有 Identity Pack 跨任务复用，侧脸与体型按需补充。
- OpenClaw：支持宿主管理原生快速模式和严格 API 模式；成图只能交给当前入站会话。
- Hermes：支持严格 API 模式；成图只能通过当前响应媒体边界交给 Gateway。
- Claude：当前只规划；不能把其他宿主执行器直接冒充 Claude 适配器。

用户可只安装一个宿主，也可以安装多个。每个宿主单独探测、配置和显示状态。

## 宿主原生模式

Codex 内置生图无需 Companion Kit API Key。人物原型、日常照片和参考图编辑都是正式的 Codex 产品路径，不再称为只可快速预览。模型和计量由 Codex 管理；本项目不向用户承诺单独的 `quality=high` 请求证明。固定身份由参考图是否成功复用来验收，不通过切换 Provider 来证明。

OpenClaw 原生快速模式精确请求 `openai/gpt-image-2`、`high`、`openai.moderation=low`、`1024x1536`、单张 PNG，并由宿主异步返回原会话。显式请求值不能排除 OAuth、自定义 Provider 或宿主路由，所以证明仍是 `host_managed`。OpenClaw 独占其后台回调和幂等补缺；Companion Kit 不建立第二套轮询、回调或重投。

Hermes 当前没有单独开放的通用原生快速模式；Claude 当前没有已证明的图片执行路径。

## OpenClaw / Hermes 严格模式

严格执行器有以下不可变条件：

- URL 只能是 `https://api.openai.com/v1/images/generations` 或 `/edits`；
- 凭据只读取当前进程的 `OPENAI_API_KEY`；
- 模型、画质、`moderation=low`、PNG 输出、单张请求固定；
- 不接受自定义 base URL，不调用兼容网关；
- 网络、限额、审核或解析失败时不自动重试；
- 错误信息不回显 Provider 响应正文、授权头或 Key。

真实调用前必须先创建短期单次授权。授权只保存摘要，并绑定当前任务或会话作用域、人格配置版本、身份版本、提示摘要、参考资产、目的、操作和固定路由。任何内容变化后失效；调用前先消费授权，避免进程重试造成重复计费。

OpenClaw 与 Hermes 共用请求执行器和图片校验逻辑，但不共用默认配置、授权、参考图、短期成图或事件作业。每个宿主进程分别读取自己的 `OPENAI_API_KEY`；Key 不复制、不持久化。Codex 不调用这套执行器。

## 固定人物原型

模板只提供通用虚构成年人文字，不携带真人照片。Codex 的最小闭环：

1. 用户可以用一句话或模板建立 Persona，外观身份可以留空；
2. 已有参考图时，可在面板选择有权使用的成年人物或虚构形象；没有时，第一次照片请求可以描述后生成，或让 Persona 自己决定；
3. 面板把当次选择的图片转换并清洗为候选 PNG；聊天入口由 Codex 内置生图生成或转换候选，并通过 `PostToolUse` 回执校验当前任务、工具调用、`generated_images` 路径和内容哈希；
4. 两种候选都会先展示，不自动成为身份；
5. 用户明确喜欢后，把候选提升为当前 `identity_version` 的唯一主脸参考；Persona 只保存这条不透明 ID；
6. 用户明确需要更稳定的侧脸或全身效果时，可基于主脸逐张生成 `profile_face` 或 `body_shape` 候选；确认前不进入身份包，普通日常成图不自动收录；
7. 后续任务通过私有 Identity Pack 路径交给 Codex 内置生图。普通场景只用主脸，侧脸或全身场景最多加入一张对应补充；只固定面部身份和稳定体型特征，动态改变发型、表情、头部角度、视线、嘴角、妆容、服饰、姿势和场景。身体可见时用自然成年人比例与正常人像透视，不能因脸部身份锚放大头部或压缩躯干。

OpenClaw / Hermes 严格模式仍在真实 API 调用前创建单次授权，并在用户确认后生成候选。两类宿主流程不能互相套用。

新候选替换旧候选，不保存候选历史。面板只接受文件选择器当次读取的图片内容，不提供任意路径上传；用户需要明确确认图片使用权，项目不得在未获授权时复刻真人身份。

## 生成与投递分离

图片任务遵守：

```text
planned → authorized → generating → generated → delivery_unknown → delivered
   └──────────────→ cancelled
   └──────────────→ failed
```

`generated` 只表示本地成图已通过校验。原型候选在进入 handoff 前会复制成一个会话绑定、路径唯一的短期快照，后续候选覆盖不会改变已经交给宿主的文件。`delivery_unknown` 表示该快照已经一次性交给宿主当前回复边界，但尚无明确媒体接管回执。只有宿主回执明确时才标记 `delivered` 并清理交付快照；没有回执时保持未知状态，依靠 TTL 清理，禁止自动重投。原始候选在用户确认前保持 pending，不进入唯一身份参考。

事件型宿主的首次 `request_event_id` 只用于创建去重键。付费确认通常来自下一条入站事件，因此后续事件编号可以改变；宿主、安装实例、会话、作业、单次授权、人格版本和原始照片请求必须保持一致。作业记录只保存这些上下文的摘要和不透明 ID，不保存联系人、绝对路径或聊天正文。

OpenClaw 的最终适配器只允许调用当前回复媒体工具，不接受任意目标。Hermes 的最终适配器只允许在当前普通响应中输出 `MEDIA:<绝对路径>` 供 Gateway 原路处理，不使用跨频道 `send_message`。绝对路径只在这一最后边界展开，不进入普通可见文本或持久状态。

## 日志与错误

- 允许记录版本、匿名计划 ID、状态码、证明类型和耗时。
- 禁止记录聊天正文、完整提示词、参考图内容或路径、API Key、授权头和联系人。
- 不向陪伴聊天输出 Router、Provider、轮询、运行秒数、plan ID 或内部提示词。
- Provider 的数据使用和留存由其条款控制，不属于本地删除可彻底覆盖的范围。
