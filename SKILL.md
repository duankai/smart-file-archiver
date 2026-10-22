---
name: smart-file-archiver
description: 按文件名语义聚类归档本地文件，生成可检索索引与本地 Web 搜索工具，支持用 WPS/Office 直接打开。当用户要求整理桌面/下载/微信接收文件、按主题归档、建立文件索引、做文件搜索页面时使用。触发词：整理桌面、文件归档、文件聚类、文件索引、文件太多找不到、微信文件整理、搜索文件工具。
version: 1.0.0
---

# Smart File Archiver

把散落在桌面、下载、微信接收目录里的成千上万个文件，按**文件名语义**聚类成主题文件夹，产出一份全量索引，并生成一个本地 Web 检索工具——搜到即可用 WPS 打开。

## 何时使用

- 用户说「整理桌面」「文件太乱了」「帮我归档」
- 用户说「文件太多找不到」「建个索引」「做个搜索工具」
- 用户要求整理微信/QQ 接收的文件
- 需要跨多个目录建立统一文件台账

## 铁律（不可跳过）

1. **先扫描，后动手。** 第一轮永远只读。产出扫描报告与聚类方案，等用户确认。
2. **备份先于移动。** 移动原件前必须生成清单备份（`manifest.json` 记录原路径），必要时 `cp -r` 全量备份。
3. **删除一律走回收站。** 本技能不删除任何用户文件；只做移动/复制。任何清理建议只输出报告。
4. **微信/QQ 等应用目录默认只索引不移动。** 这类目录的文件被应用按路径引用，移动会导致聊天记录里附件失效。若用户要求移动，必须明确告知后果并改用复制。
5. **分批执行。** 每批 ≤ 200 个文件，每批校验数量，出错立即停止。
6. **不动隐藏文件。** 跳过 `.DS_Store`、`._*`、`.~*`（Office 锁文件）、`.localized`。
7. **磁盘余量校验。** 复制模式前确认剩余空间 ≥ 待复制体积 × 1.2。

## 工作流

### Stage 0 — 侦察

递归扫描目标目录，统计：

- 文件总数、总体积
- 扩展名分布 Top 20
- 顶层目录及各自文件数
- 隐藏/锁文件数量

向用户报告，并确认三个决策点：

| 决策 | 选项 | 默认 |
|---|---|---|
| 应用目录（微信/QQ） | 只索引 / 复制 / 移动 | 只索引 |
| 现有文件夹 | 全量打散 / 保留清晰分类+重组大杂烩 / 打散但留来源标记 | 打散但留来源标记 |
| 归档区位置 | 桌面 / 用户指定 | 桌面 |

### Stage 1 — 扫描建账

`scripts/scan.py` 递归遍历，为每个文件产出：

```json
{
  "id": "sha1_of_path",
  "path": "/abs/path/to/file.pptx",
  "name": "文件名.pptx",
  "stem": "文件名",
  "ext": "pptx",
  "size": 1234567,
  "mtime": "2026-09-28T15:27:00",
  "source_domain": "desktop | wechat | downloads | custom",
  "origin_dir": "工作/2026/汇报"
}
```

### Stage 2 — 聚类

`scripts/cluster.py`，**以文件名为主信号**，按 `taxonomy.yaml` 打分：

**打分规则**

| 信号 | 权重 |
|---|---|
| 强关键词命中（主题专属名词，如「标准研制」「核保核赔」） | +10 / 次，同类封顶 20 |
| 弱关键词命中（通用词，如「汇报」「方案」） | +3 / 次，封顶 9 |
| 扩展名与该主题偏好一致 | +2 |
| 原目录名命中主题（仅作 tie-breaker，权重低） | +1 |
| 文件名含「模板」「template」「样例」 | 归入模板类，优先于内容类 |

**冲突消解**

1. 取最高分主题。
2. 同分时，按 taxonomy 中 `priority` 高者优先。
3. 全无命中（得分 0）：按扩展名归入兜底桶——`图片素材` / `数据表格` / `代码脚本` / `压缩包` / `音视频` / `待归类`。

**聚类数量**：由 taxonomy 命中情况自然决定，通常 10–18 个。若命中主题 < 8，合并低频主题；若 > 20，把 < 5 个文件的主题并入 `其他`。

