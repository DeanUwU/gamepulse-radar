# -*- coding: utf-8 -*-
"""refresh_content.py — 用当天 meme_radar 采集结果重刷 index.html 的内容板块

背景（这脚本要解决的问题）：
    daily_refresh.py 原先只刷新了「词云 / 日历 / 信源快报」，而
    「梗雷达(#hot)」「内容TOP10(#radar)」两块是手工策展的。
    结果就是：采集明明跑成功了（collectors/meme_YYYYMMDD.json 是新的），
    页面上却还挂着昨天的视频和昨天的日期 —— 又一个"静默失败"：
    流水线全绿、没有任何报错，但读者看到的是过期内容。

分工原则（重要）：
    · **机器可推导** 的，全自动重算：鬼畜信号 / 破圈动量 / 梗百科追更 / 贴吧热议 / TOP10 榜位
      —— TOP10 排序已加「游戏相关性 / 发售节点」加权（高信号大作保送，不被泛圈巨量视频淹没）。
    · **视觉焦点**：2026-07-31 起也改为全自动定稿（本站没有人工编辑，
      原「人工大图 + 🤖 待确认候选」的两段式等于摆设，候选永远没人去确认）。
      现在每次运行整块重建：一稿一类（六类信号各限 1 张，堵死清一色二游）+
      行业快报补位（产业/平台/主机/定档，B站流量榜覆盖不到的维度）。
    · **仍是静态块** 的只剩：TOP10 头条事件位、风险观察。
      这两块标 data-curated 日期，由 (e) 校验做过期告警。

热度口径：本脚本只写"原始播放量"，H 由后续 unify_heat.py 统一换算（保持单一职责，
         避免两个脚本各写一套 H 逻辑最后对不上）。
"""
import io, os, re, json, datetime, time, html as _html, urllib.parse
import datetime as dt

EVENTS_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "events.json")

BASE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.environ.get("RC_TARGET", os.path.join(BASE, "index.html"))
COLLECTORS = os.environ.get("RC_COLLECTORS", os.path.join(BASE, "collectors"))

# 鬼畜/破圈的分区归类
KUSO_TNAMES = ("鬼畜", "调教", "音MAD", "鬼畜剧场")

# 游戏相关性 / 发售·登顶节点 加权（让"登顶 Steam、正式发售"这类大作不被泛圈巨量视频淹没）
# —— 这是"高信号加权保送"在 TOP10 上的落点：纯播放量排序会把 214万 的发售 PV 埋到第 ~21 名，
#    因为前面全是 500~790万 的影视/搞笑巨量视频；加游戏/发售权重后大作能浮上来。
GAME_TNAME = ("单机游戏", "游戏", "电子竞技", "手机游戏", "电竞", "网络游戏", "桌游")
GAME_KW = re.compile(r'《[^》]+》|游戏|Steam|手游|端游|主机')
LAUNCH_KW = re.compile(r'发售|上线|公测|首发|开测|登顶|夺冠|夺魁|畅销榜|热销榜|销量|预约|定档|联动|周年|首测|实机')

# ---- 红线②（游戏/ACG 强相关）硬闸 + 红线①（≤5天）时效闸 ----
# 这两道闸统一加在所有「B站内容板块」(TOP10/梗雷达/视觉焦点) 的选材上，
# 确保全站只剩游戏/ACG 强相关、且为近期(≤3天)发布的内容。
# 白名单分区：游戏区 + 核心 ACG 区（B站 对游戏视频的官方分区标注最可靠）
ALLOWED_TNAMES = GAME_TNAME + (
    "动画", "动漫", "二次元", "虚拟UP主", "MAD·AMV", "短片·手书·配音",
    "鬼畜", "唱见", "翻唱", "舞蹈", "国创", "番剧")
# 强游戏信号（标题级）：用于「未列入白名单分区」的视频兜底判定。
# 注意：不含裸《》书名号——否则《2026TF家族运动会》这类偶像/综艺借书名号蒙混过关。
#   2026-08-03 修：`皮肤` 是裸词，被「黄皮肤」误命中，导致音乐区
#   《【揽佬】中国人能飞～黄皮肤才对～》混进内容 TOP10（违反红线②）。
#   改为负向断言排除肤色语境；同类裸词一并收紧。
STRONG_GAME_KW = re.compile(
    r'Steam|steam|手游|端游|主机|Switch|PS5|Xbox|任天堂|'
    r'电竞|战队|LPL|LCK|KPL|EDG|BLG|WBG|TES|JDG|T1|'
    r'联动|(?<![黄白黑褐红])皮肤|版本|赛季|抽卡|二游|定档|公测|首发|实机|新游|'
    r'番|动画|漫画|声优|番剧|新番')
# 硬黑名单分区：与游戏/ACG 无交集，且多为社会/时政/PR 敏感题材。
# 这类分区即便标题蹭到关键词也一律不放行（关键词兜底只服务「游戏内容错投到泛分区」）。
BLOCK_TNAMES = (
    "音乐综合", "社科·法律·心理", "资讯", "军事", "财经商业", "人文历史",
    "校园学习", "职场", "家居房产", "健身", "美食侦探", "汽车生活",
    "明星综合", "娱乐粉丝创作", "亲子", "科学科普")
MAX_AGE_DAYS = 3  # 红线①：B站项发布距今天数上限（2026-08-12 收紧至 3 天，与头条事件位一致）

def is_acg(v):
    """该 B站 视频是否游戏/ACG 强相关（红线②硬闸）。

    判定：① 分区在游戏/ACG 白名单 → 直接放行；
    ② 分区在硬黑名单 → 直接拒（不给关键词兜底机会）；
    ③ 其余分区（影视/搞笑/生活…）→ 必须标题含强游戏信号才放行，
       避免非游戏内容借《》书名号或泛词蒙混。"""
    t = v.get("tname") or ""
    if t in ALLOWED_TNAMES:
        return True
    if t in BLOCK_TNAMES:
        return False
    return bool(STRONG_GAME_KW.search(v.get("title") or ""))

def _is_game_vertical(v):
    """TOP10 垂类硬过滤（2026-08-17）：该视频是否够格进游戏垂类榜单。

    与 is_acg 的区别：is_acg 是红线②「不出现非游戏内容」的最低闸；
    本函数是「主动聚焦」——即使 is_acg 放行了（如靠关键词兜底的影视/生活类），
    只要不是游戏分区、也不是强游戏/ACG 标题信号，就排除出 TOP10 候选池，
    让榜单锁定：新游发售 / 官方 PV-CG 物料 / ACG 爆火热点。

    返回：True（强垂类，保送）/ "soft"（游戏分区但标题无强游戏语义，降权）/ False（排除）
    """
    t = v.get("tname") or ""
    title = v.get("title") or ""
    # 动画/二次元/虚拟UP主等核心 ACG 分区 → 强收
    if t in ("动画", "动漫", "二次元", "虚拟UP主", "MAD·AMV",
             "国创", "番剧", "鬼畜", "唱见", "翻唱"):
        return True
    # 游戏分区：再分两级——标题含强游戏语义（具体游戏名/PV/发售/玩法）才强收，
    #   仅挂游戏分区名但内容是泛娱乐（如「飞机安检模拟器」在单机分区）→ soft 降权
    if t in GAME_TNAME:
        if GAME_STRONG_TITLE.search(title):
            return True
        return "soft"
    # 非游戏分区但标题含强游戏/ACG 信号 → 强收（覆盖「游戏内容错投泛分区」）
    if STRONG_GAME_KW.search(title):
        return True
    # 其余（影视/生活/搞笑/知识… 无游戏强信号）→ 排除
    return False


# 游戏垂类「强标题语义」：具体游戏名/官方物料/发售节点/玩法词
# 用于区分「真游戏内容」与「挂着游戏分区名的泛娱乐」（飞机安检模拟器/自行车被偷等）
GAME_STRONG_TITLE = re.compile(
    r'《[^》]+》|Steam|steam|PS5|Xbox|Switch|任天堂|手游|端游|主机|'
    r'发售|上线|公测|定档|首测|实机|PV|CG|预告|演示|试玩|联动|版本|赛季|'
    r'抽卡|氪金|氪|皮肤|英雄|角色|关卡|副本|BOSS|boss|攻略|通关|速通|'
    r'原神|王者|和平精英|第五人格|明日方舟|崩坏|绝区零|鸣潮|恋与深空|'
    r'影之刃|黑神话|我的世界|MC|LOL|英雄联盟|DOTA|CS2|Valorant|永劫|'
    r'蛋仔|元梦|逆水寒|梦幻|传奇|三国|塞尔达|王国之泪|GTA|生化危机')

def _pub_age(v):
    """发布距今天数；无 pubdate 返回 None（不可判定）。"""
    ts = int(v.get("pubdate") or 0)
    if ts <= 0:
        return None
    return (time.time() - ts) / 86400.0

def fresh_bili(v):
    """红线①+②合闸：游戏/ACG 语境 且 发布 ≤5天；无 pubdate 视为不可判定→剔除。"""
    if not is_acg(v):
        return False
    a = _pub_age(v)
    if a is None or a > MAX_AGE_DAYS:
        return False
    return True

TIEBA_GAME_KW = re.compile(
    r'游戏|手游|端游|Steam|steam|单机|主机|任天堂|Switch|PS5|Xbox|联动|皮肤|版本|赛季|'
    r'赛事|战队|选手|LPL|LCK|KPL|EDG|BLG|WBG|TES|JDG|T1|电竞|充值|公测|上线|定档|'
    r'抽卡|氪|退款|外挂|开服|新游|版号|主播|动画|番|漫画|声优|cos|二游|更新|维护')
def is_game_tieba(name):
    """贴吧热议只留游戏/ACG 相关话题（红线②）。"""
    return bool(TIEBA_GAME_KW.search(name or ""))

# 视觉焦点·自动候选的「分类识别」规则：(分类key, 展示文案, 匹配正则)
# 按优先级从上往下匹配，第一条命中即定类。每类在候选里最多出现 1 条。
CAT_RULES = (
    ("launch",  "新品首发",  r'发售|首发|上线|公测|开测|定档|首测|开服|不删档'),
    ("collab",  "联动/周年", r'联动|周年|返场|复刻|重制|回归'),
    ("preview", "实机/前瞻", r'实机|演示|前瞻|预告|首曝|试玩|PV|CG|放出'),
    ("chart",   "榜单/爆款", r'登顶|夺冠|夺魁|畅销榜|热销榜|销量|破\d|万销量|好评|口碑'),
    ("esports", "电竞/赛事", r'决赛|季后赛|总决赛|战队|联赛|赛事|夏季赛|春季赛|S\d{2}|MSI|EDG|BLG|JDG|TES|WBG'),
    ("risk",    "行业/风波", r'裁员|停运|下架|致歉|封禁|外挂|炸服|回滚|争议|差评|退款|跑路|维权|诉讼|涉赌'),
)

# ---- 2026-08-17 治理：TOP10 垂类聚焦加权 ----
# 用户要求：榜单更聚焦游戏行业垂类，重点锚定
#   ① 新游发售动态  ② 官方账号发布的 PV/CG 物料  ③ 近期 ACG 领域爆火热点
# 三类信号给高权重保送；纯泛娱乐（靠 STRONG_GAME_KW 兜底进来的影视/搞笑）降权。
OFFICIAL_MATERIAL_KW = re.compile(
    r'PV|CG|预告|实机|首曝|演示|试玩|放出|公开|官宣|定档|发售|上线|公测|首测|宣传片|预告片')
ACG_BOOM_KW = re.compile(
    r'番|动画|新番|声优|二游|漫画|cos|手书|MAD|AMV|虚拟UP主|vtuber|VUP|演唱会|主题曲|OP|ED')
PAN_ENT_KW = re.compile(
    r'搞笑|影视|综艺|生活|美食|颜值|舞蹈|音乐综合|知识|鬼畜剧场')  # 泛娱乐（非游戏语境）兜底类

def top10_score(v):
    """TOP10 排序 = 「播放量 × 信号权重 × 新鲜度 × 垂类聚焦 × 当日热度数H」

    四层因子：
      1. 游戏相关性（分区/关键词/发售节点）→ 大作不被泛圈淹没
      2. 垂类聚焦（2026-08-17 新增）：
         · 官方 PV/CG/实机物料 + 新游发售节点 → ×1.8 强保送
         · ACG 爆火信号（番/动画/声优/二游爆发）→ ×1.4 上浮
         · 纯泛娱乐（靠关键词兜底进来的影视/搞笑）→ ×0.4 压底
      3. 新鲜度加成（来源权重）→ 热门榜靠前 = 当日爆发信号，周必看 = 持续热度
      4. 发售/登顶节点 → 正式发售、定档类大作额外保送
      5. 当日热度数 H（2026-08-20 新增）：读 wordcloud_terms.json 的 H(0-100) 统一口径，
         乘 (0.5 + H/100) 因子——H=100→×1.5 拔高、H=0→×0.5 压底。
         解决「播放量高但当日词云热度低」的内容霸榜（如旧周必看压住当日黑神话钟馗）。

    目标：让 TOP10 自然浮现「新游发售 / 官方物料 / ACG 热点 / 当日全网热议」，
         而非被泛娱乐巨量视频或陈旧周必看淹没。
    """
    view = v.get("view", 0)
    tname = v.get("tname") or ""
    title = v.get("title") or ""
    src = v.get("_src", "")

    # --- 游戏相关性 ---
    game_rel = (tname in GAME_TNAME) or bool(GAME_KW.search(title))
    launch = bool(LAUNCH_KW.search(title))
    game_boost = 4.0 if (game_rel and launch) else (1.5 if game_rel else 1.0)

    # --- 垂类聚焦（2026-08-17）---
    focus = 1.0
    if game_rel and (OFFICIAL_MATERIAL_KW.search(title) or launch):
        focus = 1.8   # 官方 PV/CG/实机 + 新游发售 → 强保送
    elif game_rel and ACG_BOOM_KW.search(title):
        focus = 1.4   # ACG 爆火（番/动画/声优/二游）
    elif PAN_ENT_KW.search(tname) and not game_rel:
        focus = 0.4   # 纯泛娱乐分区、无游戏语境 → 压底
    elif (not game_rel) and PAN_ENT_KW.search(title):
        focus = 0.4   # 标题也偏泛娱乐、无游戏强信号 → 压底

    # --- 新鲜度/增速因子 ---
    # B站热门榜本身按"当日热度"排序（含播放增量），排名越靠前 =
    # 当日爆发力越强。用排名位置近似"增速信号"（无需历史数据对比）。
    # 周必看 = 编辑部精选破圈证据，持续性强但不是"今日突发"。
    freshness = 1.0
    if src == "热门":
        rank_pos = v.get("_rank", 99)  # 由调用方注入
        if rank_pos <= 3:
            freshness = 1.8   # 前三名 = 今日大爆
        elif rank_pos <= 10:
            freshness = 1.4   # 前十 = 强劲上升
        elif rank_pos <= 25:
            freshness = 1.15  # 上榜 = 有动量
    elif src == "周必看":
        freshness = 1.0   # 持续热度，不额外加成也不 penalize

    # --- 当日热度数 H（2026-08-20 新增）---
    # 读 wordcloud_terms.json 的 H 映射，标题归一化后双向匹配（与 masthead _h_of 同逻辑）。
    h_factor = 1.0
    try:
        import re as _re
        _norm = lambda s: _re.sub(
            r'[\s《》〈〉\[\]()（）:：·\-_—,.，。、!！?？]', '', s).lower()
        _title_n = _norm(title)
        _hm = _top10_hmap()
        if _title_n in _hm:
            h = _hm[_title_n]
        else:
            h = 0
            for _t, _h in _hm.items():
                _nt = _norm(_t)
                if _nt and (_nt in _title_n or _title_n in _nt):
                    h = _h
                    break
        h_factor = 0.5 + h / 100.0  # H∈[0,100] → factor∈[0.5,1.5]
    except Exception:
        pass

    return view * game_boost * focus * freshness * h_factor


