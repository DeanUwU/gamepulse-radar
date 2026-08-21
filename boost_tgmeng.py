# -*- coding: utf-8 -*-
"""boost_tgmeng.py — 糖果梦 AI 日报词条 → 站内热度体系融合引擎

职责：
  1. 从 tgmeng 日报中抽取概念词条 + 拆分为可搜索关键词
  2. 交叉匹配站内内容（6 路：B站热门/全网热榜/贴吧/行业情报/日历事件/行业新闻）
  3. 计算 boost 权重 + 去重
  4. 输出 tgmeng_boost_YYYYMMDD.json 供下游消费

规则：
  · 日报词条只作"趋势确认信号"，不产生外部链接
  · 匹配规则：exact(完整包含concept) > partial(含2+关键词) > token(含1关键词)
  · 去重：同视频多概念命中 → 取最高分；同概念多视频命中 → 各计
  · 冲突：站内数据为主，日报仅作信号加成，不篡改站内内容
  · 权重：concept 位置越前权重越高；boost_factor ≤ 0.15
  · 匹配源分层：B站热门(0.15) > 行业新闻(0.14) > 日历事件(0.13) > 全网热榜(0.10)

输入: collectors/tgmeng_daily_YYYYMMDD.json, collectors/meme_YYYYMMDD.json,
      events.json, inbox/sources_curated.json
输出: collectors/tgmeng_boost_YYYYMMDD.json
"""

import json, os, re, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
COLLECTORS = os.path.join(BASE, "collectors")
ENV = os.environ


# ========== ① 关键词映射表（可扩展） ==========
# 每个 concept 映射到一组搜索关键词（中/英/缩写），用于在站内 content title 中匹配
KEYWORD_MAP = {
    "GTA6":         ["gta6", "gta 6", "gta", "侠盗猎车手", "侠盗猎车", "grand theft auto"],
    "ChinaJoy":     ["chinajoy", "cj", "中国国际数码互动娱乐", "chinajoy 2026", "cj 2026",
                     "chinajoy 2025"],
    "主机":          ["ps5", "xbox", "switch", "playstation", "nintendo", "console",
                     "主机游戏", "主机", "任天堂"],
    "国产游戏":      ["国产游戏", "国产单机", "国产", "国产手游", "国风", "国创"],
    "电竞":          ["电竞", "电子竞技", "esports", "lpl", "lck", "kpl", "战队",
                     "无畏契约", "valorant", "league of legends"],
    "发行":          ["发售", "上线", "公测", "首发", "定档", "预购", "发行"],
    "AI":           ["ai", "人工智能", "aigc", "大模型", "gpt", "chatgpt"],
    "出海":          ["出海", "海外", "全球化", "global", "国际版"],
    "二次元":        ["二次元", "动画", "anime", "漫画", "cos", "coser"],
    "开放世界":      ["开放世界", "open world", "沙盒", "sandbox"],
    "射击":          ["射击", "fps", "tps", "shooter", "枪战", "cod", "call of duty"],
    "卡牌":          ["卡牌", "tcg", "ccg", "抽卡", "gacha"],
    "MMO":          ["mmo", "mmorpg", "大型多人在线"],
    "独立游戏":      ["独立游戏", "indie", "独立制作"],
    "Steam":        ["steam", "蒸汽"],
    "Epic":         ["epic", "epic games"],
    "原神":          ["原神", "genshin", "genshin impact", "至冬"],
    "崩坏":          ["崩坏", "honkai", "星穹铁道", "zzz"],
    "三角洲":        ["三角洲", "delta force", "df"],
    "无畏契约":      ["无畏契约", "valorant", "瓦罗兰特", "源能行动", "瓦"],
    "明日方舟":      ["明日方舟", "arknights", "方舟"],
    "永劫无间":      ["永劫无间", "naraka"],
    "黑神话":        ["黑神话", "悟空", "black myth", "wukong"],
    "幻兽帕鲁":      ["幻兽帕鲁", "palworld", "帕鲁"],
    "最终幻想":      ["最终幻想", "final fantasy", "ff14", "ff7"],
    "战神":          ["战神", "god of war", "gow"],
    "赛博朋克":      ["赛博朋克", "cyberpunk", "2077"],
    "暗区突围":      ["暗区突围", "暗区", "arena breakout"],
    "和平精英":      ["和平精英", "pubg mobile", "吃鸡"],
    "王者荣耀":      ["王者荣耀", "honor of kings", "hok"],
    "英雄联盟":      ["英雄联盟", "league of legends", "lol"],
    "DNF":          ["dnf", "地下城与勇士", "dungeon fighter"],
    "CS":           ["cs", "csgo", "cs2", "counter strike"],
    "我的世界":      ["我的世界", "minecraft", "mc"],
    "DOTA":         ["dota", "dota2"],
    "APEX":         ["apex", "apex legends"],
    "PUBG":         ["pubg", "绝地求生", "battlegrounds"],
    "守望���锋":      ["守望先锋", "overwatch", "ow"],
    "WOW":          ["wow", "魔兽世界", "world of warcraft"],
    "FIFA":         ["fifa", "ea sports fc", "足球"],
    "NBA":          ["nba", "nba2k", "篮球"],
    "任天堂":        ["任天堂", "nintendo", "switch 2", "ns2"],
    "索尼":          ["索尼", "sony", "playstation", "ps5", "ps6"],
    "微软":          ["微软", "microsoft", "xbox", "game pass"],
    "育碧":          ["育碧", "ubisoft"],
    "EA":           ["ea", "electronic arts"],
    "暴雪":          ["暴雪", "blizzard"],
    "腾讯":          ["腾讯", "tencent", "光子", "天美", "魔方"],
    "网易":          ["网易", "netease", "雷火"],
    "米哈游":        ["米哈游", "mihoyo", "hoyoverse"],
    "鹰角":          ["鹰角", "hypergryph"],
    "游戏科学":      ["游戏科学", "game science"],
}

