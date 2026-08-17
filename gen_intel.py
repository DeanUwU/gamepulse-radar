# -*- coding: utf-8 -*-
"""
gen_intel.py — 动态渲染 #board(榜单瞭望塔/事件信号) 与 #media(行业情报站)

数据真源：inbox/sources_curated.json（每日采集+准入后的已注册信源条目）
辅助真源：events.json 的 feed_events（信源快报， enrich #board 信号层）

治理红线：
  红线② 时效：只取 pubdate 在 [today-7, today] 窗口内的条目，严禁陈旧内容。
  红线③ 溯源：只使用真实文章 URL（http(s)），剔除搜索页/商店购买页/根域名占位。
  标题一律原文（不臆改），来源/日期如实标注。

写入：复用 gen_calendar/cross_words 的「temp 文件 + os.replace」原子写，
      避免预览窗格锁文件导致 PermissionError。仅替换已有 section，不插入。
"""
import os, re, io, sys, json, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.environ.get("GEN_INTEL_INDEX", os.path.join(BASE, "index.html"))
SRC_PATH = os.environ.get("GEN_INTEL_SRC", os.path.join(BASE, "inbox", "sources_curated.json"))
EVENTS_PATH = os.environ.get("GEN_INTEL_EVENTS", os.path.join(BASE, "events.json"))
TODAY_ENV = os.environ.get("TODAY")  # 允许流水线统一传入运行日期

# 2026-08-17 治理：行业情报站只保留「今天+昨天」近2天，超2天条目自动剔除（不再生成 is-stale 隐藏补丁）
WINDOW_DAYS = 1

# ---- 分类关键词 ----
CAT_RISK = ["官宣", "登顶", "破纪录", "创纪录", "夺冠", "下架", "停服", "暴雷",
            "维权", "约谈", "整改", "抄袭", "诉讼", "侵权", "处罚", "翻车",
            "事故", "泄密", "泄露", "暂停", "取消", "跳票", "失败", "崩盘"]
CAT_CAPITAL = ["资本", "减持", "收购", "并购", "上市", "融资", "财报", "版号",
               "监管", "政策", "IPO", "入股", "投资", "估值", "利润", "营收",
               "股价", "退市", "回购", "增持", "估值"]
CAT_PRODUCT = ["上线", "测试", "公布", "新作", "DLC", "研发", "预告", "发售",
               "定档", "公测", "首曝", "实机", "PV", "版本", "更新", "联动",
               "曝光", "玩法", "测评", "评测", "演示", "首曝", "预约"]
CAT_OVERSEA = ["出海", "买量", "海外", "东南亚", "欧美", "全球", "投放", "本土化",
               "港澳台", "日韩", "国际服", "外服", "全球化"]
CAT_PLAYER = ["玩家", "社区", "平台", "Steam", "直播", "赛事", "主播", "二创",
              "同人", "吐槽", "争议", "bug", "外挂", "封禁", "Mod", "模组", "攻略"]

CAT_CSS = {  # #media 分类 -> cat-* 配色
    "事件·风险": "cat-gold",
    "政策·资本": "cat-gold",
    "产品·研发": "cat-blue",
    "出海·买量": "cat-green",
    "玩家·平台": "cat-purple",
}
MEDIA_CATS = ["事件·风险", "政策·资本", "产品·研发", "出海·买量", "玩家·平台"]


def esc(s):
    if s is None:
        return ""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def today():
    if TODAY_ENV:
        try:
            return datetime.date.fromisoformat(TODAY_ENV)
        except Exception:
            pass
    return datetime.date.today()


def load_items(path):
    if not os.path.exists(path):
        return []
    try:
        d = json.load(io.open(path, encoding="utf-8"))
    except Exception:
        return []
    if isinstance(d, list):
        return d
    for k in ("items", "data", "sources", "entries"):
        if isinstance(d.get(k), list):
            return d[k]
    return []


def in_window(it, td):
    p = it.get("pubdate") or it.get("date") or it.get("first_seen")
    if not p:
        return False
    try:
        pd = datetime.date.fromisoformat(str(p)[:10])
    except Exception:
        return False
    delta = (td - pd).days
    return 0 <= delta <= WINDOW_DAYS


