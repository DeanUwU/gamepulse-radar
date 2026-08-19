#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""cross_words.py — 读 wordcloud_terms.json → 渲染词云到 index.html #trend 区块，
并单独生成 wordcloud.html 作为热词分析补充页（wordcloud2.js 紧致排版 + 词条溯源侧边栏）。

2026-08-10 改版：golden-angle spiral → wordcloud2.js canvas 紧致矩形排版
  + 点击词条 → 侧边栏显示该词当日所有原始标题+URL（溯源功能）
2026-08-05 架构合并：全站统一为 index.html，词云不再作为独立页面存在；
wordcloud.html 降级为补充分析页（热词详解 + 趋势图），不重复展示词云主体。
"""
import io, os, re, sys, json, datetime, math, random as _rnd
from collections import Counter

BASE = os.path.dirname(os.path.abspath(__file__))

TERMS_PATH = os.environ.get("WC_TERMS_PATH",
                            os.path.join(BASE, "wordcloud_terms.json"))
WC_PATH = os.environ.get("WC_OUT_PATH",
                         os.path.join(BASE, "wordcloud.html"))
INDEX_PATH = os.environ.get("CW_INDEX",
                            os.path.join(BASE, "index.html"))

CAT_COLOR = {
    "游戏/新品": "#b388ff",
    "游戏": "#b388ff", "新品": "#b388ff",
    "事件/风险": "#ff6b4a",
    "事件": "#ff6b4a", "风险": "#ff6b4a",
    "行业/数据": "#4dabf7",
    "行业": "#4dabf7", "数据": "#4dabf7",
    "梗/社区": "#3fd68f",
    "梗": "#3fd68f", "社区": "#3fd68f",
    "厂商/平台": "#a0aec0",
    "厂商": "#a0aec0", "平台": "#a0aec0",
}
CAT_COLOR_VARIANTS = {
    "b388ff": ["#b388ff", "#c792ea", "#9b6dff", "#d4a0ff", "#8b5cf6"],
    "ff6b4a": ["#ff6b4a", "#ff7b6b", "#ff5c39", "#e85d50", "#ff8a65"],
    "4dabf7": ["#4dabf7", "#64b5f6", "#42a5f5", "#5c9ce6", "#3b8dd9"],
    "3fd68f": ["#3fd68f", "#52d99f", "#34c97e", "#66d9a8", "#2eaf6e"],
    "a0aec0": ["#a0aec0", "#b0bcc8", "#8e9db0", "#c0c8d4", "#7c8ea0"],
}
CAT_HINT = {
    "游戏": "热门游戏/内容信号", "新品": "新游/版本发布信号",
    "事件": "行业重大事件", "风险": "潜在风险/争议",
    "行业": "行业数据/趋势", "数据": "行业数据/趋势",
    "梗": "社区梗/讨论热点", "社区": "社区梗/讨论热点",
    "厂商": "厂商/平台动态", "平台": "厂商/平台动态",
}

PLATFORM_LABEL = {
    "weibo": "微博热榜", "zhihu": "知乎热榜", "douyin": "抖音热榜",
    "bilibili": "B站热搜", "xiaohongshu": "小红书热榜",
    "popular": "B站·热门", "weekly": "B站·每周必看",
    "meme_ups": "B站·梗指南", "series": "B站·梗外之音",
    "tieba": "贴吧热议",
}


# ----------------------------------- 加载与校验 -----------------------------------
terms_data = json.load(io.open(TERMS_PATH, encoding="utf-8"))
cloud_date = terms_data.get("date", "")
if cloud_date:
    today = datetime.date.today().strftime("%Y-%m-%d")
    if cloud_date != today:
        sys.stderr.write(f"ERROR: wordcloud_terms.json date={cloud_date} != today={today}\n")
        sys.exit(1)
terms = terms_data.get("terms", [])
if not terms:
    sys.stderr.write("ERROR: wordcloud_terms.json 中没有词条。\n")
    sys.exit(1)


# ---- 词条时效过滤（2026-08-18 新增）：剔除内嵌「X月X日」已过期的历史词条 ----
# 词云必须反映「当前讨论热词」，不能混入已经过了 3 天的旧事件词
# （如「三角洲8月13日更新」「永劫无间汉堡王联动8月14开启」在 8/18 已属旧闻）。
# 规则：从 term 里抽取「N月N日」；与今天比较，若该日期距今 > STALE_DAYS 天则剔除。
# 阈值与全站时效红线 MAX_AGE_DAYS=3 一致：今日热词只保留今天/昨天/未来预告的事件词。
STALE_DAYS = 3
_DATE_WORD = re.compile(r'(\d{1,2})\s*月\s*(\d{1,2})\s*日')
_y = datetime.date.today().year
_now_date = datetime.date.today()

def _term_is_stale(term):
    """词条内嵌日期是否已过期（>STALE_DAYS 天）。无日期字则视为新鲜，不剔除。"""
    for m in _DATE_WORD.finditer(term):
        mo, dd = int(m.group(1)), int(m.group(2))
        try:
            d = datetime.date(_y, mo, dd)
        except ValueError:
            continue
        # 距今天数（可能为负=未来日期，视为新鲜事件预告，不剔除）
        age = (_now_date - d).days
        if age > STALE_DAYS:
            return True
    return False

_filtered_terms = []
_stale_terms = []
for t in terms:
    term = (t.get("term") or "").strip()
    if _term_is_stale(term):
        _stale_terms.append(term)
        continue
    _filtered_terms.append(t)
if _stale_terms:
    print(f"时效过滤：剔除 {len(_stale_terms)} 条过期词条 -> {_stale_terms}")
terms = _filtered_terms
if not terms:
    sys.stderr.write("ERROR: 时效过滤后无有效词条。\n")
    sys.exit(1)

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
        # 带关键词的搜索链接仍可溯源（非主页占位），保留但降权
        mods.append("搜索溯源")
        heat = max(8, int(int(t.get("heat", 50)) * 0.8))
        t["heat"] = heat
    if _STORE_PRODUCT.search(href):
        continue
    cat = t.get("cat") or "梗/社区"
    base_color = CAT_COLOR.get(cat, "#3fd68f")
    variants = CAT_COLOR_VARIANTS.get(base_color, [base_color])
    color = variants[hash(term) % len(variants)]
    try:
        heat = int(t.get("heat", 50))
    except Exception:
        heat = 50
    rows.append({"word": term, "href": href, "color": color, "cat": cat,
                 "heat": heat, "mods": mods})

if not rows:
    sys.stderr.write("ERROR: wordcloud_terms.json 中没有有效词条。\n")
    sys.exit(1)

for r in rows:
    s = r["heat"]
    if s >= 70:   r["size"] = 22
    elif s >= 45: r["size"] = 19
    elif s >= 25: r["size"] = 16
    elif s >= 12: r["size"] = 14
    else:         r["size"] = 12
    r["cls"] = "h5" if r["size"] >= 19 else "h4" if r["size"] >= 15 else "h3" if r["size"] >= 13 else "h2"

_max = max((r["heat"] for r in rows), default=1) or 1
for r in rows:
    r["H"] = round(100 * r["heat"] / _max)
    r["reason"] = (f"热度数 H={r['H']} ｜ 来源：{'+'.join(r['mods'])}"
                   f" ｜ 原始：{int(r['heat'])}")

sorted_rows = sorted(rows, key=lambda r: -r["heat"])
total = len(sorted_rows)


# ===================== 溯源数据构建 =====================

def _collect_all_source_items(date_str):
    """收集当日所有可溯源条目（meme + hotlist），返回 [{title, url, source}]"""
    items = []
    date_compact = date_str.replace("-", "")  # 2026-08-10 → 20260810

    # 1. meme 数据
    meme_path = os.path.join(BASE, "collectors", f"meme_{date_compact}.json")
    if os.path.exists(meme_path):
        try:
            md = json.load(io.open(meme_path, encoding="utf-8"))
            for section in ["popular", "weekly", "meme_ups", "series"]:
                for item in md.get(section, []) or []:
                    title = item.get("title") or item.get("latest_title") or ""
                    url = item.get("url") or ""
                    if title and url:
                        items.append({"title": title, "url": url,
                                      "source": PLATFORM_LABEL.get(section, f"B站·{section}")})
            for item in md.get("tieba", []) or []:
                name = item.get("name", "")
                url = item.get("url", "")
                if name and url:
                    items.append({"title": name, "url": url,
                                  "source": PLATFORM_LABEL.get("tieba", "贴吧热议")})
        except Exception as e:
            print(f"WARN: 读 meme 数据失败 ({meme_path}): {e}")

    # 2. hotlist 数据
    hotlist_path = os.path.join(BASE, "collectors", f"public_hotlist_{date_compact}.json")
    if os.path.exists(hotlist_path):
        try:
            hd = json.load(io.open(hotlist_path, encoding="utf-8"))
            for plat, items_list in hd.items():
                if plat == "timestamp":
                    continue
                for item in (items_list or []):
                    topic = item.get("topic", "")
                    url = item.get("url", "")
                    if topic and url:
                        items.append({"title": topic, "url": url,
                                      "source": PLATFORM_LABEL.get(plat, plat)})
        except Exception as e:
            print(f"WARN: 读 hotlist 数据失败 ({hotlist_path}): {e}")

    return items


def build_traceability(rows_list, date_str):
    """对每个词条，在所有源数据中搜索包含该词条的原始标题→构建溯源映射。"""
    all_items = _collect_all_source_items(date_str)

    trace = {}
    for r in rows_list:
        word = r["word"]
        matches = []
        seen = set()
        for src in all_items:
            url = src["url"]
            if url in seen:
                continue
            if word in src["title"]:
                matches.append(dict(src))
                seen.add(url)

        # 把主源链排放到第一位
        primary_url = r["href"]
        for i, m in enumerate(matches):
            if m["url"] == primary_url:
                matches.insert(0, matches.pop(i))
                break

        trace[word] = {
            "H": r["H"],
            "color": r["color"],
            "cat": r["cat"],
            "primary_url": r["href"],
            "sources": matches
        }

    return trace


def build_wordcloud2_list(rows_list):
    """构建 wordcloud2.js 兼容的词列表: [[word, weight, color, category, H], ...]"""
    wl = []
    for r in rows_list:
        # weight 取原始 heat 值 (0-100)，wordcloud2.js 用此决定字号
        wl.append([r["word"], int(r["heat"]), r["color"], r["cat"], r["H"]])
    return wl


trace_data = build_traceability(sorted_rows, cloud_date)
wc2_list = build_wordcloud2_list(sorted_rows)

# 溯源数据 JSON（不包含超大词列表，仅溯源映射）
trace_json = json.dumps(trace_data, ensure_ascii=False)
# 词列表 JSON（wordcloud2.js 用）
wc2_json = json.dumps(wc2_list, ensure_ascii=False)

# ---- 统计 ----
trace_count = sum(1 for v in trace_data.values() if v["sources"])
total_source_hits = sum(len(v["sources"]) for v in trace_data.values())
print(f"溯源统计: {trace_count}/{len(sorted_rows)} 词有原始标题匹配, 共 {total_source_hits} 条溯源链接")


# ===================== 球状词云 HTML（index.html mini 版保留 golden-angle spiral） =====================

GOLDEN_ANGLE = math.radians(137.50776405)
MAX_R = 230
K = MAX_R / math.sqrt(total + 1)


def _spiral_tag(r, idx):
    angle = idx * GOLDEN_ANGLE
    radius = K * math.sqrt(idx + 1)
    x = math.cos(angle) * radius
    y = math.sin(angle) * radius
    if idx < 5:
        fs = r["size"] + 6; z_idx = 32 - idx; opacity = 1.0
    elif idx < 14:
        fs = r["size"] + 2; z_idx = 26 - idx; opacity = 0.92
    else:
        fs = max(11, r["size"] - 2); z_idx = 10 - idx % 10; opacity = 0.78
    return (
        f'<a class="wc-sphere {r["cls"]}" href="{r["href"]}" target="_blank" '
        f'title="{r["reason"]}" '
        f'style="font-size:{fs}px;color:{r["color"]};opacity:{opacity:.2f};'
        f'transform:translate({x:.0f}px,{y:.0f}px);z-index:{z_idx}"'
        f'>{r["word"]}</a>'
    )

# ---- 2026-08-10 重做：放弃 spiral（容器内只占中央 460px 一坨），主站改用 wordcloud2.js canvas ----
# 优势：自动填满整个 canvas、碰撞检测、字号落差更大、视觉密度接近 demo 图。
# 词列表 wc2_list 已在前面 build_wordcloud2_list() 生成。

WC2_CDN = "https://cdn.jsdelivr.net/npm/wordcloud@1.2.2/src/wordcloud2.min.js"


def build_main_wordcloud():
    """生成 index.html #glance 用的 wordcloud2.js canvas + 内联数据 + 渲染脚本。
    跟 wordcloud.html 共用同一引擎；点击词条在主站内展开详情面板（不再跳转外站/开新页）。"""
    # canvas 像素尺寸（CSS 会再 fit 到容器宽度）
    CW, CH = 880, 280
    payload = {
        "list": [[r["word"], int(r["heat"]), r["color"], r["cat"], r["H"]]
                 for r in sorted_rows],
        "trace": trace_data,  # 词条溯源映射（word → {H, color, cat, primary_url, sources}）
        "total": len(sorted_rows),
    }
    payload_json = json.dumps(payload, ensure_ascii=False)

    return f'''<div class="wc-cloud" id="wc-cloud">
  <canvas id="wc-canvas" width="{CW}" height="{CH}"></canvas>
  <div class="wc-cloud-tip" id="wc-cloud-tip" hidden></div>
</div>
<div class="wc-detail" id="wc-detail">
  <div class="wc-detail-head">
    <span class="wc-detail-word" id="wc-detail-word"></span>
    <span class="wc-detail-badge" id="wc-detail-badge"></span>
    <button class="wc-detail-close" id="wc-detail-close" type="button" aria-label="关闭">&times;</button>
  </div>
  <div class="wc-detail-body" id="wc-detail-body"></div>
</div>
<script id="wc-cloud-data" type="application/json">{payload_json}</script>
<script>
(function() {{
  var canvas = document.getElementById('wc-canvas');
  if (!canvas || typeof WordCloud === 'undefined') return;
  var payload = JSON.parse(document.getElementById('wc-cloud-data').textContent);

  // ---- 响应式：根据容器宽度调整 canvas 像素尺寸（保持 880x280 比例） ----
  function fitCanvas() {{
    var wrap = canvas.parentElement;
    var targetW = Math.min(880, wrap.clientWidth);
    var ratio = targetW / 880;
    canvas.style.width = targetW + 'px';
    canvas.style.height = (280 * ratio) + 'px';
  }}
  fitCanvas();
  window.addEventListener('resize', fitCanvas);

  // 记录当前 hover 的词（作为 click 的 fallback，应对 wordcloud2.js 在 zoom/缩放下 click 坐标映射偏差）
  var lastHoverWord = null;

  // ---- 词列表转 [word, weight] ----
  var wordList = payload.list.map(function(x) {{ return [x[0], x[1]]; }});
  var colorMap = {{}}, catMap = {{}}, hMap = {{}};
  payload.list.forEach(function(x) {{
    colorMap[x[0]] = x[2];
    catMap[x[0]] = x[3];
    hMap[x[0]] = x[4];
  }});

  // ---- 主站配色：暗色背景下压低饱和度，避免太花哨 ----
  var muted = {{
    '游戏/新品':   ['#9b6dff', '#a78bfa', '#8b5cf6'],
    '事件/风险':   ['#ff5c39', '#ff7b6b', '#e85d50'],
    '行业/数据':   ['#4dabf7', '#5cb0f8', '#42a5f5'],
    '梗/社区':     ['#3fd68f', '#52d99f', '#34c97e'],
    '厂商/平台':   ['#a0aec0', '#b0bcc8', '#8e9db0']
  }};
  function pickColor(word, weight) {{
    var cat = catMap[word] || '梗/社区';
    var palette = muted[cat] || muted['梗/社区'];
    return palette[Math.abs(hashCode(word)) % palette.length];
  }}
  function hashCode(s) {{
    var h = 0; for (var i = 0; i < s.length; i++) h = (h << 5) - h + s.charCodeAt(i) | 0;
    return h;
  }}

  WordCloud(canvas, {{
    list: wordList,
    gridSize: 6,
    weightFactor: function(w) {{ return Math.pow(w, 0.62) * 2.6; }},
    fontFamily: '"PingFang SC","Microsoft YaHei","Noto Sans SC",sans-serif',
    color: pickColor,
    backgroundColor: 'transparent',
    rotateRatio: 0,            /* 2026-08-13 修复：原 0.18 导致部分词旋转 ±90°（"尚未开悬念Top12"等竖排），全员水平排版 */
    rotationSteps: 1,
    shape: 'square',
    ellipticity: 0.85,
    minSize: 10,
    shrinkToFit: true,
    drawOutOfBound: false,
    weightMode: 'size',
    wait: 10,
    abortThreshold: 0,
    hover: function(item, dim, evt) {{
      var tip = document.getElementById('wc-cloud-tip');
      if (!item) {{ tip.hidden = true; canvas.style.cursor='default'; lastHoverWord = null; return; }}
      canvas.style.cursor = 'pointer';
      var w = item[0];
      lastHoverWord = w;
      tip.innerHTML = '<b>' + w + '</b><span>H=' + (hMap[w]||'--') + ' · ' + (catMap[w]||'梗/社区') + '</span>';
      tip.hidden = false;
      var wrap = document.getElementById('wc-cloud');
      var r = wrap.getBoundingClientRect();
      tip.style.left = (evt.clientX - r.left + 12) + 'px';
      tip.style.top  = (evt.clientY - r.top  - 44) + 'px';
    }},
    click: function(item) {{
      // wordcloud2.js 内部 click：item 命中则直接用；为 null 时用 hover fallback 兜底
      var word = item && item[0] ? item[0] : lastHoverWord;
      if (!word) return;
      showTrace(word);
    }}
  }});

  // Fallback：wordcloud2.js 在 CSS zoom/缩放下 click 坐标映射可能偏移导致 item=null，
  // 直接在 canvas 上绑原生 click 作为二次保险（优先用 wordcloud2 内部 click，这里仅兜底）。
  canvas.addEventListener('click', function() {{
    if (lastHoverWord) showTrace(lastHoverWord);
  }});

  canvas.addEventListener('mouseleave', function() {{
    var tip = document.getElementById('wc-cloud-tip');
    if (tip) tip.hidden = true;
    canvas.style.cursor = 'default';
  }});

  // ---- 站内详情面板：点击词条直接展开溯源，不跳转外站/不开新页 ----
  function showTrace(word) {{
    var panel = document.getElementById('wc-detail');
    var wordEl = document.getElementById('wc-detail-word');
    var badgeEl = document.getElementById('wc-detail-badge');
    var bodyEl = document.getElementById('wc-detail-body');
    if (!panel || !wordEl || !badgeEl || !bodyEl) return;

    var info = payload.trace[word];
    wordEl.textContent = word;

    // 分类徽章（H 值 + 分类）
    badgeEl.textContent = 'H=' + (info ? info.H : (hMap[word] || '--')) + ' · ' + (catMap[word] || '梗/社区');
    var c = info ? info.color : (colorMap[word] || '#a0aec0');
    badgeEl.style.color = c;
    badgeEl.style.borderColor = c;
    badgeEl.style.background = c + '18';

    var items = [];
    // 主源链（primary_url）永远排第一，即使没有溯源列表也要给个可点的主链接
    if (info && info.primary_url) {{
      items.push('<a class="wc-detail-item" href="' + info.primary_url + '" target="_blank" rel="noopener">'
               + '<span class="di-title">' + word + '</span>'
               + '<span class="di-meta"><span class="di-primary">主源</span>'
               + '<span class="di-source">策展输入</span></span></a>');
    }}
    var srcs = info && info.sources ? info.sources : [];
    for (var i = 0; i < srcs.length; i++) {{
      var src = srcs[i];
      if (info.primary_url && src.url === info.primary_url) continue; // 已作为主源显示，避免重复
      items.push('<a class="wc-detail-item" href="' + src.url + '" target="_blank" rel="noopener">'
               + '<span class="di-title">' + src.title + '</span>'
               + '<span class="di-meta"><span class="di-source">' + src.source + '</span></span></a>');
    }}
    if (items.length === 0) {{
      bodyEl.innerHTML = '<p class="wc-detail-empty">该词当日未匹配到可溯源标题<br><small>溯源基于 meme / hotlist 原始采集交叉比对</small></p>';
    }} else {{
      bodyEl.innerHTML = items.join('');
      if (srcs.length === 0) {{
        bodyEl.innerHTML += '<p class="wc-detail-empty" style="padding-top:var(--space-md)">暂无更多交叉溯源，以上为主策展链接</p>';
      }}
    }}

    panel.classList.add('open');
  }}

  document.getElementById('wc-detail-close').addEventListener('click', function() {{
    document.getElementById('wc-detail').classList.remove('open');
  }});

  // 点击面板外部空白处关闭（仅当点击目标不在面板内）
  // 注意：canvas 点击会冒泡到这里，但词云点击是"打开"不是"关闭"，
  // 因此排除 canvas 及其子元素的点击，否则会把刚打开的面板误关。
  document.addEventListener('click', function(e) {{
    var panel = document.getElementById('wc-detail');
    if (!panel.classList.contains('open')) return;
    if (panel.contains(e.target)) return;
    if (e.target && (e.target.id === 'wc-canvas' || e.target.id === 'wc-cloud-tip' || (e.target.parentElement && e.target.parentElement.id === 'wc-cloud'))) return;
    panel.classList.remove('open');
  }});
}})();
</script>'''


main_wc_html = build_main_wordcloud()

# ---- 保留的迷你预览（所有词条 chip 列表 + H 值，作为 canvas 下方的兜底可读视图） ----
preview_items = []
for r in sorted_rows:
    preview_items.append(
        f'<a class="wc-preview-item" href="{r["href"]}" target="_blank" '
        f'title="{r["reason"]}" style="color:{r["color"]}">'
        f'<span class="wc-pv-word">{r["word"]}</span>'
        f'<span class="wc-pv-h">H{r["H"]}</span></a>'
    )
preview_html = '<div class="wc-preview">' + "".join(preview_items) + '</div>'

legend = ('<div class="wc-legend">'
          '<span><i style="background:#ff5c39"></i>事件/风险</span>'
          '<span><i style="background:#9b6dff"></i>游戏/新品</span>'
          '<span><i style="background:#4dabf7"></i>行业/数据</span>'
          '<span><i style="background:#3fd68f"></i>梗/社区</span>'
          '<span><i style="background:#a0aec0"></i>厂商/平台</span>'
          f'<span style="opacity:.7">共 {len(rows)} 条 · wordcloud2.js 实时渲染 · 字号=H(0-100) · 下方全量预览</span></div>')


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


# ===================== wordcloud2.js 词云页 HTML 生成 =====================

def _esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def _load_masthead_from_index():
    """从 index.html 读取当前 masthead（<!--mh-->...<!--/mh-->），供 wordcloud.html 复用。
    2026-08-18：让词云页顶部与主站共用同一张今日头条头图，避免「头图没图」。
    若 index.html 尚无 masthead（首次/异常），返回空串，页面退回纯 hero 标题。
    """
    try:
        src = io.open(INDEX_PATH, encoding="utf-8").read()
    except Exception:
        return ""
    m = re.search(r'(<!--mh-->)(.*?)(<!--/mh-->)', src, re.S)
    if not m:
        return ""
    return m.group(0)


def generate_wordcloud_page():
    """生成 wordcloud.html：wordcloud2.js canvas 紧致排版 + 溯源侧边栏 + TOP8"""

    # ---- wordcloud2.js CDN（从 jsdelivr 加载） ----
    # 注：wordcloud2.js 是 timdream/wordcloud2.js 的标准 CDN 路径
    wc2_cdn = "https://cdn.jsdelivr.net/npm/wordcloud@1.2.2/src/wordcloud2.min.js"

    # ---- 复用主站今日头条 masthead（2026-08-18：词云页顶部不再空块/无图） ----
    masthead_html = _load_masthead_from_index()

    page = f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>GamePulse · 热词词云</title>
<link rel="icon" type="image/png" href="favicon.png">
<link rel="stylesheet" href="style.css">
<script src="{wc2_cdn}"></script>
<style>
/* ---- wordcloud2.js 词云页专属样式 ---- */
.wc-main{{display:flex;gap:var(--space-lg);align-items:flex-start;margin:var(--space-md) 0}}
.wc-canvas-wrap{{flex:1;min-width:0;background:var(--panel);border:1px solid var(--border);
  border-radius:var(--radius-lg);padding:var(--space-sm);text-align:center}}
#wc-canvas{{display:block;margin:0 auto;max-width:100%;height:auto}}
.wc-canvas-hint{{font-size:var(--text-xs);color:var(--dim);margin-top:var(--space-xs)}}

/* ---- 溯源侧边栏 ---- */
.wc-trace{{width:320px;flex:none;background:var(--panel);border:1px solid var(--border);
  border-radius:var(--radius-lg);overflow:hidden;position:sticky;top:70px;
  max-height:calc(100vh - 90px);display:flex;flex-direction:column;opacity:.65;
  transition:opacity .25s var(--ease-out-quart),border-color .25s var(--ease-out-quart)}}
.wc-trace.active{{opacity:1;border-color:var(--gold)}}
.wc-trace-header{{padding:var(--space-sm) var(--space-md);border-bottom:1px solid var(--border);
  font-size:var(--text-sm);color:var(--sub);display:flex;align-items:center;gap:var(--space-xs);flex-wrap:wrap}}
.wc-trace-word{{color:var(--txt);font-weight:800;font-size:var(--text-lg)}}
.wc-trace-badge{{font-size:var(--text-xs);border-radius:99px;padding:1px 8px;font-weight:600;
  border:1px solid;white-space:nowrap}}
.wc-trace-list{{flex:1;overflow-y:auto;padding:var(--space-xs) 0}}
.wc-trace-empty{{padding:var(--space-2xl) var(--space-md);text-align:center;color:var(--dim);
  font-size:var(--text-sm);line-height:1.7}}
.wc-trace-item{{display:block;padding:var(--space-sm) var(--space-md);border-bottom:1px solid var(--border);
  text-decoration:none;transition:background .15s;font-size:var(--text-sm);line-height:1.45}}
.wc-trace-item:last-child{{border-bottom:none}}
.wc-trace-item:hover{{background:var(--panel2);text-decoration:none}}
.wc-trace-item .ts-title{{color:var(--txt);display:block;margin-bottom:2px;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}}
.wc-trace-item .ts-meta{{font-size:var(--text-xs);color:var(--dim);display:flex;gap:var(--space-xs);align-items:center}}
.wc-trace-item .ts-source{{color:var(--sub);border:1px solid var(--border);
  border-radius:3px;padding:0 5px;white-space:nowrap}}
.wc-trace-item .ts-primary{{color:var(--gold);font-size:10px;font-weight:700;text-transform:uppercase}}
.wc-trace-footer{{padding:var(--space-xs) var(--space-md);border-top:1px solid var(--border);
  font-size:var(--text-xs);color:var(--dim)}}

/* ---- wordcloud2.js canvas 区 tooltip ---- */
.wc-tooltip{{position:absolute;background:rgba(22,27,34,.96);border:1px solid var(--border);
  border-radius:var(--radius-md);padding:var(--space-sm) var(--space-md);pointer-events:none;
  font-size:var(--text-sm);color:var(--txt);z-index:99;white-space:nowrap;
  box-shadow:var(--shadow-md);transition:opacity .15s}}
.wc-tooltip b{{display:block;font-size:var(--text-md)}}
.wc-tooltip span{{color:var(--sub);font-size:var(--text-xs)}}

/* 移动端适配 */
@media(max-width:900px){{.wc-main{{flex-direction:column}}.wc-trace{{width:100%;max-height:400px;position:static}}}}

/* 保留旧版 legend / preview 样式兼容 */
.wc-legend{{margin-top:var(--space-sm);font-size:11px;color:var(--sub)}}
.wc-legend span{{display:inline-block;margin-right:14px}}
.wc-legend i{{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px;vertical-align:middle}}
.wc-preview{{display:flex;flex-wrap:wrap;gap:4px 12px;padding:var(--space-sm) 0;border-top:1px solid var(--border);max-height:160px;overflow-y:auto}}
.wc-preview-item{{display:inline-flex;align-items:center;gap:3px;font-size:var(--text-xs);text-decoration:none;padding:2px 6px;border-radius:3px;transition:background .15s}}
.wc-preview-item:hover{{background:var(--panel2);text-decoration:none}}
.wc-pv-word{{font-weight:700}}
.wc-pv-h{{font-size:9px;opacity:.65;font-weight:600;font-variant-numeric:tabular-nums}}
.wc-list{{margin-top:12px;border-top:1px solid var(--border);padding-top:8px}}
.wc-list .row{{display:flex;gap:8px;padding:5px 0;font-size:12px;border-bottom:1px dashed var(--border)}}
.wc-list .row:last-child{{border-bottom:none}}
.wc-list .w{{flex:none;width:118px;font-weight:700}}
.wc-list .why{{color:var(--sub)}}
</style>
</head>
<body>
<header><div class="hbar"><div class="logo">&#127918; GamePulse<em>·雷达</em></div><span class="chip">&#9729; 词云</span><span class="chip fresh">{cloud_date}</span><nav><a href="index.html" target="_top">&#8962; 主站</a><a href="history.html">历史回顾</a></nav></div></header>
{masthead_html}
<div class="wrap">
<div class="hero"><h1>讨论热词词云</h1><p>玩家社区真实讨论热词（B站热搜 / 贴吧 / 梗雷达六路）+ 网站已收录参考。已过滤媒体·公众号名、宽泛行业词、非游戏内容；分类仅四色：事件炸点 / 行业议题 / 版本新品 / 梗社区（无舆情风险）。<b>字号=热度 H(0-100)</b>，<b>点击词条</b>可在右侧面板查看该词当日所有原始标题与链接（词条溯源）。</p></div>
<section id="stat">
<div class="sec-title"><span class="bar"></span>今日游戏圈讨论热词 <small>{cloud_date} · {len(rows)} 词 · 热度数 H(0-100) 统一口径 · AI 阅读理解当日标题提炼</small></div>
<div class="panel">
<p class="note">热词来源：由 AI 直接阅读当日真实采集的标题（B站热门 / 每周必看 / 梗百科账号 / 行业媒体稿 / 贴吧玩家讨论），理解语义后提炼出游戏行业热词，每条都溯源到它实际出现过的真实来源链接（B站视频 / 媒体文章 / 官方公告 / 社区话题）。<b>绝不生成 search.bilibili.com 搜索链接、绝不跳 Steam 等商店购买页</b>；无真实来源链接的词不会进入词云。<b>热度数 H（0–100 全站统一口径）</b>：每条热词展示 H，原始评分仅作附注，禁止用来源字面热度值充当热度本身（自洽红线①）。</p>

<!-- wordcloud2.js 词云主区域 -->
<div class="wc-main">
  <div class="wc-canvas-wrap" id="wc-canvas-wrap">
    <canvas id="wc-canvas" width="800" height="520"></canvas>
    <div class="wc-canvas-hint">词云由 wordcloud2.js 实时渲染 · 字号 = H 值 (0-100) 映射 · 颜色 = 分类编码</div>
  </div>
  <aside class="wc-trace" id="wc-trace">
    <div class="wc-trace-header">
      词条溯源 <span class="wc-trace-word" id="wc-trace-word"></span>
      <span class="wc-trace-badge" id="wc-trace-badge" style="display:none"></span>
    </div>
    <div class="wc-trace-list" id="wc-trace-list">
      <p class="wc-trace-empty">&#128070; 点击词云中的任意词条<br>查看该词当日所有原始标题与链接<br><small style="color:var(--dim)">约 {trace_count}/{len(sorted_rows)} 词有溯源数据（共 {total_source_hits} 条）</small></p>
    </div>
    <div class="wc-trace-footer">溯源数据来自当日 meme / hotlist 原始采集标题</div>
  </aside>
</div>

{legend}
{preview_html}
{top8_html}
</div>
</section>
</div>
<footer>GamePulse · {cloud_date} · 模块化站点，每个页面可独立迭代</footer>

<script id="wc-data" type="application/json">{wc2_json}</script>
<script id="wc-trace-data" type="application/json">{trace_json}</script>
<script>
(function() {{
  var canvas = document.getElementById('wc-canvas');
  if (!canvas || typeof WordCloud === 'undefined') {{
    console.error('wordcloud2.js 未加载或 canvas 不存在');
    return;
  }}

  // ---- 读取内联数据 ----
  var wcList = JSON.parse(document.getElementById('wc-data').textContent);
  var traceMap = JSON.parse(document.getElementById('wc-trace-data').textContent);

  // 构建 color lookup
  var colorMap = {{}};
  wcList.forEach(function(item) {{ colorMap[item[0]] = item[2]; }});

  // 构建 category lookup
  var catMap = {{}};
  wcList.forEach(function(item) {{ catMap[item[0]] = item[3]; }});

  // 构建 H lookup
  var hMap = {{}};
  wcList.forEach(function(item) {{ hMap[item[0]] = item[4]; }});

  // 构建 wordcloud2.js 兼容的列表：[word, weight]
  var wordList = wcList.map(function(item) {{
    return [item[0], item[1]];
  }});

  // ---- wordcloud2.js 配置 ----
  var options = {{
    list: wordList,
    gridSize: 9,
    weightFactor: function(w) {{
      // 映射 H=100 → ~52px, H=30 → ~16px, 产生明显字号落差
      return Math.pow(w, 0.65) * 3.2;
    }},
    fontFamily: '"PingFang SC","Microsoft YaHei","Noto Sans SC",sans-serif',
    color: function(word, weight, fontSize, distance, theta) {{
      return colorMap[word] || '#3fd68f';
    }},
    backgroundColor: '#161b22',
    rotateRatio: 0,            /* 2026-08-13 与主站统一：全员水平排版，不再随机竖排 */
    shape: 'square',
    ellipticity: 0.72,
    minSize: 11,
    weightMode: 'size',
    clearCanvas: true,
    shrinkToFit: true,
    drawOutOfBound: false,
    hover: function(item, dimension, event) {{
      if (!item) return;
      var word = item[0];
      canvas.style.cursor = 'pointer';
      // 移除非当前 tooltip
      var old = document.querySelector('.wc-tooltip');
      if (old) old.remove();
      var tip = document.createElement('div');
      tip.className = 'wc-tooltip';
      var h = hMap[word] || '--';
      var cat = catMap[word] || '梗/社区';
      tip.innerHTML = '<b>' + word + '</b><span>H=' + h + ' · ' + cat + '</span>';
      document.getElementById('wc-canvas-wrap').appendChild(tip);
      var rect = canvas.getBoundingClientRect();
      var wrapRect = document.getElementById('wc-canvas-wrap').getBoundingClientRect();
      tip.style.left = (event.clientX - wrapRect.left + 14) + 'px';
      tip.style.top = (event.clientY - wrapRect.top - 48) + 'px';
    }},
    click: function(item, dimension, event) {{
      if (!item) return;
      var word = item[0];
      showTrace(word);
    }},
    abortThreshold: 0,
    wait: 8
  }};

  WordCloud(canvas, options);

  // 移除 hover 时 tooltip
  canvas.addEventListener('mouseleave', function() {{
    canvas.style.cursor = 'default';
    var tip = document.querySelector('.wc-tooltip');
    if (tip) tip.remove();
  }});

  // ---- 溯源侧边栏交互 ----
  function showTrace(word) {{
    var traceEl = document.getElementById('wc-trace');
    var wordEl = document.getElementById('wc-trace-word');
    var badgeEl = document.getElementById('wc-trace-badge');
    var listEl = document.getElementById('wc-trace-list');

    wordEl.textContent = word;
    traceEl.classList.add('active');

    var info = traceMap[word];
    if (!info || !info.sources || info.sources.length === 0) {{
      // 显示主链接作为回退
      badgeEl.style.display = 'inline-block';
      badgeEl.textContent = 'H=' + (info ? info.H : '--');
      badgeEl.style.color = info ? info.color : 'var(--sub)';
      badgeEl.style.borderColor = info ? info.color : 'var(--border)';
      badgeEl.style.background = info ? info.color + '18' : 'var(--panel2)';

      var html = '';
      if (info && info.primary_url) {{
        html += '<a class="wc-trace-item" href="' + info.primary_url + '" target="_blank">'
             + '<span class="ts-title">' + word + '</span>'
             + '<span class="ts-meta"><span class="ts-primary">主源</span>'
             + '<span class="ts-source">策展输入</span></span></a>';
      }}
      html += '<p class="wc-trace-empty" style="padding-top:var(--space-md)">该词当日未匹配到额外溯源标题<br><small>溯源基于 meme / hotlist 原始采集交叉比对</small></p>';
      listEl.innerHTML = html;
      return;
    }}

    badgeEl.style.display = 'inline-block';
    badgeEl.textContent = 'H=' + info.H + ' · ' + info.sources.length + '条溯源';
    badgeEl.style.color = info.color;
    badgeEl.style.borderColor = info.color;
    badgeEl.style.background = info.color + '18';

    var items = info.sources.map(function(src, idx) {{
      var isPrimary = info.primary_url && src.url === info.primary_url;
      return '<a class="wc-trace-item" href="' + src.url + '" target="_blank">'
           + '<span class="ts-title">' + src.title + '</span>'
           + '<span class="ts-meta">'
           + (isPrimary ? '<span class="ts-primary">主源</span>' : '')
           + '<span class="ts-source">' + src.source + '</span></span></a>';
    }});
    listEl.innerHTML = items.join('');

    // 平滑滚动到顶部
    listEl.scrollTop = 0;
  }}

  // 页面加载后预选第一个高热词显示溯源
  setTimeout(function() {{
    if (wcList.length > 0) {{
      showTrace(wcList[0][0]);
    }}
  }}, 600);
}})();
</script>

</body></html>'''

    return page


# ===================== 写入文件 =====================

def _atomic_write(path, content):
    """原子写入：临时文件 + rename，避免 Windows 文件锁问题"""
    import time
    _tmp = path + "." + str(int(time.time() * 1000))
    io.open(_tmp, "w", encoding="utf-8").write(content)
    try:
        os.replace(_tmp, path)
    except OSError:
        try:
            os.remove(path)
            os.rename(_tmp, path)
        except OSError:
            print('WARN: cannot overwrite', path, '(locked), content in', _tmp)


# ---- A. 注入 index.html 的 #glance 词云区块（wordcloud2.js canvas 紧致排版） ----
# 注意：模板 index.html 的 #glance 使用 <!--WC2_INJECT-->...<!--/WC2_INJECT--> 稳定标记；
# style.css 已包含 .wc-cloud / #wc-canvas / .wc-cloud-tip 完整样式，无需此处再注入 CSS。
index_html = io.open(INDEX_PATH, encoding="utf-8").read()

# 1) 注入 wordcloud2.js CDN（只在 </head> 前注入一次）
if "wordcloud@1.2.2" not in index_html:
    cdn_tag = f'<script src="{WC2_CDN}"></script>'
    index_html = index_html.replace("</head>", cdn_tag + "\n</head>", 1)

# 2) 用 <!--WC2_INJECT--> / <!--/WC2_INJECT--> 稳定标记替换词云内容
#    根除了旧版用 .wc-preview / .wc-legend 做下界导致历史副本累积的 bug
inj_start = index_html.find("<!--WC2_INJECT-->")
inj_end = index_html.find("<!--/WC2_INJECT-->")
if inj_start > 0 and inj_end > inj_start:
    cut = inj_start + len("<!--WC2_INJECT-->")
    wc_full = main_wc_html + legend + preview_html
    index_html = index_html[:cut] + wc_full + index_html[inj_end:]
else:
    print("WARN: WC2_INJECT markers not found in index.html, wordcloud injection skipped")

# 3) 更新 #glance <small> 里的日期（2026-08-10 修复：id 从 trend 改为 glance）
index_html = re.sub(
    r'(id="glance".*?<small>)\d{4}-\d{2}-\d{2}',
    lambda m: m.group(1) + cloud_date,
    index_html, flags=re.DOTALL)

_atomic_write(INDEX_PATH, index_html)

# ---- B. 生成 wordcloud.html（wordcloud2.js canvas 版 + 溯源侧边栏） ----
wc_page_content = generate_wordcloud_page()
_atomic_write(WC_PATH, wc_page_content)

# ---- 输出统计 ----
print(f"热词数: {len(rows)}")
print("分类:", dict(Counter(r["cat"] for r in rows)))
print("Top10:")
for r in rows[:10]:
    print(f"  H={r['H']:>3} [{r['cat']}] {r['word']}")
print(f"溯源: {trace_count}/{len(sorted_rows)} 词可溯源, 共 {total_source_hits} 条")
print(f"\n词云已注入 index.html #glance (wordcloud2.js canvas 紧致排版) + wordcloud.html (canvas + 溯源侧边栏)")