# 话题→具体游戏/内容的宽泛映射（兜底：概念太宏观时，用具体游戏名匹配站内内容）
CONCEPT_TO_GAMES = {
    "GTA6发行":        ["gta", "gta6", "侠盗猎车", "grand theft auto", "rockstar"],
    "ChinaJoy":       ["chinajoy", "cj", "cos", "展台"],
    "主机转型":        ["ps5", "xbox", "switch", "主机", "console", "nintendo",
                        "playstation", "game pass", "实体光盘", "数字版"],
    "国产游戏":        ["原神", "崩坏", "黑神话", "明日方舟", "永劫无间", "幻兽帕鲁",
                        "三角洲", "暗区突围", "和平精英", "王者荣耀", "逆天小游戏",
                        "闪暖", "闪耀暖暖", "以闪", "祖龙"],
    "电竞赛事":        ["lpl", "lck", "kpl", "edg", "blg", "wbg", "tes", "jdg", "t1",
                        "无畏契约", "valorant", "源能行动", "瓦"],
    "硬件涨价":        ["显卡", "rtx", "nvidia", "amd", "cpu", "gpu", "涨价",
                        "ps5 pro", "pro"],
    "数字发行":        ["数字版", "实体", "光盘", "digital", "steam", "epic"],
}


def tokenize_concept(concept: str) -> list[str]:
    """将一个 concept 拆分为一组搜索关键词（分层）

    层 1: 精确关键词 — 从 KEYWORD_MAP 查表（中/英/缩写）
    层 2: 游戏级兜底 — 从 CONCEPT_TO_GAMES 查具体游戏名
    层 3: n-gram 切词 — 从 concept 原文切 3-4 字词组（去停用词）
    结果按长度降序排列（长词优先，精确度更高）
    """
    tokens = []

    # 1. 完整 concept（精确匹配用）
    tokens.append(concept.lower())

    # 2. KEYWORD_MAP 查表
    for key, kws in KEYWORD_MAP.items():
        if key.lower() in concept.lower() or any(kw in concept.lower() for kw in kws):
            tokens.extend(kws)

    # 3. CONCEPT_TO_GAMES 兜底（宏观话题 → 具体游戏名）
    for topic, games in CONCEPT_TO_GAMES.items():
        if topic.lower() in concept.lower():
            tokens.extend(games)

    # 4. 按空格/标点拆词
    parts = re.split(r'[\s\-/·｜,，、]+', concept)
    for p in parts:
        p = p.strip().lower()
        if p and p not in tokens and len(p) >= 2:
            tokens.append(p)

    # 5. 中文 n-gram（仅 3-4 字词组，2 字太短噪声大）
    chinese = re.findall(r'[\u4e00-\u9fff]+', concept)
    for ch in chinese:
        if len(ch) <= 2:
            continue
        for n in range(3, min(5, len(ch) + 1)):
            for i in range(len(ch) - n + 1):
                gram = ch[i:i + n]
                if gram not in tokens and gram not in CHINESE_STOP_TOKENS:
                    tokens.append(gram)

    # 去重 + 排序（长的优先，精确匹配更可靠）
    tokens = sorted(set(t for t in tokens if len(t) >= 2), key=lambda x: -len(x))
    return tokens