# 2026-08-20：词云 H 值映射缓存（模块级，避免每次 top10_score 重读文件）。
_TOP10_HMAP_CACHE = None


def _top10_hmap():
    """读 wordcloud_terms.json → {归一化标题: H}。带模块级缓存。"""
    global _TOP10_HMAP_CACHE
    if _TOP10_HMAP_CACHE is not None:
        return _TOP10_HMAP_CACHE
    import re as _re
    _norm = lambda s: _re.sub(
        r'[\s《》〈〉\[\]()（）:：·\-_—,.，。、!！?？]', '', s).lower()
    hmap = {}
    try:
        wc = os.path.join(BASE, "wordcloud_terms.json")
        if os.path.exists(wc):
            obj = json.load(io.open(wc, encoding="utf-8"))
            for t in obj.get("terms", []) or []:
                term = (t.get("term") or "").strip()
                if term:
                    hmap[_norm(term)] = int(t.get("heat", 0) or 0)
    except Exception:
        pass
    _TOP10_HMAP_CACHE = hmap
    return hmap


def esc(s):
    return _html.escape(s or "", quote=True)


def wan(v):
    """播放量 -> 万；不足 1 万的按万取整会变 0，这里保底 1 万避免出现 '0万'。"""
    return f"{max(1, int(v) // 10000)}万"


def clip(s, n=22):
    s = re.sub(r"\s+", " ", s or "").strip()
    return s[:n] + ("…" if len(s) > n else "")


def load_today():
    """只读取当天采集结果；禁止在采集失败时用历史文件冒充今日内容。"""
    date_s = datetime.date.today().strftime("%Y-%m-%d")
    expected = "meme_" + date_s.replace("-", "") + ".json"
    path = os.path.join(COLLECTORS, expected)
    if not os.path.exists(path):
        raise SystemExit(
            f"collectors/ 下缺少当天采集文件 {expected}；拒绝使用历史 meme_*.json 代替。"
            "请先成功运行 meme_radar.py。")
    return json.load(io.open(path, encoding="utf-8")), date_s


# ---------------- 各区块选材 ----------------
def pick_kuso(data, n=5):
    """鬼畜信号：popular 里鬼畜类分区，按播放量取前 n。"""
    rows = [v for v in data.get("popular", [])
            if any(t in (v.get("tname") or "") for t in KUSO_TNAMES) and is_acg(v)]
    rows.sort(key=lambda x: -x.get("view", 0))
    return rows[:n]


def pick_break(data, n=5):
    """破圈动量：取 popular+weekly 合并池，按两道红线（游戏ACG + ≤5天）过滤后取高播放前 n。
    注：每周必看官选本身约7天龄、必超5天红线，会自动被 fresh_bili 淘汰，
    由 popular 中新鲜的高播放游戏/ACG 视频补位，板块不空且全合规。"""
    pool = list(data.get("weekly", [])) + list(data.get("popular", []))
    rows = [v for v in pool if fresh_bili(v)]
    rows.sort(key=lambda x: -x.get("view", 0))
    return rows[:n]


def pick_wiki(data, n=6):
    """梗百科追更：梗解读类账号的新片，按播放量取前 n。
    红线②：只留游戏/ACG 语境（meme_ups 无 tname，靠标题关键词判定）。"""
    rows = [v for v in data.get("meme_ups", []) if is_acg(v)]
    rows.sort(key=lambda x: -x.get("view", 0))
    return rows[:n]


def pick_tieba(data, n=5):
    """贴吧热议：榜单前 n（榜单本身已按热度排序，不再二次排序）。
    红线②：只留游戏/ACG 相关话题，剔除泛圈社会新闻。"""
    return [t for t in data.get("tieba", []) if is_game_tieba(t.get("name", ""))][:n]


def tieba_tag(name):
    """给贴吧条目打一个粗分类标签（仅用于视觉分组，不参与任何排序）。"""
    if re.search(r"LPL|LCK|BLG|WBG|TES|EDG|JDG|T1|战队|选手|赛|冠军|阿水|Bin|厂长", name):
        return "电竞"
    if re.search(r"游戏|退款|外挂|steam|Steam|手游|端游", name):
        return "游戏"
    return "泛圈"


# ---------------- 区块渲染 ----------------
def mu_badge(src):
    """数据源徽章：<span class="mu-badge">B站热门</span>"""
    return f'<span class="mu-badge">{esc(src)}</span>'


def community_link_html(v, badge, metric=""):
    """社区风向下的一条链接。badge=来源标签，metric=尾部数字（万播放等）。"""
    url = v.get("url", "")
    title = clip(v.get("title") or v.get("name", ""), 20)
    extra = f"<em>{metric}</em>" if metric else ""
    return f'<a target="_blank" href="{esc(url)}">{esc(title)}{mu_badge(badge)}{extra}</a>'


def hotlist_link_html(it):
    """全网热榜下的一条链接。"""
    plat = it["platform"]
    icon = _PLAT_ICON.get(plat, "")
    label = _PLAT_LABEL.get(plat, plat)
    topic_short = clip(it["topic"], 18)
    url = it["url"] or "#"
    return (f'<a target="_blank" href="{esc(url)}" '
            f'title="{esc(it["topic"])} · {label}#{it["rank"]} · 热力值 {it["hotValue"]}">'
            f'{icon} {esc(topic_short)}{mu_badge(f"{label}#{it["rank"]}")}</a>')


def _lcs_len(a, b):
    """最长公共子串长度——用于把热搜词落到当日真实视频页。"""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def resolve_hotword_href(kw, pool):
    """把热搜词落到当日真实采集到的视频页；落不到就返回空（绝不拼搜索页）。"""
    best_u, best_n = "", 0
    for t, u in pool:
        n = _lcs_len(kw, t)
        if n > best_n:
            best_u, best_n = u, n
    return best_u if best_n >= 4 else ""


