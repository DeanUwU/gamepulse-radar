# -*- coding: utf-8 -*-
"""collector_tgmeng.py — GamePulse 糖果梦 AI 游戏日报��集器
   
   https://tgmeng.com/daily/game
   每日 AI 生成游戏行业综述，页面内嵌结构化 JSON 数据。
   字段: id, title, summary, publishDate, concepts(标签), aiPlatform, aiModel, 
         tokenUsage, generatedAt, readMinutes, heat, markdown, coverImage

   输出: 雷达站/collectors/tgmeng_daily_YYYYMMDD.json  （当日条目 + 全量列表）
        雷达站/collectors/tgmeng_archive.json          （按日期去重归档）

   用法: python collector_tgmeng.py
   单日采集，非阻断——失败时输出 error 标记。
"""

import json, os, re, datetime, urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.environ.get("TGMENG_OUT_DIR", os.path.join(BASE, "collectors"))
ARCHIVE_FILE = os.path.join(OUT_DIR, "tgmeng_archive.json")

SOURCE_URL = "https://tgmeng.com/daily/game"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def extract_json_from_html(html: str) -> list[dict] | None:
    """从 HTML 中提取内嵌的文章 JSON 数组
    
    页面在接近末尾处嵌入了一个完整的 JSON 数组:
      [{"id":...,"title":"...","summary":"...","publishDate":"...","concepts":[...]},...]
    """
    # 找开头的 [
    m = re.search(r'\[{"id":\d+,"title":"', html)
    if not m:
        return None

    start = m.start()
    # 手动找匹配的 ]（处理内嵌 JSON 字符串中的括号）
    depth = 1
    in_str = False
    escape = False
    i = start + 1
    while i < len(html) and depth > 0:
        c = html[i]
        if escape:
            escape = False
            i += 1
            continue
        if c == '\\':
            escape = True
        elif c == '"':
            in_str = not in_str
        elif not in_str:
            if c == '[':
                depth += 1
            elif c == ']':
                depth -= 1
        i += 1

    json_str = html[start:i]
    try:
        articles = json.loads(json_str)
        return articles if isinstance(articles, list) else None
    except json.JSONDecodeError:
        return None


def transform(articles: list[dict]) -> list[dict]:
    """将原始 JSON 转换为 GamePulse 标准格式"""
    result = []
    for a in articles:
        title = (a.get("title") or "").strip()
        summary = (a.get("summary") or "").strip()
        date = (a.get("publishDate") or "").strip()[:10]

        if not title or not date:
            continue

        result.append({
            "date": date,
            "title": title,
            "summary": summary,
            "tags": a.get("concepts") or [],
            "url": f"https://tgmeng.com/daily/game/{date}",
            "ai_platform": a.get("aiPlatform", ""),
            "ai_model": a.get("aiModel", ""),
            "token_usage": a.get("tokenUsage", 0),
            "generated_at": a.get("generatedAt", ""),
            "read_minutes": a.get("readMinutes", 0),
            "heat": a.get("heat", 0),
            "cover_image": a.get("coverImage", ""),
            "markdown": a.get("markdown", ""),
        })

    # 按日期倒序
    result.sort(key=lambda x: x["date"], reverse=True)
    return result


def fetch():
    """抓取并解析"""
    req = urllib.request.Request(SOURCE_URL, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    articles = extract_json_from_html(html)
    if not articles:
        raise ValueError("未找到内嵌 JSON 数据")

    return transform(articles)


def save(entries: list[dict]):
    """保存当日数据 + 更新归档"""
    os.makedirs(OUT_DIR, exist_ok=True)
    today = datetime.date.today().strftime("%Y%m%d")
    today_iso = datetime.date.today().strftime("%Y-%m-%d")

    # 找当日条目
    matched = None
    for e in entries:
        if e["date"] == today_iso:
            matched = e
            break
    if not matched and entries:
        matched = entries[0]

    daily_file = os.path.join(OUT_DIR, f"tgmeng_daily_{today}.json")
    output = {
        "source": SOURCE_URL,
        "fetched": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "entry": matched,
        "all_entries_count": len(entries),
        "all_entries": entries
    }
    with open(daily_file, "w", encoding="utf-8") as fh:
        json.dump(output, fh, ensure_ascii=False, indent=2)

    # 归档（按日期去重，保留 60 天）
    archive = {}
    if os.path.exists(ARCHIVE_FILE):
        try:
            archive = json.load(open(ARCHIVE_FILE, encoding="utf-8"))
        except Exception:
            archive = {}

    for e in entries:
        archive[e["date"]] = e

    sorted_keys = sorted(archive.keys(), reverse=True)[:60]
    archive = {k: archive[k] for k in sorted_keys}

    with open(ARCHIVE_FILE, "w", encoding="utf-8") as fh:
        json.dump(archive, fh, ensure_ascii=False, indent=2)

    return daily_file, matched


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"📡 采集 {SOURCE_URL} ...")

    try:
        entries = fetch()
    except Exception as e:
        print(f"  ❌ 抓取失败: {e}")
        today = datetime.date.today().strftime("%Y%m%d")
        daily_file = os.path.join(OUT_DIR, f"tgmeng_daily_{today}.json")
        output = {"source": SOURCE_URL, "error": str(e),
                  "fetched": datetime.datetime.now().isoformat()}
        # 用 Python 硬写避免 PermissionError
        try:
            with open(daily_file, "w", encoding="utf-8") as fh:
                json.dump(output, fh, ensure_ascii=False, indent=2)
        except PermissionError:
            alt = os.path.join(os.environ.get("TEMP", OUT_DIR),
                              f"tgmeng_daily_{today}.json")
            with open(alt, "w", encoding="utf-8") as fh:
                json.dump(output, fh, ensure_ascii=False, indent=2)
            print(f"  ⚠ 输出目录被锁定，写至: {alt}")
        return

    if not entries:
        print("  ⚠ 未提取到任何条目")
        return

    daily_file, matched = save(entries)

    if matched:
        print(f"  ✓ 当日: {matched['date']}  「{matched['title'][:50]}」")
        tags = matched.get("tags", [])
        if tags:
            print(f"    标签: {' · '.join(tags[:8])}")
        if matched.get("ai_platform"):
            print(f"    AI: {matched['ai_platform']} / {matched['ai_model']}  "
                  f"(tokens={matched.get('token_usage',0):,})")
        if matched.get("summary"):
            print(f"    摘要: {matched['summary'][:120]}...")
    else:
        print(f"  ⚠ 未匹配当日条目，最新: {entries[0]['date']}")

    print(f"  ✓ 输出: {daily_file}")
    print(f"  ✓ 归档: {ARCHIVE_FILE}  ({len(entries)} 条)")


if __name__ == "__main__":
    main()