# 中文通用词黑名单：太短/太泛的词不参与匹配（避免假阳性）
CHINESE_STOP_TOKENS = {
    "游戏", "数字", "创新", "转型", "发行", "赛事", "产业", "行业",
    "技术", "平台", "市场", "内容", "产品", "品牌", "服务", "系统",
    "版本", "更新", "新增", "优化", "升级", "上线", "首秀", "曝光",
    "数字", "化", "性", "国产", "2026", "2025",
}


def match_text(text: str, keywords: list[str]) -> tuple[str, float]:
    """在文本中匹配关键词，返回 (匹配类型, 分数)

    匹配类型:
      exact   = 文本完整包含 concept（原词）            → 1.0
      strong  = 文本含 1+ 长关键词(≥4字)或英文/专有名词  → 0.8
      partial = 文本含 2+ 有效关键词                     → 0.6
      weak    = 文本含 1 有效关键词(≥3字，非stop词)      → 0.35
      none    = 无有效匹配                               → 0.0

    有效关键词规则:
      · 英文/数字词：≥3 字符即可
      · 中文词：≥3 字，且不在 CHINESE_STOP_TOKENS 中
    """
    text_lower = text.lower()

    def is_valid(kw: str) -> bool:
        """关键词是否有效（非噪声）"""
        kw = kw.strip().lower()
        if len(kw) < 2:
            return False
        # 英文/数字词：≥3 字符
        if re.search(r'[a-z0-9]', kw):
            return len(kw) >= 3
        # 中文词：≥3 字 + 不在停用词表
        if len(kw) < 3:
            return False
        if kw in CHINESE_STOP_TOKENS:
            return False
        return True

    # 原词匹配（第一个 keyword = 完整的 concept 原文）
    concept_original = keywords[0] if keywords else ""
    if concept_original and len(concept_original) >= 3 and concept_original in text_lower:
        return ("exact", 1.0)

    # 收集有效命中
    all_hits = [kw for kw in keywords if kw.lower() in text_lower]
    valid_hits = [kw for kw in all_hits if is_valid(kw)]

    if not valid_hits:
        # 兜底：即使只有短词命中，若命中 ≥2 个依然给弱分
        if len(all_hits) >= 2:
            return ("partial", 0.6)
        return ("none", 0.0)

    # 检查是否有强关键词（长中文词 ≥4 字，或英文专有名词 ≥5 字符）
    has_strong = any(
        (len(kw) >= 4 and not re.search(r'[a-z0-9]', kw)) or
        (re.search(r'[a-z0-9]', kw) and len(kw) >= 5)
        for kw in valid_hits
    )

    if has_strong:
        return ("strong", 0.8)

    if len(valid_hits) >= 2:
        return ("partial", 0.6)

    return ("weak", 0.35)


