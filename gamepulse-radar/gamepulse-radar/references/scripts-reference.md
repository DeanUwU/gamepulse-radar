# GamePulse 脚本参考手册

每个核心脚本的用途、输入输出、关键参数、注意事项。

---

## daily_refresh.py — 每日刷新总入口

**用途**：一键执行完整流水线（采集→词云→信源→内容→热度→日历→主站→校验→日志）

**参数**：
- `COLLECT_SOURCES=0` 环境变量：跳过信源采集（节省时间）
- `DISCOVER_SOURCES=0` 环境变量：跳过信源自发现

**输出**：
- 更新 `daily.html` / `calendar.html` / `wordcloud.html` / `index.html`
- 写入 `自洽日志_当天.md`
- 打印运行日志到 stdout

**阻断条件**：
- meme_radar 非零退出
- 当日 `collectors/meme_当天.json` 不存在
- cross_words 非零退出
- events.json 缺失
- gen_calendar 失败
- rebuild_main 非零退出
- 词云无 H 值
- GameLook 主页占位
- 搜索链接
- 日历名域错配

---

## meme_radar.py — 梗雷达六路采集

**用途**：从 B站 × 5 + 贴吧热议 采集当日梗/游戏内容信号

**六路**：
1. B站热门（popular）：鬼畜/搞笑/翻唱区 + 高热游戏二创（≥80万播放）
2. B站热搜（search/square）：突现怪词/人名
3. 每周必看（wbi签名）：官方盖章的破圈内容
4. 梗解读合集追更 + 热评：盯"梗外之音"等合集最新集
5. 梗百科类账号追更：视频搜索按发布时间追更 梗指南/网梗指南 等
6. 贴吧热议榜：HTML 解析 hottopic 榜单

**时效红线**：
- `SERIES_MAX_AGE_DAYS = 14`：合集最新集最大天数
- `MEME_UP_MAX_AGE_DAYS = 14`：梗百科搜索结果最大天数
- `MEME_UP_MAX_PAGE = 3`：冷门关键词最多翻几页

**输出**：`collectors/meme_YYYYMMDD.json` + `.md`

**失败处理**：单路失败不阻断，状态以字典输出（OK(N)/FAIL/SKIP）

---

## cross_words.py — 词云渲染器

**用途**：将 `wordcloud_terms.json`（Agent 阅读理解产出）渲染进 `wordcloud.html`

**设计原则**：脚本不做任何"理解"，只做渲染+安全护栏。词条由 Agent 在读标题时产出。

**安全护栏**：
- 拒绝 `search.bilibili.com` 搜索链接
- 拒绝 Steam 商店购买页（`store.steampowered.com/app/ID`），但保留 news 公告页

**H 换算**：`H = round(100 * heat / max_heat)`（组内归一化）

**字号分档**：H≥70→22px, H≥45→19px, H≥25→16px, H≥12→14px, 其余→12px

**验证**：`wordcloud_terms.json.date` 必须等于当天，否则 `sys.exit(2)`

**更新 header**：
- 替换 chip 日期为当天
- 更新热词来源说明文本（含 H 口径说明）

---

## rebuild_main.py — 主站拼装器

**用途**：从 daily/calendar/wordcloud 三个子页抽取内容拼装主站

**流程**：
1. 读 `daily.html` → 按 `DAILY_ORDER` 排序抽取 sections
2. 读 `calendar.html` → 抽取 `cal` + `forward` 两个 sections
3. 读 `wordcloud.html` → 抽取 `.wc` 词云本体 + `.wc-legend` 图例 + 样式块
4. 抽取 masthead 区块（`<!--mh-->...<!--/mh-->`）
5. 注入 GSAP 品牌色呼吸动画 + 日历"今天"高亮 JS
6. 拼装完整 HTML → 写入 `index.html`

**DAILY_ORDER**：`['brief','visual','board','radar','hot','media','rival']`

**关键模板结构**：
```html
<header>...</header>
{masthead}
<div class="wrap">
  <section id="glance">词云概览</section>
  <div class="zone-head">日报标题</div>
  {daily sections}
  <div class="zone-head">日历标题</div>
  {calendar sections}
  {source_html}
  <footer>...</footer>
</div>  <!-- 闭合 wrap -->
</div>  <!-- 闭合 body 内层 -->
```
**必须确保 `<div class="wrap">` 有且仅有两个 `</div>` 闭合**。

---

## gen_calendar.py — 日历生成器

**用途**：从 `events.json` 生成 `calendar.html`

**真源**：`events.json` 包含两部分——
- `scaffold`：日历模板（含头部日期 chip 文本、月份、格子结构）
- `feed_events`：信源快报条目

**注意**：日历头部日期来自 scaffold，改日期必须改 events.json，光改 calendar.html 下次重跑会回退。

---

## refresh_content.py — 内容板块自动刷新

**用途**：用当天 meme 采集结果重刷 `daily.html` 的 #hot（梗雷达）和 #radar（TOP10）板块

**必须在 meme_radar 之后、unify_heat 之前运行**（本步只写原始播放量，H 由 unify_heat 统一换算）

---

## unify_heat.py — 热度口径统一

**用途**：将 TOP10/梗雷达/视觉焦点 的原始播放量换算为统一 H(0–100)

**必须在 meme_radar 采集之后、rebuild_main 之前运行**

**注意**：目前仅覆盖三个板块的热度槽位（em / t10-meta / vf-cap small），行业情报站等其他板块尚未统一 H。

---

## collect_sources.py — Phase 2 信源采集

**用途**：从 `sources.toml` 读取所有注册信源，拉取最新条目到 `inbox/`

**支持格式**：rss、news_list（HTML 解析）、static（仅探活）、json_api（含 data_path 导航）

**超时**：240 秒（daily_refresh 传入）

---

## admit_sources.py — Phase 3 准入安检

**用途**：对 inbox 候选条目做准入检查（死链、错配、主页占位）

**输出**：`inbox/sources_curated.json` + `inbox/准入报告.md`

**剔除条件**：死链（HTTP 非 200）、跨游戏错配、主页根页占位

---

## promote_sources.py — Phase 4 策展晋升

**用途**：将准入通过的"adopt"条目写入 `events.json.feed_events`

---

## discover_sources.py — Phase 5 信源自发现

**用途**：扫描已登记源的同域栏目/跨域站点 + 行业种子 → 新信源建议

**节流**：内置 7 天节流，日跑不会每天联网重扫

**输出**：`inbox/sources_discovered.json` + `inbox/新信源建议.md`

---

## 运行时环境

**Python**：`C:/Users/shudizhao/.workbuddy/binaries/python/versions/3.13.12/python.exe`

**关键环境变量**：
- `PYTHONIOENCODING=utf-8`：避免 Windows GBK 编码假死
- `PYTHONUTF8=1`：强制 UTF-8 模式
- `COLLECT_SOURCES=0`：跳过信源采集
- `DISCOVER_SOURCES=0`：跳过信源自发现
