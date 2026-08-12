# OpenClaw / Hermes 事件型宿主适配

这两类宿主的共同边界是“只回复当前入站会话”。Companion Kit 不保存联系人、chat ID、thread ID，也不提供任意目标参数。四个宿主互不依赖；只安装其中一个完全正常。

## 目录

- [初始化与状态](#初始化与状态)
- [OpenClaw 原生快速模式](#openclaw-原生快速模式)
- [两宿主的严格固定形象模式](#两宿主的严格固定形象模式)
- [安装边界](#安装边界)

## 初始化与状态

为当前宿主单独创建配置：

```bash
python3 scripts/companionctl.py init \
  --host <openclaw|hermes> \
  --template <模板> \
  --display-name '<称呼>'

python3 scripts/companionctl.py event-photo status \
  --host <openclaw|hermes>
```

状态页不创建付费授权、不调用图片 API，也不创建私有图片目录。严格模式的 Key 只从实际运行 Skill 的宿主进程环境读取。

## OpenClaw 原生快速模式

只有当前 OpenClaw 确实提供 `image_generate` 时才开放。先用 `decide` 得到结构化 `photo_prompt`，再进行一次工具调用：

```text
tool: image_generate
action: generate
prompt: <decision.photo_prompt>
model: openai/gpt-image-2
quality: high
openai:
  moderation: low
size: 1024x1536
outputFormat: png
count: 1
```

不传联系人或目标。OpenClaw 负责异步生成并返回原会话；Companion Kit 不轮询、不建立第二套回调、不自动补发。该路径必须标记为 `host_managed`：显式模型参数不能证明宿主实际走官方 OpenAI Image API 直连，因此它适合快速试拍，不能冒充固定形象严格模式。

对用户只给符合人格的一句自然回应和最终图片，不播报工具名、模型、后台状态或等待秒数。

## 两宿主的严格固定形象模式

严格模式固定官方 OpenAI Image API、`gpt-image-2`、`high`、`moderation=low`、单张 PNG，不接受自定义网关，不降级、不自动重试。`low` 是 OpenAI 官方提供的较低限制级别，并不关闭内容安全审核。它使用三个宿主提供的不透明上下文：

- `instance-scope`：当前安装实例内稳定；
- `conversation-scope`：当前会话内稳定；
- `request-event-id`：当前入站事件的稳定标识，只用于首次去重。

这些值不能使用联系人昵称，不能从聊天正文提取，也不能猜测“最近会话”。

### 1. 创建一次授权

```bash
python3 scripts/companionctl.py event-photo prepare \
  --host <openclaw|hermes> \
  --instance-scope '<实例不透明值>' \
  --conversation-scope '<当前会话不透明值>' \
  --request-event-id '<照片请求事件标识>' \
  --purpose <prototype|photo> \
  --text '<用户原始照片请求>'
```

`prototype` 用于还没有固定参考图的首次原型，`photo` 用于已有参考图的日常照片。此步骤只建本地短期授权；没有 `OPENAI_API_KEY` 时会在建档前停止。

向用户只说明一次：“这次会按 OpenAI API 用量计费，会发送本次画面描述；已有固定形象时还会发送一张参考图。继续吗？”照片请求本身不等于付费确认。

### 2. 用户确认后生成

确认通常来自下一条入站消息，所以新的 `request-event-id` 可以不同；宿主、实例、会话、原始照片文本、`job_id` 和 `plan_id` 必须保持一致。

```bash
python3 scripts/companionctl.py event-photo run \
  --host <openclaw|hermes> \
  --instance-scope '<同一实例>' \
  --conversation-scope '<同一会话>' \
  --request-event-id '<当前确认事件标识>' \
  --purpose <prototype|photo> \
  --text '<完全相同的原始照片请求>' \
  --job-id '<job_id>' \
  --plan-id '<plan_id>' \
  --confirm-once
```

返回 `generated` 只说明本地 PNG 已校验完成，不表示宿主已经发送。不要向用户展示返回 JSON、资产 ID 或本地路径。

### 3. 一次性交给当前回复

```bash
python3 scripts/companionctl.py event-photo handoff \
  --host <openclaw|hermes> \
  --instance-scope '<同一实例>' \
  --conversation-scope '<同一会话>' \
  --request-event-id '<当前事件标识>' \
  --job-id '<job_id>' \
  --asset-id '<asset_id>'
```

此命令只能成功一次，随后作业进入 `delivery_unknown`：

- OpenClaw：把返回的 `media` 交给当前回复的消息工具，`action=send`，不填写任意 `target`。
- Hermes：在本次普通回复中加入返回的 `response_directive`，即 `MEDIA:<绝对路径>`；Gateway 会按当前事件原路处理。不得改用显式跨频道 `send_message`。

绝对路径只允许出现在上述最后宿主边界。不能把它写进聊天正文、配置、作业记录或日志。

只有宿主明确返回“当前会话媒体已接管”的回执时，才运行：

```bash
python3 scripts/companionctl.py event-photo delivered \
  --host <openclaw|hermes> \
  --instance-scope '<同一实例>' \
  --conversation-scope '<同一会话>' \
  --request-event-id '<当前事件标识>' \
  --job-id '<job_id>' \
  --asset-id '<asset_id>' \
  --confirm-receipt
```

没有回执时不要运行。保持 `delivery_unknown`，等待短期文件按 TTL 清理；不得再次 handoff、再次调用 API 或告诉用户“已经发送”。

### 4. 固定首次人物原型

用户看到候选并明确喜欢后：

```bash
python3 scripts/companionctl.py event-photo confirm-identity \
  --host <openclaw|hermes> \
  --instance-scope '<同一实例>' \
  --conversation-scope '<同一会话>' \
  --request-event-id '<当前确认事件标识>' \
  --candidate-id '<candidate_id>' \
  --profile-version '<profile_version>'
```

只有确认后才保留一张当前身份版本的参考 PNG。用户不喜欢时不要固定；再次生成必须建立新的付费授权和再次确认，不保留候选历史。

## 安装边界

```bash
python3 scripts/companionctl.py install --host openclaw --apply
python3 scripts/companionctl.py install --host hermes --apply
```

OpenClaw 使用宿主原生 Skill 安装机制；Hermes 安装到自己的用户 Skill 目录。安装或更新后建议开启新会话。不要改写宿主全局 `SOUL.md`，不要读取或导入现有 persona、记忆、聊天和照片目录。

实现依据可参考 OpenClaw 官方的 [Image Generation](https://docs.openclaw.ai/tools/image-generation) 与 [Infer CLI](https://docs.openclaw.ai/cli/infer)，以及 Hermes 官方仓库的 [Skills 文档](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/skills.md)。当前版本已用隔离假 Provider 验证完整状态与交付适配器，但没有在公开验收中发出真实外部聊天媒体，也没有进行付费 API 调用。
