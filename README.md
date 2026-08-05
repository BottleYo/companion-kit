# Companion Kit

> 当前版本是 `0.6.0-dev.1`，面向愿意帮忙试用的开发者。Codex 是第一优先级，OpenClaw、Hermes 与 Claude 暂时保留已有适配，不并行增加新功能。

虚拟陪伴最容易在真正开始聊天时露出工具感。刚选好名字和性格，对话里却先出现“Skill 已启动”；想看一张照片，又被追问场景、模式、模型和费用。人物还没来得及让人喜欢，配置流程已经把气氛打断了。

Companion Kit 想把这些准备工作放回本地面板。你只配置一次，之后打开 Codex 新任务就能直接说话。她可以陪你聊几句，也可以接着检查代码、整理资料、分析问题。人格负责怎么和你说话，事情仍由 Codex 原来的能力完成。

固定人物照片是这个项目的另一半。我们希望你说一句“拍张照片给我”，收到的是人物此刻愿意分享的一张照片，而不是一段生图任务回执。在 Codex 中，这件事只使用 Codex 自带的图片生成能力，不要求 API Key，也没有 Provider 选择。当前任务内已经可以直接生成和继续修改；跨任务自动保存同一张人物参考图还在接入。

## 现在做到哪一步了

| 部分 | 当前状态 |
| --- | --- |
| Codex 自然聊天 | 已接入完整 Plugin。新任务会安静读取本地人格和关系投影，不需要输入 `$virtual-companion` |
| Codex 原有能力 | 保留。代码、分析、写作和工具调用照常工作，人格只影响表达 |
| 本地初始化面板 | 已有四套通用模板，可设置称呼、相处起点和恋爱式陪伴开关 |
| 关系模型 | 三维状态、短期气氛、事件规则、事务和隐私存储已完成；从真实聊天自动提取事件仍未接入 |
| 固定人物图片 | Codex 直接使用内置 `gpt-image-2`，无需 API Key；自然照片指令和当前任务参考图可用，跨任务参考图桥接仍在开发 |
| OpenClaw / Hermes | 保留现有 Skill、严格图片流程和当前会话投递边界，暂不扩展 |
| Claude | 保留人格与照片计划，暂未接入真实图片执行 |

这意味着当前版本适合验证 Codex 的自然聊天入口，也适合开发者联调图片流程。它还不适合当作已经完成的普通用户产品推广。

## 它想做成什么样

理想的使用过程很短。

1. 安装项目，打开本地面板。
2. 选一套模板，改个称呼，决定从自然认识开始还是像已经熟悉一阵。
3. 第一次想看照片时，由 Codex 内置生图生成一张虚构成年人候选；喜欢后确认这个形象。
4. 以后正常打开 Codex 聊天。闲聊、拍照和解决问题都不需要先喊出功能名称。

面板只负责少数真正需要你决定的事情。Codex 日常聊天不应该反复解释人格、提示词和内部任务状态，更不应该要求用户配置生图 Provider。

## 五分钟开始体验 Codex

### 1. 下载项目

```bash
git clone https://github.com/BottleYo/companion-kit.git
cd companion-kit
```

需要 Python 3.11 或更高版本。Codex 中的人格聊天和图片生成都不需要单独的 OpenAI API Key。

### 2. 打开本地面板

```bash
python3 skills/virtual-companion/scripts/companionctl.py ui
```

浏览器会自动打开。面板只监听本机地址，每次启动都会生成新的随机访问令牌。

在面板里选择 Codex，然后完成这些选择。

- 人格模板。温柔治愈、元气朋友、冷静搭档、轻松幽默。
- 你想使用的称呼。
- 从自然认识开始，或者像已经熟悉一阵。
- 是否允许关系以后发展出恋人式表达。打开开关不会直接把关系变成恋人。

保存后再点“安装到 Codex”。面板会先展示安装方案，只有你再次确认才会写入 Codex。

### 3. 完成一次 Hook 信任确认

