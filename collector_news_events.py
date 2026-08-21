#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""新闻事件采集器（互联网检索 -> events.json 日历节点）。

数据流：
1. 每日自动化 Agent 用 WebSearch 检索游戏资讯（版本/公测/发售/前瞻），
   把结果整理成 collectors/news_events_inbox.json（人工/AI 预填，带溯源链接）。
2. 本脚本读 inbox -> 提取【游戏名+日期+动作+来源链接】-> 写成 events 节点（kind=cal），
   按 id(news_<hash>) 去重，不覆盖手工维护的事件。
3. gen_calendar.py 按今天±窗口动态渲染进日历。

inbox 格式（collectors/news_events_inbox.json）：
[
  {
    "game": "鸣潮",
    "date": "2026-08-20",          # 发售/版本上线日（ISO）
    "action": "3.6版本「蜃云灯影」上线，新角色清宵/景燃",
    "url": "https://m.ali213.net/news/gl2608/1798527.html",
    "source": "游民星空/3DM"        # 来源媒体名（溯源用）
  },
  ...
]

红线：每条必须有 url（溯源到具体文章），无 url 的不写。
非阻断：inbox 不存在或为空时跳过，不影响日报发布。
"""
import json, io, os, sys, datetime, re, hashlib, urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__))
DOC = os.path.join(BASE, "events.json")
INBOX = os.environ.get(
    "NEWS_INBOX",
    os.path.join(BASE, "collectors", "news_events_inbox.json"))

def main():
    if not os.path.exists(INBOX):
        print("WARN: news inbox 不存在（%s），跳过新闻事件采集" % INBOX)
        return 0
    try:
        inbox = json.load(io.open(INBOX, encoding="utf-8"))
    except Exception as e:
        print("WARN: inbox 解析失败：%s" % e, file=sys.stderr)
        return 0
    if not inbox:
        print("news inbox 为空，无新闻事件")
        return 0

    doc = json.load(io.open(DOC, encoding="utf-8"))
    events = doc["events"]
    existing = {ev.get("id") for ev in events}
    added = 0
    skipped = 0
    def is_root_url(u):
        """根页占位拦截：无 path/query/fragment 的首页链接（如 https://gp.qq.com/）不承载具体事件。"""
        if not u.startswith(("http://", "https://")):
            return True  # 非 http 链接一律不写
        p = urllib.parse.urlparse(u)
        return p.path in ("", "/") and not p.query and not p.fragment

    for item in inbox:
        url = (item.get("url") or "").strip()
        if not url:
            skipped += 1
            continue  # 红线：无溯源链接不写
        if is_root_url(url):
            skipped += 1
            continue  # 红线：禁止主页/根页占位（必须深链到具体文章/公告/详情页）
        game = (item.get("game") or "").strip()
        date = (item.get("date") or "").strip()
        action = (item.get("action") or "").strip()
        src = (item.get("source") or "").strip()
        if not (game and date and action):
            skipped += 1
            continue
        # 校验日期格式
        try:
            y, m, d = map(int, date.split("-"))
            datetime.date(y, m, d)
        except (ValueError, AttributeError):
            skipped += 1
            continue
        eid = "news_%s" % hashlib.md5(
            (game + date + action).encode("utf-8")).hexdigest()[:10]
        if eid in existing:
            continue
        node = {
            "id": eid, "kind": "cal", "game": game,
            "title": "%s：%s" % (game, action),
            "source_url": url,
            "source_name": "%s 将于 %s：%s（%s）"
                           % (game, date, action, src or "网络检索"),
            "anchor": '<a class="cal-ev" target="_blank" href="%s" title="%s">%s</a>'
                      % (url, game, action[:30]),
            "date_start": date, "date_end": date,
        }
        events.append(node)
        existing.add(eid)
        added += 1

    json.dump(doc, io.open(DOC, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    # 清空 inbox（已消费），防止重复
    io.open(INBOX, "w", encoding="utf-8").write("[]")
    print("新闻事件采集：inbox %d 条，新增 %d 条，跳过 %d 条（无链接/无日期）"
          % (len(inbox), added, skipped))
    return 0

if __name__ == "__main__":
    sys.exit(main())