# ========== ② 站内内容交叉匹配 ==========

def load_meme_data(today_str: str) -> dict | None:
    """加载当天 meme 采集数据"""
    meme_file = os.path.join(COLLECTORS, f"meme_{today_str.replace('-', '')}.json")
    if not os.path.exists(meme_file):
        # 尝试 YYYYMMDD 格式
        for f in sorted(os.listdir(COLLECTORS), reverse=True):
            if f.startswith("meme_202") and f.endswith(".json"):
                meme_file = os.path.join(COLLECTORS, f)
                break
    if os.path.exists(meme_file):
        return json.load(open(meme_file, encoding="utf-8"))
    return None


def load_hotlist_data(today_str: str) -> dict | None:
    """加载当天热榜数据"""
    hl_file = os.path.join(COLLECTORS, f"public_hotlist_{today_str.replace('-', '')}.json")
    if not os.path.exists(hl_file):
        for f in sorted(os.listdir(COLLECTORS), reverse=True):
            if f.startswith("public_hotlist_202") and f.endswith(".json"):
                hl_file = os.path.join(COLLECTORS, f)
                break
    if os.path.exists(hl_file):
        return json.load(open(hl_file, encoding="utf-8"))
    return None


def load_events_data() -> list[dict] | None:
    """加载日历事件数据（events.json）"""
    ev_path = os.path.join(BASE, "events.json")
    if not os.path.exists(ev_path):
        return None
    data = json.load(open(ev_path, encoding="utf-8"))
    events = data.get("events", [])
    if not events:
        return None
    # 提取匹配所需的字段：title + source_name 合成搜索文本，source_url 为链接
    items = []
    for ev in events:
        title = ev.get("title", "")
        source_name = ev.get("source_name", "")
        source_url = ev.get("source_url", "")
        if not title or not source_url:
            continue
        items.append({
            "title": title,
            "source_name": source_name,
            "url": source_url,
            "search_text": f"{title} {source_name}",
        })
    return items


def load_inbox_data() -> list[dict] | None:
    """加载行业新闻数据（inbox/sources_curated.json）"""
    ib_path = os.path.join(BASE, "inbox", "sources_curated.json")
    if not os.path.exists(ib_path):
        return None
    data = json.load(open(ib_path, encoding="utf-8"))
    items_raw = data.get("items", [])
    if not items_raw:
        return None
    items = []
    for it in items_raw:
        title = it.get("title", "")
        url = it.get("url", "")
        src_name = it.get("src_name", "")
        if not title or not url:
            continue
        items.append({
            "title": title,
            "url": url,
            "src_name": src_name,
            "search_text": f"{title} {src_name}",
        })
    return items


