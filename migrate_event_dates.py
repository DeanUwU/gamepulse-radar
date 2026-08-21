#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一次性迁移：从 events[].source_name 抽取结构化日期，写入 date_start / date_end。

规则：
- 优先匹配区间 "M/D-M/D" 或 "M/D~M/D" -> date_start=前者, date_end=后者
- 否则匹配单点 "M/D" -> date_start=date_end=M/D
- year 固定 2026（数据均为 2026 年）
- anchor 为空（已撤下/无锚点）的节点：date_start=None，跳过不填
- 抽不到任何日期的：date_start=None（保留人工策展位，动作流不显示）
输出：备份原 events.json -> events.json.bak，再回写带日期字段的版本。
"""
import json, io, os, re, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, "events.json")
BAK = os.path.join(BASE, "events.json.bak")
YEAR = 2026

doc = json.load(io.open(SRC, encoding="utf-8"))
events = doc["events"]

# 备份（仅首次）
if not os.path.exists(BAK):
    io.open(BAK, "w", encoding="utf-8").write(
        io.open(SRC, encoding="utf-8").read())
    print("已备份 events.json -> events.json.bak")

# 区间优先：7/31-8/3  8/8-8/23  8/8~8/21  07/27~08/30
RE_RANGE = re.compile(r"(?<!\d)(\d{1,2})[/\-.](\d{1,2})\s*[-~]\s*(\d{1,2})[/\-.](\d{1,2})(?!\d)")
# 单点：8/13  8/20  7/31  （排除 ISO 年份前缀如 2026-07，要求前面不是 4 位数字+短横）
RE_SINGLE = re.compile(r"(?<![\d\-])(\d{1,2})[/\-.](\d{1,2})(?!\d)(?!-)")

def to_iso(m, d):
    return "%04d-%02d-%02d" % (YEAR, int(m), int(d))

stats = {"filled": 0, "range": 0, "single": 0, "empty_anchor": 0, "no_date": 0}
for ev in events:
    sn = ev.get("source_name", "") or ""
    anchor = ev.get("anchor", "") or ""
    # 已撤下节点（anchor 空）跳过
    if not anchor.strip():
        ev["date_start"] = None
        ev["date_end"] = None
        stats["empty_anchor"] += 1
        continue
    m = RE_RANGE.search(sn)
    if m:
        ev["date_start"] = to_iso(m.group(1), m.group(2))
        ev["date_end"] = to_iso(m.group(3), m.group(4))
        stats["range"] += 1
        stats["filled"] += 1
        continue
    m = RE_SINGLE.search(sn)
    if m:
        ev["date_start"] = to_iso(m.group(1), m.group(2))
        ev["date_end"] = ev["date_start"]
        stats["single"] += 1
        stats["filled"] += 1
        continue
    # 抽不到
    ev["date_start"] = None
    ev["date_end"] = None
    stats["no_date"] += 1

doc["meta"]["date_migrated"] = datetime.date.today().isoformat()
json.dump(doc, io.open(SRC, "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

print("迁移完成：", json.dumps(stats, ensure_ascii=False))
print("区间节点 %d，单点节点 %d，空锚点 %d，无日期 %d"
      % (stats["range"], stats["single"], stats["empty_anchor"], stats["no_date"]))