def is_signal(it):
    """信号层判定：feed_events 注入的条目属于 #board 信号层，应从 #media 排除。"""
    # curated 自身的 src_id/src_name 是站内源，feed 注入条目的 src_name 通常是 events.json 的 source_name
    src = (it.get("src_name") or it.get("source_name") or "").strip()
    if src.startswith("信源快报") or src.startswith("信源·"):
        return True
    # 兜底：含特定标记
    if it.get("from_feed"):
        return True
    return False


def clean_url(u):
    if not u or not str(u).startswith("http"):
        return None
    u = str(u).strip()
    low = u.lower()
    # 红线③：剔除搜索页/商店购买页/根域名占位
    if "search.bilibili.com" in low:
        return None
    if "store.steampowered.com/app/" in low:
        return None
    # 根域名（无 path/query/fragment）视为占位，剔除
    m = re.match(r"https?://[^/]+/?$", low)
    if m:
        return None
    return u


def classify(it):
    t = (it.get("title") or "") + " " + (it.get("game") or "")
    for kw in CAT_RISK:
        if kw in t:
            return "事件·风险"
    for kw in CAT_CAPITAL:
        if kw in t:
            return "政策·资本"
    for kw in CAT_OVERSEA:
        if kw in t:
            return "出海·买量"
    for kw in CAT_PLAYER:
        if kw in t:
            return "玩家·平台"
    for kw in CAT_PRODUCT:
        if kw in t:
            return "产品·研发"
    return "产品·研发"


def fmt_md(p):
    try:
        return str(p)[:10][5:]  # MM-DD
    except Exception:
        return ""


# B站交叉验证：返回 {游戏关键词: 最大view}，用于给 #media 打 🔥
def load_bili_heat():
    out = {}
    p = os.path.join(BASE, "collectors", "meme_%s.json" % today().strftime("%Y%m%d"))
    if not os.path.exists(p):
        # 回退到最新一份 meme 文件
        import glob
        fs = sorted(glob.glob(os.path.join(BASE, "collectors", "meme_*.json")))
        if not fs:
            return out
        p = fs[-1]
    try:
        d = json.load(io.open(p, encoding="utf-8"))
    except Exception:
        return out
    entries = (d.get("popular") or []) + (d.get("meme_ups") or [])
    # 游戏关键词表（与 #media 标题匹配）
    GAME_KW = ["gta", "无人深空", "no man", "塔科夫", "逃离塔科夫", "三国", "剑心雕龙",
               "王者荣耀", "opus", "unity", "roblox", "steam", "永劫", "萤火", "阴阳师",
               "dota", "三角洲", "原神", "鸣潮", "恋与", "海猫", "卡厄思", "穿越火线",
               "无畏契约", "valorant", "影之刃", "卡拉彼丘", "coin master", "tga"]
    for e in entries:
        t = (e.get("title") or "").lower()
        view = e.get("view") or 0
        for kw in GAME_KW:
            if kw in t:
                out[kw] = max(out.get(kw, 0), view)
    return out


def bili_hot_flag(title, bili_heat):
    """命中 B站百万播放则返回 🔥 标记，否则空串"""
    t = (title or "").lower()
    for kw, view in bili_heat.items():
        if kw in t and view >= 1_000_000:
            return " 🔥"
    return ""


# 2026-08-17 治理：高热度关键词（与手动补丁一致，给常规卡打 is-hot 突出）
HIGH_HEAT_KW = re.compile(
    r'(实机|演示|预购|上线|发售|登顶|新版本|大爆款|大事件|新资料片|新角色|新皮肤|科隆|D23|曝光|破圈|干到畅销榜|暴增|即将上线|即将开测|开启预购|首曝|定档|即将登陆)')


def is_hot_title(title):
    return bool(HIGH_HEAT_KW.search(title or ""))


