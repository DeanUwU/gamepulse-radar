# -*- coding: utf-8 -*-
"""_gate_check.py — 发布前阻断项门禁校验（每日刷新专用）

用法：python _gate_check.py
退出码 0 = 全部 PASS 可发布；1 = 有 FAIL 必须修复。

已知误报陷阱（勿重犯）：
  ① search.bilibili 只统计真实链接属性（href=/src=）里出现的，
     wordcloud.html 脚注写着「绝不生成 search.bilibili.com 搜索链接」是说明文案，非链接。
  ② masthead 检查要匹配 class="masthead"，不是字面 ".masthead"。
"""
import json, re, sys, datetime, os

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

# ⑦b masthead 头条时效（阻断）：头条必须是「当日/近 3 天新闻」或「今天发售/上线」，
#   不得用旧闻当"今日头条"。2026-08-24 治理：events[] 日历定档节点无 pubdate，
#   曾把 8-13 发布的旧闻《月光光心慌慌》(9-08 发售)顶上头条。规则：
#   · B站视频链接 → 通过（B站候选池已有时效闸 ≤MAX_AGE_DAYS）
#   · feed_events 命中 → pubdate 须在 3 天窗口内
#   · events[] 命中 → 有 pubdate 且 ≤3 天，或无 pubdate 但 date_start==今天
#   · 均不命中 → FAIL（头条来源不在已知数据，无法判定时效）
mh_m = re.search(r'class="masthead[^"]*".*?<h1>\s*<a[^>]+href="([^"]+)"', idx, re.S)
mh_url = mh_m.group(1) if mh_m else None
if not mh_url:
    chk("masthead 头条链接可提取", False, "未找到 masthead <h1> 链接")
else:
    chk("masthead 头条链接可提取", True, mh_url)
    lo3 = (datetime.date.today() - datetime.timedelta(days=3)).strftime("%Y-%m-%d")
    ok_mh = False
    mh_detail = ""
    if "bilibili.com/video/" in mh_url:
        ok_mh = True
        mh_detail = "B站视频(候选池已有时效闸)"
    else:
        fe_by_url = {e.get("source_url"): e for e in ev.get("feed_events", [])}
        ev_by_url = {e.get("source_url"): e for e in ev.get("events", [])}
        if mh_url in fe_by_url:
            pd = fe_by_url[mh_url].get("pubdate")
            if pd and lo3 <= pd <= TODAY:
                ok_mh = True
                mh_detail = f"feed_events pubdate={pd}"
            else:
                mh_detail = f"feed_events pubdate={pd} 越界/缺失"
        elif mh_url in ev_by_url:
            ce = ev_by_url[mh_url]
            pd = ce.get("pubdate")
            ds = ce.get("date_start")
            if pd and lo3 <= pd <= TODAY:
                ok_mh = True
                mh_detail = f"events pubdate={pd}"
            elif (not pd) and ds == TODAY:
                ok_mh = True
                mh_detail = f"events 今日发售 date_start={ds}"
            else:
                mh_detail = f"events 时效未知/预告 pubdate={pd} date_start={ds}"
        else:
            mh_detail = "头条链接不在 feed_events/events 已知源内"
    chk("masthead 头条时效 ≤3 天（非旧闻）", ok_mh, mh_detail)

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
chk("wordcloud_terms 条数 28-32", 28 <= len(terms) <= 32, f"{len(terms)} 条")
chk("wordcloud_terms 零违规链接", not wbad, str(wbad[:5]) if wbad else "0")

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
