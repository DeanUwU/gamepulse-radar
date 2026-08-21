#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Steam 即将发售采集器（Coming Soon）。

抓 https://store.steampowered.com/genre/Coming_Soon/ ，
提取未来 N 天内发售的游戏（Released: DD Mon, YYYY），
转成 events.json 的 cal 节点（带 date_start/date_end），追加进 events[]。
- 只补 Steam 来源的发售节点，不覆盖手工维护的 events（按 id 去重：steam_<appid>）
- 链接溯源到具体商店页 app/<appid>/（非根页，过红线）
- 非阻断：抓取失败写空列表，不影响日报发布
输出：events.json（追加）+ collectors/steam_coming_YYYYMMDD.json（当天快照）
"""
import json, io, os, re, sys, datetime, urllib.request, urllib.error

BASE = os.path.dirname(os.path.abspath(__file__))
DOC = os.path.join(BASE, "events.json")
OUT_DIR = os.path.join(BASE, "collectors")
os.makedirs(OUT_DIR, exist_ok=True)
HORIZON = int(os.environ.get("STEAM_HORIZON", "14"))  # 未来 14 天
# Steam 即将发售过滤页（只列未发售游戏，按日期升序，第一条即最近发售）
URL = ("https://store.steampowered.com/search/"
       "?filter=comingsoon&supportedlang=schinese&cc=CN&l=schinese")

MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}

def fetch():
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "ignore")

def parse_date(txt):
    # 中文：2026 年 8 月 17 日
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", txt)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        # 英文：Aug 17, 2026
        m = re.search(r"([A-Za-z]{3})\s+(\d{1,2}),?\s+(\d{4})", txt)
        if not m:
            return None
        mo, d, y = MONTHS.get(m.group(1).title()), int(m.group(2)), int(m.group(3))
    if not mo:
        return None
    try:
        return datetime.date(y, mo, d)
    except ValueError:
        return None

def parse_items(html):
    items = []
    blk = html[html.find("search_resultsRows"):] if "search_resultsRows" in html else html
    for aid, nm, dt in re.findall(
            r'data-ds-appid="(\d+)".*?'
            r'<span class="title">(.*?)</span>.*?'
            r'search_released[^>]*>(.*?)</div>', blk, re.S):
        name = re.sub(r"<.*?>", "", nm).strip()
        href = "https://store.steampowered.com/app/%s/" % aid
        d = parse_date(dt)
        if d:
            items.append((aid, name, href, d))
    return items

def main():
    today = datetime.date.today()
    hi = today + datetime.timedelta(days=HORIZON)
    try:
        html = fetch()
    except (urllib.error.URLError, OSError, Exception) as e:
        print("WARN: Steam ComingSoon 抓取失败：%s" % e, file=sys.stderr)
        json.dump([], io.open(os.path.join(
            OUT_DIR, "steam_coming_%s.json" % today.strftime("%Y%m%d")),
            "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return 0

    raw = parse_items(html)
    # 只留今天及未来 HORIZON 天内发售的（跳过历史已发售）
    future = [(a, n, h, d) for (a, n, h, d) in raw
              if today <= d <= hi]
    future.sort(key=lambda x: x[3])

    # 过滤掉 Demo / 原声带 / DLC / 免费试玩 等非正式发售
    SKIP_PAT = re.compile(
        r'(Demo|demo|原声带|Soundtrack|soundtrack|OST|ost|'
        r'DLC|dlc|Test|test|Beta|beta|'
        r'Free to Play|免费|试玩|Preview|preview)', re.I)
    future = [(a, n, h, d) for (a, n, h, d) in future
              if not SKIP_PAT.search(n)]

    # 每天最多 MAX_PER_DAY 条（避免垃圾游戏刷屏，保留高热度优先）
    MAX_PER_DAY = int(os.environ.get("STEAM_MAX_PER_DAY", "8"))
    by_day = {}
    for item in future:
        by_day.setdefault(item[3], []).append(item)
    future = []
    for day in sorted(by_day):
        future.extend(by_day[day][:MAX_PER_DAY])

    # 写进 events.json（带 Demo/DLC 过滤，正式发售全进日历，保证信息量）
    doc = json.load(io.open(DOC, encoding="utf-8"))
    events = doc["events"]
    existing = {ev.get("id") for ev in events}
    added = 0
    for appid, name, href, d in future:
        eid = "steam_%s" % appid
        if eid in existing:
            continue
        ds = d.isoformat()
        node = {
            "id": eid, "kind": "cal", "game": name,
            "title": "%s Steam 发售" % name,
            "source_url": href,
            "source_name": "%s 将于 %s 在 Steam 发售（官方商店页）"
                           % (name, d.strftime("%Y-%m-%d")),
            "anchor": '<a target="_blank" href="%s" title="%s Steam 商店页">'
                      'Steam 发售</a>' % (href, name),
            "date_start": ds, "date_end": ds,
        }
        events.append(node)
        existing.add(eid)
        added += 1

    json.dump(doc, io.open(DOC, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    snap = [{"appid": a, "name": n, "url": h, "release": d.isoformat()}
            for (a, n, h, d) in future]
    json.dump(snap, io.open(os.path.join(
        OUT_DIR, "steam_coming_%s.json" % today.strftime("%Y%m%d")),
        "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("Steam 即将发售：抓取 %d 条（窗口内 %d 条，已过滤 Demo/DLC），"
          "新增进 events %d 条" % (len(raw), len(future), added))
    return 0

if __name__ == "__main__":
    sys.exit(main())