Companion Kit 使用 Codex 官方的 `SessionStart` Hook，在新任务开始时读取本地配置。Codex 不会自动信任新安装的命令 Hook。首次启用时请按 Codex 提示查看并信任它，也可以在支持的界面里打开 `/hooks` 检查。

这个 Hook 只在任务开始时工作。它不会读取本轮用户消息，不读取聊天转录，也不保存聊天正文。配置不存在或读取失败时，它会安静退出，不阻断 Codex。

### 4. 开一个新任务，直接聊天

不需要输入 Skill 名称。可以直接说：

> 你今天过得怎么样？

也可以直接把事情交给她：

> 陪我看看这个项目为什么测试失败。

如果刚刚在面板里改过人格，请新开一个任务，让 Codex 重新读取配置。

### 不使用面板

命令行也可以完成同样的初始化和安装。

```bash
python3 skills/virtual-companion/scripts/companionctl.py init \
  --host codex \
  --template warm_healer \
  --display-name '小禾'

python3 skills/virtual-companion/scripts/companionctl.py install --host codex
python3 skills/virtual-companion/scripts/companionctl.py install --host codex --apply
```

第一条安装命令只预览，第二条带 `--apply` 的命令才会注册项目内的 Codex marketplace 并安装完整 Plugin。

## 人格怎样工作

人格是一层很薄的表达上下文。新任务开始时，Codex 会拿到称呼、性格倾向、说话习惯、人物边界、外观描述和当前关系投影。它还会拿到两条明确规则。

- 闲聊时自然地用第一人称说话，不介绍模板，不播报加载过程，也不虚构共同经历。
- 遇到具体任务时完整使用 Codex 原能力。事实、安全和任务结果的优先级始终高于人设表达。

项目不会替换 Codex 的系统提示，也不会把陪伴对象做成另一个只会聊天的机器人。

## 关系怎样变化

关系没有一条从 1 级升到 5 级的直线。核心同时保存三个长期维度。

- 熟悉度表示彼此了解了多少。
- 信任度决定人物是否愿意接受更私人的表达。
- 亲近度影响语气、主动程度和照片表达的距离。

短期气氛单独保存，比如轻松、温柔、需要缓和。它会按时间恢复自然，不会把一次小摩擦永久写进长期关系。长期关系也不会因为几天没聊天就自动掉分。

真正改变关系的是少量、可解释的事件，例如明确感谢、尊重偏好、尊重边界、明确不适和修复。重复消息有幂等保护，同一天的变化有上限，低置信度判断不会写入。

当前 `0.6.0-dev.1` 会在 Codex 新任务开始时读取已有关系投影，但还不会从真实聊天自动生成这些事件。也就是说，多维关系核心已经可用，自动变化的接线还没有完成。项目不会用关键词偷偷给亲密度加分。

更完整的规则见[关系状态规范](docs/RELATIONSHIP_SPEC.md)。

## 固定人物照片怎样来

内置模板提供的是通用外观方向和照片质感，不包含任何真人照片。用户第一次想固定形象时，Codex 会按模板和用户调整过的外观描述，直接调用内置 `gpt-image-2` 生成一张虚构成年人候选原型。

用户喜欢并确认后，后续照片把这张图作为视觉参考，让脸、发型和整体气质尽量稳定。当前任务可以直接使用最近确认的图片；跨任务自动保存到 Companion Kit 私有参考槽的桥接仍在开发。换一套模板不会自动继承旧人物，也不会偷偷导入其他宿主的照片。

Codex 只有一条图片路径。

- 人物原型、日常照片和参考图编辑都使用 Codex 内置图片生成。
- 图片计入用户现有 Codex 方案的使用量或额度。内置图片不可用或额度用完时，本次照片能力暂停。
- Companion Kit 不检查 `OPENAI_API_KEY`，不要求选择 Provider，不发起额外 API 付费确认。
- 用户没有指定场景时，人物根据关系边界和当前对话自己选择普通生活场景。
- 用户已经说清场景时直接生成，不再追问模式、服装、动作或模型参数。