def match_against_site(concepts: list[dict], meme: dict | None,
                       hotlist: dict | None, events: list[dict] | None = None,
                       inbox: list[dict] | None = None) -> list[dict]:
    """将 concept 列表与站内内容交叉匹配（6 路：B站热门/全网热榜/贴吧/日历事件/行业新闻）"""
    matched = []
    seen = {}  # key: (url) → 去重

    for ci, c in enumerate(concepts):
        concept_name = c["concept"]
        keywords = c["keywords"]
        # concept 权重：越靠前越重要（指数衰减）
        c_weight = max(0.4, 1.0 - ci * 0.12)

        # --- A. 匹配 B站热门 ---
        if meme and "popular" in meme:
            for v in meme["popular"]:
                title = (v.get("title") or "")
                url = v.get("url", "")
                if not title or not url:
                    continue

                match_type, score = match_text(title, keywords)
                if score <= 0:
                    continue

                key = url
                if key in seen and seen[key][0] >= score:
                    continue
                seen[key] = (score, concept_name)

                matched.append({
                    "concept": concept_name,
                    "source": "bilibili",
                    "title": title,
                    "url": url,
                    "view": v.get("view", 0),
                    "tname": v.get("tname", ""),
                    "match_type": match_type,
                    "match_score": round(score, 2),
                    "boost_factor": round(score * c_weight * 0.15, 3),
                })

        # --- B. 匹配全网热榜 ---
        if hotlist:
            for platform_key in ("weibo", "zhihu", "douyin", "bilibili", "xiaohongshu"):
                items = hotlist.get(platform_key, [])
                if isinstance(items, list):
                    for item in items:
                        title = (item.get("title") or item.get("name") or "")
                        url = item.get("url", "")
                        if not title:
                            continue

                        match_type, score = match_text(title, keywords)
                        if score <= 0:
                            continue

                        key = url or f"{platform_key}:{title}"
                        if key in seen and seen[key][0] >= score:
                            continue
                        seen[key] = (score, concept_name)

                        matched.append({
                            "concept": concept_name,
                            "source": platform_key,
                            "title": title,
                            "url": url,
                            "view": item.get("heat", 0),
                            "match_type": match_type,
                            "match_score": round(score, 2),
                            "boost_factor": round(score * c_weight * 0.10, 3),
                        })

        # --- C. 匹配日历事件（events.json）---
        # 权重高于热榜、略低于 B站，因日历事件是人工策展的高质量信号
        if events:
            for ev in events:
                search_text = ev.get("search_text", "")
                url = ev.get("url", "")
                if not search_text or not url:
                    continue

                match_type, score = match_text(search_text, keywords)
                if score <= 0:
                    continue

                key = url
                if key in seen and seen[key][0] >= score:
                    continue
                seen[key] = (score, concept_name)

                matched.append({
                    "concept": concept_name,
                    "source": "events_calendar",
                    "title": ev.get("title", ""),
                    "url": url,
                    "view": 0,
                    "match_type": match_type,
                    "match_score": round(score, 2),
                    "boost_factor": round(score * c_weight * 0.13, 3),
                })

        # --- D. 匹配行业新闻（inbox/sources_curated.json）---
        # 行业新闻是最丰富的外部信号，权重高于热榜和日历事件
        if inbox:
            for item in inbox:
                search_text = item.get("search_text", "")
                url = item.get("url", "")
                if not search_text or not url:
                    continue

                match_type, score = match_text(search_text, keywords)
                if score <= 0:
                    continue

                key = url
                if key in seen and seen[key][0] >= score:
                    continue
                seen[key] = (score, concept_name)

                matched.append({
                    "concept": concept_name,
                    "source": item.get("src_name", "inbox"),
                    "title": item.get("title", ""),
                    "url": url,
                    "view": 0,
                    "match_type": match_type,
                    "match_score": round(score, 2),
                    "boost_factor": round(score * c_weight * 0.14, 3),
                })

    # 按 boost_factor 降序
    matched.sort(key=lambda x: x["boost_factor"], reverse=True)
    return matched


# ========== ③ 输出 ==========

def compute_boost_summary(matched: list[dict], concepts: list[dict]) -> dict:
    """生成 boost 摘要：按 concept 聚合 + 统计"""
    by_concept = {}
    for m in matched:
        c = m["concept"]
        if c not in by_concept:
            by_concept[c] = {"total": 0, "items": [], "max_boost": 0}
        by_concept[c]["total"] += 1
        by_concept[c]["items"].append(m)
        by_concept[c]["max_boost"] = max(by_concept[c]["max_boost"], m["boost_factor"])

    matched_concepts = [c["concept"] for c in concepts
                        if c["concept"] in by_concept]
    unmatched_concepts = [c["concept"] for c in concepts
                          if c["concept"] not in by_concept]

    return {
        "by_concept": by_concept,
        "matched_concepts": matched_concepts,
        "unmatched_concepts": unmatched_concepts,
        "total_matched_items": len(matched),
        "total_concepts": len(concepts),
    }


