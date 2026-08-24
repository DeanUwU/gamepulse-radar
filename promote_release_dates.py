# -*- coding: utf-8 -*-
"""promote_release_dates.py — 定档事件桥接（防漏报·手段1 的闭环补丁）

背景：collector_release_dates.py 已经能产出「已核实定档事件」到
  collectors/release_dates_YYYYMMDD.json，但它只停留在"供量级告警闸核对"，
  从不写进 events.json 的 events[]（日历主数据）。而 gen_calendar.py 只遍历
  events[]，于是诡秘之主 8/21 公测这类已核实定档事件永远进不了日历——漏报。

本脚本职责：读当日 release_dates_*.json 的 items，把其中
  ① 有 release_date（非空）
  ② 有 source_url（非根页，可溯源到具体文章）
的定档事件，写成 events[] 的 kind=cal 节点（复用 collector_news_events 的 node 结构）。
按 id(release_<hash>) 去重，不覆盖手工维护的事件。

红线：无 release_date 或无有效 source_url 的条目一律不写（宁缺毋滥，不编造）。

非阻断：release_dates 文件缺失/为空时跳过，不影响日报发布。

用法: python promote_release_dates.py
     环境变量 RELEASE_OUT_DIR 可覆盖 release_dates 读取目录（预览锁文件时应急）。
"""
import json, io, os, sys, datetime, hashlib, urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__))
DOC = os.path.join(BASE, "events.json")
RD_DIR = os.environ.get("RELEASE_OUT_DIR", os.path.join(BASE, "collectors"))


def is_root_url(u):
    """根页占位拦截：无 path/query/fragment 的首页链接不承载具体事件。"""
    if not u.startswith(("http://", "https://")):
        return True
    p = urllib.parse.urlparse(u)
    return p.path in ("", "/") and not p.query and not p.fragment


def main():
    today = datetime.date.today().strftime("%Y%m%d")
    rd_path = os.path.join(RD_DIR, f"release_dates_{today}.json")
    if not os.path.exists(rd_path):
        print("WARN: 当日 release_dates 不存在（%s），跳过定档桥接" % rd_path)
        return 0
    try:
        rd = json.load(io.open(rd_path, encoding="utf-8"))
    except Exception as e:
        print("WARN: release_dates 解析失败：%s" % e, file=sys.stderr)
        return 0
    items = rd.get("items", [])
    if not items:
        print("定档桥接：当日无定档事件")
        return 0

    doc = json.load(io.open(DOC, encoding="utf-8"))
    events = doc["events"]
    existing = {ev.get("id") for ev in events}
    # 语义去重：按 (game, date_start) 判断是否已有同游戏同日的定档节点，
    # 避免与 news inbox 补录（id 前缀 news_）撞出两条重复的日历条目。
    existing_semantic = set()
    for ev in events:
        g = (ev.get("game") or "").strip()
        ds = (ev.get("date_start") or "").strip()
        if g and ds:
            existing_semantic.add((g, ds))
    added = 0
    skipped = 0

    for it in items:
        game = (it.get("game") or "").strip()
        release_date = (it.get("release_date") or "").strip()
        url = (it.get("source_url") or "").strip()
        title = (it.get("title") or "").strip()
        rtype = (it.get("release_type") or "上线").strip()
        src = (it.get("source_name") or "").strip()

        if not (game and release_date):
            skipped += 1
            continue
        if is_root_url(url):
            skipped += 1  # 红线：无有效溯源链接不写
            continue
        # 校验日期格式
        try:
            y, m, d = map(int, release_date.split("-"))
            datetime.date(y, m, d)
        except (ValueError, AttributeError):
            skipped += 1
            continue

        # 动作文案：优先用 title 里的实质内容，否则用 release_note 兜底
        action = (title or f"{game} {rtype}").strip()
        eid = "release_%s" % hashlib.md5(
            (game + release_date).encode("utf-8")).hexdigest()[:10]
        if eid in existing:
            continue  # 已存在，不重复
        if (game, release_date) in existing_semantic:
            continue  # 语义去重：同游戏同日已有定档节点（可能来自 news inbox 补录）

        node = {
            "id": eid, "kind": "cal", "game": game,
            "title": "%s：%s（%s %s）" % (game, action, release_date, rtype),
            "source_url": url,
            "source_name": "%s %s %s（%s）" % (game, release_date, rtype, src or "网络检索"),
            "anchor": '<a class="cal-ev" target="_blank" href="%s" title="%s">%s</a>'
                      % (url, game, action[:30]),
            "date_start": release_date, "date_end": release_date,
            # 日历定档节点是"未来发售安排"，非新闻，无新闻原始发布时间；
            # 仅当 release_dates 条目显式提供 pubdate 时透传，否则 null。
            "pubdate": (it.get("pubdate") or None),
        }
        events.append(node)
        existing.add(eid)
        existing_semantic.add((game, release_date))
        added += 1

    if added:
        json.dump(doc, io.open(DOC, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    print("定档桥接：release_dates %d 条，新增进日历 %d 条，跳过 %d 条（无日期/无链接）"
          % (len(items), added, skipped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
