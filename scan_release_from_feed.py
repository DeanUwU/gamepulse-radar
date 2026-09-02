# -*- coding: utf-8 -*-
"""scan_release_from_feed.py — 定档回流（防漏报·手段2）

背景：collector_release_dates.py 只扫 watchlist.json「必盯名单」里的游戏，
像《伊莫》这种当天才被 B 站 PV 定档、又不在名单里的新作，全站其他板块
（词云 / 新闻流 / 信源快报）都看得到，唯独进不了日历 events[] ——
因为没有任何环节把非名单游戏的定档消息回流到 release_dates。

本脚本职责：从近 7 天的新闻池与信源快报里，用「具体日期 + 定档/公测/发售」
句式识别已定档事件，补充写入 collectors/release_dates_YYYYMMDD.json，
再由 promote_release_dates.py 统一提升进 events[]（去重与红线都在那边把关）。

红线：
  - 只收 release_date >= 今天 的前瞻节点（过去的上线动作无日历价值）。
  - 排除维护/封禁/处罚/停运等运营公告，避免污染日历。
  - 无有效溯源链接（根页/搜索页）不写，宁缺毋滥。

非阻断：任一池子缺失或解析失败时跳过，不影响日报发布。

用法: python scan_release_from_feed.py [--dry]
"""
import json, io, os, re, sys, datetime, urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__))
DOC = os.path.join(BASE, "events.json")
CURATED = os.path.join(BASE, "inbox", "sources_curated.json")
RD_DIR = os.environ.get("RELEASE_OUT_DIR", os.path.join(BASE, "collectors"))
WINDOW_DAYS = 7

# 定档动作关键词（越靠前优先级越高，用于判定 release_type）
_TYPE_RULES = [
    ("公测", ("公测", "开测", "删档测试", "不删档")),
    ("发售", ("发售", "首发", "推出", "上市")),
    ("开服", ("开服", "服务器开启")),
    ("上线", ("定档", "正式上线", "全平台上线", "全球上线", "上线")),
]
_NOISE_KW = ("维护", "封禁", "处罚", "违规", "停运", "下架", "补偿", "回滚", "优化公告")
_BAD_URL = ("search.bilibili", "s.weibo.com", "/search/", "?search=")


def is_bad_url(u):
    if not u or not u.startswith(("http://", "https://")):
        return True
    p = urllib.parse.urlparse(u)
    if p.path in ("", "/") and not p.query and not p.fragment:
        return True  # 根页占位
    return any(b in u for b in _BAD_URL)


def parse_date(text):
    """从标题里解析具体日期，返回 (date, 命中文本)。跨年自动 +1 年。"""
    today = datetime.date.today()
    m = re.search(r"(\d{1,2})月(\d{1,2})日", text)
    if not m:
        # 9/16 形式；左侧不允许是数字或点，避免把版本号 2.4 当日期
        m = re.search(r"(?<![\d.])(\d{1,2})/(\d{1,2})(?![\d.])", text)
    if not m:
        return None, ""
    mo, dd = int(m.group(1)), int(m.group(2))
    if not (1 <= mo <= 12 and 1 <= dd <= 31):
        return None, ""
    for year in (today.year, today.year + 1):
        try:
            d = datetime.date(year, mo, dd)
        except ValueError:
            continue
        if d >= today:
            return d, m.group(0)
    return None, ""


def guess_game(text, fallback):
    """游戏名：优先用结构化 game 字段，'全行业' 时从标题紧邻动作词前的《》里取。"""
    g = (fallback or "").strip()
    if g and g != "全行业":
        return g
    names = re.findall(r"《([^》]{1,20})》", text)
    if not names:
        return ""
    # 取距第一个定档动作词最近的书名号（国服译名常写在后面）
    first_kw = min([text.find(k) for k in ("定档", "公测", "上线", "发售", "开测", "开服")
                    if text.find(k) >= 0] or [len(text)])
    before = [n for n in names if text.find("《" + n + "》") <= first_kw]
    return (before or names)[-1]


def guess_type(text):
    for label, kws in _TYPE_RULES:
        for k in kws:
            if k in text:
                return label
    return ""


def main():
    dry = "--dry" in sys.argv
    today = datetime.date.today()
    lo = today - datetime.timedelta(days=WINDOW_DAYS)
    pools = []

    # 池1：近 7 天新闻流
    try:
        doc = json.load(io.open(DOC, encoding="utf-8"))
        for it in doc.get("feed_events", []):
            pd = it.get("pubdate")
            if pd and pd >= lo.isoformat():
                pools.append((it.get("title") or "", it.get("source_url") or "",
                              it.get("game") or "", pd, it.get("source_name") or ""))
    except Exception as e:
        print("WARN: feed_events 读取失败：%s" % e, file=sys.stderr)

    # 池2：信源快报（只取已准入 adopt，避免把待复核的噪音带进日历）
    try:
        sc = json.load(io.open(CURATED, encoding="utf-8"))
        for it in (sc if isinstance(sc, list) else sc.get("items", [])):
            if it.get("verdict") != "adopt":
                continue
            pools.append((it.get("title") or "", it.get("url") or "",
                          it.get("game") or "", it.get("pubdate"),
                          it.get("src_name") or ""))
    except Exception as e:
        print("WARN: sources_curated 读取失败：%s" % e, file=sys.stderr)

    out, seen = [], set()
    for title, url, game, pubdate, src in pools:
        if not title:
            continue
        if re.search("|".join(_NOISE_KW), title):
            continue
        rtype = guess_type(title)
        if not rtype:
            continue
        d, hits = parse_date(title)
        if not d:
            continue
        if is_bad_url(url):
            continue
        g = guess_game(title, game)
        if not g:
            continue
        key = (g, d.isoformat())
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "game": g,
            "release_date": d.isoformat(),
            "release_type": rtype,
            "title": "%s %s（%s）" % (rtype, hits, d.isoformat()),
            "source_url": url,
            "source_name": src or "站内定档回流",
            "pubdate": pubdate,
            "scanned_by": "scan_release_from_feed",
        })

    if not out:
        print("定档回流：近 %d 天新闻池/快报中无新的前瞻定档" % WINDOW_DAYS)
        return 0

    print("定档回流：命中 %d 条候选" % len(out))
    for it in out:
        print("   - %s | %s | %s | %s" % (it["game"], it["release_date"],
                                          it["release_type"], it["source_url"][:70]))
    if dry:
        print("（--dry 模式，未写入）")
        return 0

    rd_path = os.path.join(RD_DIR, "release_dates_%s.json" % today.strftime("%Y%m%d"))
    rd = {"date": today.isoformat(), "items": []}
    if os.path.exists(rd_path):
        try:
            rd = json.load(io.open(rd_path, encoding="utf-8"))
            rd.setdefault("items", [])
        except Exception:
            print("WARN: 既有 release_dates 解析失败，本次覆盖写入", file=sys.stderr)
    had = {(i.get("game"), i.get("release_date")) for i in rd["items"]}
    add = [i for i in out if (i["game"], i["release_date"]) not in had]
    rd["items"].extend(add)

    # 原子替换：os.replace 覆盖目标即可，不要用 os.remove+os.rename ——
    # Windows 沙箱会拦截 os.remove（SAFE_DELETE_FAIL_CLOSED），且 remove 失败后
    # rename 会因目标已存在抛 WinError 183（与 refresh_content.py:2337 同一坑）。
    tmp = rd_path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(rd, f, ensure_ascii=False, indent=1)
    os.replace(tmp, rd_path)
    print("定档回流：新增 %d 条 -> %s（现有 %d 条）" % (len(add), rd_path, len(rd["items"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
