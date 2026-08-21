#!/usr/bin/env python3
"""build_history.py — 扫描 collectors 历史数据，生成 history_data.json 供 history.html 使用"""

import json, os, glob, re
from datetime import datetime, timedelta
from collections import defaultdict

COLLECTORS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "collectors")
OUTPUT_FILE = os.path.join(COLLECTORS_DIR, "history_data.json")

HOT_THRESHOLD = 3_000_000             # 高热线
GAME_TNAMES = {"单机游戏", "网络游戏", "手机游戏", "GMV", "桌游棋牌"}

def compute_h(view: float, threshold: float = HOT_THRESHOLD) -> float:
    """统一 H(0–100) 标度"""
    v = view / max(threshold, 1)
    return round(min(100, max(0, v * 70)), 1)

def extract_date(filename: str) -> str | None:
    m = re.search(r"(\d{8})", os.path.basename(filename))
    if not m:
        return None
    d = m.group(1)
    try:
        datetime.strptime(d, "%Y%m%d")
        return d
    except ValueError:
        return None

def load_meme(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None

def load_hotlist(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None

def build_daily_summary(date_str: str, meme: dict, hotlist: dict | None) -> dict:
    popular = meme.get("popular") or []
    hotwords = meme.get("hotwords") or []
    tieba = meme.get("tieba") or []
    meme_ups = meme.get("meme_ups") or []

    # Top 8 popular 视频
    top_pop = popular[:8]

    # 游戏类 top 3
    game_videos = [v for v in popular if v.get("tname") in GAME_TNAMES][:3]

    # Top 5 hotwords
    top_hw = hotwords[:5]

    # Top 5 tieba
    top_tb = tieba[:5]

    # 梗百科 top 5
    top_meme_up = sorted(meme_ups, key=lambda x: x.get("view", 0), reverse=True)[:5]

    # 统计
    total_views = sum(v.get("view", 0) for v in popular)
    avg_views = round(total_views / max(len(popular), 1))
    max_view_item = max(popular, key=lambda x: x.get("view", 0)) if popular else None

    # 热榜
    hotlist_summary = {}
    if hotlist:
        for plat in ("weibo", "zhihu", "douyin", "bilibili", "xiaohongshu"):
            entries = hotlist.get(plat) or []
            hotlist_summary[plat] = len(entries)

    return {
        "date": format_date_display(date_str),
        "date_ymd": date_str,
        "total_videos": len(popular),
        "total_views": total_views,
        "avg_views": avg_views,
        "max_view": max_view_item["view"] if max_view_item else 0,
        "max_view_title": max_view_item["title"] if max_view_item else "",
        "top_popular": [
            {
                "title": v.get("title", ""),
                "tname": v.get("tname", ""),
                "view": v.get("view", 0),
                "H": compute_h(v.get("view", 0)),
                "zone": v.get("zone", ""),
                "url": v.get("url", "")
            }
            for v in top_pop
        ],
        "top_games": [
            {
                "title": v.get("title", ""),
                "tname": v.get("tname", ""),
                "view": v.get("view", 0),
                "url": v.get("url", "")
            }
            for v in game_videos
        ],
        "top_hotwords": [
            {"kw": hw.get("kw", ""), "heat": hw.get("heat", 0)}
            for hw in top_hw
        ],
        "top_tieba": [
            {"name": tb.get("name", ""), "url": tb.get("url", "")}
            for tb in top_tb
        ],
        "top_meme_up": [
            {"title": u.get("title", ""), "up": u.get("up", ""),
             "view": u.get("view", 0), "url": u.get("url", "")}
            for u in top_meme_up
        ],
        "hotlist_counts": hotlist_summary,
        "high_heat_count": sum(1 for v in popular if v.get("view", 0) >= HOT_THRESHOLD),
        "meme_zone_count": sum(1 for v in popular if v.get("zone") == "梗区")
    }

def format_date_display(ymd: str) -> str:
    try:
        dt = datetime.strptime(ymd, "%Y%m%d")
        return f"{dt.month}月{dt.day}日"
    except ValueError:
        return ymd

def format_period_label(d1: str, d2: str) -> str:
    try:
        a = datetime.strptime(d1, "%Y%m%d")
        b = datetime.strptime(d2, "%Y%m%d")
        return f"{a.month}/{a.day}–{b.month}/{b.day}"
    except ValueError:
        return f"{d1}–{d2}"

def build_week_summaries(daily_data: list[dict]) -> list[dict]:
    """滑窗式 7 天汇总，按日期排序滚动"""
    if len(daily_data) < 2:
        return []

    sorted_dates = sorted(daily_data, key=lambda d: d["date_ymd"])
    summaries = []

    for i in range(len(sorted_dates)):
        window = sorted_dates[max(0, i - 6):i + 1]
        start_date = window[0]["date_ymd"]
        end_date = window[-1]["date_ymd"]

        total_videos = sum(d["total_videos"] for d in window)
        total_views = sum(d["total_views"] for d in window)
        total_hotwords = sum(len(d["top_hotwords"]) for d in window)
        high_heat_total = sum(d["high_heat_count"] for d in window)
        meme_zone_total = sum(d["meme_zone_count"] for d in window)

        # 选周内最高的 8 条视频
        all_videos = []
        for d in window:
            for v in d["top_popular"]:
                all_videos.append({**v, "date": d["date"]})
        top_week_videos = sorted(all_videos, key=lambda x: x["view"], reverse=True)[:8]

        # 合并热词（去重，取前 8）
        seen_kw = set()
        all_hw = []
        for d in window:
            for hw in d["top_hotwords"]:
                if hw["kw"] not in seen_kw:
                    seen_kw.add(hw["kw"])
                    all_hw.append(hw)
        top_week_hw = all_hw[:8]

        # 按日期划分视频数量
        per_day_counts = {d["date"]: d["total_videos"] for d in window}
        per_day_high = {d["date"]: d["high_heat_count"] for d in window}

        # 趋势判断
        if i >= 1:
            prev_start = max(0, i - 7)
            prev = sorted_dates[prev_start:i]
            prev_week_videos = sum(d["total_videos"] for d in prev) if prev else 0
            trend = "↑" if total_videos > prev_week_videos else ("↓" if total_videos < prev_week_videos else "→")
        else:
            trend = "→"

        summaries.append({
            "period": format_period_label(start_date, end_date),
            "start_date": start_date,
            "end_date": end_date,
            "days_in_window": len(window),
            "total_videos": total_videos,
            "total_views": total_views,
            "total_hotwords": total_hotwords,
            "high_heat_count": high_heat_total,
            "meme_zone_count": meme_zone_total,
            "per_day_videos": per_day_counts,
            "per_day_high": per_day_high,
            "trend": trend,
            "top_videos": [
                {"title": v["title"], "view": v["view"], "H": v["H"],
                 "date": v["date"], "url": v.get("url", "")}
                for v in top_week_videos
            ],
            "top_hotwords": top_week_hw
        })

    return summaries

def main():
    # 扫描 meme 文件
    meme_files = glob.glob(os.path.join(COLLECTORS_DIR, "meme_202*.json"))
    # 过滤掉 test/run 变体
    meme_files = [f for f in meme_files 
                  if not re.search(r"(test|run|_tmp)", os.path.basename(f))]

    # 扫描 hotlist 文件
    hotlist_files = {
        extract_date(f): f
        for f in glob.glob(os.path.join(COLLECTORS_DIR, "public_hotlist_202*.json"))
        if extract_date(f)
    }

    daily_summaries = []
    for fpath in sorted(meme_files):
        date_str = extract_date(fpath)
        if not date_str:
            continue

        meme = load_meme(fpath)
        if not meme:
            print(f"⚠ 跳过无法解析: {os.path.basename(fpath)}")
            continue

        hotlist = None
        if date_str in hotlist_files:
            hotlist = load_hotlist(hotlist_files[date_str])

        summary = build_daily_summary(date_str, meme, hotlist)
        daily_summaries.append(summary)
        print(f"✓ {summary['date']} ({date_str}): "
              f"{summary['total_videos']}视频, {summary['total_views']:,}总播放")

    # 周汇总
    week_summaries = build_week_summaries(daily_summaries)
    print(f"\n✓ 生成 {len(week_summaries)} 个周期汇总 (7天滑窗)")

    # 查缺日期
    all_dates = set(d["date_ymd"] for d in daily_summaries)
    if daily_summaries:
        d0 = datetime.strptime(daily_summaries[0]["date_ymd"], "%Y%m%d")
        d1 = datetime.strptime(daily_summaries[-1]["date_ymd"], "%Y%m%d")
        expected = set()
        cur = d0
        while cur <= d1:
            expected.add(cur.strftime("%Y%m%d"))
            cur += timedelta(days=1)
        missing = sorted(expected - all_dates)
        if missing:
            print(f"⚠ 缺失日期: {', '.join(missing)}")

    output = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "daily_count": len(daily_summaries),
        "week_count": len(week_summaries),
        "daily": daily_summaries,
        "weekly": week_summaries
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
        json.dump(output, fh, ensure_ascii=False, indent=2)

    print(f"\n➜ 输出: {OUTPUT_FILE}")
    print(f"   共 {len(daily_summaries)} 天 + {len(week_summaries)} 周期")

if __name__ == "__main__":
    main()