def _card_html(c, it, bili_heat, cls):
    """渲染一张 #media 卡片；返回 (html, title_plain, src, md, is_hot) 或 None（url 无效）"""
    url = clean_url(it.get("url"))
    if not url:
        return None
    title = esc(it.get("title") or it.get("game") or "（无标题）")
    title_plain = it.get("title") or it.get("game") or "（无标题）"
    src = esc(it.get("src_name") or it.get("source") or "未知来源")
    md = fmt_md(it.get("pubdate"))
    flag = bili_hot_flag(title, bili_heat)
    hot = is_hot_title(title_plain)
    extra = " is-hot" if hot else ""
    html = ('<a class="%s%s" target="_blank" href="%s">'
            '<span class="intel-cat %s">%s</span>'
            '<div class="intel-body"><div class="intel-title">%s%s</div>'
            '<div class="intel-meta">%s · %s</div></div></a>'
            % (cls, extra, esc(url), CAT_CSS[c], c, title, flag, src, md))
    return (html, title_plain, src, md, hot)


def build_media(items, td):
    bili_heat = load_bili_heat()
    # items: 近2天、非信号的常规情报
    buckets = {c: [] for c in MEDIA_CATS}
    for it in items:
        cat = classify(it)
        if cat not in buckets:
            cat = "产品·研发"
        buckets[cat].append(it)
    # 排序：日期倒序
    for c in buckets:
        buckets[c].sort(key=lambda x: x.get("pubdate") or "", reverse=True)

    chips = "".join(
        '<span class="chip %s">%s %d</span>' % (CAT_CSS[c], c, len(buckets[c]))
        for c in MEDIA_CATS
    )

    # ===== 视觉分层 =====
    # 头条区：事件·风险 + 政策·资本（用户最关心的硬新闻，大卡）
    top_cats = ["事件·风险", "政策·资本"]
    top_items = []
    for c in top_cats:
        for it in buckets[c]:
            top_items.append((c, it))
    # 常规区：其余分类，再按热度分「高热度」+「日常折叠」
    reg_items = []
    for c in MEDIA_CATS:
        if c in top_cats:
            continue
        for it in buckets[c]:
            reg_items.append((c, it))

    dates = []
    srcs = set()
    hot_titles = []   # 焦点事件（高热度标题）
    today_cnt = 0
    # 头条卡片（大卡）
    top_cards = []
    for c, it in top_items:
        r = _card_html(c, it, bili_heat, "intel-top")
        if not r:
            continue
        html, tplain, src, md, hot = r
        dates.append(it.get("pubdate"))
        srcs.add(src)
        if str(it.get("pubdate"))[:10] == td.isoformat():
            today_cnt += 1
        if hot:
            hot_titles.append(tplain)
        top_cards.append(html)

    # 常规卡片：拆「高热度」与「日常」两组
    hot_cards = []
    daily_cards = []
    for c, it in reg_items:
        r = _card_html(c, it, bili_heat, "intel-item")
        if not r:
            continue
        html, tplain, src, md, hot = r
        dates.append(it.get("pubdate"))
        srcs.add(src)
        if str(it.get("pubdate"))[:10] == td.isoformat():
            today_cnt += 1
        if hot:
            hot_cards.append(html)
            hot_titles.append(tplain)
        else:
            daily_cards.append(html)

    total_visible = len(top_cards) + len(hot_cards) + len(daily_cards)
    n_media = len(srcs)
    n_hot = len(hot_titles)
    yesterday_cnt = total_visible - today_cnt

    # 今日小结（数据全部实算，不写死）
    focus_items = "".join('<li><b>%s</b></li>' % esc(t) for t in hot_titles[:4])
    summary = (
        '<div class="intel-summary">'
        '<div class="sum-head"><i>◆</i>今日小结 · %s</div>'
        '<span class="sum-stat">%d</span> 条 行业日报（覆盖 <span class="sum-stat">%d</span> 家媒体 · '
        '<span class="sum-stat">%d</span> 条当日 / <span class="sum-stat">%d</span> 条昨日 · '
        '<span class="sum-stat">%d</span> 件<span class="sum-stat gold">高热度</span>事件）。'
        '焦点事件：<ul>%s</ul></div>'
        % (fmt_md(td.isoformat()), total_visible, n_media, today_cnt,
           yesterday_cnt, n_hot, focus_items)
    )

    # 组装 grid
    parts = []
    if top_cards:
        parts.append('<div class="intel-top-grid">%s</div>' % "".join(top_cards))
    parts.append(summary)
    if hot_cards:
        parts.append('<div class="intel-grid intel-grid-hot">%s</div>' % "".join(hot_cards))
    # 日常条目折叠
    parts.append('<details class="intel-fold"><summary><b>行业日常日报</b>'
                 '<span class="fold-count">%d 条</span></summary>' % len(daily_cards))
    if daily_cards:
        parts.append('<div class="intel-grid">%s</div>' % "".join(daily_cards))
    parts.append('</details>')

    if not top_cards and not hot_cards and not daily_cards:
        grid = '<div class="empty">近 2 天暂无已准入信源条目，明日刷新后自动更新。</div>'
    else:
        grid = "".join(parts)

    # 日期范围 chip
    valid = [d for d in dates if d]
    if valid:
        lo = fmt_md(min(valid)); hi = fmt_md(max(valid))
        rng = lo if lo == hi else "%s ~ %s" % (lo, hi)
    else:
        rng = fmt_md(td.isoformat())
    head = ('<div class="sec-title"><span class="bar" style="background:var(--blue)"></span>'
            '行业情报站 <small>已注册信源 · 近2天 · 按内容分类 · 🔥=B站百万播放</small>'
            '<span class="chip" style="margin-left:auto">%s</span></div>' % rng)
    return ('<section id="media">%s<div class="intel-chips">%s</div>%s</section>'
            % (head, chips, grid))


