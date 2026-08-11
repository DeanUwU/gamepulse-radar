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

WINDOW_DAYS = 3

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
    "政策·资本": "cat-gold",
    "产品·研发": "cat-blue",
    "出海·买量": "cat-green",
    "玩家·平台": "cat-purple",
}
MEDIA_CATS = ["政策·资本", "产品·研发", "出海·买量", "玩家·平台"]


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


def build_media(items, td):
    # items: 近7天、非信号的常规情报
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
    cards = []
    dates = []
    for c in MEDIA_CATS:
        for it in buckets[c]:
            url = clean_url(it.get("url"))
            if not url:
                continue
            title = esc(it.get("title") or it.get("game") or "（无标题）")
            src = esc(it.get("src_name") or it.get("source") or "未知来源")
            md = fmt_md(it.get("pubdate"))
            dates.append(it.get("pubdate"))
            cards.append(
                '<a class="intel-item" target="_blank" href="%s">'
                '<span class="intel-cat %s">%s</span>'
                '<div class="intel-body">'
                '<div class="intel-title">%s</div>'
                '<div class="intel-meta">%s · %s</div>'
                '</div></a>' % (esc(url), CAT_CSS[c], c, title, src, md)
            )
    if not cards:
        grid = '<div class="empty">近 7 天暂无已准入信源条目，明日刷新后自动更新。</div>'
    else:
        grid = '<div class="intel-grid">%s</div>' % "".join(cards)
    # 日期范围 chip
    valid = [d for d in dates if d]
    if valid:
        lo = fmt_md(min(valid)); hi = fmt_md(max(valid))
        rng = lo if lo == hi else "%s ~ %s" % (lo, hi)
    else:
        rng = fmt_md(td.isoformat())
    head = ('<div class="sec-title"><span class="bar" style="background:var(--blue)"></span>'
            '行业情报站 <small>已注册信源 · 近7天 · 按内容分类 · 标题即原文标题</small>'
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