### Stage 3 — 方案确认

输出 Markdown 表格：

```
| 主题文件夹 | 文件数 | 体积 | 代表文件（3 个） |
```

外加：待归类数量、跨主题可疑文件（得分接近的 top 5）、预计耗时。

**必须等用户确认后才执行 Stage 4。**

### Stage 4 — 执行归档

`scripts/archive.py`：

| 参数 | 作用 |
|---|---|
| `--mode move\|copy` | 全局模式；不指定则按来源域自动判定 |
| `--mode-map desktop=move,wechat=copy` | 按来源域分别指定 |
| `--execute` | 真正动手（不加则预演） |
| `--resume` | 从归档区已有 manifest 续跑，跳过已完成项 |

**自动判定规则**：来源域属于 `wechat` / `qq` / `app` / `im` 的一律 `copy`，其余 `move`。这样一次跑完多来源时，不会把微信附件搬走，也不会把桌面文件复制成两份。

执行过程：

1. 写 `manifest.json`（原路径 → 新路径映射），这是撤销依据。每 200 条落盘一次。
2. 分批（200/批），每批后核对源/目标文件数。
3. 全程写 `archive.log`。
4. 产出归档报告：成功数、失败数、冲突改名列表。

**中断恢复**：若中途被系统拦截或手动中止，先确认 `archive.log` 与 `manifest.json` 是否同步；若 manifest 落后于日志，用日志重建清单后再 `--resume` 续跑，避免丢失已完成项的撤销依据。

### Stage 4.5 — 清理源文件（仅复制模式需要）

当环境限制导致无法 `move`（例如宿主对批量删除有阈值保护），归档只能以 `copy` 完成。此时源文件仍在原位，用 `clean_source.py` 收尾：

```bash
# 预演
clean_source.py --manifest <归档区>/manifest.json --dry-run

# 确认后执行（移入废纸篓，可还原）
clean_source.py --manifest <归档区>/manifest.json --confirm
```

它逐条校验归档副本存在且大小一致才动手，一律移入 `~/.Trash`，最后清理变空的文件夹。

> 这一步必须由用户本人确认执行，不要在无人确认的情况下代跑。

### Stage 5 — 索引

产出三个文件放在归档区根目录：

- `INDEX.md` — 人类可读总目录，按主题分组，含文件名、大小、修改时间、原路径
- `index.csv` — 可导入 Excel/WPS 表格
- `index.json` — Web 工具的数据源

索引中每条记录必须包含 `path`（当前绝对路径），供打开使用。

### Stage 6 — Web 检索工具

`scripts/serve.py`：Python 标准库 HTTP 服务，无需第三方依赖。

**接口**

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 单页检索界面 |
| GET | `/api/search?q=&cat=&ext=&limit=` | 返回匹配记录 |
| POST | `/api/open` | body `{"path": "..."}`，服务端调用 `open -a wpsoffice` |
| GET | `/api/reveal?path=` | 在 Finder 中显示 |

**检索能力**：文件名模糊匹配（大小写不敏感、支持多关键词空格分隔、子序列匹配）、按主题筛选、按扩展名筛选、结果高亮、按修改时间/大小排序。

**安全**：只允许打开归档区白名单内的路径，拒绝 `..` 穿越。

启动方式（三选一，按便利度排序）：

1. **macOS 应用（推荐）**：`bundle.py --dest <归档区> --install-app "/Applications/文件搜索.app"`。
   自绘图标、双击即用，可加入 Dock / Launchpad / Spotlight。启动器会自动检测
   已在运行的同索引服务并复用（端口 8766-8829），避免重复起进程；归档区不存在时弹系统提示。
2. `启动文件搜索.command` 双击脚本（依赖终端窗口存活）。
3. 手动：`python3 _tool/serve.py --index index.json --open-browser`。

### .app 的三个必须知道的细节（踩过坑）

- **服务用双重 fork + `setsid` 拉成守护进程**：只 `nohup` 起子进程会被系统连进程组一起回收
  （双击弹「服务启动超时」、之后永远连不上）；让应用进程自己当服务（`exec`）则会导致服务在跑时
  再双击只「激活」旧实例、页面不打开。守护进程模式两者都避开。停服务 = `pkill -f _tool/serve.py`。
