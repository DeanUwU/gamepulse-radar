# -*- coding: utf-8 -*-
"""boost_hotlist.py — 全网热榜 → 词云交叉加权

读当天的 public_hotlist.json（五平台热榜中游戏/ACG 相关条目）和
wordcloud_terms.json（Agent 提炼的词条），
对在热榜中出现的词条进行 heat 加权，生成增强版词条文件

用法: python boost_hotlist.py [--dry-run]
集成位置: daily_refresh.py 中 cross_words 之前
"""
import io, os, sys, json, re, datetime, argparse

BASE = os.path.dirname(os.path.abspath(__file__))
TODAY = datetime.date.today()
TERMS_PATH = os.environ.get("WC_TERMS_PATH", os.path.join(BASE, "wordcloud_terms.json"))
HL_DIR = os.environ.get("HL_COLLECTORS", os.path.join(BASE, "collectors"))

# ====== 热榜加权参数 ======
BOOST_CAP = 25           # 单条最大加分
BOOST_PER_PLATFORM = 6   # 每多一个平台出现，额外加分
BOOST_SINGLE_APPEAR = 5  # 单平台出现的基础加分


def load_hotlist():
    """读当日全网热榜"""
    hl_path = os.path.join(HL_DIR,
                           f"public_hotlist_{TODAY.strftime('%Y%m%d')}.json")
    if not os.path.exists(hl_path):
        print(f"  [热榜加权] 未找到当日热榜文件 {hl_path}，跳过加权")
        return None
    with io.open(hl_path, encoding="utf-8") as f:
        return json.load(f)


def match_score(term, hotlist):
    """计算词条在热榜中的匹配得分"""
    tl = term.lower()
    score = 0
    platforms = 0
    matched_topics = []

    for plat in ("weibo", "zhihu", "douyin", "bilibili", "xiaohongshu"):
        items = hotlist.get(plat, [])
        if not items:
            continue
        plat_match = False
        for item in items:
            topic = (item.get("topic") or "").lower()
            if not topic:
                continue
            # 子串匹配：词条是话题的子串 或 话题是词条的子串
            if tl in topic or topic in tl:
                plat_match = True
                matched_topics.append(f"{plat}:{item.get('topic')}")
                # 按排名靠前加分：rank 越小越靠前，加分越多
                rank = item.get("rank", 50)
                if rank <= 5:
                    score += 5
                elif rank <= 10:
                    score += 3
                elif rank <= 20:
                    score += 2
                else:
                    score += 1
                break  # 同一平台只匹配一次（取最佳匹配）
        if plat_match:
            platforms += 1

    if platforms == 0:
        return 0, 0, []

    # 多平台加权
    score += BOOST_SINGLE_APPEAR + (platforms - 1) * BOOST_PER_PLATFORM
    return min(score, BOOST_CAP), platforms, matched_topics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="仅打印加权结果，不写回文件")
    args = parser.parse_args()

    if not os.path.exists(TERMS_PATH):
        print(f"ERROR: 未找到 {TERMS_PATH}，请先由 Agent 产出词条")
        sys.exit(1)

    hotlist = load_hotlist()
    if hotlist is None:
        sys.exit(0)

    doc = json.load(io.open(TERMS_PATH, encoding="utf-8"))
    terms = doc.get("terms", [])

    boosted = 0
    total_boost = 0
    for t in terms:
        term = (t.get("term") or "").strip()
        if not term:
            continue
        score, plats, matched = match_score(term, hotlist)
        if score > 0:
            old_heat = int(t.get("heat", 50))
            new_heat = min(old_heat + score, 100)
            t["heat"] = new_heat
            t["hotlist_boost"] = score
            t["hotlist_platforms"] = plats
            t["hotlist_sources"] = matched[:5]
            boosted += 1
            total_boost += score
            print(f"  +{score:>2}  [{term}] → heat {old_heat}→{new_heat}（{plats} 平台: {', '.join(matched[:3])}）")

    if boosted == 0:
        print("  [热榜加权] 当日热榜中无词云词条匹配，无需加权")
        return

    print(f"\n  加权完成: {boosted}/{len(terms)} 词条获加权 · 合计 +{total_boost} 分")

    if args.dry_run:
        print("  (dry-run 模式，未写回文件)")
        return

    # 写回增强版词条文件
    with io.open(TERMS_PATH, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print(f"  已写回 {TERMS_PATH}")


if __name__ == "__main__":
    main()
