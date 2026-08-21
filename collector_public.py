# -*- coding: utf-8 -*-
"""collector_public.py — GamePulse 全网热榜交叉采集（常驻工具，不要删）

从五平台热榜采集游戏/ACG 相关内容，作为梗雷达的「第七路」信号源：
  1. 微博热搜（官方 API）
  2. 知乎热榜（洛樱云 API）
  3. 抖音热榜（洛樱云 API）
  4. B站热搜（洛樱云 API）
  5. 小红书热榜（60s API）

输出: 雷达站/collectors/public_hotlist_YYYYMMDD.json
用法: python collector_public.py
单路失败不阻断，输出只包含成功采到的平台。
"""
import json, os, time, datetime, re
import urllib.request, urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.environ.get("PUBLIC_OUT_DIR", os.path.join(BASE, "collectors"))
os.makedirs(OUT_DIR, exist_ok=True)

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
    "Accept": "application/json, text/html",
}

# ====== 游戏/ACG 相关关键词（用于热榜过滤） ======
GAME_KEYWORDS = [
    # 热门游戏名（含简称/别称）
    "原神", "崩铁", "崩坏", "星穹铁道", "绝区零", "鸣潮",
    "王者荣耀", "王者", "和平精英", "吃鸡", "英雄联盟", "LOL",
    "永劫无间", "永劫", "第五人格", "蛋仔派对", "蛋仔",
    "明日方舟", "终末地", "恋与深空", "火影忍者", "火影",
    "暗区突围", "三角洲行动", "无畏契约", "瓦罗兰特", "瓦",
    "DNF", "地下城", "CS2", "CSGO", "DOTA2", "DOTA",
    "阴阳师", "金铲铲", "三国志", "晶核", "暗黑不朽",
    "炉石传说", "炉石", "魔兽世界", "魔兽", "WOW",
    "穿越火线", "CF", "梦幻西游", "梦幻", "逆水寒",
    "剑网3", "天涯明月刀", "天刀", "光遇",
    "崩坏3", "碧蓝航线", "碧蓝", "FGO", "命运冠位",
    "赛马娘", "蔚蓝档案", "BA", "妮姬", "NIKKE",
    "幻塔", "无限暖暖", "暖暖",
    # 热门新游/主机大作
    "黑神话", "黑悟空", "悟空", "GTA6", "GTA", "老头环",
    "塞尔达", "马里奥", "宝可梦", "最终幻想", "FF",
    "怪物猎人", "怪猎", "怪猎荒野", "MH", "生化危机",
    "艾尔登法环", "只狼", "巫师", "赛博朋克",
    # Steam/平台
    "Steam", "Epic", "Xbox", "PlayStation", "PS5", "PS4",
    "Nintendo", "Switch", "NS",
    # 电竞/赛事
    "KPL", "LPL", "S赛", "TI", "Major", "电竞", "电子竞技",
    # 泛 ACG
    "二次元", "动漫", "新番", "cos", "Cosplay", "漫展",
    "B站", "bilibili", "鬼畜", "梗", "手书",
    # 游戏行业
    "游戏版号", "游戏审批", "未成年防沉迷",
]

# 编译正则（一次）
_GAME_PAT = re.compile("|".join(re.escape(k) for k in GAME_KEYWORDS), re.IGNORECASE)

def _match_game(text):
    """判断热榜条目标题是否包含游戏/ACG 关键词"""
    return bool(_GAME_PAT.search(text))

def get(url, referer="", retries=2):
    """HTTP GET → JSON，失败抛出异常"""
    headers = dict(UA)
    if referer:
        headers["Referer"] = referer
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return json.loads(raw)
        except Exception:
            if i == retries:
                raise
            time.sleep(1.5)

# ====== 五平台采集函数 ======

def collect_weibo():
    """微博热搜 → 筛游戏/ACG"""
    j = get("https://weibo.com/ajax/statuses/hot_band",
            referer="https://weibo.com")
    items = (j.get("data") or {}).get("band_list") or []
    out = []
    for idx, item in enumerate(items):
        word = (item.get("word") or item.get("topic_name") or "").strip()
        if not word:
            continue
        if not _match_game(word):
            continue
        out.append({
            "rank": idx + 1,
            "topic": word,
            "hotValue": item.get("raw_hot") or item.get("num") or 0,
            "label": item.get("label_name") or "",
            "url": f"https://s.weibo.com/weibo?q={urllib.parse.quote(word)}",
        })
    return out


def _luoying_fetch(platform, referer=""):
    """洛樱云 API 通用拉取（知乎/抖音/B站）"""
    j = get(f"https://apiserver.alcex.cn/daily-hot/{platform}",
            referer=referer)
    if j.get("code") != 200 or not j.get("data"):
        raise RuntimeError(f"洛樱云 {platform} 返回异常: code={j.get('code')}")
    items = j["data"]
    out = []
    for idx, item in enumerate(items):
        title = (item.get("title") or "").strip()
        if not title:
            continue
        if not _match_game(title):
            continue
        out.append({
            "rank": idx + 1,
            "topic": title,
            "hotValue": item.get("hot") or 0,
            "label": item.get("desc") or "",
            "url": item.get("url") or "",
        })
    return out


def collect_zhihu():
    return _luoying_fetch("zhihu")


def collect_douyin():
    return _luoying_fetch("douyin")


def collect_bilibili_hot():
    return _luoying_fetch("bilibili", referer="https://www.bilibili.com")


def collect_xiaohongshu():
    """小红书热榜（60s API）"""
    j = get("https://60s.viki.moe/v2/rednote")
    if j.get("code") != 200 or not j.get("data"):
        raise RuntimeError(f"小红书 API 返回异常: code={j.get('code')}")
    items = j["data"]
    out = []
    for item in items:
        title = (item.get("title") or "").strip()
        if not title:
            continue
        if not _match_game(title):
            continue
        out.append({
            "rank": item.get("rank") or 0,
            "topic": title,
            "hotValue": item.get("score") or 0,
            "label": item.get("word_type") or "",
            "url": item.get("link") or "",
        })
    return out


# ====== 主入口 ======

def main():
    today = datetime.date.today().strftime("%Y%m%d")
    result, status = {}, {}

    collectors = [
        ("weibo", collect_weibo),
        ("zhihu", collect_zhihu),
        ("douyin", collect_douyin),
        ("bilibili", collect_bilibili_hot),
        ("xiaohongshu", collect_xiaohongshu),
    ]

    for key, fn in collectors:
        try:
            result[key] = fn()
            status[key] = f"OK({len(result[key])})"
            print(f"  [{key}] 采集成功: {len(result[key])} 条游戏/ACG相关")
        except Exception as e:
            result[key] = []
            status[key] = f"FAIL {e}"
            print(f"  [{key}] 采集失败: {e}")

    result["timestamp"] = datetime.datetime.now().isoformat()

    # 输出 JSON
    jpath = os.path.join(OUT_DIR, f"public_hotlist_{today}.json")
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    # 打印摘要
    total = sum(len(v) for k, v in result.items() if k != "timestamp")
    ok_count = sum(1 for v in status.values() if v.startswith("OK"))
    print(f"\n全网热榜交叉采集完成:")
    print(f"  通过: {ok_count}/5 平台")
    print(f"  总计: {total} 条游戏/ACG相关热榜")
    print(f"  输出: {jpath}")
    print(f"  状态: {status}")

    return result, status


if __name__ == "__main__":
    main()
