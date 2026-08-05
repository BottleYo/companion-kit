# Companion Kit

> **发布状态：0.5.0 Developer Preview（开发者内测）**
> 当前版本适合在隔离环境中联调，不面向普通用户承诺稳定服务。请先阅读[开发者内测指南](docs/DEVELOPER_PREVIEW.md)，尤其不要直接在私人会话、生产 IM 或高额度 API 账号中进行首次测试。

Companion Kit 是一个轻量、可安装的虚拟陪伴示例：用通用模板建立人格和固定人物形象，同时保留 OpenClaw、Hermes、Codex 与 Claude 原有的问题解决能力。

当前 `0.5.0` 已按宿主拆开：Codex 有原生预览和严格固定形象模式；OpenClaw 有宿主管理的原生快速模式，也可选择严格模式；Hermes 支持严格模式并只把成图交给当前入站会话；Claude 仍保持安全的照片计划。四者不需要同时安装或配置。

项目不包含任何私人 persona、聊天记录、生成照片、记忆或凭据。内置人物都是通用的虚构成年人模板。

## 支持情况

| 宿主 | 人格聊天与原任务 | 照片能力 | 安装关系 |
|---|---|---|---|
| Codex | 已支持 | 原生快速预览；严格固定人物闭环 | 可单独安装 |
| OpenClaw | 已支持 | 原生快速模式；严格固定人物；只回当前会话 | 可单独安装 |
| Hermes | 已支持 | 严格固定人物；只回当前会话 | 可单独安装 |
| Claude | 已支持 | 只输出计划 | 可单独安装 |

用户可以只用其中一个，也可以安装多个；不要求四个宿主同时存在。

## 最简单的开始方式

打开只允许本机访问的初始化与管理面板：

```bash
python3 skills/virtual-companion/scripts/companionctl.py ui
```

面板会自动打开浏览器，可以：

- 先选择正在使用的工具，再从四套通用模板中选择人格；
- 设置称呼、相处起点和是否允许关系自然发展为恋爱式陪伴；
- 预览聊天方式、固定形象与照片质感；
- 查看各工具的图片模式和当前面板启动环境；最终以对应宿主内的状态检查为准；
- 预览并二次确认安装到任意一个宿主。

面板只监听 `127.0.0.1`，每次启动使用新的随机授权链接。它不读取现有 `SOUL.md`、`USER.md`、记忆、聊天或照片目录，也不接受 API Key 输入。

不想打开网页时，直接运行：

```bash
python3 skills/virtual-companion/scripts/companionctl.py init
```

向导只问模板和称呼，两项都可以按回车使用推荐值。命令行可用 `--host codex|openclaw|hermes|claude` 指定工具；Web 面板直接下拉选择。每个工具各自保存，互不覆盖。可以用 `COMPANION_HOME` 改变私有数据根目录。

例如只为 Hermes 初始化：

```bash
python3 skills/virtual-companion/scripts/companionctl.py init \
  --host hermes \
  --template warm_healer \
  --display-name '小禾'
```

内置模板：

- 温柔治愈（推荐）：耐心、柔和；
- 元气朋友：明快、有行动力；
- 冷静搭档：理性、可靠；
- 轻松幽默：自然、有趣但有分寸。

## 安装到一个或多个宿主

先预览：

```bash
python3 skills/virtual-companion/scripts/companionctl.py install --host codex
```

确认后安装：

```bash
python3 skills/virtual-companion/scripts/companionctl.py install --host codex --apply
```

把 `codex` 换成 `openclaw`、`hermes` 或 `claude` 即可。安装器默认只预览；只有显式加入 `--apply` 或在 Web 面板二次确认后才会写入。

- OpenClaw：使用原生 `openclaw skills install`；
- Hermes：安装到 Hermes 的用户 Skill 目录；
- Codex：安装到用户级 `.agents/skills/virtual-companion`；
- Claude：安装到用户级 `.claude/skills/virtual-companion`。

新装后请开启新任务：Codex 使用 `$virtual-companion`，Claude 独立 Skill 使用 `/virtual-companion`。Skill 会按当前宿主自动读取其独立配置。

## 实际使用体验

普通任务仍由原宿主完成。例如：

> 帮我检查这个项目为什么测试失败。

陪伴对象只改变表达方式，不会截断工具能力，也不会为了“维持人设”牺牲事实准确性。

明确照片请求使用：

> 照片：雨后街角轻松散步

对用户来说只需要做很少的选择：

- 快速试拍：Codex 原生能力，或 OpenClaw 的宿主管理原生能力；
- 固定形象：严格模式，首次先生成一张候选原型，用户喜欢后固定，后续照片复用该参考图。

生图过程中不会把完整提示词、Router、Provider、后台状态、轮询秒数或本地路径发给用户。技术信息只在首次配置、付费确认或失败时出现。

## 图片 Provider 怎么配置

### 原生快速模式

Codex 直接使用当前任务提供的原生图片能力，不需要 Companion Kit API Key。OpenClaw 可以请求宿主的 `image_generate`，显式请求 `openai/gpt-image-2`、`high`、单张 PNG，并由宿主异步返回原会话。

这两条路径都标记为 `host_managed`：它们轻便，但 Companion Kit 不能把宿主路由证明成官方 OpenAI Image API 直连。原生预览不建立可审计的跨任务固定身份，也不能冒充严格模式。

### 严格模式

Codex、OpenClaw 与 Hermes 的严格模式共用同一套安全请求规则，但配置和本地状态按宿主隔离。它直接使用官方 OpenAI Image API，并固定：

- `model = gpt-image-2`；
- `quality = high`；
- 新人物原型使用 `images/generations`；
- 固定人物后使用 `images/edits` 与唯一参考图；
- 每次只生成一张，不自动重试、不切换模型或网关。

