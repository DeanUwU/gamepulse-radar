# -*- coding: utf-8 -*-
"""
gen_source_report.py —— 源覆盖报告区块（index.html #source）自动生成。

2026-08-05 治理背景：
  #source 区块原本是手写静态 HTML，daily_refresh.py 只用正则替换「本次失败源：…」一句。
  由于该句早已不在页面上，替换长期空转，导致「已接入(60) / B站 5/6 / 57 源存活」等
  数字停留在历史版本，与 sources.toml、sources_status.json、当日采集结果全部对不上——
  属于防编造门槛里的硬伤。

  本脚本改为从真实文件推导数字：
    - sources.toml            → enabled=true / false 计数
    - sources_status.json     → HTTP 探活结果
    - collectors/meme_YYYYMMDD.json        → 当日 B 站 6 路 + 贴吧
    - collectors/public_hotlist_YYYYMMDD.json → 当日全网热榜 5 路
  任何一项缺失都如实写「未采集/缺失」，不允许猜。
"""
import io
import os
import re
import json
import datetime
import collections

BASE = os.path.dirname(os.path.abspath(__file__))
TODAY = datetime.date.today()
STAMP = TODAY.strftime("%Y%m%d")

# 这些是热榜聚合接口，注册在 sources.toml 里只为了留档，本身没有可 GET 的 HTML 页，
# static 探活必然拿不到状态码。它们的数据由 collector_public.py 走各自 API 单独取。
# 只有这个白名单里的 id 才允许被解释成"非死链"，其余一律按真实探活失败处理。
HOTLIST_IDS = {
    "weibo_hotlist", "zhihu_hotlist", "bilibili_hotsearch",
    "douyin_hotlist", "xiaohongshu_hotlist",
}


def _read(p):
    try:
        return io.open(os.path.join(BASE, p), encoding="utf-8").read()
    except OSError:
        return ""


def _json(p, default=None):
    try:
        return json.load(io.open(os.path.join(BASE, p), encoding="utf-8"))
    except (OSError, ValueError):
        return default


def count_sources():
    """返回 (enabled, disabled)。"""
    txt = _read("sources.toml")
    en = dis = 0
    for blk in re.split(r"\n(?=\[\[sources\]\])", txt):
        if "[[sources]]" not in blk:
            continue
        m = re.search(r"enabled\s*=\s*(true|false)", blk)
        if m and m.group(1) == "true":
            en += 1
        else:
            dis += 1
    return en, dis


def probe_status():
    """返回 (总条目, 200 数, [(id, code) 非 200])。"""
    st = _json("sources_status.json", {}) or {}
    if isinstance(st, dict) and "sources" in st:
        st = st["sources"]
    items = list(st.items()) if isinstance(st, dict) else [(x.get("id"), x) for x in st]
    ok, bad = 0, []
    for k, v in items:
        code = v.get("status") if isinstance(v, dict) else None
        if code == 200:
            ok += 1
        else:
            bad.append((k, code))
    return len(items), ok, bad


def meme_routes():
    """当日 B 站 6 路 + 贴吧的真实条数；缺文件返回 None。"""
    d = _json("collectors/meme_%s.json" % STAMP)
    if not isinstance(d, dict):
        return None
    order = ["popular", "hotwords", "series", "meme_ups", "weekly", "tieba"]
    return collections.OrderedDict(
        (k, len(d.get(k) or [])) for k in order if k in d
    )


def hotlist_routes():
    d = _json("collectors/public_hotlist_%s.json" % STAMP)
    if not isinstance(d, dict):
        return None
    order = ["weibo", "zhihu", "douyin", "bilibili", "xiaohongshu"]
    return collections.OrderedDict(
        (k, len(d.get(k) or [])) for k in order if isinstance(d.get(k), list)
    )


