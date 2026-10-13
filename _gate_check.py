# -*- coding: utf-8 -*-
"""_gate_check.py — 发布前阻断项门禁校验（每日刷新专用）

用法：python _gate_check.py
退出码 0 = 全部 PASS 可发布；1 = 有 FAIL 必须修复。

已知误报陷阱（勿重犯）：
  ① search.bilibili 只统计真实链接属性（href=/src=）里出现的，
     wordcloud.html 脚注写着「绝不生成 search.bilibili.com 搜索链接」是说明文案，非链接。
  ② masthead 检查要匹配 class="masthead"，不是字面 ".masthead"。
"""
import json, re, sys, datetime, os, time, html as html_lib

BASE = os.path.dirname(os.path.abspath(__file__))
TODAY = datetime.date.today().strftime("%Y-%m-%d")
MD = TODAY[5:].replace("-", "-")  # 08-24
results = []


def chk(name, ok, detail=""):
    results.append((name, ok, detail))


def read(p):
    with open(os.path.join(BASE, p), encoding="utf-8") as f:
        return f.read()


idx = read("index.html")
wc = read("wordcloud.html")

# ① 两页「页面日期 chip」（class 含 fresh）日期全为今天
#   2026-08-24 修复：只查页面身份日期 chip（chip fresh），不查内容时间范围 chip
#   （如 intel 板块的 "08-23 ~ 08-24" 是信源时间范围，非页面日期，属误报）。
bad_chip = []
for fname, html in (("index.html", idx), ("wordcloud.html", wc)):
    chips = re.findall(r'<[^>]*class="[^"]*chip[^"]*fresh[^"]*"[^>]*>(.*?)</', html, re.S)
    for c in chips:
        for d in re.findall(r"2026-\d\d-\d\d", c):
            if d != TODAY:
                bad_chip.append((fname, d))
        for d in re.findall(r"\b(\d\d)-(\d\d)\b", re.sub(r"2026-\d\d-\d\d", "", c)):
            iso = f"2026-{d[0]}-{d[1]}"
            if iso != TODAY:
                bad_chip.append((fname, iso))
chk("chip 日期全为今天", not bad_chip, f"越界 {bad_chip[:6]}" if bad_chip else f"全为 {TODAY}")

# ② 零残留占位符
ph = []
for fname, html in (("index.html", idx), ("wordcloud.html", wc)):
    n1 = len(re.findall(r"<<EVT_\d+>>", html))
    n2 = html.count("__SRC__")
    if n1 or n2:
        ph.append((fname, n1, n2))
chk("零 <<EVT_>> / __SRC__ 占位符", not ph, str(ph) if ph else "0")

# ③ GameLook 链接均为具体文章
gl = re.findall(r'https?://(?:www\.)?gamelook\.com\.cn[^"\'\s<>]*', idx + wc)
gl_bad = [u for u in gl if not re.search(r"/20\d\d/\d\d/\d+/?", u)]
chk("GameLook 均为具体文章", not gl_bad, f"共 {len(gl)} 条，异常 {gl_bad[:4]}")

# ④ 零 search.bilibili.com 真实链接
sb = []
for fname, html in (("index.html", idx), ("wordcloud.html", wc)):
    hits = re.findall(r'(?:href|src)\s*=\s*["\'][^"\']*search\.bilibili\.com[^"\']*', html)
    if hits:
        sb.append((fname, len(hits), hits[:2]))
chk("零 search.bilibili 搜索链接", not sb, str(sb) if sb else "0")

# ④b Steam 购买页 / 通用搜索页 / 主页根占位
steam_buy = re.findall(r'https?://store\.steampowered\.com/app/\d+', idx + wc)
chk("零 Steam 购买页 /app/", not steam_buy, f"{len(steam_buy)} 条" if steam_buy else "0")
generic_search = re.findall(r'(?:href)\s*=\s*["\'][^"\']*(?:/search/|\?q=|search\?)[^"\']*', idx + wc)
chk("零通用搜索页链接", not generic_search, f"{len(generic_search)} 条 {generic_search[:2]}" if generic_search else "0")

# ⑤ index.html 含 masthead
chk('index.html 含 class="masthead"', 'class="masthead' in idx, "存在" if 'class="masthead' in idx else "缺失")

# ⑥ events.json scaffold 日期为今天
ev = json.load(open(os.path.join(BASE, "events.json"), encoding="utf-8"))
sc = ev["scaffold"] if isinstance(ev["scaffold"], str) else json.dumps(ev["scaffold"], ensure_ascii=False)
sc_dates = set(re.findall(r"2026-\d\d-\d\d", sc))
chk("events.json scaffold 日期为今天", sc_dates == {TODAY} or (TODAY in sc_dates and len(sc_dates) == 1),
    f"scaffold 日期集合 {sorted(sc_dates)}")
