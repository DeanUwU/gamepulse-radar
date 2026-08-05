---
name: gamepulse-radar
description: GamePulse 游戏日报雷达站——完整项目架构与运维知识库。涵盖项目结构、每日刷新流水线、热度数 H(0-100) 统一口径、自洽治理规则、常见问题修复方案、以及 GitHub Pages 部署流程。当需要在雷达站工作区执行每日刷新、修复内容问题、新增信源、新增板块、排查构建错误、部署站点、或理解项目全貌时使用本 skill。
description_zh: GamePulse 雷达站完整项目架构与运维知识库
agent_created: true
---

# GamePulse 雷达站 · 项目架构与运维

## 使用时机

当用户涉及以下任一场景时加载本 skill：

- 执行每日刷新或排查刷新流水线问题
- 修改/新增雷达站板块、信源、或页面结构
- 修复 HTML 构建错误（div 不平衡、占位符残留等）
- 统一热度口径（H 值换算、跨板块一致性）
- 新增游戏信源到 `sources.toml`
- 部署站点到 GitHub Pages 或其他托管平台
- 理解项目全貌或排查不熟悉的问题

工作目录：`C:\Users\shudizhao\WorkBuddy\Claw\雷达站`

## 项目架构

```
雷达站/
├── daily.html          ← 日报页（8板块）：简报/视觉焦点/榜单/梗雷达/TOP10/行业情报/竞对/源覆盖
├── calendar.html        ← 版本日历·前瞻哨（由 events.json → gen_calendar.py 生成）
├── wordcloud.html       ← 词云页（由 cross_words.py 渲染）
├── index.html           ← 主站唯一入口（由 rebuild_main.py 拼装）
├── style.css            ← 全站样式（含三档移动端适配：900px/640px/400px）
├── favicon.png
│
├── events.json          ← 日历真源（scaffold 模板 + feed_events 信源快报）
├── wordcloud_terms.json ← 词云真源（Agent 阅读当日标题后产出，date 必须等于当天）
├── sources.toml         ← 信源注册表（40+ 游戏官方源 + 行业媒体源）
├── sources_status.json  ← 信源存活状态（Phase 2 collect_sources 产出）
│
├── daily_refresh.py     ← ★ 每日刷新总入口（一键流水线）
├── meme_radar.py        ← 梗雷达六路采集（B站×5 + 贴吧热议）→ collectors/meme_当天.json
├── collect_sources.py   ← Phase 2：sources.toml → inbox/ 各信源最新条目
├── admit_sources.py     ← Phase 3：inbox 候选 → 准入安检（死链/错配/主页占位检查）
├── promote_sources.py   ← Phase 4：准入 adopt 条目 → events.json 信源快报
├── discover_sources.py  ← Phase 5：信源自发现（扫同域/跨域 → 新信源建议）
├── refresh_content.py   ← 内容板块自动刷新（梗雷达/TOP10/视觉焦点）
├── unify_heat.py        ← 热度口径统一（原始播放量 → H(0-100)）
├── gen_calendar.py      ← events.json → calendar.html
├── cross_words.py       ← wordcloud_terms.json → wordcloud.html（渲染+验证）
├── rebuild_main.py      ← 子页拼装 → index.html
│
├── collectors/          ← meme_radar 每日输出（meme_YYYYMMDD.json/.md）
├── inbox/               ← Phase 2-5 中间产物（sources_curated.json/准入报告/新信源建议）
├── _publish/            ← 发布用副本（HTML+CSS+favicon）
├── .git/                ← GitHub Pages 部署仓库
├── .gitignore           ← 排除 *.py / collectors/ / _publish/ / backup_*/
├── 自洽日志_YYYY-MM-DD.md ← 每日自洽校验报告
└── 迭代计划.md           ← 优化级 backlog
```

## 每日刷新流水线

`daily_refresh.py` 是唯一总入口，按以下顺序执行：

```
① meme_radar   → collectors/meme_当天.json（六路采集，单源失败跳过）
② 源覆盖报告   → daily.html 中更新"本次失败源"
③ cross_words  → 词云生成（验证 wordcloud_terms.json.date == today）
③-信源        → collect_sources（sources.toml → inbox，COLLECT_SOURCES=0 可跳过）
③-准入        → admit_sources（inbox → 准入安检，死链/错配剔除）
③-策展        → promote_sources（adopt → events.json 信源快报）
③-自发现      → discover_sources（7天节流扫描，DISCOVER_SOURCES=0 可跳过）
③-内容        → refresh_content（当天采集 → 重刷梗雷达/TOP10）
③-热度        → unify_heat（原始播放量 → 统一 H）
④ gen_calendar → events.json → calendar.html
④ rebuild_main → 子页拼装 → index.html
⑤ 自洽校验     → 9 项检查（H口径/GameLook主页占位/根页占位/待接入源告警/
                  搜索链接/日历名域匹配/信源存活/准入安检/策展过期）
⑥ 写日志       → 自洽日志_当天.md
```

