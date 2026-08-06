# Codex 安全升级

升级这件事很容易写成一句“重新安装即可”。问题是，Persona、关系记录和固定人物参考不是浏览器缓存。删了不能靠刷新页面长回来。

Companion Kit 把程序和 TA 的资料分开处理：新程序可以换，用户资料默认不动。`0.7.0-dev.5` 已经接通本地 Codex Plugin 的检查、恢复点、程序切换和失败回滚。

## 最省事的用法

先在原来的项目目录更新代码：

```bash
git pull --ff-only
python3 skills/virtual-companion/scripts/companionctl.py ui
```

打开面板后找到“备份和更新”：

1. 点“检查一下”；
2. 有新版本时，面板才会显示“确认更新”；
3. 建议先结束其他正在运行的 Codex 任务，再确认；
4. 更新完成后新开一个 Codex 任务。

面板不会自己下载 GitHub 新代码，也不会在打开页面时自动替换 Plugin。这样少一点魔法，多一点可预期。

如果原项目目录有未提交修改，`git pull --ff-only` 可能会停下来。先处理这些修改，不要为了升级直接强制覆盖。

## 它实际做了什么

确认更新后，顺序固定：

1. 只读检查当前 Codex 安装、版本、数据格式和人物参考完整性；
2. 创建 Persona、关系数据库和 Identity Pack 的一致性恢复点，并逐项校验哈希；
3. 在恢复副本上试读旧 Persona，旧关系库需要迁移时也只在副本上试跑；
4. 另存并校验一份当前 Codex Plugin 程序；
5. 调用 Codex 自己的 Plugin 安装命令切换程序；
6. 再次检查版本、启用状态和用户数据；
7. 即时检查失败时，只恢复旧程序，不拿旧资料覆盖当前 Persona。

最后一条很重要。更新期间用户资料没有被原地改写，就没有理由上来先“恢复数据”。贸然覆盖反而可能抹掉刚保存的新设定。

## 哪些内容会进恢复点

会保存：

- 当前 Codex Persona；
- 关系状态和结构化关系事件；
- 已确认主脸、可选侧脸与体型参考；
- 仍在候选槽里的身份候选。

不会保存：

- 完整聊天正文，因为项目本来就不保存；
- Codex 全局记忆和个性化设置，因为项目不管理它们；
- 日常生成的短期照片、图片工具回执和运行缓存；
- API Key、Token 或 Provider 凭据；
- 其他宿主的 Persona。

程序回滚副本与用户恢复点分开存放。旧 Plugin 里没有 Persona，Persona 恢复点里也不会混进一套可执行程序。

## 命令行用法

先看，不动：

```bash
python3 skills/virtual-companion/scripts/companionctl.py upgrade check
python3 skills/virtual-companion/scripts/companionctl.py upgrade plan
```

手动创建并复查恢复点：

```bash
python3 skills/virtual-companion/scripts/companionctl.py backup create
python3 skills/virtual-companion/scripts/companionctl.py backup list
python3 skills/virtual-companion/scripts/companionctl.py backup verify --backup-id <恢复点 ID>
```

确认更新：

```bash
python3 skills/virtual-companion/scripts/companionctl.py upgrade apply --confirm
```

升级结果会返回恢复点 ID 和旧程序快照 ID。目前两者使用同一个 ID，方便一起找到，但内容仍是分开的。

如果新版本通过了即时检查，后来才发现程序层面的回归，可以只回滚 Plugin：

```bash
python3 skills/virtual-companion/scripts/companionctl.py upgrade rollback \
  --snapshot-id <程序快照 ID> \
  --confirm
```

回滚后也要新开一个 Codex 任务。已经打开的任务不会在半路换一套 Hook。

## 恢复数据为什么只恢复到新目录

恢复命令故意不直接覆盖正在使用的数据：

```bash
python3 skills/virtual-companion/scripts/companionctl.py backup recover-copy \
  --backup-id <恢复点 ID> \
  --destination <一个全新的目录>
```

命令会先重新校验恢复点，再复制到空目录。之后可以比较 Persona、关系库和参考图，确认真要恢复哪些内容。当前版本没有“闭眼覆盖全部资料”的按钮。

## 当前边界

- 安全切换目前只支持项目安装器创建的本地 Codex Marketplace。来源不是本地、原项目目录被移动，或 Marketplace 指向另一份仓库时会停止；
- 面板不会替用户执行 `git pull`，也不会读取 GitHub 账号或凭据；
- 发现比当前项目更新的数据格式或 Plugin 版本时拒绝降级；
- 程序快照和恢复点暂不自动清理。预览阶段宁愿多占一点本地空间，也不自作主张替用户扔回忆；
- 即时健康检查能发现版本、启用状态、Persona、关系库和 Identity Pack 的明显问题，不能替代新开任务后的人工体验验收；
- 自动卸载和“确认后清除全部本地数据”仍未开放。

这套流程当前只处理 Codex。OpenClaw、Hermes 和 Claude 继续保持各自 Persona 与安装边界，不会跟着一次 Codex 更新一起搬家。
