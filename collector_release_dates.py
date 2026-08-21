# -*- coding: utf-8 -*-
"""collector_release_dates.py — GamePulse 定档事件采集器（防漏报·手段1）

背景：诡秘之主 8/21 公测曾因"日历吃 events.json、词云吃 wordcloud_terms.json"
两条管线不同步而漏报。本采集器定向盯「必盯名单 watchlist.json」内的高量级游戏，
检索其「定档 / 公测 / 上线 / 发售」消息，产出结构化定档事件，供 daily_refresh 交叉核对。

设计原则：
  1. 只盯 watchlist.json 名单内游戏（不泛抓全量新闻，避免噪音）。
  2. 定向权威源：17173 / 游民星空 / 机核 / 游戏陀螺 / 白鲸出海 等已接入信源。
  3. 关键词门禁：标题须含游戏别名 + 定档信号词（定档/公测/上线/发售/开服/全平台 等）。
  4. 单源失败跳过，非阻断——缺失时由 daily_refresh 的量级告警闸兜底告警。

输出: 雷达站/collectors/release_dates_YYYYMMDD.json
      { "generated_at": ..., "items": [ {game, title, release_date, release_type,
                                          source_url, source_name, matched_alias} ] }

用法: python collector_release_dates.py
      环境变量 RELEASE_OUT_DIR 可覆盖输出目录（预览锁文件时应急）。
"""

import json, os, re, datetime, urllib.request, urllib.parse, html as _html

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.environ.get("RELEASE_OUT_DIR", os.path.join(BASE, "collectors"))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

# 定向权威源（已接入信源优先）——用站内搜索接口，query = 游戏别名 + 定档信号
# (name, search_url_template) —— {q} 会被 urlencode 后的查询词替换
SEARCH_SOURCES = [
    ("游戏陀螺", "https://www.youxituoluo.com/?s={q}"),
    ("游民星空", "https://so.gamersky.com/allnews.asp?keyword={q}"),
    ("机核", "https://www.gcores.com/search?keyword={q}"),
    ("17173", "https://search.17173.com/game?keyword={q}"),
    ("白鲸出海", "https://www.baijing.cn/search?keyword={q}"),
]

# 定档信号词（标题命中才算定档事件，避免"传闻""猜测"误报）
RELEASE_SIGNALS = ["定档", "公测", "正式上线", "全平台上线", "开服", "发售", "发布日期", "上线日期", "开测", "不删档", "公测时间"]


def _fetch(url: str, timeout: int = 12) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    # 常见中文编码尝试
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "ignore")


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s)


def _extract_links(page_html: str, base_hint: str = "") -> list[tuple[str, str]]:
    """从搜索结果页提取 (url, title) 列表。宽松匹配，尽量兼容多种搜索页结构。"""
    links = []
    for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page_html, re.S):
        url = m.group(1)
        title = _html.unescape(_strip_tags(m.group(2)))
        title = re.sub(r"\s+", " ", title).strip()
        if not title or len(title) < 6:
            continue
        if url.startswith("//"):
            url = "https:" + url
        if not url.startswith("http"):
            continue
        links.append((url, title))
    return links


def _find_release(game: str, aliases: list[str]) -> dict | None:
    """对一个游戏做定向检索，返回命中定档信号的条目（或 None）。"""
    # 用别名逐个检索（别名越短越精确，优先用正名）
    for alias in [game] + [a for a in aliases if a != game]:
        for sname, tmpl in SEARCH_SOURCES:
            q = urllib.parse.quote(f"{alias} 定档 公测 上线")
            try:
                page = _fetch(tmpl.format(q=q))
            except Exception:
                continue
            for url, title in _extract_links(page):
                # 标题须同时命中别名 + 定档信号词
                if alias not in title:
                    continue
                if not any(sig in title for sig in RELEASE_SIGNALS):
                    continue
                # 提取日期（若有）
                date_m = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", title)
                release_date = None
                if date_m:
                    release_date = f"{date_m.group(1)}-{int(date_m.group(2)):02d}-{int(date_m.group(3)):02d}"
                # 定档类型
                rtype = None
                for sig in ("公测", "不删档", "正式上线", "全平台上线"):
                    if sig in title:
                        rtype = "公测"
                        break
                if not rtype and "发售" in title:
                    rtype = "发售"
                if not rtype:
                    rtype = "上线"
                return {
                    "game": game,
                    "title": title,
                    "release_date": release_date,
                    "release_type": rtype,
                    "source_url": url,
                    "source_name": sname,
                    "matched_alias": alias,
                }
    return None


def main():
    wl_path = os.path.join(BASE, "watchlist.json")
    if not os.path.exists(wl_path):
        print("状态: {'error': 'watchlist.json 不存在'}")
        return

    wl = json.load(open(wl_path, encoding="utf-8"))
    items = wl.get("watchlist", [])

    found = []
    checked = []
    seen = set()  # 按 (game, release_date) 去重

    # 第一优先：watchlist 里已联网核实定档（verified=true + release_date）的条目，
    # 直接作为最可靠的定档事件产出（这是手段3「联网核实」固化的权威数据）。
    for it in items:
        game = it.get("game", "")
        checked.append(game)
        if it.get("verified") and it.get("release_date"):
            key = (game, it["release_date"])
            if key in seen:
                continue
            seen.add(key)
            found.append({
                "game": game,
                "title": it.get("release_note", f"{game} 定档 {it['release_date']} {it.get('release_type','')}"),
                "release_date": it["release_date"],
                "release_type": it.get("release_type"),
                "source_url": it.get("source_url", ""),
                "source_name": it.get("source_name", ""),
                "official_url": it.get("official_url", ""),
                "matched_alias": game,
                "verified": True,
            })
            print(f"  [{game}] ✅ 已核实定档(联网固化): {it['release_date']} {it.get('release_type','')}")

    # 第二优先：站内定向检索，发现 watchlist 未核实的增量定档消息。
    for it in items:
        game = it.get("game", "")
        aliases = it.get("aliases", [game])
        if it.get("verified") and it.get("release_date"):
            continue  # 已核实的不重复搜
        try:
            hit = _find_release(game, aliases)
        except Exception as e:
            print(f"  [{game}] 检索异常: {repr(e)[:100]}")
            continue
        if hit:
            key = (game, hit.get("release_date"))
            if key in seen:
                continue
            seen.add(key)
            found.append(hit)
            print(f"  [{game}] ✅ 检索命中定档: {hit['title'][:40]}")

    today = datetime.date.today().strftime("%Y%m%d")
    out = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "checked": checked,
        "count": len(found),
        "items": found,
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"release_dates_{today}.json")
    json.dump(out, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"状态: {{'OK': {len(found)}, 'checked': {len(checked)}}}")
    print(f"输出: {out_path}")


if __name__ == "__main__":
    main()