严格模式使用独立的 OpenAI API 用量计费。图片请求参数可以被本项目证明；审美质量和像素级一致性仍不能被任何固定参数完全保证。

检查 Codex 状态：

```bash
python3 skills/virtual-companion/scripts/companionctl.py photo status
```

检查事件型宿主状态：

```bash
python3 skills/virtual-companion/scripts/companionctl.py event-photo status --host openclaw
python3 skills/virtual-companion/scripts/companionctl.py event-photo status --host hermes
```

严格模式只从实际运行该 Skill 的宿主进程环境读取 `OPENAI_API_KEY`，不把 Key 写进 TOML、项目、面板、关系库或图片清单。因此用户只需给自己选择的宿主配置；安装多个时也可以只启用其中一部分的严格模式。macOS/Linux 可在启动对应宿主前用隐藏输入临时设置：

```bash
read -s COMPANION_OPENAI_KEY
export OPENAI_API_KEY="$COMPANION_OPENAI_KEY"
unset COMPANION_OPENAI_KEY
```

桌面应用或后台 Gateway 若没有继承这个终端环境，状态会显示“需要配置”。不要把 API Key 粘贴到聊天里。

## 严格模式的确认与固定形象

严格模式把“想要一张照片”和“同意一次 API 付费调用”分开：

1. 明确照片请求只创建计划，不联网；
2. 向用户说明计费、数据去向与虚构成年人边界；
3. 用户明确确认后，授权绑定当前任务、人格版本、提示摘要、参考图和路由；
4. 授权在调用前先消费，只能使用一次；进程中断也不会自动重放；
5. 首张候选需要用户第二次确认，才成为当前身份版本的唯一参考图；
6. 后续成图只交给当前任务或当前入站会话，生成成功和投递成功分别处理。

内部命令由 Skill 自动执行。高级用户可以查看完整步骤：

```bash
python3 skills/virtual-companion/scripts/companionctl.py photo prepare \
  --purpose prototype \
  --task-scope '<当前任务的不透明值>' \
  --text '照片：自然光身份参考照'
```

用户确认后，使用返回的 `plan_id` 运行同一原文：

```bash
python3 skills/virtual-companion/scripts/companionctl.py photo run \
  --purpose prototype \
  --task-scope '<同一值>' \
  --plan-id '<plan_id>' \
  --text '照片：自然光身份参考照' \
  --confirm-once
```

候选图不会形成图库。确认后只保留一张去除文本和 EXIF 等元数据的 PNG；配置只记录不透明 `reference_id`，不记录真实路径。

## 本地数据与隐私

默认私有数据在 `.companion-kit` 下，发行项目之外。Codex 使用根级目录；OpenClaw、Hermes 和 Claude 默认位于各自的 `hosts/<host>/` 命名空间：

- `profiles/default.toml`：人格与唯一不透明参考标识；
- `private/images`：一个候选槽、当前身份版本的一张参考图、待当前任务接管的短期成图；
- `private/authorizations`：短期单次授权摘要，不保存提示正文或任务原文；
- `private/event-jobs`：事件去重与交付阶段，只保存摘要和不透明 ID，不保存联系人或绝对路径；
- 关系状态库：只保存结构化数值与事件，不保存聊天正文。

参考图和成图保存前会严格校验 PNG、像素大小、CRC 和解码结果，并重写为只含像素必需分块的 PNG。目录和文件使用私有权限，路径遇到符号链接时拒绝。严格模式当前只接受 Companion Kit 当次生成且由用户确认的虚构成年人原型，不导入任意外部照片或真人身份。

Provider 会收到本次提示；已有固定形象时还会收到一张参考图。Provider 侧的使用与留存不受本地删除完全控制。

## 架构边界

```text
明确照片请求
  → CompanionKernel 只产出计划
  → 当前任务或当前会话单次授权
  → 固定官方 Image API 请求
  → 私有候选或短期成图
  → Codex 当前任务，或 OpenClaw / Hermes 当前入站会话
```

Kernel 不读取凭据、不联网、不解析图片路径。Provider 路由只判断能力与证据；API 执行器只处理固定请求；资产库只解析不透明 ID。事件型宿主在最后一刻才展开绝对路径，并且不接受联系人或任意目标。人格 TOML 和关系 SQLite 不承担图片历史。

完整规格：

- [关系状态规范](docs/RELATIONSHIP_SPEC.md)
- [图片 Provider 契约](docs/PROVIDER_CONTRACT.md)
- [数据生命周期](docs/DATA_LIFECYCLE.md)
- [威胁模型](docs/THREAT_MODEL.md)

## 发布前自检

```bash
PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH=skills/virtual-companion/scripts \
  python3 -m unittest discover -s tests -v

PYTHONDONTWRITEBYTECODE=1 \
  python3 scripts/verify_public_bundle.py \
  --forbid-text '<你的私人角色名称>'
```

第二条会扫描整个项目与 Git 对象，拒绝私人角色文件、聊天/会话状态、人物媒体、数据库、密钥文件、符号链接、缓存和常见本机绝对路径。

## 当前未包含

- Claude 的真实图片执行；
- 对 OpenClaw / Hermes 外部聊天平台真实发图的现场验收；当前只完成隔离假 Provider 和当前会话交付适配器验证；
- 任意外部参考照片上传或真人身份复刻；
- 从聊天自动提取并写入关系事件；
- 自动跨宿主共享关系或图片状态；
- 图片图库、候选历史、提示词历史或聊天历史。

这些能力会作为独立适配器逐项加入，不进入轻量公共核心。OpenClaw / Hermes 没有明确宿主回执时，状态停在 `delivery_unknown`，短期文件等待 TTL 清理，绝不自动重发或声称“已发送”。