def hotword_link_html(h):
    """B站热搜信号下的一条链接。href 由 resolve_hotword_href 预先落到真实视频页。"""
    kw = h.get("kw", "")
    heat = h.get("heat", 0)
    href = h.get("_href", "")
    if not href:
        return ""
    w = max(1, heat // 10000)
    return (f'<a target="_blank" '
            f'href="{href}" '
            f'title="B站热搜 · {esc(kw)} · 热度 {w} 万 · 已落到当日真实视频页">'
            f'&#127916; {esc(clip(kw, 16))}{mu_badge("B站热搜")}<em>{w}万</em></a>')


def build_intro(community_items, hotlist_items, hotword_items, date_s):
    """板块顶部导语：Agent 读数据后生成 1-2 句话总结今日风向。"""
    parts = []
    # B站 top 视频
    top = [it for it in community_items if it.get("_src") == "B站热门"][:2]
    if top:
        parts.append(f'B站游戏区被「{clip(top[0].get("title",""),16)}」'
                     f'（{wan(top[0].get("view",0))}）领衔')
    # 热搜 top 词
    if hotword_items:
        parts.append(f'B站热搜「{hotword_items[0].get("kw","")}」破圈上榜')
    # 贴吧 highlights
    tb = [it for it in community_items if it.get("_src") == "贴吧"][:1]
    if tb:
        parts.append(f'贴吧热议「{clip(tb[0].get("name",""),12)}」')
    # 全平台统计
    plat_set = set(it.get("platform", "") for it in hotlist_items)
    if len(plat_set) >= 3:
        parts.append(f'游戏/ACG 话题覆盖 {"、".join(_PLAT_LABEL.get(p,p) for p in list(plat_set)[:4])} 等 {len(plat_set)} 个平台')
    if not parts:
        return ""
    return f'<div class="mu-intro">{date_s} · {"；".join(parts)}。</div>'


def build_community_block(data):
    """子板块① 社区风向：合并 B站 鬼畜 + 破圈 + 梗百科 + 贴吧，按源打徽章。"""
    items = []
    for v in pick_break(data):
        items.append(dict(v, _src="B站热门"))
    for v in pick_tieba(data):
        items.append(dict(v, _src="贴吧", _title=v.get("name","")))
    for v in pick_wiki(data):
        items.append(dict(v, _src="梗百科UP主"))
    for v in pick_kuso(data):
        items.append(dict(v, _src="B站鬼畜"))

    if not items:
        return ""

    links = []
    for it in items[:16]:  # 最多 16 条（2026-08-06 扩容：原 12 条）
        badge = it.get("_src", "B站")
        if badge == "B站热门" or badge == "B站鬼畜" or badge == "梗百科UP主":
            links.append(community_link_html(it, badge, wan(it.get("view", 0))))
        elif badge == "贴吧":
            links.append(community_link_html(
                dict(it, url=it.get("url",""), title=it.get("_title","")),
                badge, tieba_tag(it.get("_title",""))))
        else:
            links.append(community_link_html(it, badge))

    return (f'<div class="mu-block"><h4 class="mu-block-title">&#128200; 社区风向 '
            f'<span>B站 + 贴吧 · {len(links)} 条</span></h4>'
            f'<div class="mu-links">\n{"\n".join(links)}\n</div></div>')


def build_hotlist_block(date_s):
    """子板块② 全网热榜：合并 热榜交叉信号 + B站热搜信号。"""
    parts = []
    # 热榜交叉信号
    hl = load_hotlist(date_s)
    hotlist_items = []
    if hl:
        items = []
        for plat in ("weibo", "zhihu", "douyin", "bilibili", "xiaohongshu"):
            for item in hl.get(plat, []):
                topic = (item.get("topic") or "").strip()
                if not topic:
                    continue
                items.append({
                    "topic": topic, "hotValue": item.get("hotValue") or 0,
                    "url": item.get("url") or "", "platform": plat,
                    "rank": item.get("rank") or 99,
                })
        items.sort(key=lambda x: -x["hotValue"])
        seen = set()
        hotlist_items = []
        for it in items:
            key = it["topic"][:20]
            if key in seen:
                continue
            seen.add(key)
            hotlist_items.append(it)
        for it in hotlist_items[:8]:
            parts.append(hotlist_link_html(it))

    # B站热搜信号
    hw_path = os.path.join(COLLECTORS, f"meme_{date_s.replace('-', '')}.json")
    hotword_items = []
    if os.path.exists(hw_path):
        try:
            with io.open(hw_path, encoding="utf-8") as f:
                meme = json.load(f)
            hw_list = sorted(meme.get("hotwords", []), key=lambda x: -x.get("heat", 0))
            game_hw = [h for h in hw_list if HW_GAME.search(h.get("kw", ""))]
            # 2026-08-05 治理：热搜词原先拼 search.bilibili.com 搜索页，违反"禁止搜索页"红线。
            # 改为必须落到当日真实采集的视频页，落不到的热搜词直接丢弃。
            hw_pool = []
            for sec in ("popular", "weekly", "meme_ups", "series"):
                for v in (meme.get(sec) or []):
                    t = (v.get("title") or "").strip()
                    u = (v.get("url") or v.get("href") or "").strip()
                    if t and u:
                        hw_pool.append((t, u))
            for h in game_hw:
                href = resolve_hotword_href(h.get("kw", ""), hw_pool)
                if not href:
                    continue
                h["_href"] = href
                hotword_items.append(h)
                if len(hotword_items) >= 8:
                    break
            for h in hotword_items:
                parts.append(hotword_link_html(h))
        except Exception:
            pass

    if not parts:
        return "", hotlist_items, hotword_items

    block = (f'<div class="mu-block"><h4 class="mu-block-title">&#128301; 全网热榜 '
             f'<span>五平台交叉 + B站热搜 · {len(parts)} 条</span></h4>'
             f'<div class="mu-links">\n{"\n".join(parts)}\n</div></div>')
    return block, hotlist_items, hotword_items


# B站热搜信号过滤：匹配 game/ACG 相关热词
HW_GAME = re.compile(
    r'《[^》]+》|游戏|Steam|手游|端游|主机|Switch|PS5|Xbox|'
    r'联动|皮肤|版本|赛季|公测|首发|发售|上线|定档|开服|新游|'
    r'电竞|动画|番|声优|cos|二游|抽卡|氪|直播|同人|连载|漫画|'
    r'原神|崩铁|崩坏|星穹|铁道|王者|和平|光遇|第五|明日|方舟|'
    r'EDG|BLG|TES|JDG|WBG|T1|WE|LGD|RW|TTG|IVL|LPL|LCK|'
    r'完蛋|EVA|火影|海贼|柯洁|LOL|英雄联盟|无畏|契约|瓦洛|'
    r'三角洲|暗区|永劫|蛋仔|阴阳师|FGO|崩坏3|三战|'
    r'金铲铲|自走棋|棋魂|梗图|恐怖梗|B萌|Re0|'
    r'蜘蛛侠|鬼泣|仁王|雾海|猎人|怪猎')


def build_risk_block(risk_html):
    """子板块③ 风险信号：保留原风险观察行内容，但改标题。"""
    if not risk_html or not risk_html.strip():
        return ""
    # 把 mu-row 的 label 样式保留，但放到 mu-block 里
    # 提取链接文本
    links = re.findall(r'<a[^>]*>.*?</a>', risk_html)
    if not links:
        return ""
    # 从原行里提取策展日期
    curated_match = re.search(r'data-curated="([^"]+)"', risk_html)
    curated_attr = f' data-curated="{curated_match.group(1)}"' if curated_match else ""
    return (f'<div class="mu-block mu-block-risk"{curated_attr}>'
            f'<h4 class="mu-block-title">&#9888;&#65039; 风险信号 '
            f'<span>人工策展 · {len(links)} 条</span></h4>'
            f'<div class="mu-links">\n{"\n".join(links)}\n</div></div>')


def build_hot(data, date_s):
    """重写：拆分为 3 个子板块 + 板块导语。"""
    # 子板块① 社区风向
    community = build_community_block(data)

    # 子板块② 全网热榜
    hotlist_block, hotlist_items, hotword_items = build_hotlist_block(date_s)

    # 子板块③ 风险信号（由 replace_hot 拼接时从旧 HTML 提取后注入）
    # 这里只传占位符，实际内容由 replace_hot 替换
    risk_placeholder = "<!--RISK_PLACEHOLDER-->"

    # 板块导语
    # 收集 community items for intro
    comm_items_all = []
    for v in pick_break(data):
        comm_items_all.append(dict(v, _src="B站热门"))
    for v in pick_tieba(data):
        comm_items_all.append(dict(v, _src="贴吧"))
    intro = build_intro(comm_items_all, hotlist_items, hotword_items, date_s)

    body = community
    if hotlist_block:
        body += "\n" + hotlist_block
    body += "\n" + risk_placeholder

    return body, intro, date_s


# ---------------- 全网热榜交叉信号 ----------------
_HL_DIR = os.environ.get("RC_COLLECTORS", os.path.join(BASE, "collectors"))
_PLAT_ICON = {
    "weibo":        "&#127760;",   # 微博
    "zhihu":        "&#129504;",   # 知乎
    "douyin":       "&#127925;",   # 抖音
    "bilibili":     "&#127916;",   # B站
    "xiaohongshu":  "&#128247;",   # 小红书
}
_PLAT_LABEL = {
    "weibo": "微博", "zhihu": "知乎", "douyin": "抖音",
    "bilibili": "B站", "xiaohongshu": "小红书",
}
_HL_MAX_ITEMS = 15  # 最多展示 15 条交叉信号（2026-08-06 扩容：原 8 条太少）


def load_hotlist(date_s):
    """读当日全网热榜中的 game/ACG 条目"""
    hl_path = os.path.join(_HL_DIR,
                           f"public_hotlist_{date_s.replace('-', '')}.json")
    if not os.path.exists(hl_path):
        return None
    with io.open(hl_path, encoding="utf-8") as f:
        return json.load(f)


def build_top10(data, date_s):
    """兼容旧雷达：保留以防其它脚本引用。新版请用 build_podium()。
    TOP10 的 B站条目位（第 2~10 位）。第 1 位是头条事件位，由 build_top10_head() 生成。
    排序用 top10_score（播放量 × 游戏/发售信号权重）。"""
    pool = {}
    for idx, v in enumerate(data.get("popular", [])):
        pool[v["url"]] = dict(v, _src="热门", _rank=idx + 1)
    for v in data.get("weekly", []):
        # 每周必看是编辑部精选，破圈证据更强，覆盖同 URL 的热门条目
        if v["url"] not in pool:
            pool[v["url"]] = dict(v, _src="周必看", _rank=999)
    rows = sorted([v for v in pool.values() if fresh_bili(v)],
                  key=top10_score, reverse=True)[:9]

    out = []
    for i, v in enumerate(rows, start=2):
        tname = v.get("tname") or ""
        if any(t in tname for t in KUSO_TNAMES):
            tag, color = "鬼畜", "#3fd68f"
        elif v.get("_src") == "周必看":
            tag, color = "破圈", "#4da3ff"
        elif "游戏" in tname:
            tag, color = "游戏", "#c792ea"
        else:
            tag, color = "高热", "#ffb020"
        pic = v.get("pic") or ""
        img = (f'<span class="t10-img" style="background-image:url({esc(pic)})" '
               f'title="{esc(clip(v["title"], 40))}"></span>') if pic else \
              '<span class="t10-img"></span>'
        out.append(
            f'<a class="top10-item" target="_blank" href="{esc(v["url"])}">'
            f'<span class="t10-rank">{i}</span>{img}'
            f'<div class="t10-body"><b>{esc(clip(v["title"], 26))}</b>'
            f'<span class="t10-meta">{wan(v["view"])} · {esc(tname)}</span>'
            f'<span class="t10-tag" style="color:{color};border-color:{color}33;'
            f'background:{color}18">{tag}</span></div></a>'
        )
    return out


# ---- 2026-08-17 治理：TOP1 含金量交叉验证 ----
# 用户反馈：今天出现了缺乏权威性的 TOP1（单源 B站视频被推上头条大卡）。
# 新增机制：任何内容被判定为 top1 时，先在全站多源库内检索，
#   确认是否有「多家媒体/多平台」报道或该关键词被反复提及，再决定其头条资格。
# 检索源（按可用性分级）：
#   · feed_events（events.json，含 source_name 多源）→ 强证据
#   · tgmeng 当日 AI 综述（全文 + all_entries）→ 强证据（已聚合多家媒体）
#   · public_hotlist（微博/知乎/抖音/B站/小红书 5 平台话题）→ 可选证据（采集可能为空）
# 判定阈值：
#   ≥3 家不同源/平台命中 → 多源印证，保留 TOP1（标 ✓）
#   2 家命中 → 保留但标 ⚠ 单源待观察
#   <2 家（仅 B站自身）→ 降级：撤出头条大卡，退回视频流按 top10_score 排（标 ✗）

# 实体提取：优先抓《游戏名》书名号，其次抓英文/中文专有名词（Steam/PS5/Xbox/厂商名等）
_ENTITY_BOOK = re.compile(r'《([^》]{2,20})》')
_ENTITY_CAPS = re.compile(r'\b([A-Z][A-Za-z0-9]{2,20})\b')  # 英文专名（Compulsion/South of Midnight 等）
_ENTITY_GAME = re.compile(
    r'(Steam|PS5|Xbox|Switch|任天堂|米哈游|腾讯|网易|鹰角|库洛|'
    r'影之刃零|湮灭之潮|黑神话|原神|王者荣耀|和平精英|第五人格|明日方舟|崩坏|'
    r'South of Midnight|Compulsion Games|Halo|光环|DOTA2|CS2|英雄联盟|'
    r'Valorant|绝区零|鸣潮|恋与深空|重返未来|明日方舟)')
# 泛平台/通用词：命中只能证明"游戏圈在聊"，不能证明"该具体事件被多源报道"，
# 必须从实体里剔除，否则任何含 Steam 的标题都会被 feed_events 里所有 Steam 官闻虚高命中。
_VERIFY_GENERIC = re.compile(
    r'^(Steam|PS5|Xbox|Switch|任天堂|Steam 国区|热销榜|畅销榜|全球|国区)$')
# 停用词：太泛、匹配不到真实实体的词
_VERIFY_STOP = re.compile(r'^(的|了|是|在|和|与|为|被|将|一款|这个|正式|首次|公开|上线|发售)$')


def _extract_entities(title):
    """从标题抽取用于跨源检索的实体词列表。
    2026-08-17 收紧：剔除 Steam/Xbox 等泛平台词，只留具体游戏名/厂商名/书名号实体，
        避免弱证据标题因含泛词被虚高判为「多源印证」。
    """
    if not title:
        return []
    ents = []
    for m in _ENTITY_BOOK.findall(title):
        ents.append(m.strip())
    for m in _ENTITY_GAME.findall(title):
        ents.append(m.strip())
    for m in _ENTITY_CAPS.findall(title):
        if m in ("PV", "CG", "ED", "OP", "AI", "B站"):
            continue
        ents.append(m.strip())
    # 去重 + 过滤过短/停用/泛平台词
    seen, out = set(), []
    for e in ents:
        if not e or e in seen or len(e) < 3:
            continue
        if _VERIFY_STOP.match(e) or _VERIFY_GENERIC.match(e):
            continue
        seen.add(e)
        out.append(e)
    return out


def _verify_top1(top1, data, date_s):
    """对 top1 做多源交叉验证。返回 (verdict, sources_hit, detail)

    verdict ∈ {"multi", "single", "weak"}：
      multi  → ≥3 家不同源/平台命中，含金量足，保留 TOP1
      single → 2 家命中，保留但标 ⚠
      weak   → <2 家（仅 B站自身），降级撤出头条
    sources_hit = 命中源名列表（用于渲染透明标注）
    """
    title = top1.get("title") or ""
    entities = _extract_entities(title)
    # 若实体为空，退化为用标题里的关键片段（去标点）做宽松匹配
    if not entities:
        frag = re.sub(r'[^\w\u4e00-\u9fa5]', '', title)[:12]
        entities = [frag] if frag else [title[:12]]

    hit_sources = []   # (源名, 命中实体)
    seen_src = set()

    def _add(src_name, ent):
        if src_name not in seen_src:
            seen_src.add(src_name)
            hit_sources.append((src_name, ent))

    # 1) feed_events（多 source_name）
    try:
        ev = json.load(io.open(os.path.join(BASE, "events.json"), encoding="utf-8"))
        for fe in (ev.get("feed_events") or []):
            blob = " ".join([
                str(fe.get("title") or ""), str(fe.get("game") or ""),
                str(fe.get("source_name") or ""), str(fe.get("anchor") or "")
            ])
            for e in entities:
                if e and e in blob:
                    _add("events:%s" % (fe.get("source_name") or "事件源"), e)
                    break
    except Exception:
        pass

    # 2) tgmeng 当日 AI 综述（全文 + all_entries 标题/摘要）
    try:
        tg_path = os.path.join(
            COLLECTORS,
            "tgmeng_daily_" + date_s.replace("-", "") + ".json")
        if os.path.exists(tg_path):
            tg = json.load(io.open(tg_path, encoding="utf-8"))
            blobs = []
            ent = tg.get("entry") or {}
            blobs.append(str(ent.get("title") or "") + " " + str(ent.get("summary") or ""))
            for ae in (tg.get("all_entries") or []):
                blobs.append(str(ae.get("title") or "") + " " + str(ae.get("summary") or ""))
            full = " ".join(blobs)
            for e in entities:
                if e and e in full:
                    _add("糖果梦AI日报", e)
                    break
    except Exception:
        pass

    # 3) public_hotlist（5 平台话题，可选证据，可能为空）
    try:
        hl_path = os.path.join(
            COLLECTORS,
            "public_hotlist_" + date_s.replace("-", "") + ".json")
        if os.path.exists(hl_path):
            hl = json.load(io.open(hl_path, encoding="utf-8"))
            for plat, items in hl.items():
                if plat == "timestamp" or not isinstance(items, list):
                    continue
                for it in items:
                    topic = str(it.get("topic") or "")
                    for e in entities:
                        if e and e in topic:
                            _add("热榜:%s" % plat, e)
                            break
    except Exception:
        pass

    # 判定
    n = len(seen_src)
    if n >= 3:
        return "multi", hit_sources, entities
    elif n == 2:
        return "single", hit_sources, entities
    else:
        return "weak", hit_sources, entities


# ---- 2026-08-17 重构：真实热度评分（替代脆弱的"命中源数量"） ----
# 用户反馈："为什么这么多未验证和单源事件？我的目的很简单"。
# 根因：_verify_top1 用"命中源数量"当热度，但 feed_events 各说各话（22 条来自 14 个
#   独立 source，彼此不交叉报道），任何事件标题去 feed_events 检索只能命中自己 → 永远 weak。
#   而真正的热度信号在 tgmeng 当日 AI 日报里——它聚合全网多家媒体写成综述，
#   一个事件被综述反复点名（如"影之刃零"出现 7 次）才是真热度的强代理。
# 方案：新增 _heat_score()，综合多信号算真实热度分数，用于 TOP1/头图排序。
def _tgmeng_mention_count(title, date_s):
    """统计某事件在 tgmeng 当日 AI 日报里的提及频次（真热度代理）。
    返回 (count, matched_keywords)。匹配用实体 + 关键中文片段双路。
    """
    try:
        tg_path = os.path.join(COLLECTORS, "tgmeng_daily_" + date_s.replace("-", "") + ".json")
        if not os.path.exists(tg_path):
            return 0, []
        tg = json.load(io.open(tg_path, encoding="utf-8"))
        blobs = []
        ent = tg.get("entry") or {}
        blobs.append(str(ent.get("title") or "") + " " + str(ent.get("summary") or ""))
        for ae in (tg.get("all_entries") or []):
            blobs.append(str(ae.get("title") or "") + " " + str(ae.get("summary") or ""))
        full = " ".join(blobs)
        # 匹配词：实体 + 《书名号》内容 + 关键英文大写片段（如 GTA6/Kingdom Hearts）
        kws = set()
        for e in _extract_entities(title):
            if e:
                kws.add(e)
        for m in re.findall(r'《([^》]+)》', title):
            kws.add(m.strip())
        for m in re.findall(r'[A-Z][A-Za-z0-9]+(?:\s+[A-Z][A-Za-z0-9]+)?', title):
            if len(m) >= 3:
                kws.add(m)
        # 只保留长度≥2 且非泛词的关键词
        generic = {"Steam", "PS5", "Xbox", "Switch", "PV", "CG", "AI", "D23", "GTA", "PC"}
        matched = []
        total = 0
        for kw in kws:
            kw = kw.strip()
            if len(kw) < 2 or kw in generic:
                continue
            c = full.count(kw)
            if c > 0:
                matched.append((kw, c))
                total += c
        return total, matched
    except Exception:
        return 0, []


def _heat_score(title, data, date_s, age=None):
    """综合多信号计算事件/视频的真实热度分数（越大越热）。
    信号权重：
      · tgmeng 日报提及频次（真热度，权重最高）：每提及一次 +4，上限 +20
      · feed_events 多源命中数：每多一家 +2（弱信号，只作辅助）
      · 强事件信号（登顶/发售/预购/首曝/联动/PV）：+3
      · 事件来源权威度（机核/GameLook/游戏陀螺/游民星空/17173 > Steam 官方公告）：+1
      · 新鲜度（时效性，2026-08-17 补上）：age 越小加分越多，age=0 +8、1天 +5、
        2天 +3、3天 +1、4天 -2、5天 -4、≥6天 -8（旧闻持续被提也会被显著拉低，
        避免 5 天前的影之刃零被 tgmeng 提及硬顶到 TOP1）。
    返回 (score, dict 详情) —— 详情供渲染透明标注用。
    """
    # 1) tgmeng 提及频次（真热度）
    tg_cnt, tg_kws = _tgmeng_mention_count(title, date_s)
    score = min(tg_cnt * 4, 20)

    # 2) feed_events 多源命中数
    verdict, hits, ents = _verify_top1({"title": title}, data, date_s)
    n_src = len({h[0] for h in hits})
    score += n_src * 2

    # 3) 强事件信号
    if re.search(r'登顶|夺冠|夺魁|发售|预购|预售|首曝|定档|公测|不删档|联动|上线', title):
        score += 3

    # 4) 来源权威度（feed_events 命中源里是否含权威媒体）
    auth_src = {"机核", "GameLook", "游戏陀螺", "游民星空", "17173", "白鲸出海"}
    auth_hit = any(s.split(":")[-1].strip() in auth_src for s, _ in hits)
    if auth_hit:
        score += 1

    # 5) 新鲜度衰减（时效性）—— 越新加分越多，越旧扣分越重
    fresh_bonus = 0.0
    if age is not None:
        try:
            a = int(age)
            fresh_bonus = {0: 8, 1: 5, 2: 3, 3: 1, 4: -2, 5: -4}.get(a, -8 if a >= 6 else 0)
        except Exception:
            pass
    score += fresh_bonus

    detail = {
        "tgmeng_count": tg_cnt,
        "tgmeng_kws": tg_kws,
        "n_src": n_src,
        "verdict": verdict,
        "hits": hits,
        "age": age,
        "fresh_bonus": fresh_bonus,
    }
    return score, detail


# ---- 2026-08-19 升级：S 级判定 = 多源印证 + 跨平台声量 ----
# 旧口径问题：S 级只靠单一信号（B站播放量>200万 / 标题强事件关键词），
#   既没交叉印证，也没看跨平台声量，容易把单源冷门内容误抬为 S 级。
# 新口径：S 级必须同时满足「多源印证」与「跨平台声量」两个维度——
#   · 多源印证：_verify_top1 命中 ≥3 家不同源/平台（feed_events 多源 + tgmeng 日报 + 热榜）
#   · 跨平台声量：public_hotlist 的 5 平台（微博/知乎/抖音/B站/小红书）中 ≥2 个平台热榜命中该实体
# 两条都达标才给 S；只满足一条降 A；都不满足给 B（单源弱证据）。

def _cross_platform_hits(title, date_s):
    """统计某标题实体在 public_hotlist 5 平台热榜中的命中情况。

    返回 (platforms_hit, n_platforms)：platforms_hit 是命中平台名列表（去重），
    n_platforms 是命中平台数。跨平台声量就是看有多少个不同平台的热榜
    独立点名了这个游戏/事件（同一平台多条话题只算 1 个平台，避免灌水虚高）。
    """
    entities = _extract_entities(title)
    if not entities:
        frag = re.sub(r'[^\w\u4e00-\u9fa5]', '', title)[:12]
        entities = [frag] if frag else [title[:12]]

    hit_platforms = []
    seen_plat = set()
    try:
        hl_path = os.path.join(
            COLLECTORS, "public_hotlist_" + date_s.replace("-", "") + ".json")
        if not os.path.exists(hl_path):
            return [], 0
        hl = json.load(io.open(hl_path, encoding="utf-8"))
        for plat, items in hl.items():
            if plat == "timestamp" or not isinstance(items, list):
                continue
            for it in items:
                topic = str(it.get("topic") or "")
                for e in entities:
                    if e and e in topic:
                        if plat not in seen_plat:
                            seen_plat.add(plat)
                            hit_platforms.append(plat)
                        break
    except Exception:
        pass
    return hit_platforms, len(hit_platforms)


def _signal_grade(title, data, date_s, view=0, age=None):
    """统一信号分级：S / A / B。返回 (grade, basis_dict)。

    basis_dict 供渲染透明标注，含：
      · verdict / n_src / hits：多源印证结果（multi/single/weak 与命中源列表）
      · platforms / n_plat：跨平台声量（命中平台名列表与平台数）
      · reason：分级理由（人类可读）

    分级规则（2026-08-19 升级）：
      S 级 = 多源印证（multi，≥3 源）+ 跨平台声量（≥2 平台）同时达标
      A 级 = 多源印证（multi 或 single）或 单平台热榜声量 或 高播放量（≥150万）任一
      B 级 = 仅单源 / 无跨平台声量（弱证据）
    """
    verdict, hits, _ = _verify_top1({"title": title}, data, date_s)
    n_src = len({h[0] for h in hits})
    platforms, n_plat = _cross_platform_hits(title, date_s)

    is_multi = (verdict == "multi") or (n_src >= 3)
    is_cross = n_plat >= 2

    if is_multi and is_cross:
        grade = "S"
        reason = f"多源印证（{n_src} 源）+ 跨平台声量（{n_plat} 平台）"
    elif is_multi or (verdict == "single") or (n_plat >= 1) or (view >= 1500000):
        grade = "A"
        reason = (
            f"多源印证（{n_src} 源）" if is_multi or verdict == "single"
            else f"单平台热榜声量（{n_plat} 平台）" if n_plat >= 1
            else f"高播放量（{view}）"
        )
    else:
        grade = "B"
        reason = f"单源弱证据（{n_src} 源，跨平台 {n_plat} 平台）"

    return grade, {
        "verdict": verdict,
        "n_src": n_src,
        "hits": hits,
        "platforms": platforms,
        "n_plat": n_plat,
        "reason": reason,
    }


def _podium_top1(data, date_s, exclude_urls=None):
    """生成焦点榜 TOP1 头条位（事件类优先）。返回 dict 或 None。
    来源优先级：events.json feed_events 当日 → meme 风险词视�� → 视频池 H 最高。
    2026-08-17（三轮修复）：事件选择不再按 feed_events 时间顺序取第一条，
      改为「印证强度(multi>single>weak) + 事件权重(发售/登顶/大事件)」排序，
      选出含金量最高的事件当头条，弱证据冷门事件不再霸榜。
    2026-08-18：加 exclude_urls（masthead 头图已用 URL），让头图优先、焦点榜避让。
    2026-08-21：例行运维噪音不进 TOP1。封禁/处罚/违规这类"游戏运营处罚"公告
      虽被多源转载刷出高分，但信息增量≈0，是白噪音不是新闻——直接排除头条候选。
    """
    # 例行运维噪音关键词（2026-08-21）：封禁/处罚/违规等"运营处罚"类公告。
    # 注意：不含"停服"——停服是重大事件（暴雷），必须保留；也不含"公告"（太宽）。
    _routine_noise = re.compile(
        r'违规|封禁|封号|处罚|禁言|黑名单|外挂|作弊|净化|整治|处罚通知|封禁公告|处罚名单')
    if not exclude_urls:
        exclude_urls = set()
    else:
        exclude_urls = set(exclude_urls)
    # 1) events.json feed_events：按 _heat_score 真实热度排序
    #    时效红线：统一「近 3 天」（2026-08-17 收紧——不再因 tgmeng 提及就放宽到 7 天；
    #    时效性改由 _heat_score 内的 fresh_bonus 连续衰减负责，旧闻即使被日报反复提及也会被扣分）。
    ev_path = os.path.join(BASE, "events.json")
    try:
        ev = json.load(io.open(ev_path, encoding="utf-8"))
        scored = []
        for fe in (ev.get("feed_events") or []):
            pd = fe.get("pubdate") or fe.get("first_seen") or ""
            title = fe.get("title") or fe.get("game") or "（无标题）"
            # 与 masthead 头图去重（2026-08-18：头图优先，焦点榜避让）
            if (fe.get("source_url") or fe.get("url") or "") in exclude_urls:
                continue
            # 例行运维噪音不进 TOP1（2026-08-21）——封禁/处罚/违规公告直接跳过
            if _routine_noise.search(title):
                continue
            age = None
            if pd:
                try:
                    d = datetime.date.fromisoformat(str(pd)[:10])
                    age = (datetime.date.today() - d).days
                    if age > 3:
                        continue
                except Exception:
                    age = None
            score, detail = _heat_score(title, data, date_s, age=age)
            scored.append((score, detail, fe))
        if scored:
            scored.sort(key=lambda x: -x[0])
            score, detail, fe = scored[0]
            # 尝试从 meme 视频池找同事件的相关封面图（feed_events 本身无图片字段）
            # 2026-08-20 修复：借图必须"高相关"——game 字段为核心匹配词，且排除
            #   "全行业"等泛词；标题前6字仅作弱信号。避免事件文章借到不相关视频封面
            #   （如「灵犀互娱易主」周报借到《黑神话：钟馗》实机图，封面与标题语义不符）。
            #   借不到高相关图时留空，走无图渐变卡分支（is_event=True），绝不强行错配。
            top1_img = ""
            _game = (fe.get("game") or "").strip()
            _title = fe.get("title") or ""
            # 核心匹配词：优先 game 字段（非空且非泛词），其次标题里《》包裹的游戏名
            _t1_kw = []
            if _game and _game not in ("全行业", "未知", "其他"):
                _t1_kw.append(_game)
            _title_game = re.search(r'《([^》]+)》', _title)
            if _title_game:
                _t1_kw.append(_title_game.group(1))
            # 标题前6字作为弱信号，但排除纯泛词（如"灵犀互娱"无法对应任何视频）
            _weak = _title[:6]
            for v in (data.get("popular", []) + data.get("weekly", [])):
                if not v.get("pic"):
                    continue
                vt = v.get("title") or ""
                # 强匹配：视频标题含核心游戏名（game 或《》内游戏名）
                if _t1_kw and any(k in vt for k in _t1_kw):
                    top1_img = v["pic"]
                    break
                # 弱匹配：仅当核心词为空时，才退而用标题前6字（避免泛词错配）
                if (not _t1_kw) and _weak and _weak in vt:
                    top1_img = v["pic"]
                    break
            return {
                "rank": 1,
                "url": fe.get("source_url") or fe.get("url") or "#",
                "title": fe.get("title") or fe.get("game") or "（无标题）",
                "tag": "事件",
                "color": "#ff5c39",
                "img": top1_img,
                "meta": "%s · %s" % (
                    fe.get("source_name") or "未知来源",
                    (fe.get("pubdate") or fe.get("first_seen") or "")[5:10]),
                "is_event": bool(not top1_img),  # 有图则不标记 is_event，走有图渲染分支
                "_heat_detail": detail,
            }
    except Exception:
        pass
    # 2) meme 风险词视频
    RISK_KW = ["处罚", "封禁", "下架", "暴雷", "维权", "约谈", "整改", "泄露", "泄密",
               "收购", "减持", "停服", "跳票", "官宣", "定档", "登顶", "预购", "开启预购",
               "预售", "正式发售", "上线"]
    for v in (data.get("popular", []) + data.get("weekly", [])):
        if (v.get("url") or "") in exclude_urls:
            continue
        if fresh_bili(v) and any(k in (v.get("title") or "") for k in RISK_KW):
            return _podium_video_to_dict(v, 1)
    # 3) 降级：视频池 top1（按 top10_score）作为头条
    pool = {}
    for idx, v in enumerate(data.get("popular", [])):
        pool[v["url"]] = dict(v, _src="热门", _rank=idx + 1)
    for v in data.get("weekly", []):
        if v["url"] not in pool:
            pool[v["url"]] = dict(v, _src="周必看", _rank=999)
    rows = sorted([v for v in pool.values() if fresh_bili(v) and v["url"] not in exclude_urls],
                  key=top10_score, reverse=True)
    if rows:
        return _podium_video_to_dict(rows[0], 1)
    return None


def _podium_video_to_dict(v, rank):
    """把 B 站视频 dict 转换为 podium 条目 dict。
    2026-08-17：强化垂类 tag 判定——官方 PV/CG 物料、新游发售独立标签色，
        让游戏垂类信号在榜单上更可读；soft 项（游戏分区但无强游戏语义的泛娱乐）
        明确标「泛娱乐」而非伪装成游戏，透明展示垂类纯度。
    """
    tname = v.get("tname") or ""
    title = v.get("title") or ""
    vertical = _is_game_vertical(v)
    if any(t in tname for t in KUSO_TNAMES):
        tag, color = "鬼畜", "#3fd68f"
    elif v.get("_src") == "周必看":
        tag, color = "破圈", "#4da3ff"
    elif vertical is True:
        # 强游戏垂类：再细分官方物料 / 新游发售 / ACG 爆火
        if re.search(r'PV|CG|预告|实机|首曝|演示|宣传片|预告片', title):
            tag, color = "官方PV/CG", "#ff7a3d"
        elif LAUNCH_KW.search(title):
            tag, color = "新游发售", "#ff5c39"
        elif ACG_BOOM_KW.search(title):
            tag, color = "ACG热点", "#c792ea"
        else:
            tag, color = "游戏", "#c792ea"
    elif vertical == "soft":
        # 游戏分区但内容为泛娱乐（飞机安检/自行车被偷等）→ 透明标「泛娱乐」
        tag, color = "泛娱乐", "#ffb020"
    else:
        tag, color = "高热", "#ffb020"
    return {
        "rank": rank,
        "url": v["url"],
        "title": v.get("title") or "",
        "tag": tag,
        "color": color,
        "img": v.get("pic") or "",
        "meta": "%s · %s" % (wan(v.get("view", 0)), tname),
        "is_event": False,
    }


def build_podium(data, date_s, exclude_urls=None):
    """生成「今日焦点」#podium 板块（金字塔三档）。
    设计：TOP1 榜首大卡 + TOP2-5 中卡 + TOP6-10 小卡，全部去重自同一 pool。
    2026-08-18：加 exclude_urls（masthead 头图已用 URL），让头图优先、焦点榜避让。
    """
    if not exclude_urls:
        exclude_urls = set()
    else:
        exclude_urls = set(exclude_urls)
    # 排序视频池：popular + weekly，按 top10_score
    # 2026-08-17 垂类硬过滤：TOP10 只收「游戏分区」或「标题含强游戏/ACG 信号」的视频，
    #   纯泛娱乐（影视/生活/搞笑，靠 STRONG_GAME_KW 兜底误进的）一律排除，
    #   让榜单自然聚焦新游发售 / 官方 PV-CG / ACG 热点。
    pool = {}
    for idx, v in enumerate(data.get("popular", [])):
        pool[v["url"]] = dict(v, _src="热门", _rank=idx + 1)
    for v in data.get("weekly", []):
        if v["url"] not in pool:
            pool[v["url"]] = dict(v, _src="周必看", _rank=999)
    # 2026-08-17 垂类分层：强垂类(True)优先填满 TOP10；不足 10 条才用
    #   soft（游戏分区但标题无强游戏语义的泛娱乐）兜底，避免泛娱乐霸榜。
    strong_pool = sorted(
        [v for v in pool.values()
         if fresh_bili(v) and _is_game_vertical(v) is True
         and v["url"] not in exclude_urls],
        key=top10_score, reverse=True)
    soft_pool = sorted(
        [v for v in pool.values()
         if fresh_bili(v) and _is_game_vertical(v) == "soft"
         and v["url"] not in exclude_urls],
        key=top10_score, reverse=True)
    ranked_videos = (strong_pool + soft_pool)[:10]  # 强垂类优先，soft 仅兜底

    # TOP1：头条事件（视频或文章），URL 可能跟 ranked_videos[0] 重合
    top1 = _podium_top1(data, date_s, exclude_urls=exclude_urls)

    # ---- 2026-08-17 治理：TOP1 真实热度印证（基于 _heat_score） ----
    # top1 已由 _heat_score 选出真热事件（tgmeng 提及频次 + 多源 + 强事件信号），
    # 这里只根据 _heat_detail 生成正向印证标签，不再跑脆弱的"命中源数量"判定。
    if top1:
        detail = top1.get("_heat_detail") or {}
        tg_cnt = detail.get("tgmeng_count", 0)
        n_src = detail.get("n_src", 0)
        # 权威媒体来源本身即可信，不需"多源印证"证明（只有 B站 视频才需要）
        src = top1.get("meta", "") or ""
        auth_src = {"机核", "GameLook", "游戏陀螺", "游民星空", "17173", "白鲸出海"}
        is_auth = any(a in src for a in auth_src)
        if tg_cnt >= 3:
            top1["_verify"] = {"verdict": "multi", "label": "全网热榜印证",
                               "n_src": n_src, "tgmeng": tg_cnt}
        elif tg_cnt >= 1 or n_src >= 2:
            top1["_verify"] = {"verdict": "single", "label": "多源提及",
                               "n_src": n_src, "tgmeng": tg_cnt}
        elif is_auth:
            top1["_verify"] = {"verdict": "single", "label": "权威媒体",
                               "n_src": n_src, "tgmeng": tg_cnt}
        else:
            top1["_verify"] = {"verdict": "weak", "label": "单源",
                               "n_src": n_src, "tgmeng": tg_cnt}

    # 排除 TOP1 的 URL（若 TOP1 来自视频池）
    seen = set()
    if top1 and top1["url"] in pool:
        seen.add(top1["url"])

    # TOP2-10：从 ranked_videos 按顺序取 9 条（去重）
    mids = []
    smalls = []
    for v in ranked_videos:
        if v["url"] in seen:
            continue
        seen.add(v["url"])
        d = _podium_video_to_dict(v, len(mids) + len(smalls) + 2)
        if len(mids) < 4:
            mids.append(d)
        elif len(smalls) < 5:
            smalls.append(d)
        else:
            break

    # 渲染三档
    cards_html = []
    if top1:
        cards_html.append(_podium_render_top1(top1))
    for e in mids:
        cards_html.append(_podium_render_mid(e))
    for e in smalls:
        cards_html.append(_podium_render_small(e))

    n_total = len(cards_html)
    # 2026-08-17：统计垂类纯度，透明展示「游戏垂类 / 泛娱乐兜底」占比
    strong_cnt = sum(1 for e in [top1] + mids + smalls
                     if e and _is_game_vertical(_v_from_dict(e)) is True)
    soft_cnt = sum(1 for e in [top1] + mids + smalls
                   if e and _is_game_vertical(_v_from_dict(e)) == "soft")
    mix_note = (f'垂类聚焦：游戏垂类 {strong_cnt} 条'
                + (f' · 泛娱乐兜底 {soft_cnt} 条' if soft_cnt else '')
                + ' · TOP1 按真实热度排序（全网日报提及+多源+强事件信号）')
    sec = (
        f'<section id="podium"><div class="sec-title">'
        f'<span class="bar" style="background:var(--accent)"></span>'
        f'今日焦点 <small>合并「视觉焦点 + 内容 TOP10」· 三档金字塔布局 · '
        f'TOP1 榜首大图 / TOP2-5 中卡 / TOP6-10 紧凑小卡 · '
        f'热度数 H(0-100)：组内最热=100，原始播放量仅作附注；{esc(mix_note)}</small>'
        f'</div><div class="pb-grid">'
        + "".join(cards_html) +
        '</div></section>'
    )
    return sec, n_total


def _v_from_dict(e):
    """从 podium 渲染 dict 反查原始 B站 视频字段（供垂类统计复用）。"""
    if not e:
        return {}
    return {"tname": (e.get("meta") or "").split(" · ")[-1],
            "title": e.get("title") or ""}


def _verify_badge(e):
    """生成 TOP1 验证标记 HTML（正向显示真实热度印证状态）。"""
    v = e.get("_verify")
    if not v:
        return ""
    verdict = v.get("verdict")
    label = v.get("label", "")
    tg_cnt = v.get("tgmeng", 0)
    n_src = v.get("n_src", 0)
    if verdict == "multi":
        return (f'<span class="pb-verify pb-verify-ok" title="全网热榜印证：AI 日报提及 {tg_cnt} 次，{n_src} 家来源">'
                f'✓ {label} · 日报提及{tg_cnt}次</span>')
    elif verdict == "single":
        return (f'<span class="pb-verify pb-verify-ok" title="多源提及：AI 日报提及 {tg_cnt} 次，{n_src} 家来源">'
                f'✓ {label}</span>')
    else:
        return (f'<span class="pb-verify pb-verify-warn" title="仅单源报道，热度待观察">'
                f'⚠ {label}</span>')


def _podium_render_top1(e):
    """渲染 TOP1 大卡。事件无图 = 渐变底+白字；有图 = 图+叠层文字。
    2026-08-17：追加「多源印证」验证标记（pb-verify），透明显示头条含金量。
    """
    title = esc(e["title"])
    meta = esc(e["meta"])
    tag = esc(e["tag"])
    color = e["color"]
    vbadge = _verify_badge(e)
    if e.get("is_event") or not e.get("img"):
        # 事件无图：渐变底 + 大字白标题
        return (
            f'<a class="pb-card-1 pb-card-1-event" target="_blank" href="{esc(e["url"])}" '
            f'style="background:linear-gradient(135deg,#ff5c39 0%,#ffb020 60%,#ff7a3d 100%);">'
            f'<div class="pb-cap-1">'
            f'<span class="pb-rank-1">TOP 1 · {tag}</span>'
            f'<i class="pb-tag-1" style="background:#fff"></i>'
            f'<div class="pb-title-1">{title}</div>'
            f'<div class="pb-meta-1">'
            f'<span class="pb-h-1" style="color:#fff">{tag}</span>'
            f'<span style="color:#fff">{meta}</span>'
            f'{vbadge}'
            f'</div></div></a>'
        )
    # 有图：图片背景 + 暗渐变 + 白字
    img = esc(e["img"])
    return (
        f'<a class="pb-card-1" target="_blank" href="{esc(e["url"])}">'
        f'<img src="{img}" alt="{title}" loading="lazy" referrerpolicy="no-referrer" '
        f'onerror="this.style.display=\'none\'">'
        f'<div class="pb-cap-1">'
        f'<span class="pb-rank-1">TOP 1 · {tag}</span>'
        f'<i class="pb-tag-1" style="background:{color}"></i>'
        f'<div class="pb-title-1">{title}</div>'
        f'<div class="pb-meta-1">'
        f'<span class="pb-h-1">{tag}</span>'
        f'<span>{meta}</span>'
        f'{vbadge}'
        f'</div></div></a>'
    )


def _podium_render_mid(e):
    """渲染 TOP2-5 中卡：有图=缩略图+标题+meta；无图=纯文字+大色条。"""
    title = esc(clip(e["title"], 30))
    meta = esc(e["meta"])
    tag = esc(e["tag"])
    color = e["color"]
    rank = e["rank"]
    vbadge = _verify_badge(e) if e.get("rank") == 1 else ""
    img_html = ""
    if e.get("img"):
        img_html = (f'<img src="{esc(e["img"])}" alt="{title}" loading="lazy" '
                    f'referrerpolicy="no-referrer" '
                    f'onerror="this.style.display=\'none\'">')
    return (
        f'<a class="pb-card-mid" target="_blank" href="{esc(e["url"])}">'
        f'{img_html}'
        f'<div class="pb-mid-body">'
        f'<div class="pb-mid-head">'
        f'<span class="pb-rank-mid">#{rank}</span>'
        f'<span class="pb-tag-mid" style="color:{color};border:1px solid {color}55;background:{color}15;">{tag}</span>'
        f'</div>'
        f'<div class="pb-title-mid">{title}</div>'
        f'<div class="pb-meta-mid">{meta}{vbadge}</div>'
        f'</div></a>'
    )


def _podium_render_small(e):
    """渲染 TOP6-10 小卡：上图下文（与中卡同款布局），缩略图全宽 + head(rank+tag) + title(2行clamp) + meta。
    2026-08-13 v5：用户反馈「不要把文字搞成几个字一行的」→ 从横排改回纵排，
        标题用 -webkit-line-clamp:2 限制 2 行（~250px 列宽下可读性最优）。
    """
    title = esc(clip(e["title"], 40))
    tag = esc(e["tag"])
    color = e["color"]
    rank = e["rank"]
    meta_short = esc(e["meta"])
    vbadge = _verify_badge(e) if e.get("rank") == 1 else ""
    img_html = ""
    if e.get("img"):
        img_html = (f'<img class="pb-thumb-small" src="{esc(e["img"])}" alt="{title}" '
                    f'loading="lazy" referrerpolicy="no-referrer" '
                    f'onerror="this.style.display=\'none\'">')
    return (
        f'<a class="pb-card-small" target="_blank" href="{esc(e["url"])}">'
        f'{img_html}'
        f'<div class="pb-small-body">'
        f'<div class="pb-small-head">'
        f'<span class="pb-rank-small">#{rank}</span>'
        f'<span class="pb-tag-small" style="color:{color};border:1px solid {color}55;background:{color}15;">{tag}</span>'
        f'</div>'
        f'<div class="pb-title-small">{title}</div>'
        f'<div class="pb-meta-small">{meta_short}{vbadge}</div>'
        f'</div>'
        f'</a>'
    )


def replace_podium(src, sec_html):
    """整块替换 #podium section。"""
    pat = re.compile(r'<section id="podium">.*?</section>', re.S)
    new_src, n = pat.subn(sec_html, src)
    if n == 0:
        return src, 0
    return new_src, n


def build_top10_head(data, date_s):
    """生成 TOP10 第 1 位头条事件卡（当日最新事件类内容，红线②：近3天）。
    来源优先级：events.json feed_events（当日）→ meme 事件类 → 降级为空（不写旧内容）。"""
    # 1) events.json feed_events 中当日/近3天条目
    ev_path = os.path.join(BASE, "events.json")
    try:
        ev = json.load(io.open(ev_path, encoding="utf-8"))
        for fe in (ev.get("feed_events") or []):
            pd = fe.get("pubdate") or fe.get("first_seen") or ""
            if not pd:
                continue
            try:
                d = datetime.date.fromisoformat(str(pd)[:10])
            except Exception:
                continue
            if (datetime.date.today() - d).days <= 3:
                title = fe.get("title") or fe.get("game") or "（无标题）"
                url = fe.get("source_url") or fe.get("url") or "#"
                src = fe.get("source_name") or fe.get("source") or "未知来源"
                md = str(pd)[5:10]
                return (f'<a class="top10-item" target="_blank" href="{esc(url)}">'
                        f'<span class="t10-rank">1</span>'
                        f'<span class="t10-img" style="background:linear-gradient(135deg,#ff5c39,#ffb020)" '
                        f'title="{esc(clip(title, 40))}"></span>'
                        f'<div class="t10-body"><b>{esc(clip(title, 40))}</b>'
                        f'<span class="t10-meta">{esc(src)} · {md}</span>'
                        f'<span class="t10-tag" style="color:#ff5c39;border-color:#ff5c3933;'
                        f'background:#ff5c3918">事件</span></div></a>')
    except Exception:
        pass
    # 2) meme 里最新的事件类视频（标题含事件关键词）
    RISK_KW = ["处罚", "封禁", "下架", "暴雷", "维权", "约谈", "整改", "泄露", "泄密",
               "收购", "减持", "停服", "跳票", "官宣", "定档"]
    for v in (data.get("popular", []) + data.get("weekly", [])):
        if fresh_bili(v) and any(k in (v.get("title") or "") for k in RISK_KW):
            pic = v.get("pic") or ""
            img = (f'<span class="t10-img" style="background-image:url({esc(pic)})" '
                   f'title="{esc(clip(v["title"], 40))}"></span>') if pic else \
                  '<span class="t10-img" style="background:linear-gradient(135deg,#ff5c39,#ffb020)"></span>'
            return (f'<a class="top10-item" target="_blank" href="{esc(v["url"])}">'
                    f'<span class="t10-rank">1</span>{img}'
                    f'<div class="t10-body"><b>{esc(clip(v["title"], 26))}</b>'
                    f'<span class="t10-meta">{wan(v.get("view", 0))} · {esc(v.get("tname") or "")}</span>'
                    f'<span class="t10-tag" style="color:#ff5c39;border-color:#ff5c3933;'
                    f'background:#ff5c3918">事件</span></div></a>')
    # 3) 降级：无当日事件内容则不写头条（避免陈旧继承）
    return None


def _cat_of(title, tname):
    """给候选打「为什么推荐」的分类标签。返回 (分类key, 展示文案) 或 None。
    2026-07-31：原逻辑只认「游戏分区 + 发售词」，结果全是二游新品，
    改成多类识别 + 每类限额，让候选覆盖首发/联动/实机/榜单/电竞/风波多条线。
    但前置闸门仍是「必须是游戏语境」——否则「隔壁被下架的视频」这类
    日常区标题会因为撞上『下架』被误当成行业风波捞进来。"""
    t = title
    # 闸一：必须是游戏语境（分区是游戏类，或标题含《书名号》/游戏/Steam/手游等硬信号）
    if not ((tname in GAME_TNAME) or GAME_KW.search(t)):
        return None
    # 「作品名/平台名」硬锚点：有它才算指名道姓的行业动态，没有多半是玩梗视频
    anchored = bool(re.search(r'《[^》]+》|Steam|手游|端游|主机|PC测试|新作|新游', t))
    for key, label, pat in CAT_RULES:
        if not re.search(pat, t):
            continue
        # 闸二（差异化）：collab / risk 这两类关键词最容易撞车
        #   （「复刻」「下架」在娱乐视频标题里满天飞），必须有硬锚点才放行；
        #   launch / preview / chart / esports 信号本身够强，游戏分区内即可放行。
        if key in ("collab", "risk") and not anchored:
            continue
        if not anchored and tname not in GAME_TNAME:
            continue
        return key, label
    # 2026-07-31 追加 — fallback 分类：游戏/ACG 分区的热门视频，即使没有命中六大类
    # 关键词（发售/联动/实机/榜单/电竞/风波），也应当有资格进入视觉焦点。
    # 缺了这一步，今天 14 个游戏区热门视频全军覆没——全是"海绵宝宝僵尸""三角洲大运"
    # 这类内容向游戏视频，六大类一个都不触发，视觉焦点只剩 events.json 补的 2 张纯文字卡。
    # 标"游戏热门"降级展示（vf-cap 不带彩色 tag、不会抢首发/定档的专业感）。
    if tname in GAME_TNAME or (GAME_KW.search(t) and anchored):
        return "game", "游戏热门"
    return None


FEED_SKIP = re.compile(r'维护|公告|违规名单|停服|签到|兑换码|每日|周报预告')
FEED_CAT = (
    ("industry", "产业/报告", r'报告|产业|协会|理事长|大会|峰会|政策|版号|出海|营收|流水|财报|市场'),
    ("platform", "平台/生态", r'鸿蒙|微软|索尼|任天堂|Switch|PS5|Xbox|Epic|Steam|苹果|应用商店|渠道'),
    ("console",  "主机/PC",   r'主机|PC|端游|次世代|掌机|GTA|3A'),
    ("launch2",  "定档/发售", r'定档|发售|上线|公测|开测|抢先体验|首曝|开服'),
)


FEED_CAT = (
    ("industry", "产业/报告", r'报告|产业|协会|理事长|大会|峰会|政策|版号|出海|营收|流水|财报|市场'),
    ("platform", "平台/生态", r'鸿蒙|微软|索尼|任天堂|Switch|PS5|Xbox|Epic|Steam|苹果|应用商店|渠道'),
    ("console",  "主机/PC",   r'主机|PC|端游|次世代|掌机|GTA|3A'),
    ("launch2",  "定档/发售", r'定档|发售|上线|公测|开测|抢先体验|首曝|开服'),
)


# ---------------- 视觉焦点·全自动定稿 ----------------
# 2026-07-31 改版：本站没有人工编辑，视觉焦点改由脚本直接选稿定稿，
# 原「人工大图 + 🤖 待确认候选」的两段式作废（候选块永远没人去确认，等于摆设）。
# 选材三原则：
#   ① 一稿一类：六类信号每类最多 1 张，从根上堵死「清一色二游新品」；
#   ② 大图位必须有封面图，无图稿只能进小卡；
#   ③ B 站流量榜每天只出 2~3 条行业稿，不够的用行业快报（产业/平台/主机/定档）补齐,
#      那是流量榜完全覆盖不到的维度。
VF_TAG = {
    "launch":   ("#ff5c39", "今日首发"),
    "launch2":  ("#ff5c39", "定档 / 发售"),
    "risk":     ("#ff5c39", "风波观察"),
    "preview":  ("#c792ea", "实机 / 前瞻"),
    "collab":   ("#c792ea", "联动 / 周年"),
    "chart":    ("#ffb020", "榜单 / 爆款"),
    "esports":  ("#4da3ff", "电竞 / 赛事"),
    "industry": ("#4da3ff", "产业 / 报告"),
    "platform": ("#4da3ff", "平台 / 生态"),
    "console":  ("#4da3ff", "主机 / PC"),
    "game":     ("#3fd68f", "游戏热门"),   # fallback：游戏区热门但未命中六大类事件关键词
}
VF_SLOTS = 5          # 1 张大图 + 4 张小卡，正好填满 4 列 × 2 行的网格


def _bili_picks(data, seen, used_cat, used_game, limit):
    """源 A：B 站热门 / 每周必看。带封面图，撑得起大图位。

    分两阶段选材：
      阶段 1 — 特定分类（launch/preview/chart/esports/collab/risk）：每类限 1；
      阶段 2 — fallback「游戏热门」：game/ACG 分区热门视频，不限类不限款，
               只保证同游戏不重复上两遍。用来填满事件型分类覆盖不到的缺口。

    2026-07-31 实测：14 个游戏区热门视频没有一个命中六大类关键词，
    阶段 1 空手而归，全靠阶段 2 兜底。"""
    pool = []
    for idx, v in enumerate(data.get("popular", [])):
        pool.append(dict(v, _src="热门", _rank=idx + 1))
    for v in data.get("weekly", []):
        pool.append(dict(v, _src="周必看", _rank=999))

    sorted_pool = sorted(pool, key=top10_score, reverse=True)

    def _collect(allowed_cats, max_pick, seen, used_cat, used_game):
        out = []
        for v in sorted_pool:
            url = v.get("url")
            title = v.get("title") or ""
            if not url or url in seen:
                continue
            a = _pub_age(v)
            if a is not None and a > MAX_AGE_DAYS:
                continue
            hit = _cat_of(title, v.get("tname") or "")
            if not hit or hit[0] not in allowed_cats:
                continue
            if hit[0] != "game" and hit[0] in used_cat:
                continue
            gm = re.search(r'《([^》]+)》', title)
            gname = gm.group(1) if gm else ""
            if gname and gname in used_game:
                continue
            seen.add(url)
            used_cat.add(hit[0])
            if gname:
                used_game.add(gname)
            out.append({
                "cat": hit[0], "title": title, "url": url,
                "pic": v.get("pic") or "", "view": v.get("view", 0),
                "note": ("B站热门第 %d 位" % v["_rank"]) if v.get("_src") == "热门" else "每周必看收录",
            })
            if len(out) >= max_pick:
                break
        return out

    # 阶段 1：特定分类（game 除外）
    specific_cats = {k for k, _lb, _pat in CAT_RULES}
    out = _collect(specific_cats, limit, seen, used_cat, used_game)

    # 阶段 2：fallback「游戏热门」填满剩余
    remaining = limit - len(out)
    if remaining > 0:
        out += _collect({"game"}, remaining, seen, used_cat, used_game)
    return out


def _feed_picks(seen, used_cat, used_game, limit):
    """源 B：行业情报快报。只取 48 小时内、有权威日期的稿，纯文字卡。"""
    if limit <= 0:
        return []
    try:
        with io.open(EVENTS_JSON, encoding="utf-8") as f:
            feed = json.load(f).get("feed_events", [])
    except Exception:
        return []
    today = dt.date.today()
    out = []
    for e in feed:
        title = (e.get("title") or "").strip()
        url = e.get("source_url") or ""
        if not title or not url or url in seen or FEED_SKIP.search(title):
            continue
        pub = e.get("pubdate")
        if not pub:                       # 无权威日期不进版（沿用全站时效红线）
            continue
        try:
            age = (today - dt.date.fromisoformat(pub[:10])).days
        except Exception:
            continue
        if age > 2:
            continue
        cat = None
        for k, _lb, pat in FEED_CAT:
            if re.search(pat, title):
                cat = k
                break
        if not cat or cat in used_cat:
            continue
        gm = re.search(r'《([^》]+)》', title)
        if gm and gm.group(1) in used_game:
            continue
        seen.add(url)
        used_cat.add(cat)
        if gm:
            used_game.add(gm.group(1))
        out.append({
            "cat": cat, "title": title, "url": url, "pic": "", "view": 0,
            "note": "%s · %s" % (e.get("source_name") or "行业信源", pub[:10]),
        })
        if len(out) >= limit:
            break
    return out


def _vf_card(it, big):
    """渲染一张视觉焦点卡。有封面用图卡，没封面用文字卡（渐变底 + 大字标题）。

    注意：这里只写「原始播放量」，H 交给后续 unify_heat.py 统一换算——
    和本文件头的单一职责约定保持一致，两个脚本各算一套 H 迟早对不上。"""
    color, label = VF_TAG.get(it["cat"], ("#4da3ff", "今日信号"))
    title = it["title"]
    cls = "vf-card vf-big" if big else "vf-card"
    if it["view"]:
        # 「万播放」这三个字不能省：unify_heat.fix_visual 靠正则 (\d+)万播放 认领这一条，
        # 写成「807万」它就匹配不到，H 会静默不补。
        meta = "%s播放 · %s · %s" % (wan(it["view"]), it["note"], label)
    else:
        # 行业快报没有播放量口径，硬凑一个 H 就是假数据，只标信源和日期
        meta = "%s · %s" % (it["note"], label)
    cap = ('<span class="vf-cap"><i class="vf-tag" style="background:%s"></i>'
           '<b>%s</b><small>%s</small></span>'
           % (color, esc(clip(title, 40 if big else 30)), esc(meta)))
    if it["pic"]:
        return ('<a class="%s" target="_blank" href="%s">'
                '<img src="%s" alt="%s" loading="lazy" referrerpolicy="no-referrer" '
                'onerror="this.closest(\'a\').style.display=\'none\'">%s</a>'
                % (cls, esc(it["url"]), esc(it["pic"]), esc(clip(title, 40)), cap))
    return ('<a class="%s vf-txt" target="_blank" href="%s" '
            'style="--vf-accent:%s">%s</a>' % (cls, esc(it["url"]), color, cap))


def build_visual_focus(data, exclude_urls):
    """全自动生成视觉焦点整块（1 大 + 4 小）。exclude_urls 为已进 TOP10 的链接，
    跨板块不重复上稿。"""
    seen, used_cat, used_game = set(exclude_urls), set(), set()
    picks = _bili_picks(data, seen, used_cat, used_game, VF_SLOTS)
    picks += _feed_picks(seen, used_cat, used_game, VF_SLOTS - len(picks))
    if not picks:
        return "", 0
    # 大图位：优先第一条有封面图的（文字卡撑不起 2×2）
    big_i = next((i for i, p in enumerate(picks) if p["pic"]), 0)
    picks.insert(0, picks.pop(big_i))
    cards = [_vf_card(p, i == 0) for i, p in enumerate(picks)]
    cls = 'vf-grid' + (' vf-sparse' if len(picks) <= 3 else '')
    return f'<div class="{cls}">' + "".join(cards) + '</div>', len(picks)


def replace_visual_focus(src, grid):
    """整块换掉 vf-grid，并清掉历史遗留的「🤖 自动候选」块。"""
    src = re.sub(r'<div class="vf-recs">.*?</div>\s*(?=</section>)', '', src, flags=re.S)
    if not grid:
        return src, 0
    m = re.search(r'<div class="vf-grid">.*?</div>\s*(?=<div class="vf-recs"|</section>)',
                  src, re.S)
    if not m:
        return src, 0
    return src[:m.start()] + grid + src[m.end():], 1


# ---------------- 今日速览·文字墙→卡片化 ----------------
# 2026-07-31：原 #brief 是一段 <p> 把所有标题用 ；连成文字墙，
# 不可扫读。改为卡片网格——每行一个信号，保留原 HTML 粗体标题。
# 2026-08-06：为每条速览匹配当日 meme 封面图，无匹配用渐变占位。
# ---------------- 头图 masthead·每日轮换 ----------------
# 2026-07-31 改：旧版头图硬写一张 Steam 静态图（如轮回之兽），每天刷新后仍是同一张。
# 改为按当日 B站 游戏区热门自动选：
#   1) 优先选有"发售/上线/联动/登顶"事件关键词的（更接近 S 级信号语义）
#   2) 否则按播放量选游戏区第一
#   3) 没有任何带图的候选 → 保留原版不动（不覆盖人工排版）
# 2026-08-19 升级：S 级判定改为「多源印证 + 跨平台声量」双达标（见 _signal_grade），
#   不再只看 B站单平台播放量或标题关键词。播放量降为 A 级兜底信号之一。
MASTHEAD_KICKERS = [
    ("S", "今日头条 · S 级信号 · 多源印证+跨平台声量"),
    ("A", "今日头条 · A 级信号"),
    ("B", "今日头条 · B 级信号"),
]


def _load_masthead_history():
    """读取最近几天的头图历史（用于避免重复轮换）。

    存储在 .workbuddy/masthead_history.json，结构：[{"date":"2026-08-04","bvid":"BV1xxx"}, ...]
    最多保留 7 天；head 超过即丢弃。
    """
    hfile = os.path.join(BASE, ".workbuddy", "masthead_history.json")
    if not os.path.exists(hfile):
        return []
    try:
        h = json.load(io.open(hfile, encoding="utf-8"))
        if not isinstance(h, list):
            return []
        return h
    except Exception:
        return []


def _save_masthead_history(history):
    """把今天的头图追加到历史尾部，裁剪到 7 天。写失败优雅降级（sandbox 锁时不阻断）。

    2026-08-21 修复：按 date 去重（同一天只保留最后一条）。
      此前只 append 不去重，导致每日刷新 + 手动测试 + 重跑累积出多条同日期重复记录
      （如 08-20 同 bvid 出现 4 次），污染 used_recent 集合、文件无谓膨胀。
    """
    hfile = os.path.join(BASE, ".workbuddy", "masthead_history.json")
    try:
        os.makedirs(os.path.dirname(hfile), exist_ok=True)
        # 按 date 去重（后写的覆盖先写的，保留当天最新）
        _dedup = {}
        for _h in history:
            if isinstance(_h, dict) and _h.get("date"):
                _dedup[_h["date"]] = _h
        _history = [_dedup[_d] for _d in _dedup]
        _history = _history[-7:]  # 最多保留 7 天
        # 用 temp-file + os.replace 模式（与 daily.html 写入一致）
        _tmp = hfile + ".tmp_" + str(int(dt.datetime.now().timestamp()))
        io.open(_tmp, "w", encoding="utf-8").write(json.dumps(_history, ensure_ascii=False, indent=1))
        try:
            os.replace(_tmp, hfile)
        except OSError:
            try:
                os.remove(hfile)
                os.rename(_tmp, hfile)
            except OSError:
                # sandbox 锁 / safe-delete 不可用时优雅降级，不阻断主流程
                try: os.remove(_tmp)
                except OSError: pass
    except OSError:
        pass


def _bvid_of(v):
    """从 B站视频条目里抽 bvid（多种可能字段名）。"""
    bid = v.get("bvid") or v.get("bv") or v.get("id") or ""
    if bid:
        return bid
    # 从 url 里抽 BV 号
    url = v.get("url") or v.get("href") or ""
    m = re.search(r'(BV[0-9A-Za-z]+)', url)
    if m:
        return m.group(1)
    # 退化：用标题做去重键（最后兜底）
    return v.get("title", "")


def _masthead_pick(data, exclude_urls=None):
    """挑出当天的头图候选：(url, title, src_url, kicker_lv, sub_chips)。

    2026-08-05 治理：连续多天不要重复同一张头图（每周必看是周更的，
    不避开的话一周都是同一张）。避开最近 2 天用过的 bvid。
    2026-08-17 治理：
      · 加 _is_game_vertical 硬过滤：头条必须是强游戏垂类（True），soft/False 一律排除
      · 接收 exclude_urls 参数（podium TOP1~TOP10 已用 URL），与 TOP10 去重
    """
    if not exclude_urls:
        exclude_urls = set()
    else:
        exclude_urls = set(exclude_urls)

    # 2026-08-20 治理：引入词云 H 值作为头图排序的主信号。
    #   此前 evt/candidates 仅按 play(view) 降序排序，导致今天最热（词云 H=98）但
    #   播放量非第一的《黑神话：钟馗》(89.8万) 被压在《七界梦谭》(345万) 之后，
    #   封面/标题与当日热度不符。改为 H 优先、view 次级。
    def _load_hmap():
        """读 wordcloud_terms.json，返回 {归一化标题: H} 映射（H∈0-100）。"""
        hmap = {}
        try:
            import glob as _glob
            wc = os.path.join(BASE, "wordcloud_terms.json")
            if os.path.exists(wc):
                obj = json.load(io.open(wc, encoding="utf-8"))
                for t in obj.get("terms", []) or []:
                    term = (t.get("term") or "").strip()
                    if term:
                        hmap[term] = int(t.get("heat", 0) or 0)
        except Exception:
            pass
        return hmap
    hmap = _load_hmap()

    def _h_of(v):
        """取候选视频的当日词云 H 值；无匹配则按 view 估算兜底 H（view/30万 截顶100）。"""
        title = (v.get("title") or "").strip()
        if title in hmap:
            return hmap[title]
        # 归一化：去《》、空格、中英文标点，便于词云 term 与视频标题跨格式匹配。
        import re as _re
        norm = lambda s: _re.sub(r'[\s《》〈〉\[\]()（）:：·\-_—,.，。、!！?？]', '', s).lower()
        nt = norm(title)
        # 1) 精确归一化匹配
        for term, h in hmap.items():
            if norm(term) == nt:
                return h
        # 2) 包含匹配（双向）：标题含 term 核心或 term 含标题核心
        for term, h in hmap.items():
            nterm = norm(term)
            if nterm and (nterm in nt or nt in nterm):
                return h
        view = v.get("view", 0) or 0
        return min(100, int(view / 300000))

    # 2026-08-05 治理：来源标注必须与条目真实出处一致，不能一律写"B 站热门"。
    # 打上 _origin 标签，渲染时按真实出处显示（全站排行 / 每周必看）。
    pool = []
    for v in data.get("popular", []) or []:
        v = dict(v); v["_origin"] = "B 站全站排行"; pool.append(v)
    for v in data.get("weekly", []) or []:
        v = dict(v); v["_origin"] = "B 站每周必看"; pool.append(v)
    # 限游戏垂类（强垂类 True）+ 时效闸 ≤MAX_AGE_DAYS 天 + 与 TOP10 去重
    candidates = []
    now_ts = time.time()
    for v in pool:
        t = v.get("tname", "") or ""
        title = v.get("title", "") or ""
        url = v.get("url") or ""
        if not v.get("pic"):
            continue
        # 时效过滤：头图与 TOP10 统一为近 3 天（红线：不得出现陈旧内容）。
        # 每周必看虽是周更，pubdate 天然偏旧，但「旧了就是旧了」，不能因是门面素材破例放宽。
        a = _pub_age(v)
        if a is not None and a > MAX_AGE_DAYS:
            continue
        # 与 TOP10 去重（2026-08-17）
        if url in exclude_urls:
            continue
        # 游戏分区直接入选；其他分区需硬锚点
        if t in GAME_TNAME or (GAME_KW.search(title) and re.search(r'《[^》]+》|Steam|手游|端游|主机|新作|新游', title)):
            # 2026-08-17：优先只收强垂类（True），排除 soft/False
            vert = _is_game_vertical(v)
            if vert is True:
                candidates.append(v)
            elif vert == "soft":
                # soft 作为降级池（仅当强垂类无候选时才启用）
                pass  # 在后面 fallback 阶段处理

    # 2026-08-17 二次治理：禁用 soft 泛娱乐兜底（用户反馈热度太低）
    #   强垂类不足时回退到 events.json 事件类头条，绝不放低热度泛娱乐上头图。
    #   （exclude_urls 一并传入，events 事件与 TOP10 去重）
    if not candidates:
        return _masthead_fallback_event(data, exclude_urls)

    # 读历史头图 bvid，跳过最近 2 天用过的（避免周更视频连续霸屏）
    history = _load_masthead_history()
    today = dt.date.today().strftime("%Y-%m-%d")
    used_recent = set()
    for h in history:
        if h.get("date") and h.get("bvid"):
            try:
                if (dt.date.today() - dt.date.fromisoformat(h["date"])).days <= 2:
                    used_recent.add(h["bvid"])
            except Exception:
                pass

    # 把候选中最近用过的 bvid 大幅降权（有效播放量 ×0.05），
    # 仅当没有其他候选时才可能被选（避免周更视频连续霸屏）。
    def penalized_view(v):
        bvid = _bvid_of(v)
        view = v.get("view", 0) or 0
        if bvid and bvid in used_recent:
            return int(view * 0.05)
        return view

    # 2026-08-21 修复：H 值也必须对「最近 2 天用过的 bvid」降权。
    #   否则 H 优先排序下，连任视频靠高 H 值无视 penalized_view 继续霸榜，
    #   违反「头图每天都要换，不要死卡排名」的红线（08-20/08-21 钟馗同视频连任即此 bug）。
    def _h_penalized(v):
        h = _h_of(v)
        bvid = _bvid_of(v)
        if bvid and bvid in used_recent:
            return h * 0.05
        return h

    # 1) 事件优先（发售/上线/登顶/联动/实机/周年/首曝/爆料）
    # 2026-08-05 扩：加 "爆料/新皮肤/时装/资料片" 匹配 popular 池的王者新皮肤
    # 2026-08-19 扩：补硬核大事件关键词（专场/State of Play/发布会/直面会/PV/前瞻/志怪/中元），
    #   让"影之刃零专场 State of Play""鸣潮清宵PV"这类硬核游戏大事件优先于 MC 整活视频。
    evt = [v for v in candidates if re.search(
        r'发售|上线|公测|首发|定档|开测|首测|不删档|联动|周年|登顶|实机|首曝|预约|开服|夺魁|夺冠|爆料|新皮肤|新角|时装|资料片|主题曲|CG|'
        r'专场|State\s?of\s?Play|发布会|直面会|前瞻|PV|宣传片|预告片|志怪|中元|鬼节|关卡',
        v.get("title", ""))]
    evt.sort(key=lambda x: (-_h_penalized(x), -penalized_view(x)))
    if evt:
        main = evt[0]
        # 记录今天用过的 bvid
        if _bvid_of(main):
            history.append({"date": today, "bvid": _bvid_of(main), "title": main.get("title", "")[:60]})
            _save_masthead_history(history)
        # 2026-08-19 升级：S 级不再只看播放量>200万，改为「多源印证 + 跨平台声量」双达标。
        #   view 仅作为 A 级兜底信号之一，不再是 S 级唯一门槛。
        _date_s = datetime.date.today().strftime("%Y-%m-%d")
        kicker_lv, _basis = _signal_grade(
            main.get("title", ""), data, _date_s, view=main.get("view", 0))
        sub_chips = [v.get("title", "")[:22] for v in evt[1:3]]
        return (main, kicker_lv, sub_chips, False)

    # 2) 硬核大事件优先（2026-08-19 修复）：B站候选池里没有命中事件关键词时，
    #    不要急着落到"游戏区第一"（那会把 MC 整活这类泛娱乐视频顶上头图）。
    #    先尝试从 events.json 找一条近 3 天的硬核游戏大事件（定档/公测/专场/PV/关卡，
    #    带权威溯源）。有的话优先上头图——符合"头条优先硬核游戏大事件"的诉求。
    evt_fb = _masthead_fallback_event(data, exclude_urls)
    if evt_fb:
        fb_main, fb_lv, fb_sub, fb_fallback = evt_fb
        # 只当事件有权威印证（非"单源事件"）或带强事件信号时才启用，
        # 避免把一条冷门公告硬顶掉有热度的 B站视频。
        if fb_main.get("_verdict") not in ("单源事件",) or re.search(
                r'公测|定档|首发|发售|专场|发布会|PV|志怪|中元|关卡', fb_main.get("title", "")):
            return (fb_main, fb_lv, fb_sub, False)

    # 3) 落到游戏区第一（最近用过的大幅降权；H 优先、view 次级，H 同样对连任降权）
    candidates.sort(key=lambda x: (-_h_penalized(x), -penalized_view(x)))
    main = candidates[0]
    if _bvid_of(main):
        history.append({"date": today, "bvid": _bvid_of(main), "title": main.get("title", "")[:60]})
        _save_masthead_history(history)
    # 2026-08-19 升级：S 级 = 多源印证 + 跨平台声量，播放量降为 A 级兜底信号。
    _date_s = datetime.date.today().strftime("%Y-%m-%d")
    kicker_lv, _basis = _signal_grade(
        main.get("title", ""), data, _date_s, view=main.get("view", 0))
    sub_chips = [v.get("title", "")[:22] for v in candidates[1:3]]
    return (main, kicker_lv, sub_chips, False)


def _masthead_fallback_event(data, exclude_urls=None):
    """强垂类 B站 视频不足时，回退到 events.json 事件类头条（无图渐变卡）。

    2026-08-17 治理（三轮）：
      · 用户明确「头图以游戏发售/官号PV物料/大事件为主」，不放低热度泛娱乐上头图。
      · 修复热度判断：不再按 feed_events 时间顺序取第一条，改为——
          1) 先排除 exclude_urls（与 TOP10 去重，events URL 也要参与）；
          2) 对每条事件做 _verify_top1 多源印证，印证强度高者优先（multi>single>weak）；
          3) 同强度下按事件权重（发售/登顶/首曝等大事件 > 普通更新公告）。
      · 只有当近 3 天没有任何一条「有权威印证（multi/single）」的事件时才放弃，
        返回 None 保留人工版式，绝不硬上一条冷门公告。
    """
    if not exclude_urls:
        exclude_urls = set()
    else:
        exclude_urls = set(exclude_urls)

    try:
        ev = json.load(io.open(os.path.join(BASE, "events.json"), encoding="utf-8"))
    except Exception:
        return None

    # 事件排序由 _heat_score 真实热度负责（tgmeng 提及 + 多源 + 强事件 + 新鲜度），
    #   不再单独定义强/次事件正则做分级——分级统一交给 _signal_grade（多源+跨平台）。
    scored = []
    for fe in (ev.get("feed_events") or []):
        title = fe.get("title") or fe.get("game") or "（无标题）"
        url = fe.get("source_url") or fe.get("url") or "#"
        pd = fe.get("pubdate") or fe.get("first_seen") or ""
        # 去重：已在 TOP10 出现的事件不再上头图
        if url in exclude_urls:
            continue
        # 真实热度评分（tgmeng 提及频次 + 多源 + 强事件信号 + 新鲜度衰减）
        age = None
        if pd:
            try:
                d = datetime.date.fromisoformat(str(pd)[:10])
                age = (datetime.date.today() - d).days
                if age > 3:
                    continue
            except Exception:
                age = None
        score, detail = _heat_score(title, data, datetime.date.today().strftime("%Y-%m-%d"), age=age)
        scored.append((score, detail, fe))

    # 2026-08-19：把 events[] 里带未来定档日期（date_start 在今天~+30天）的日历节点
    #   也纳入候选（诡秘之主 8/21、燕云十六声 8/27 这类定档事件只在 events[]，
    #   不进 feed_events，之前 fallback 永远扫不到它们 → 漏上头图）。
    #   定档节点是"硬核大事件"，优先级高，给一个正向基础分（不靠新鲜度衰减打压）。
    try:
        _today = datetime.date.today()
        for ce in (ev.get("events") or []):
            ds = ce.get("date_start")
            if not ds:
                continue
            try:
                d = datetime.date.fromisoformat(str(ds)[:10])
            except Exception:
                continue
            if not (_today <= d <= _today + datetime.timedelta(days=30)):
                continue  # 只看未来 30 天内的定档节点
            title = ce.get("title") or ce.get("game") or "（无标题）"
            url = ce.get("source_url") or ce.get("url") or "#"
            if not url or url == "#":
                continue  # 红线：无溯源链接不上头图
            if url in exclude_urls:
                continue
            # 定档事件按"今天发布的新闻"算新鲜度（age=0 最热），
            #   其日期是"未来发生日"而非"发布日"，不应被当作旧闻扣分。
            score, detail = _heat_score(title, data, datetime.date.today().strftime("%Y-%m-%d"), age=0)
            # 定档/公测/专场/关卡类硬核大事件，额外加权确保优先于普通更新
            if re.search(r'公测|定档|首发|发售|专场|发布会|PV|志怪|中元|关卡|联动', title):
                score += 6
            scored.append((score, detail, ce))
    except Exception:
        pass

    if not scored:
        return None

    # 真实热度优先
    scored.sort(key=lambda x: -x[0])
    score, detail, fe = scored[0]

    title = fe.get("title") or fe.get("game") or "（无标题）"
    url = fe.get("source_url") or fe.get("url") or "#"
    src = fe.get("source_name") or "未知来源"
    tg_cnt = detail.get("tgmeng_count", 0)
    n_src = detail.get("n_src", 0)
    # 权威媒体来源（机核/GameLook/游戏陀螺/白鲸出海等）本身即可信，无需"多源印证"证明；
    # 只有 B站 个人 UP 主视频才需要交叉印证。事件类头条优先显示"权威媒体"而非"单源"。
    auth_src = {"机核", "GameLook", "游戏陀螺", "游民星空", "17173", "白鲸出海"}
    is_auth = any(a in (src or "") for a in auth_src)
    if tg_cnt >= 3:
        verdict_label = "全网热榜印证"
    elif tg_cnt >= 1 or n_src >= 2:
        verdict_label = "多源提及"
    elif is_auth:
        verdict_label = "权威媒体"
    else:
        verdict_label = "单源事件"
    fake_v = {
        "title": title,
        "url": url,
        "pic": "",
        "tname": src,
        "_origin": "事件源",
        "_verdict": verdict_label,
        "_hits": [f"{k}×{c}" for k, c in (detail.get("tgmeng_kws") or [])],
        "view": 0,
    }
    # 2026-08-19 升级：事件类头条也统一走「多源印证 + 跨平台声量」分级，
    #   不再因标题含"发售/联动"等强事件关键词就单点给 S——S 必须多源+跨平台双达标。
    _date_s = datetime.date.today().strftime("%Y-%m-%d")
    lv, _basis = _signal_grade(title, data, _date_s, view=0)
    # 跨平台声量作为透明标注补进 _hits（命中平台列出来），供前端展示证据链。
    if _basis.get("platforms"):
        fake_v["_platforms"] = _basis["platforms"]
    return (fake_v, lv, [], False)


def build_masthead(data, exclude_urls=None):
    """根据当日数据构造头图 HTML。无候选时返回 None（保留原版）。
    2026-08-17：新增 exclude_urls 参数（podium TOP1~TOP10 已用 URL），与 TOP10 去重。
    """
    pick = _masthead_pick(data, exclude_urls=exclude_urls)
    if not pick:
        return None
    main, lv, sub_chips, is_fallback = pick
    pic = main.get("pic", "")
    title = main.get("title", "")
    url = main.get("url", "")
    tname = main.get("tname", "")
    kicker = next((label for k, label in MASTHEAD_KICKERS if k == lv),
                  MASTHEAD_KICKERS[0][1])

    sub_html = ""
    if sub_chips:
        joined = " · ".join(f"<b>{esc(s)}</b>" for s in sub_chips[:2] if s)
        if joined:
            sub_html = f'<p class="mh-sub">次条：{joined}</p>'

    # 2026-08-17：无图时走事件渐变底分支（复用 masthead 原生 mh-inner/mh-txt 结构，
    #   仅加 .mh-event 类换橙渐变底，文字样式与有图分支一致，不再混用 podium 的 pb-* 类）。
    if not pic:
        verdict_html = ""
        if main.get("_verdict"):
            hits = " · ".join(main.get("_hits") or [])[:80]
            vlabel = main.get("_verdict")
            # 全网热榜印证/多源提及/权威媒体=绿勾；单源事件=橙标
            cls = "pb-verify-ok" if vlabel in ("全网热榜印证", "多源提及", "权威媒体") else "pb-verify-warn"
            mark = "✓" if cls == "pb-verify-ok" else "⚠"
            verdict_html = (
                f'<span class="pb-verify {cls}" '
                f'title="印证：{esc(hits)}">{mark} {esc(vlabel)}</span>'
            )
        return (
            '<div class="masthead mh-event">'
            '<div class="mh-inner">'
            '<div class="mh-txt">'
            f'<span class="mh-kicker">{esc(kicker)} · 游戏大事件</span>'
            '<h1>'
            f'<a target="_blank" href="{esc(url)}">{esc(title)}</a>'
            '</h1>'
            f'<p>{esc(tname)} · {esc(main.get("_origin") or "事件源")}'
            + (f' {verdict_html}' if verdict_html else '')
            + '</p>'
            f'{sub_html}'
            '</div>'
            '<div class="mh-logo">'
            '<svg class="gp-logo" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 460 110">'
            '<text x="50%" y="50%" dominant-baseline="central" text-anchor="middle" '
            'font-family="Arial Black,PingFang SC,Microsoft YaHei,sans-serif" '
            'font-size="76" font-weight="900" letter-spacing="1">GamePulse</text>'
            '</svg>'
            '<span class="mh-wm">@DeanOvO</span>'
            '</div>'
            '</div></div>'
        )

    return (
        '<div class="masthead">'
        f'<img src="{esc(pic)}" alt="{esc(title)}" '
        'referrerpolicy="no-referrer" '
        'onerror="this.style.display=\'none\'">'
        '<div class="mh-inner">'
        '<div class="mh-txt">'
        f'<span class="mh-kicker">{esc(kicker)}</span>'
        '<h1>'
        f'<a target="_blank" href="{esc(url)}">{esc(title)}</a>'
        '</h1>'
        f'<p>{esc(tname)} · {esc(main.get("_origin") or "B 站采集")}'
        + (f' <span class="pb-verify pb-verify-warn" title="降级候选：今日强垂类已用尽，此为泛娱乐兜底">⚠ 泛娱乐兜底</span>' if is_fallback else '')
        + '</p>'
        f'{sub_html}'
        '</div>'
        '<div class="mh-logo">'
        '<svg class="gp-logo" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 460 110">'
        '<text x="50%" y="50%" dominant-baseline="central" text-anchor="middle" '
        'font-family="Arial Black,PingFang SC,Microsoft YaHei,sans-serif" '
        'font-size="76" font-weight="900" letter-spacing="1">GamePulse</text>'
        '</svg>'
        '<span class="mh-wm">@DeanOvO</span>'
        '</div>'
        '</div></div>'
    )


def replace_masthead(src, html):
    """把 <!--mh-->...<!--/mh--> 整段换成新头图。无候选时原样返回（保留人工排版）。"""
    if not html:
        return src, 0
    m = re.search(r'(<!--mh-->)(.*?)(<!--/mh-->)', src, re.S)
    if not m:
        return src, 0
    return src[:m.start()] + '<!--mh-->' + html + '<!--/mh-->' + src[m.end():], 1


# ---------------- 写回页面 ----------------
def replace_hot(src, body, intro, date_s):
    """整块替换 #hot section。body 里 <!--RISK_PLACEHOLDER--> 会被替换为旧 HTML 中的风险观察行。"""
    m = re.search(r'<section id="hot">.*?</section>', src, re.S)
    if not m:
        return src, 0
    sec = m.group(0)

    # 提取旧的风险观察行（保留人工策展内容 + data-curated 日期）
    # 兼容两种格式：旧 .mu-row 和新 .mu-block-risk（二次刷新）
    risk = re.search(
        r'<div class="mu-block mu-block-risk"[^>]*>.*?</div>\s*</div>', sec, re.S)
    if not risk:
        risk = re.search(
            r'<div class="mu-row"[^>]*data-curated[^>]*>.*?</div></div>', sec, re.S)
    risk_html = risk.group(0) if risk else ""
    risk_block = build_risk_block(risk_html) if risk_html else ""

    # 替换占位符
    body = body.replace("<!--RISK_PLACEHOLDER-->", risk_block)

    # 构建新的 section 内容
    mu_head = (
        f'<div class="mu-head"><span class="dot" style="background:#ff5c39"></span>'
        f'<b>今日风向总览</b>'
        f'<small>采集: B站 + 贴吧 + 全网热榜 · {date_s}</small></div>'
    )
    intro_html = intro if intro else ""
    new_sec = (f'<section id="hot">'
               f'<div class="sec-title"><span class="bar" style="background:var(--green)"></span>'
               f'社区风向与全网热榜 <small>多路采集 · 游戏/ACG 强相关过滤 · B站+贴吧+五平台热榜</small></div>'
               f'<div class="meme-card meme-unified">'
               f'{mu_head}\n{intro_html}\n{body}'
               f'</div></section>')

    return src[:m.start()] + new_sec + src[m.end():], 1


def replace_top10(src, items, head=None):
    m = re.search(r'<section id="radar">.*?</section>', src, re.S)
    if not m:
        return src, 0
    sec = m.group(0)
    cards = re.findall(r'<a class="top10-item".*?</a>', sec, re.S)
    if not cards:
        return src, 0
    # 头条事件位：优先用当日生成的 head；无则降级为第 2~10 位直接上位（不继承陈旧旧头条）
    if head:
        new_list = "\n".join([head] + items)
    else:
        new_list = "\n".join(items)
    start = sec.find(cards[0])
    end = sec.rfind("</a>") + len("</a>")
    new_sec = sec[:start] + new_list + sec[end:]
    return src.replace(sec, new_sec), len(items)


def stamp_curated(src, date_s):
    """给仍未打标的 mu-block-risk 打上数据源策展日期。

    旧架构是 .mu-row[data-curated]，新架构改为 .mu-block-risk[data-curated]。
    build_risk_block() 会从旧 HTML 携带已有日期；（e）过期告警也认这个属性。"""
    n = 0
    # visual 的历史 data-curated 全部清除（现在是全自动板块）
    src = re.sub(r'(<section id="visual")\s+data-curated="[^"]*"', r'\1', src)
    # 如果 mu-block-risk 还没有 data-curated，打上当日日期
    if 'mu-block-risk" data-curated=' not in src and 'mu-block-risk data-curated=' not in src:
        src = src.replace('<div class="mu-block mu-block-risk"',
                          f'<div class="mu-block mu-block-risk" data-curated="{date_s}"', 1)
        n += 1
    return src, n


def main():
    data, date_s = load_today()
    src = io.open(TARGET, encoding="utf-8").read()
    before = src

    body, intro, _ = build_hot(data, date_s)
    src, ok1 = replace_hot(src, body, intro, date_s)
    src, n3 = stamp_curated(src, date_s)
    # 今日焦点（#podium）：合并原「视觉焦点 + 内容 TOP10」，三档金字塔布局。
    #   TOP1 榜首大图（事件/视频） / TOP2-5 中卡（视频+缩略图） / TOP6-10 紧凑小卡（无图）。
    #   候选同源：popular+weekly → top10_score 排序 + events.json feed_events 当日事件。
    # 2026-08-18 顺序调整：masthead 头图优先选（门面素材），podium 避让头图已用 URL。
    masthead_html = build_masthead(data)
    _mh_urls = set()
    for m in re.finditer(r'href="([^"]*)"', masthead_html or ""):
        u = m.group(1)
        if u and u != "#" and not u.startswith("javascript:"):
            _mh_urls.add(u)

    podium_html, n_podium = build_podium(data, date_s, exclude_urls=_mh_urls)
    src, _ = replace_podium(src, podium_html)
    src, n_mh = replace_masthead(src, masthead_html)

    # 页面身份日期必须随当天采集同步；只改标题和头部 chip，绝不粗暴替换正文里的事件日期。
    src = re.sub(r'(<title>GamePulse · 游戏内容雷达 )\d{4}-\d{2}-\d{2}(</title>)',
                 lambda m: m.group(1) + date_s + m.group(2), src)
    src = re.sub(r'(<span class="chip fresh">)\d{4}-\d{2}-\d{2}(</span>)',
                 lambda m: m.group(1) + date_s + m.group(2), src)

    if src == before:
        print("refresh_content: 无变化")
        return
    # 写临时文件再替换，防止预览窗格锁住目标文件导致 PermissionError
    _tmp = TARGET + "." + dt.datetime.now().strftime("%H%M%S")
    io.open(_tmp, "w", encoding="utf-8").write(src)
    # 2026-08-13 修复：os.remove 失败被 except 吞掉后，os.rename 在 Windows 上因目标已存在抛 WinError 183，
    #   且把 TARGET 留下、_tmp 残留 → 下次再跑继续冲突。改为 os.replace 原子替换（覆盖目标，失败即抛错不静默）。
    try:
        os.replace(_tmp, TARGET)
    except OSError as e:
        # 目标被 IDE 预览锁住时，保留 _tmp 供人工拼装，明确报错而非静默吞掉
        print(f"refresh_content: 写入失败（目标被锁？）: {e}；临时文件保留在 {_tmp}")
        raise
    print(f"refresh_content: 已用 {date_s} 采集结果重刷内容板块"
          f"（梗雷达 3 子板块 / 今日焦点 {n_podium} 张（金字塔三档：TOP1 大 + TOP2-5 中 + TOP6-10 小）"
          f" / 头图轮换 {n_mh} / 新增策展标记 {n3}）")
    print("  注：今日焦点（原视觉焦点+TOP10 合并）脚本全自动定稿，无人工确认环节；")
    print("      风险观察仍为静态块，由 (e) 校验做过期告警。")


if __name__ == "__main__":
    main()