**Python 运行时**：
```
C:/Users/shudizhao/.workbuddy/binaries/python/versions/3.13.12/python.exe
```
所有脚本需设置 `PYTHONIOENCODING=utf-8` + `PYTHONUTF8=1` 以避免 Windows GBK 编码导致的 emoji 假死。

## 治理规则（三大红线）

### 红线①：热度口径统一

- 全站唯一热度指标 = **热度数 H(0–100)**
- 禁止使用来源字面热度值（如 B站"热度=300"、播放量"1165万"）充当热度
- 原始量仅保留在 tooltip/附注中
- 展示格式：`热度数 H=XX ｜ 来源：… ｜ 原始：…`

### 红线②：内容时效

- 近 7 天窗口（含当日），超期自动淘汰
- 词云 `wordcloud_terms.json.date` 必须等于当日，否则 `cross_words.py` 直接退出

### 红线③：链接溯源

- 所有链接必须指向【具体文章/页面】，禁止只链主页占位
- GameLook 必须深抓到 `gamelook.com.cn/2026/07/XXXXXX/` 格式
- 禁止 `search.bilibili.com` 搜索链接
- 禁止 Steam 商店购买页（`store.steampowered.com/app/ID`），但允许 Steam 官方 news 公告页
- 日历中游戏名必须与链接域名匹配（`GAME_DOMAIN` 映射表）

### 其他约束

- **源覆盖透明**：待接入源（竞核/手游那点事/DataEye）必须 ⚠ 告警占位
- **单源失败跳过**：不阻断整体生成，失败源记入源覆盖报告
- **板块职能不重叠不真空**：8 个板块各司其职

## 常见问题与修复

### HTML div 不平衡（"整个板块都消失了"）
- **根因**：`rebuild_main.py` 模板中 `<div class="wrap">` 少一个 `</div>`
- **修复**：检查模板末尾必须是 `</footer></div></div>`（footer + wrap + body 三层闭合）

### 日历头部日期不更新
- **根因**：`calendar.html` 的 `<span class="chip fresh">日期</span>` 来自 `events.json` 的 `scaffold` 模板
- **修复**：修改 `events.json` 中的 scaffold 日期，然后重跑 `gen_calendar.py`
- **禁止**：只改 `calendar.html` 不修 events.json（下次重跑会回退）

### 跨板块 H 不一致
- **根因**：TOP10/梗雷达/视觉��点 仍展示原始播放量
- **修复**：确保 `unify_heat.py` 在流水线中运行（daily_refresh.py 已包含此步）

### 词云脚本失败
- **根因**：`wordcloud_terms.json` 不存在或 date≠today
- **修复**：基于当日 `collectors/meme_当天.json` + `events.json.feed_events` 重产词条

### GitHub Pages 部署后手机打不开
- **根因**：CloudStudio `.woa.com` 域名是腾讯内网
- **修复**：使用 GitHub Pages（公网可访问）

### npm 安装极慢
- **根因**：公司网络限速 npm registry
- **方案**：使用淘宝镜像 `--registry https://registry.npmmirror.com`

## 扩展与迭代

### 新增游戏信源
1. 在 `sources.toml` 末尾添加 `[[sources]]` 段
2. 在 `daily_refresh.py` 的 `GAME_DOMAIN` 中添加新游戏的域名映射
3. 运行 `collect_sources.py` 验证能拉到条目
4. 运行 `admit_sources.py` 验证通过准入

### 新增板块
1. 在 `daily.html` 中添加 `<section id="板块id">` 区块
2. 在 `rebuild_main.py` 的 `DAILY_ORDER` 列表中加入该板块 id
3. 在 `style.css` 中添加对应样式（含三档移动端适配）
4. 在 `daily_refresh.py` 的自洽校验中补充该板块的检查项（如适用）

### 移动端适配
- 三档断点：`@media(max-width:900px)` 平板 / `640px` 手机 / `400px` 小屏
- 关键模式：导航横向滚动、卡片单列、日历横向滚动、视觉焦点双列→单列

## 参考文件

加载以下 references 以获取详细文档：

- `references/governance.md` — 完整的自洽提示词治理体系（系统指令/校验规则/迭代SOP）
- `references/scripts-reference.md` — 每个脚本的详细说明、参数、输入输出
- `references/common-issues.md` — 历史问题库，含根因分析与修复步骤
- `references/deployment.md` — GitHub Pages / CloudStudio 部署完整流程

## 注意事项

- 已有 `gamepulse-safe-daily-refresh` skill 覆盖日常刷新操作流程，本 skill 是更上层的项目架构知识库
- 不要删除 `collectors/` 目录下的历史 meme 文件（可用于回溯分析）
- 修改 `GAME_DOMAIN` 后必须重跑自洽校验第 (c3) 项
- `events.json` 是日历的唯一真源，禁止绕过它直接改 `calendar.html`