def main():
    today = datetime.date.today()
    today_iso = today.strftime("%Y-%m-%d")
    today_yyyymmdd = today.strftime("%Y%m%d")

    # 找到最新的 tgmeng 数据
    tg_files = sorted(
        [f for f in os.listdir(COLLECTORS) if f.startswith("tgmeng_daily_") and f.endswith(".json")],
        reverse=True)
    if not tg_files:
        print("⚠ 未找到 tgmeng_daily_*.json，跳过 boost")
        return

    tg_path = os.path.join(COLLECTORS, tg_files[0])
    tg_data = json.load(open(tg_path, encoding="utf-8"))
    entry = tg_data.get("entry") or {}

    if not entry or entry.get("error"):
        print("⚠ tgmeng 数据不可用（错误/空），跳过 boost")
        return

    # 提取 concepts
    concepts_raw = entry.get("tags") or entry.get("concepts") or []
    if not concepts_raw:
        print("⚠ tgmeng 日报无 concept 标签，跳过 boost")
        return

    # 构建 concept → keywords 映射
    concepts = []
    print(f"📊 日报概念 ({len(concepts_raw)}): {' · '.join(concepts_raw)}")
    for tag in concepts_raw:
        keywords = tokenize_concept(tag)
        concepts.append({
            "concept": tag,
            "keywords": keywords,
        })
        print(f"  → {tag}: {keywords[:6]}")

    # 加载站内数据（6 路）
    meme = load_meme_data(today_iso)
    hotlist = load_hotlist_data(today_iso)
    events = load_events_data()
    inbox = load_inbox_data()
    print(f"\n📡 站内数据: meme={'✓' if meme else '✗'}  hotlist={'✓' if hotlist else '✗'}"
          f"  events={'✓' if events else '✗'}({len(events) if events else 0})"
          f"  inbox={'✓' if inbox else '✗'}({len(inbox) if inbox else 0})")

    # 交叉匹配
    matched = match_against_site(concepts, meme, hotlist, events, inbox)
    print(f"\n🎯 匹配结果: {len(matched)} 条命中")

    for m in matched[:10]:
        print(f"  [{m['match_type']:7s} | +{m['boost_factor']:.3f}] "
              f"{m['concept']} ← {m['title'][:50]}")

    # 生成摘要
    summary = compute_boost_summary(matched, concepts)
    print(f"\n📋 摘要: {summary['total_matched_items']} 条命中 "
          f"({len(summary['matched_concepts'])}/{summary['total_concepts']} 概念确认)")
    if summary["unmatched_concepts"]:
        print(f"  ⚠ 未匹配概念: {' · '.join(summary['unmatched_concepts'])}")

    # 持久化: 只保留 boost_factor >= 0.03 的命中（过滤噪音）
    significant = [m for m in matched if m["boost_factor"] >= 0.03]
    output = {
        "generated": today_iso,
        "source": tg_files[0],
        "concepts": [{"concept": c["concept"], "keywords": c["keywords"]}
                      for c in concepts],
        "matched_items": significant,
        "summary": summary,
    }

    out_path = os.path.join(COLLECTORS, f"tgmeng_boost_{today_yyyymmdd}.json")
    # 文件可能被 IDE 预览锁定，fallback 写 TEMP
    try:
        json.dump(output, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    except PermissionError:
        alt = os.path.join(os.environ.get("TEMP", COLLECTORS),
                          f"tgmeng_boost_{today_yyyymmdd}.json")
        json.dump(output, open(alt, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        out_path = alt
    print(f"\n✅ 输出: {out_path} ({len(significant)} 条有效命中)")

    # 返回摘要用于管道日志
    return summary


if __name__ == "__main__":
    main()
