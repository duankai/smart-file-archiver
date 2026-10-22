# Smart File Archiver

> 按文件名语义把成千上万个散乱文件整理成主题文件夹，产出可检索索引，并附一个本地网页工具——搜到即用 WPS 打开。

[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)]()
[![Platform](https://img.shields.io/badge/platform-macOS-lightgrey)]()

---

## 解决什么问题

桌面堆了几千个文件，微信收了 8000 个附件，想找一份三个月前的汇报材料，只能靠记忆翻文件夹。

这个工具做三件事：

1. **聚类归档** —— 递归扫描所有文件，按文件名语义分成 10–18 个主题文件夹
2. **全量索引** —— 生成 Markdown 总目录、CSV 表格、JSON 数据源
3. **本地检索** —— 起一个网页，输入关键词秒出结果，页面内直接预览，点一下用 WPS 打开

零第三方依赖，纯 Python 标准库。

## 特性

| 能力 | 说明 |
|---|---|
| 语义聚类 | 领域专属词高权重、通用文书词低权重，避免「标准」这类泛词把一切吞掉 |
| 置信度分级 | `high` / `medium` / `low`，低置信度文件可单独筛出来人工复核 |
| 垃圾识别 | 自动识别 Office 锁文件、LaTeX 中间产物、WPS 导出残留、哈希命名文件 |
| 双重模式 | `move` 移动 / `copy` 复制。微信、QQ 等应用目录自动用 copy，不破坏聊天附件关联 |
| 中断续跑 | `--resume` 从 manifest 幂等恢复，被系统拦截也不丢已完成项 |
| 完全可撤销 | 每次归档写 `manifest.json`，一条命令反向恢复 |
| 默认预演 | 不加 `--execute` 绝不改任何文件 |
| 本地检索 | 模糊搜索、分类/格式/来源筛选、按时间或大小排序、键盘操作 |
| 页面内预览 | 图片、PDF、文本类文件在详情抽屉里直接看，不用切换应用 |
| 打开历史 | 自动记录最近打开（保留 300 条），侧栏一键回访 |
| 库存统计 | 分类与格式分布、月度时间直方图、丢失文件检测 |
| macOS 应用 | 一键打包成 `.app`，自绘图标，可加入 Dock / Launchpad / Spotlight |
| WPS 直开 | 按 WPS → MS Office → 系统默认顺序探测，Finder 一键定位 |

## 快速开始

```bash
git clone https://github.com/duankai/smart-file-archiver.git && cd smart-file-archiver

# 1. 扫描（只读，不改任何东西）
python3 scripts/scan.py --root ~/Desktop --domain desktop --out scan.json

# 2. 聚类，生成方案
python3 scripts/cluster.py --scan scan.json \
        --taxonomy examples/taxonomy.json --out plan.json

# 3. 预演归档（还是不动文件）
python3 scripts/archive.py --plan plan.json --dest ~/Desktop/文件归档

# 4. 确认无误，真正执行
python3 scripts/archive.py --plan plan.json --dest ~/Desktop/文件归档 \
        --mode move --execute

# 5. 生成索引
python3 scripts/index_gen.py --plan plan.json --dest ~/Desktop/文件归档 \
        --manifest ~/Desktop/文件归档/manifest.json

# 6. 打包工具并生成 macOS 应用（可选 --install-app）
python3 scripts/bundle.py --dest ~/Desktop/文件归档 \
        --install-app "/Applications/文件搜索.app"

# 7. 或直接启动检索界面
python3 scripts/serve.py --index ~/Desktop/文件归档/index.json --open-browser
```

归档区会得到 `启动文件搜索.command`（双击即用）和 `清理桌面已归档文件.command`
（校验副本一致后把源文件移入废纸篓）。

### 关于 macOS 应用的两个坑

`.app` 双击时先探测 8766–8829 上有没有同索引的服务：有就只打开浏览器，没有就**让应用进程
原地变身为检索服务**（`exec`，不是 `nohup` 拉子进程）。这一点很关键——早先的写法是后台起子
进程再退出，但 macOS 会在应用退出时连带回收子进程所在的进程组，结果就是双击后弹「服务启动超时」、
之后一直连不上。改成 `exec` 后，应用活着服务就在；想停服务就退出该进程（`pkill -f _tool/serve.py`）。

改过内容的 `.app` 必须**重新做临时签名**，否则 Gatekeeper 判为 `no usable signature`，
`open` 静默返回、双击毫无反应。`make_app.py` 已在打包末尾自动执行
`codesign --force --deep --sign -`。

排错看日志：`~/Library/Logs/FileSearch/serve.log`（含启动器每一步）。
不要写归档区里的 `_tool/serve.log`——那个文件由别的应用创建，本应用无权追加。

索引文件被重写后服务会**自动热加载**（最多每 2 秒 stat 一次 mtime），增量同步完不用重启。

### 多来源合并

```bash
# 桌面
python3 scripts/scan.py --root ~/Desktop --domain desktop --append all.json

# 下载目录
python3 scripts/scan.py --root ~/Downloads --domain downloads --append all.json

# 微信接收文件（macOS 微信 4.x）
python3 scripts/scan.py \
  --root ~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<wxid>/msg/file \
  --domain wechat --append all.json

python3 scripts/cluster.py --scan all.json --taxonomy examples/taxonomy.json --out plan.json
```

> 微信/QQ 目录**务必用 `--mode copy`**。原文件被应用按路径引用，移动后聊天里点开附件会失效。

## 自定义分类词典

`examples/taxonomy.json` 是整个工具的判断依据，改它比改代码有效得多。

```json
{
  "id": "01",
  "name": "标准研制与AI治理",
  "priority": 95,
  "boost": 1.3,
  "strong": ["标准研制", "规范", "治理", "合规"],
  "weak": ["要求", "审查", "风险"],
  "prefer_ext": ["docx", "pdf"]
}
```

| 字段 | 作用 |
|---|---|
| `strong` | 领域专属名词，命中 +10/次（同类别封顶 20），长度 ≥4 再加 2 |
| `weak` | 通用词，命中 +3（封顶 9） |
| `boost` | 类别权重系数。领域词要压过「总结/报告/方案」这类文书类型词时调高它 |
| `priority` | 同分时的优先级 |
| `prefer_ext` | 扩展名偏好 +2，**仅在已有语义命中时生效** |

调词典的经验：

- 先跑一遍 `--preview`，看「待归类」和低置信度的文件，它们的文件名就是缺失的关键词
- 泛词（「标准」「数据」「方案」）放进 `weak`，别放 `strong`
- 某个领域总是被别的类抢走，调 `boost` 而不是无脑加词

## 安全设计

这个工具会移动你的文件，所以每层都设了刹车：

- **默认 dry-run** —— 不加 `--execute` 永远只打印计划
- **不覆盖同名文件** —— 冲突自动加 `_2`、`_3`
- **不删任何文件** —— 只做 move / copy，清理建议只出报告
- **manifest 可回溯** —— 每个文件的来源去向都有记录
- **分批校验** —— 每 200 个核对一次计数，对不上立即中断
- **复制前查磁盘** —— 剩余空间不足 1.05 倍直接中止
- **服务只监听 127.0.0.1** —— 且只允许打开索引白名单内的路径

## 撤销

```bash
# 先看会恢复什么
python3 scripts/rollback.py --manifest ~/Desktop/文件归档/manifest.json --dry-run

# 执行恢复
python3 scripts/rollback.py --manifest ~/Desktop/文件归档/manifest.json
```

原路径已被占用时跳过并报告，绝不覆盖。

## 产出物

```
文件归档/
├── INDEX.md                    # 人读总目录
├── index.csv                   # WPS / Excel 可直接打开
├── index.json                  # 检索工具的数据源
├── history.json                # 最近打开记录（运行时生成）
├── manifest.json               # 撤销依据
├── archive.log                 # 逐条操作日志
├── 启动文件搜索.command          # 双击启动检索
├── 清理桌面已归档文件.command     # 校验副本后清理源文件
├── _tool/                      # 检索服务与界面模板
├── 01_标准研制与AI治理/
├── 02_人工智能与大模型/
├── ...
└── _杂项/
    ├── 图片素材/
    ├── 数据表格/
    ├── 系统杂项/
    └── 待归类/
```

## 检索工具接口

| 接口 | 用途 |
|---|---|
| `GET /api/search?q=&cat=&ext=&conf=&source=&sort=&limit=` | 搜索，`sort` 支持 `mtime` / `size` |
| `GET /api/meta` | 分类、格式、来源统计（轻量） |
| `GET /api/stats` | 分类/格式分布、月度直方图、丢失文件检测（30s 缓存） |
| `GET /api/preview?path=` | 页面内预览（图片/PDF/文本，带大小上限与白名单校验） |
| `GET /api/history` | 最近打开记录 |
| `POST /api/open` `{"path":...}` | 用 WPS 打开并记入历史 |
| `POST /api/reveal` `{"path":...}` | 在 Finder 中定位 |

界面支持暗色模式（跟随系统）、详情抽屉、类型彩色图标、键盘操作
（`↑↓` 选择 / `Enter` 打开 / `空格` 预览 / `⌘Enter` 定位 / `Esc` 关闭）。

## 细分类（53 类）

粗分类只回答「大概是什么」，细分类回答「具体是哪一类」。`refine.py` 在**不移动任何文件**的
前提下，给索引里每个条目追加 `fine_name` / `fine_group` / `fine_conf`，界面按 A–H 八组展示。

```bash
python3 scripts/refine.py --index ~/Desktop/文件归档/index.json \
        --taxonomy examples/taxonomy_v3.json --report --write
```

| 分组 | 类目 |
|---|---|
| A 治理与标准 | 大模型治理、标准研制、监管政策法规、合规与审计、数据安全与隐私 |
| B AI 与技术 | 大模型技术、AI 产品与平台、算法团队汇报、技术方案与架构、算法论文与综述、数据开发与建模、代码与脚本、产品需求与设计、测试与验收 |
| C 保险业务 | 个险渠道、银保渠道、区域拓展与机构、核保理赔、客服与投诉、产品与精算、经营分析与业绩 |
| D 工作管理 | 工作汇报与总结、会议纪要、立项与可研、项目管理与推进、培训与分享、公文与通知、手册与指南、模板与范式、人事与绩效、简历与求职 |
| E 财务与商务 | 预算与费用、报销与发票、合同与协议、招标与采购、供应商与运维 |
| F 外部与市场 | 行业报告与白皮书、竞品与对标、演讲与路演、大赛与评选、调研与问卷 |
| G 个人与生活 | 党建与组织、读书笔记与书稿、个人发展、家庭与孩子、运动与健康、旅行与证件 |
| H 材料与其他 | 图片素材、音视频、电子书与资料、安装包与工具、压缩包与备份、数据表格与明细 |

打分上比粗分类多做两件事：**关键词区分度衰减**（在越多类别里出现的词权重越低，解决
「报告/材料」这类泛词导致的打平）与**短英文词边界匹配**（`cv` 不会命中 `asyncvideo`）。
零命中的文件老实标为「未分类」，不按优先级硬塞。

## 增量同步

只处理「上次同步之后」的新文件，水位线存在 `sync_state.json`，按来源域分别记录。

```bash
# 预演：看看微信自上次以来新增了什么（不改任何文件）
python3 scripts/sync.py --archive ~/Desktop/文件归档 --source wechat

# 执行：复制进 _增量/年月/细分类/，追加索引，推进水位线
python3 scripts/sync.py --archive ~/Desktop/文件归档 --source wechat --execute

# 首次接入，忽略水位线全量纳入
python3 scripts/sync.py --archive ~/Desktop/文件归档 --source wechat --all --execute
```

去重依据「路径 + 文件名 + 大小 + mtime」；已归档过的文件不会重复复制。脚本带进程锁
（`.sync.lock`），定时任务与手动执行不会并发写索引。

配合 WorkBuddy 的定时任务即可无人值守：每天固定时间跑一次微信增量。

## 工作流

```
scan.py     →  scan.json      递归建账（只读）
cluster.py  →  plan.json      语义打分、主题分配、置信度
archive.py  →  manifest.json  执行移动/复制（支持 --resume 续跑）
index_gen.py → INDEX.md       三类索引
refine.py   →  fine_* 字段    53 类细分打标（不动文件）
sync.py     →  _增量/ + 水位线 增量同步微信/桌面新文件
bundle.py   →  _tool/ + .app  打包检索工具与启动器
serve.py    →  Web 界面       搜索 / 预览 / 统计 / WPS 打开
clean_source.py → 废纸篓      校验副本一致后清理源文件
rollback.py →  原路径          按 manifest 反向恢复
build_taxonomy_v3.py → taxonomy_v3.json  从代码常量生成 53 类词典
rebuild_manifest.py → manifest.json     从 archive.log 重建撤销清单
```

每个环节的产物都是可读 JSON，可以随时人工干预后再跑下一步。

## 词典维护与清单重建

两个独立的小工具，归档跑完之后随时可用。

**改词典**：53 类词表写在 `scripts/build_taxonomy_v3.py` 的 `CATS` 常量里，
它是唯一事实源——改完重新生成，用 `git diff` 就能看清动了哪个关键词，
不用在 28KB 的 JSON 里翻找。

```bash
python3 scripts/build_taxonomy_v3.py --check   # 只校验：结构 + 可优化项
python3 scripts/build_taxonomy_v3.py           # 写回 examples/taxonomy_v3.json
```

`--check` 会做三类检查：id / 类名唯一、分组完整、boost 在 1.0–2.0（错误级）；
同一类内重复关键词、强关键词跨类共用（提示级，后者会被 `refine.py` 按区分度自动降权）。

**重建清单**：`manifest.json` 是撤销的唯一依据，万一中断、误删、残缺，
只要 `archive.log` 还在就能反推：

```bash
python3 scripts/rebuild_manifest.py --dest ~/Desktop/文件归档 --dry-run
python3 scripts/rebuild_manifest.py --dest ~/Desktop/文件归档 --verify
```

解析 `[COPY] src -> dst` / `[MOVE]` / `[FAIL]` / `[SKIP]` 四类行，按 `(src, dst)`
去重（续跑会重复写日志），按源路径推断来源域，`--verify` 逐条确认副本仍在，
覆盖前自动备份为 `manifest.json.bak-时间戳`。

## 用 API 推送（git 被代理拦截时的备选）

本机 `git push` 走 HTTPS 被代理拦截（`HTTP2 framing layer` / `CONNECT 502`）时，
`api.github.com` 往往还是通的，可以用 `scripts/push_api.py` 直接走 Git Data API 提交：

```bash
export GITHUB_TOKEN=github_pat_xxx
python3 scripts/push_api.py --repo <owner>/<name> --dry-run   # 先看差异
python3 scripts/push_api.py --repo <owner>/<name> -m "feat: xxx"
```

按照 git blob SHA1 精确比对文件内容，只上传真正变化的文件，自动处理新增、修改与删除
（删除需显式加 `--allow-delete`），生成一条普通提交。不往磁盘写 token。

## 兼容性

- **macOS**：完整支持（WPS、Finder 集成）
- **Linux**：脚本可用，`serve.py` 的打开功能需改 `xdg-open`
- **Windows**：脚本可用，需把 `serve.py` 里的 `open -a` 换成 `start`

## License

MIT