- **改过内容必须重新签名**：`make_app.py` 打包末尾已自动
  `codesign --force --deep --sign -`。没有有效签名时 Gatekeeper 判 `no usable signature`，
  `open` 静默返回、双击毫无反应（用 `codesign -dv` / `spctl -a` 可查）。
- **日志位置**：`~/Library/Logs/FileSearch/serve.log`。不要写归档区 `_tool/serve.log`——
  该文件由其他应用创建，本应用无权追加。
- 索引被重写后服务自动热加载（2 秒内 stat 一次 mtime），增量同步完无需重启。

## 打开文件的 App 探测顺序

```
/Applications/wpsoffice.app        （WPS，优先）
/Applications/Microsoft Word.app   / Excel / PowerPoint
open（系统默认）
```

首次运行探测并写入 `config.json`，用户可覆盖。

## 产出物清单

```
<归档区>/
├── INDEX.md
├── index.csv
├── index.json
├── manifest.json          # 撤销依据
├── archive.log
├── 启动文件搜索.command
├── 01_主题A/
├── 02_主题B/
└── ...
```

## 词典维护与清单重建

两个维护脚本，都可在归档跑完之后独立使用：

```bash
# 词典：CATS 常量是唯一事实源，改完重新生成 JSON（--check 只校验不写）
python scripts/build_taxonomy_v3.py --check        # 结构自检 + 列出可优化项
python scripts/build_taxonomy_v3.py                # 写回 examples/taxonomy_v3.json
python scripts/build_taxonomy_v3.py --out /tmp/t.json   # 写到别处做对比

# 清单：manifest 丢失/残缺时从 archive.log 重建（撤销依据）
python scripts/rebuild_manifest.py --dest <归档区> --dry-run
python scripts/rebuild_manifest.py --dest <归档区> --verify   # 顺带确认副本仍在
```

- `build_taxonomy_v3.py`：把 53 类词表写在代码里，避免在 28KB JSON 里翻找；校验 id/类名唯一、
  分组完整、boost 范围，并提示「同一类内重复关键词」和「强关键词跨类共用」
  （后者会被 `refine.py` 按区分度自动降权到 0.6，属可接受项）。
- `rebuild_manifest.py`：解析 `[COPY]/[MOVE]/[FAIL]/[SKIP]` 四类日志行，按 `(src, dst)` 去重，
  按源路径推断来源域，覆盖前自动备份旧清单。
  实测 10,313 行日志重建出 10,303 条（移动 98 / 复制 10,205），与原清单一致。

## 撤销

```bash
python scripts/rollback.py --manifest <归档区>/manifest.json --dry-run
python scripts/rollback.py --manifest <归档区>/manifest.json
```

按 manifest 反向移动，恢复到原路径。目标已存在则跳过并报告。

## 验收标准

- [ ] 扫描文件数与 `find` 结果一致（排除隐藏文件）
- [ ] 每个文件在索引中有且仅有一条记录
- [ ] 主题文件夹内文件数之和 = 归档成功数
- [ ] Web 工具能搜到任一索引记录
- [ ] 点击「打开」能用 WPS 打开真实文件
- [ ] `manifest.json` 可完整反向恢复

## 参考

- `examples/taxonomy.json` — 分类词典样例（保险/AI/标准/党建领域），17 个主题类 + 兜底桶，可整体替换为任意领域
- `assets/index_template.html` — 检索界面模板
- `scripts/bundle.py` — 把 `serve.py` 与界面模板复制进归档区，生成双击启动脚本；`--install-app` 额外产出 macOS .app
- `scripts/make_app.py` — 自绘图标（纯 Python 画 PNG → sips/iconutil 转 icns）并组装 .app 包，末尾自动 `codesign -` 临时签名，零第三方依赖

## 已达成的实测基准

在 10,307 个真实文件（桌面 2,253 + 微信 8,054）上的表现：

| 指标 | 数值 |
|---|---|
| 高置信归类 | 70% |
| 中置信 | 13% |
| 低置信（建议人工复核） | 17% |
| 完全无法归类 | 7.7% |

低置信与待归类的主要来源：微信收到的哈希命名文件、`文档1.docx` 这类无名附件。这部分诚实标记而非硬塞，是设计选择。