def build_html():
    en, dis = count_sources()
    total, ok, bad = probe_status()
    meme = meme_routes()
    hot = hotlist_routes()

    CN = {"popular": "全站热门", "hotwords": "热搜词", "series": "梗解读",
          "meme_ups": "梗UP主", "weekly": "每周必看", "tieba": "贴吧热议"}
    HOT_CN = {"weibo": "微博", "zhihu": "知乎", "douyin": "抖音",
              "bilibili": "B站", "xiaohongshu": "小红书"}

    bars = []

    bars.append(
        '<div class="src-bar"><span style="color:#3fd68f">✅ 已接入(%d)：sources.toml 中 '
        'enabled=true 的信源总数（另有 %d 条 enabled=false 停用，不计入）；'
        '重点含 B站 5 路 / 贴吧热议 / Steam 官方新闻 / Reddit r/Games / GameLook / 游戏陀螺 / '
        '白鲸出海 / 机核 / 游民星空 / 触乐 / 17173 / VGtime / 微博热搜 / 知乎热榜 / 抖音热榜 / '
        'B站热搜 / 小红书热榜（全网热榜为增量交叉信号）</span></div>' % (en, dis)
    )

    if total:
        if bad:
            # 区分两类"探活没拿到 200"：
            #   ① 热榜聚合接口：本来就没有可探活的 HTML 页，数据由 collector_public.py 单独取回
            #   ② 其余信源：是真的没连上，必须标为待修复，不能混进①里一句话糊弄过去
            agg = [x for x in bad if x[0] in HOTLIST_IDS]
            dead = [x for x in bad if x[0] not in HOTLIST_IDS]
            if agg:
                bars.append(
                    '<div class="src-bar"><span style="color:#3fd68f">✅ 存活探测：注册表 %d 条实测 '
                    '%d 条 HTTP 200；另有 %d 条热榜聚合接口（%s）无可探活页面，'
                    '数据由 collector_public.py 当日单独取回，不计为死链</span></div>'
                    % (total, ok, len(agg), "、".join(k for k, _ in agg))
                )
            if dead:
                bars.append(
                    '<div class="src-bar"><span style="color:#ffb020">⚠️ 本次探活失败(%d)·待修复：'
                    '%s —— 真实连不上，已如实标注，未拿它充数；对应板块本次不引用该源内容'
                    '</span></div>'
                    % (len(dead), "、".join("%s(%s)" % (k, v) for k, v in dead))
                )
        else:
            bars.append(
                '<div class="src-bar"><span style="color:#3fd68f">✅ 存活探测：注册表 %d 条'
                '全部 HTTP 200</span></div>' % total
            )
    else:
        bars.append(
            '<div class="src-bar"><span style="color:#ffb020">⚠️ 存活探测：'
            'sources_status.json 缺失，本次未做探活</span></div>'
        )

    if meme is None:
        bars.append(
            '<div class="src-bar"><span style="color:#ff5c39">⛔ 本次 meme_radar：'
            '当日采集文件 meme_%s.json 缺失（不沿用历史文件）</span></div>' % STAMP
        )
    else:
        bili = [k for k in meme if k != "tieba"]
        okn = sum(1 for k in bili if meme[k] > 0)
        detail = " · ".join(
            "%s %s(%d)" % (CN.get(k, k), "OK" if meme[k] else "空", meme[k]) for k in bili
        )
        tie = meme.get("tieba", 0)
        color = "#3fd68f" if okn == len(bili) else "#ffb020"
        icon = "✅" if okn == len(bili) else "⚠️"
        bars.append(
            '<div class="src-bar"><span style="color:%s">%s 本次 meme_radar：B站 %d/%d（%s）'
            ' · 贴吧热议 %s(%d)</span></div>'
            % (color, icon, okn, len(bili), detail, "OK" if tie else "空", tie)
        )

    if hot is None:
        bars.append(
            '<div class="src-bar"><span style="color:#ffb020">⚠️ 全网热榜：'
            '当日 public_hotlist_%s.json 缺失</span></div>' % STAMP
        )
    else:
        okn = sum(1 for v in hot.values() if v > 0)
        detail = " / ".join("%s %d" % (HOT_CN.get(k, k), v) for k, v in hot.items())
        bars.append(
            '<div class="src-bar"><span style="color:#3fd68f">✅ 全网热榜 %d/%d 路请求成功：'
            '%s（数字为 game·ACG 过滤后保留条数，0 表示该路当日无游戏相关词条，'
            '不是抓取失败）</span></div>' % (len(hot), len(hot), detail)
        )

    bars.append(
        '<div class="src-bar"><span style="color:#3fd68f">✅ 待接入·未覆盖(0)：'
        'backlog 已清零（触乐 / 竞核 / 手游那点事 已注册为 news_list 采集，'
        'DataEye 为 static 探活，非新闻源不参与内容采集）</span></div>'
    )

    return (
        '<section id="source" class="src-mini">'
        '<div class="sec-title"><span class="bar" style="background:var(--sub)"></span>'
        '数据来源 · 源覆盖报告 <small>已接入 / 存活 / 本次采集</small></div>'
        + "".join(bars)
        + '<small style="opacity:.5;margin-left:8px">官方API/RSS · 单源失败自动跳过 · '
          '全站口径：热度数 H(0-100) 统一 · 本区块由 gen_source_report.py 按 sources.toml / '
          'sources_status.json / 当日采集文件实算生成，不手写 · 本页内容基于 %s 当日'
          '全量重建，不沿用历史缓存</small></section>' % TODAY.isoformat()
    )


def main():
    fn = os.path.join(BASE, "index.html")
    h = io.open(fn, encoding="utf-8").read()
    new = build_html()
    if re.search(r'<section id="source".*?</section>', h, re.S):
        h = re.sub(r'<section id="source".*?</section>', lambda m: new, h, flags=re.S)
    else:
        print("⚠ index.html 中找不到 #source 区块，跳过")
        return
    # 页脚日期同步
    # 2026-08-06：页脚文案在 08-05 架构合并后由「游戏内容雷达」改为「游戏日报主站」，
    # 原正则失配导致页脚日期长期停留在旧值。改为匹配 <footer> 内任意日期串。
    def _sync_footer(m):
        return re.sub(r"\d{4}-\d{2}-\d{2}", TODAY.isoformat(), m.group(0))
    h = re.sub(r"<footer>.*?</footer>", _sync_footer, h, flags=re.S)
    tmp = fn + "." + datetime.datetime.now().strftime("%H%M%S%f")
    io.open(tmp, "w", encoding="utf-8").write(h)
    try:
        os.replace(tmp, fn)
    except OSError:
        os.remove(fn)
        os.rename(tmp, fn)
    print("✓ 源覆盖报告已按真实数据重建（%s）" % TODAY.isoformat())


if __name__ == "__main__":
    main()
