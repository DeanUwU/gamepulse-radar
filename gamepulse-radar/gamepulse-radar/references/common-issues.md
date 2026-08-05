# GamePulse 常见问题库

历史出现过的问题、根因分析、修复步骤。

---

## 问题 1：HTML div 不平衡导致"整个板块都消失了"

**症状**：日报页翻到一半，下面所有板块（从某个 section 之后开始）全部消失不见

**根因**：`rebuild_main.py` 中 `<div class="wrap">` 开标签后少了一个 `</div>` 闭合标签

**具体位置**：模板第 120 行原本是 `</footer></div>`，应该是 `</footer></div></div>`
- 第一个 `</div>` 闭合 wrap
- 第二个 `</div>` 闭合 body 内层结构

**修复步骤**：
1. 修改 `rebuild_main.py` 模板末尾
2. 重跑 `rebuild_main.py`
3. 验证 HTML：数 `<div` 开头 = `</div>` 结尾数量（标准：133/133）

**已验证的修复**（2026-07-31）：模板已改为 `</footer></div></div>`，5 个生成的 HTML 文件均验证 div 平衡。

---

## 问题 2：日历头部日期不更新

**症状**：`calendar.html` 的 `<span class="chip fresh">日期</span>` 总是显示旧日期

**根因**：日历 chip 的日期来自 `events.json` 的 `scaffold` 模板，不是由脚本动态计算的

**修复步骤**：
1. 编辑 `events.json`，找到 `scaffold` 字段
2. 将 chip 中的日期改为当天
3. 重跑 `gen_calendar.py`

**禁止操作**：只改 `calendar.html` 不改 `events.json`——下次重跑 gen_calendar 会回退。

---

## 问题 3：词云脚本退出（date ≠ today）

**症状**：`cross_words.py` 报 `ERROR: wordcloud_terms.json 日期为 ... 不是当天`

**根因**：`wordcloud_terms.json` 的 `date` 字段不是当日

**修复步骤**：
1. 读取当日 `collectors/meme_当天.json` + `events.json.feed_events`
2. 基于当日标题和信源，重新生成 `wordcloud_terms.json`（term + href + heat + cat + sources）
3. 将 `date` 设为当天，然后重跑 `cross_words.py`

---

## 问题 4：GameLook 链接自查失败

**症状**：自洽校验 (b) 报告仍存在 GameLook 主页占位链接

**根因**：`daily.html` 或 `wordcloud.html` 中的链接是 `http://www.gamelook.com.cn/`（根页）而非具体文章

**修复步骤**：
1. 找到对应链接所在位置
2. 替换为具体文章 URL（如 `http://www.gamelook.com.cn/2026/07/598608/`）
3. 如果无法确认具体文章 → 删除该条目，不要假装有链接

---

## 问题 5：CloudStudio 部署后手机无法访问

**症状**：电脑能打开 `.woa.com` 结尾的 URL，手机不行

**根因**：`.woa.com` 是腾讯内网域名，外网无法解析

**修复**：使用 GitHub Pages（公网可访问）
- Repo: `https://github.com/dd2095965283-lgtm/gamepulse-radar`
- Pages URL: `https://dd2095965283-lgtm.github.io/gamepulse-radar/`

---

## 问题 6：npm install 极慢（netlify-cli 等）

**症状**：公司网络下 `npm install -g netlify-cli` 超时/极慢

**根因**：公司网络对 npm registry 限速

**方案**：
- 使用淘宝镜像：`npm install --registry https://registry.npmmirror.com`
- 超时仍可能发生（264 个包安装后主包未完成），耐心等待

---

## 问题 7：日历游戏名链接到错误游戏

**症状**：自洽校验 (c3) 报告游戏名↔域名错配（如"蛋仔派对"链到"暗区突围"官网）

**根因**：`events.json` 中日历条目的链接 URL 与游戏名不匹配

**修复步骤**：
1. 核对 `GAME_DOMAIN` 映射表
2. 修正 `events.json` 中对应条目的 href
3. 如 `GAME_DOMAIN` 缺少新游戏映射，补充后重跑校验

**GAME_DOMAIN 当前覆盖**（23 个游戏/类别）：
恋与深空、原神、鸣潮、LOL手游、崩坏星穹铁道、火影忍者、三国志·战略版、
永劫无间、萤火突击、绝区零、晶核、暗黑不朽、王者荣耀、CS2、第五人格、
明日方舟：终末地、明日方舟、DOTA2、阴阳师、和平精英、三角洲行动、
DNF手游、暗区突围、金铲铲之战、无畏契约、主机/PC新作、主机/PC 大作

---

## 问题 8：meme_radar 部分源失败

**症状**：`daily_refresh.py` 日志显示 meme_radar 某个源 FAIL/SKIP

**处理**：单源失败不阻断整体生成，自动跳过。关注失败频率——如某源连续多日失败，可能需要：
- 检查 API 是否变更（B站接口版本）
- 检查 wbi 签名是否过期
- 更新 UA 头

---

## 问题 9：跨板块 H 不一致（优化级）

**症状**：自洽校验 (d) 报告热度槽位仍是原始播放量

**根因**：`unify_heat.py` 未覆盖该板块

**当前状态**：TOP10/梗雷达/视觉焦点的 H 通过 unify_heat.py 统一；行业情报站等其他板块尚未纳入 H 体系（记入 backlog）。