官方说明中，Codex 内置生图使用 `gpt-image-2`，消耗现有 Codex 使用额度。只有用户主动选择大批量的编程式生图时，API 才是另一个可选产品；它不属于 Companion Kit 的 Codex 日常路径。参见 [Codex 图片生成说明](https://learn.chatgpt.com/docs/image-generation)。

下一步重点是参考图桥接。它会在用户明确确认候选以后，把这一张图安全放进私有参考槽；新任务再拍照时，Codex 内置生图会自动带上这张参考图。桥接没有完成以前，项目可以承认跨任务固定尚未就绪，但不能借机引导用户配置 API。

图片细节见[Provider 契约](docs/PROVIDER_CONTRACT.md)和[数据生命周期](docs/DATA_LIFECYCLE.md)。

## 为什么还保留 Skill

Codex 的正常聊天入口已经不依赖 Skill。`virtual-companion` Skill 仍然保留，主要有三个用途。

- 检查配置和图片能力。
- 检查 Codex 内置图片能力和跨任务参考图状态。
- 为 OpenClaw、Hermes 和 Claude 提供现阶段兼容入口。

Codex 清单已经设置 `allow_implicit_invocation: false`。普通聊天不会自行启动这个 Skill，也不会由项目输出“Skill 已启动”之类内容。Codex 自己的工作耗时和折叠工具轨迹属于宿主界面，项目无法承诺隐藏。

## 只安装你正在用的工具

四个宿主互相独立。用户可以只装一个，也可以装多个。

| 宿主 | 当前安装形式 | 当前图片能力 |
| --- | --- | --- |
| Codex | 完整 Plugin，含安静会话 Hook 和显式诊断 Skill | 只用 Codex 内置生图；跨任务固定人物桥接开发中 |
| OpenClaw | 独立 Skill | 宿主管理快速模式与严格模式，只交给当前会话 |
| Hermes | 独立 Skill | 严格模式，只交给当前会话 |
| Claude | 独立 Skill | 只输出安全计划 |

OpenClaw、Hermes 和 Claude 的安装预览仍可使用：

```bash
python3 skills/virtual-companion/scripts/companionctl.py install --host hermes
python3 skills/virtual-companion/scripts/companionctl.py install --host hermes --apply
```

把 `hermes` 换成 `openclaw` 或 `claude` 即可。已经在使用 OpenClaw 或 Hermes 的用户，请先阅读[开发者内测指南](docs/DEVELOPER_PREVIEW.md)，不要拿私人会话和生产 IM 做第一次联调。

## 本地数据和隐私

这个公开仓库只有通用模板和程序，不包含任何私人 persona、聊天记录、记忆、人物照片、账号或凭据。

本地配置默认保存在项目目录之外。各宿主使用独立命名空间，不会因为人物称呼相同就自动共享。

- 人格配置保存称呼、表达方式、通用外观和不透明参考 ID。
- 关系库只保存结构化分数和事件，不保存聊天正文。
- 图片目录只保留一个候选槽、一张已确认身份参考和短期待投递图片，不形成图库。
- 授权记录只保存摘要和过期时间，不保存提示正文。

跨任务参考图接入后，图片保存前会检查 PNG 结构、尺寸、CRC 和实际解码结果，并去除文本和 EXIF 等非必要元数据。Codex 内置生图会收到本次图片提示；使用固定形象时还会收到参考图。宿主侧的使用和留存不受本地删除完全控制。

完整边界见[隐私说明](PRIVACY.md)和[威胁模型](docs/THREAT_MODEL.md)。

## 开发与检查

```bash
PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH=skills/virtual-companion/scripts \
  python3 -m unittest discover -s tests -v

PYTHONDONTWRITEBYTECODE=1 \
  python3 scripts/verify_public_bundle.py \
  --forbid-text '<你的私人角色名称>'
```

公开包检查会扫描当前工作树和 Git 对象，拒绝人物媒体、聊天状态、数据库、密钥、符号链接、缓存、本机绝对路径和调用者指定的私人标识。

想了解接下来为什么先做 Codex、关系与图片怎样接起来，可以继续看[产品逻辑](docs/PRODUCT_LOGIC.md)和[实施计划](docs/IMPLEMENTATION_PLAN.md)。