def atomic_write(path, html):
    tmp = path + ".genintel.tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(html)
    if os.path.exists(path):
        os.remove(path)
    os.replace(tmp, path)


def replace_section(html, sid, new_sec):
    m = re.search(r'<section id="%s">.*?</section>' % sid, html, re.S)
    if not m:
        return html, False
    return html[:m.start()] + new_sec + html[m.end():], True


def main():
    td = today()
    log = lambda s: print("  [gen_intel] " + s, flush=True)
    curated = load_items(SRC_PATH)
    recent = [it for it in curated if in_window(it, td)]
    log("sources_curated 总数=%d，近7天=%d" % (len(curated), len(recent)))

    # enrich #board 信号层：并入 events.json feed_events 中近7天条目
    feed = []
    if os.path.exists(EVENTS_PATH):
        try:
            ev = json.load(io.open(EVENTS_PATH, encoding="utf-8"))
            for fe in (ev.get("feed_events") or []):
                if in_window(fe, td):
                    feed.append({
                        "title": fe.get("title"),
                        "url": fe.get("source_url"),
                        "src_name": fe.get("source_name"),
                        "pubdate": fe.get("pubdate") or fe.get("first_seen"),
                        "game": fe.get("game"),
                    })
        except Exception as e:
            log("feed_events 读取失败：%s" % e)
    log("feed_events 近7天 enrich=%d" % len(feed))

    all_recent = recent + feed
    # 去重：同 url 或同标题（跨源重复报道）只留一条，优先 curated
    seen_url = set(); seen_title = set(); dedup = []
    for it in all_recent:
        u = clean_url(it.get("url"))
        if not u:
            continue
        t = re.sub(r'\s+', '', str(it.get("title") or ""))
        if u in seen_url or t in seen_title:
            continue
        seen_url.add(u); seen_title.add(t); dedup.append(it)

    if not os.path.exists(INDEX_PATH):
        log("index.html 不存在，跳过")
        return 1
    html = io.open(INDEX_PATH, encoding="utf-8").read()

    media_html = build_media([it for it in dedup if not is_signal(it)], td)

    html, ok_m = replace_section(html, "media", media_html)
    if not ok_m:
        log("⚠ 未找到 #media section，跳过（仅替换不插入）")
        return 1

    atomic_write(INDEX_PATH, html)
    log("✓ #media 已动态重写（media 卡=%d）" % html.count('class="intel-item"'))
    return 0


if __name__ == "__main__":
    sys.exit(main())