chk("events.json meta.last_update 为今天", ev.get("meta", {}).get("last_update") == TODAY,
    str(ev.get("meta", {}).get("last_update")))

# ⑦ feed_events 全在 7 天窗口内
lo = (datetime.date.today() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
out = [(x["id"], x.get("pubdate")) for x in ev.get("feed_events", [])
       if (not x.get("pubdate") and not x.get("date_unverified"))
       or (x.get("pubdate") and not (lo <= x["pubdate"] <= TODAY))]
chk("feed_events 全在 7 天窗口", not out, f"共 {len(ev.get('feed_events', []))} 条，越窗 {out[:6]}")

# ⑦b 核心内容分层时效（阻断）
#   2026-10-13 治理："没有超过 3 天"只是尾部绝对红线，不代表有资格占据日报核心位。
#   · masthead：文章必须是当天新闻/今天上线，B站视频必须在近 24 小时内；
#   · 今日焦点 TOP1-5：文章最多昨天发布，B站视频必须在近 24 小时内；
#   · 今日焦点 TOP6-10：允许近 3 天素材低位补充；
#   · 所有内容都必须能在当日采集文件或 events.json 中反查，未知日期直接 FAIL。
fe_by_url = {html_lib.unescape(e.get("source_url") or e.get("url") or ""): e
             for e in ev.get("feed_events", [])}
ev_by_url = {html_lib.unescape(e.get("source_url") or e.get("url") or ""): e
             for e in ev.get("events", [])}


def _walk_video_items(node):
    if isinstance(node, dict):
        u = node.get("url")
        if u and node.get("pubdate"):
            yield html_lib.unescape(str(u)), node
        for value in node.values():
            yield from _walk_video_items(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_video_items(value)


collector_path = os.path.join(BASE, "collectors", f"meme_{TODAY.replace('-', '')}.json")
try:
    collector = json.load(open(collector_path, encoding="utf-8"))
    video_by_url = dict(_walk_video_items(collector))
    collector_error = ""
except Exception as exc:
    video_by_url = {}
    collector_error = f"当日采集文件不可读: {exc}"


def _video_age_hours(url):
    item = video_by_url.get(html_lib.unescape(url))
    if not item:
        return None
    try:
        age = (time.time() - int(item.get("pubdate") or 0)) / 3600.0
        return age if age >= 0 else None
    except Exception:
        return None


def _article_age_days(url):
    """返回 (发布日期距今天数, 描述)；今天上线但无 pubdate 的日历事件按 0 天处理。"""
    url = html_lib.unescape(url)
    item = fe_by_url.get(url) or ev_by_url.get(url)
    if not item:
        return None, "链接不在 feed_events/events 已知源内"
    pd = str(item.get("pubdate") or "")[:10]
    ds = str(item.get("date_start") or "")[:10]
    if pd:
        try:
            age = (datetime.date.today() - datetime.date.fromisoformat(pd)).days
        except ValueError:
            return None, f"pubdate={pd} 无法解析"
        if ds and ds > TODAY:
            return None, f"未来定档预告 pubdate={pd} date_start={ds}"
        return age, f"pubdate={pd}"
    if ds == TODAY:
        return 0, f"今日发售/上线 date_start={ds}"
    return None, f"时效未知 pubdate={pd or None} date_start={ds or None}"


def _freshness(url, article_days, video_hours):
    """按链接类型检查时效，返回 (ok, detail)。"""
    url = html_lib.unescape(url)
    if "bilibili.com/video/" in url:
        hours = _video_age_hours(url)
        if hours is None:
            return False, collector_error or "B站链接未在当日采集文件中找到/无 pubdate"
        return hours <= video_hours, f"B站 age={hours:.1f}h，上限 {video_hours:g}h"
    days, detail = _article_age_days(url)
    return days is not None and 0 <= days <= article_days, f"文章 {detail}，上限 {article_days} 天"


mh_m = re.search(r'class="masthead[^"]*".*?<h1>\s*<a[^>]+href="([^"]+)"', idx, re.S)
mh_url = html_lib.unescape(mh_m.group(1)) if mh_m else None
if not mh_url:
    chk("masthead 头条链接可提取", False, "未找到 masthead <h1> 链接")
else:
    chk("masthead 头条链接可提取", True, mh_url)
    ok_mh, mh_detail = _freshness(mh_url, article_days=0, video_hours=24)
    chk("masthead 当天文章 / 近24小时视频", ok_mh, mh_detail)

# 提取今日焦点三档卡片并按 rank 校验。TOP1 结构中无 #1 文本，固定为 rank=1。
podium_m = re.search(r'<section id="podium">(.*?)</section>', idx, re.S)
podium_cards = []
if podium_m:
    card_re = re.compile(
        r'<a class="pb-card-(1|mid|small)[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
    for tier, url, body in card_re.findall(podium_m.group(1)):
        rank = 1 if tier == "1" else None
        if rank is None:
            rank_m = re.search(r'class="pb-rank-(?:mid|small)">#(\d+)</span>', body)
            rank = int(rank_m.group(1)) if rank_m else None
        podium_cards.append((rank, html_lib.unescape(url)))
chk("今日焦点卡片 rank 可提取", bool(podium_cards) and all(r is not None for r, _ in podium_cards),
    f"提取 {len(podium_cards)} 条: {[r for r, _ in podium_cards]}")

core_bad = []
tail_bad = []
for rank, url in podium_cards:
    if rank is None:
        continue
    if rank <= 5:
        ok, detail = _freshness(url, article_days=1, video_hours=24)
        if not ok:
            core_bad.append((rank, detail, url))
    else:
        ok, detail = _freshness(url, article_days=3, video_hours=72)
        if not ok:
            tail_bad.append((rank, detail, url))
chk("今日焦点 TOP1-5：文章≤1天 / 视频≤24小时", not core_bad,
    f"异常 {core_bad[:4]}" if core_bad else "全部通过")
chk("今日焦点 TOP6-10：已知时效≤3天", not tail_bad,
    f"异常 {tail_bad[:4]}" if tail_bad else "全部通过")

# ⑧ wordcloud_terms 校验
w = json.load(open(os.path.join(BASE, "wordcloud_terms.json"), encoding="utf-8"))
terms = w.get("terms", [])
wbad = []
for t in terms:
    u = t.get("href", "")
    if "search.bilibili.com" in u or re.search(r"store\.steampowered\.com/app/\d", u) or re.fullmatch(r"https?://[^/]+/?", u):
        wbad.append(t.get("term"))
    if not (0 <= t.get("heat", -1) <= 100):
        wbad.append(t.get("term") + "(heat)")
chk("wordcloud_terms 日期=今天", w.get("date") == TODAY, str(w.get("date")))
chk("wordcloud_terms 条数 24-32", 24 <= len(terms) <= 32, f"{len(terms)} 条")
chk("wordcloud_terms 零违规链接", not wbad, str(wbad[:5]) if wbad else "0")

# ⑧b 词条时效（阻断）：wordcloud_terms.json 里 _pubdate 已知的 term 不能 > STALE_DAYS 天
#   2026-08-24 治理：B 站每周必看 / 梗 UP 等历史视频曾被策展进"今日热词"，
#   而词云只过滤了字面"X月X日"日期。runtime 已在 cross_words.py 剥除，但数据层必须有兜底——
#   本红线禁止任何带 _pubdate 的过期词条留在数据文件里（一旦写进 JSON 即阻断）。
lo3c = (datetime.date.today() - datetime.timedelta(days=3)).strftime("%Y-%m-%d")
stale_terms = []
for t in terms:
    pd = t.get("_pubdate")
    if pd and re.match(r"20\d{2}-\d{2}-\d{2}", str(pd)):
        if not (lo3c <= pd <= TODAY):
            stale_terms.append((t.get("term"), pd, t.get("href", "")[:50]))
n_pub = sum(1 for t in terms if t.get("_pubdate"))
chk("wordcloud_terms 已知 _pubdate 全部 ≤3 天",
    not stale_terms,
    f"过期 {len(stale_terms)} 条: {stale_terms[:4]}" if stale_terms else f"共 {n_pub} 条带 _pubdate 全 ≤3 天")

# ⑨ 日历只渲染未来
cal_html = re.search(r'id="cal"(.*?)</section>', idx, re.S)
chk("index.html 含 #cal 区段", bool(cal_html), "存在" if cal_html else "缺失")

print("=" * 60)
fails = 0
for name, ok, detail in results:
    print(("✅ PASS " if ok else "❌ FAIL ") + name + ("  ｜ " + detail if detail else ""))
    if not ok:
        fails += 1
print("=" * 60)
print(f"阻断项 {len(results)} 条 ｜ FAIL {fails}")
sys.exit(1 if fails else 0)
