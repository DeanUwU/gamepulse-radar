#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""cross_words.py — 读 wordcloud_terms.json → 渲染词云到 index.html #glance 区块，
并单独生成 wordcloud.html 作为热词分析补充页（含 TOP8 详解 + 趋势对比）。

2026-08-05 架构合并：全站统一为 index.html，词云不再作为独立页面存在；
wordcloud.html 降级为补充分析页（热词详解 + 趋势图），不重复展示词云主体。
"""
import io, os, re, sys, json, datetime
from collections import Counter

BASE = os.path.dirname(os.path.abspath(__file__))

TERMS_PATH = os.environ.get("WC_TERMS_PATH",
                            os.path.join(BASE, "wordcloud_terms.json"))
WC_PATH = os.environ.get("WC_OUT_PATH",
                         os.path.join(BASE, "wordcloud.html"))
INDEX_PATH = os.path.join(BASE, "index.html")

CAT_COLOR = {
    "游戏": "#b388ff", "新品": "#b388ff",
    "事件": "#ff6b4a", "风险": "#ff6b4a",
    "行业": "#4dabf7", "数据": "#4dabf7",
    "梗": "#3fd68f", "社区": "#3fd68f",
    "厂商": "#a0aec0", "平台": "#a0aec0",
}
CAT_HINT = {
    "游戏": "热门游戏/内容信号", "新品": "新游/版本发布信号",
    "事件": "行业重大事件", "风险": "潜在风险/争议",
    "行业": "行业数据/趋势", "数据": "行业数据/趋势",
    "梗": "社区梗/讨论热点", "社区": "社区梗/讨论热点",
    "厂商": "厂商/平台动态", "平台": "厂商/平台动态",
}

# ----------------------------------- 加载与校验 -----------------------------------
terms_data = json.load(io.open(TERMS_PATH, encoding="utf-8"))
cloud_date = terms_data.get("date", "")
if cloud_date:
    # 强制云词日期必须是当天；cross_words 不承担"理解"任务，只做渲染
    today = datetime.date.today().strftime("%Y-%m-%d")
    if cloud_date != today:
        sys.stderr.write(f"ERROR: wordcloud_terms.json date={cloud_date} != today={today}\n")
        sys.exit(1)
terms = terms_data.get("terms", [])
if not terms:
    sys.stderr.write("ERROR: wordcloud_terms.json 中没有词条。\n")
    sys.exit(1)

# 黑名单：搜索结果页 / Steam 商店商品页
_STORE_PRODUCT = re.compile(r"store\.steampowered\.com/(?:app|sub)/\d+(?!/news)")
_SEARCH_PAGE = re.compile(
    r"search\.bilibili\.com|s\.weibo\.com|/search[/?]|[?&](?:keyword|q|wd|query)=",
    re.I)

rows = []
for t in terms:
    term = (t.get("term") or "").strip()
    href = (t.get("href") or "").strip()
    if not term or not href:
        continue
    mods = t.get("sources") or ["当日采集"]
    if isinstance(mods, str):
        mods = [mods]
    if _SEARCH_PAGE.search(href):
        continue
    if _STORE_PRODUCT.search(href):
        continue
    cat = t.get("cat") or "梗/社区"
    color = CAT_COLOR.get(cat, "#3fd68f")
    try:
        heat = int(t.get("heat", 50))
    except Exception:
        heat = 50
    rows.append({"word": term, "href": href, "color": color, "cat": cat,
                 "heat": heat, "mods": mods})

if not rows:
    sys.stderr.write("ERROR: wordcloud_terms.json 中没有有效词条。\n")
    sys.exit(1)

# 字号（按 heat）
for r in rows:
    s = r["heat"]
    if s >= 70:   r["size"] = 22
    elif s >= 45: r["size"] = 19
    elif s >= 25: r["size"] = 16
    elif s >= 12: r["size"] = 14
    else:         r["size"] = 12
    r["cls"] = "h5" if r["size"] >= 19 else "h4" if r["size"] >= 15 else "h3" if r["size"] >= 13 else "h2"

# 统一热度数 H（0–100 全站唯一口径）
_max = max((r["heat"] for r in rows), default=1) or 1
for r in rows:
    r["H"] = round(100 * r["heat"] / _max)
    r["reason"] = (f"热度数 H={r['H']} ｜ 来源：{'+'.join(r['mods'])}"
                   f" ｜ 原始：{int(r['heat'])}")

# ===================== 生成 HTML 片段 =====================
wc_html = '<div class="wc">' + "".join(
    f'<a class="{r["cls"]}" href="{r["href"]}" target="_blank" title="{r["reason"]}" '
    f'style="font-size:{r["size"]}px;color:{r["color"]}">{r["word"]}</a>' for r in rows) + '</div>'

legend = ('<div class="wc-legend">'
          '<span><i style="background:#ff6b4a"></i>事件/风险</span>'
          '<span><i style="background:#b388ff"></i>游戏/新品</span>'
          '<span><i style="background:#4dabf7"></i>行业/数据</span>'
          '<span><i style="background:#3fd68f"></i>梗/社区</span>'
          '<span><i style="background:#a0aec0"></i>厂商/平台</span>'
          f'<span style="opacity:.7">共 {len(rows)} 条 · AI 阅读理解当日标题提炼 · 真实来源溯源</span></div>')

def build_top8(rs):
    out = ['<h3 style="margin-top:16px">TOP8 为什么热</h3><div class="wc-list">']
    for i, r in enumerate(rs[:8], 1):
        mods = "、".join(r["mods"]) if r["mods"] else "当日采集"
        why = (f'H={r["H"]}（全站第 {i} 位）· 命中来源：{mods}'
               f' · {CAT_HINT.get(r["cat"], "当日采集信号")}')
        out.append(f'<div class="row"><span class="w" style="color:{r["color"]}">'
                   f'{r["word"]}</span><span class="why">{why}</span></div>')
    out.append('</div>')
    return "".join(out)

top8_html = build_top8(rows)

# ===================== A. 注入 index.html 的 #glance 区块 =====================
index_html = io.open(INDEX_PATH, encoding="utf-8").read()

# 替换 #glance 内的 wc 词云
wc_tag = '<div class="wc">'
wc_pos = index_html.find(wc_tag, index_html.find('id="glance"'))
if wc_pos > 0:
    # 找到对应的 </div> 结束标签（匹配嵌套层级）
    depth = 0
    end_pos = wc_pos
    for _i in range(wc_pos, len(index_html)):
        if index_html[_i:_i+6] == '<div c' or index_html[_i:_i+5] == '<div>':
            depth += 1
        elif index_html[_i:_i+6] == '</div>':
            depth -= 1
            if depth == 0:
                end_pos = _i + 6
                break
    index_html = index_html[:wc_pos] + wc_html + index_html[end_pos:]

# 更新 #glance 中的日期
index_html = re.sub(
    r'(id="glance">.*?<small>)\d{4}-\d{2}-\d{2}( [·，].*?)</small>',
    lambda m: m.group(1) + cloud_date + m.group(2) + '</small>',
    index_html, flags=re.DOTALL)

# 写临时文件再替换
_tmp = INDEX_PATH + "." + str(int(datetime.datetime.now().timestamp()))
io.open(_tmp, "w", encoding="utf-8").write(index_html)
try:
    os.replace(_tmp, INDEX_PATH)
except OSError:
    try:
        os.remove(INDEX_PATH)
        os.rename(_tmp, INDEX_PATH)
    except OSError:
        print('WARN: cannot overwrite', INDEX_PATH, '(locked), content in', _tmp)

# ===================== B. 生成 wordcloud.html 补充页 =====================
wc_page = io.open(WC_PATH, encoding="utf-8").read()
wc_page = re.sub(r'<div class="wc">.*?</div>\s*<div class="wc-legend">.*?</div>',
                 wc_html + legend, wc_page, flags=re.DOTALL)
wc_page = re.sub(r'<h3[^>]*>TOP8 为什么热</h3>\s*<div class="wc-list">.*?</div>\s*</div>',
                 top8_html, wc_page, flags=re.DOTALL)
wc_page = re.sub(r'讨论热词 <small>.*?</small>',
                 f'讨论热词 <small>{cloud_date} · {len(rows)} 词 · 热度数 H(0-100) 统一口径 · AI 阅读理解当日标题提炼</small>',
                 wc_page)
wc_page = re.sub(r'<span class="chip fresh">\d{4}-\d{2}-\d{2}</span>',
                 f'<span class="chip fresh">{cloud_date}</span>', wc_page)
wc_page = re.sub(r'<p class="note">热词来源：.*?</p>',
                 '<p class="note">热词来源：由 AI 直接阅读当日真实采集的标题'
                 '（B站热门 / 每周必看 / 梗百科账号 / 行业媒体稿 / 贴吧玩家讨论），'
                 '理解语义后提炼出游戏行业热词，每条都溯源到它实际出现过的真实来源链接'
                 '（B站视频 / 媒体文章 / 官方公�� / 社区话题）。'
                 '<b>绝不生成 search.bilibili.com 搜索链接、绝不跳 Steam 等商店购买页</b>；'
                 '无真实来源链接的词不会进入词云。'
                 '<b>热度数 H（0–100 全站统一口径）</b>：每条热词展示 H，原始评分仅作附注，'
                 '禁止用来源字面热度值充当热度本身（自洽红线①）。</p>',
                 wc_page, flags=re.DOTALL)

_wc_tmp = WC_PATH + "." + str(int(datetime.datetime.now().timestamp()))
io.open(_wc_tmp, "w", encoding="utf-8").write(wc_page)
try:
    os.replace(_wc_tmp, WC_PATH)
except OSError:
    try:
        os.remove(WC_PATH)
        os.rename(_wc_tmp, WC_PATH)
    except OSError:
        print('WARN: cannot overwrite', WC_PATH, '(locked), content in', _wc_tmp)

print(f"热词数: {len(rows)}")
print("分类:", dict(Counter(r["cat"] for r in rows)))
print("Top10:")
for r in rows[:10]:
    print(f"  H={r['H']:>3} [{r['cat']}] {r['word']}")
print(f"\n词云已注入 index.html #glance + wordcloud.html 补充页")
