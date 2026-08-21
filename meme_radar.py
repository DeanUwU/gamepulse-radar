# -*- coding: utf-8 -*-
"""meme_radar.py — GamePulse 梗雷达（常驻工具，不要删）

给网站供给「类似 梗外之音 系列」的梗内容信号，六路采集：
  1. B站热门(popular)       裸跑 · 筛鬼畜/搞笑/翻唱等梗高发区 + 高热游戏二创
  2. B站热搜(search/square) 裸跑 · 突现怪词/人名 = 新梗前兆
  3. 每周必看(wbi签名)       官方盖章的破圈内容, 梗浓度高; -352 时自动跳过
  4. 梗解读UP合集追更+热评   盯"梗外之音"等合集最新集, 拉高赞评论挖梗词条
  5. 梗百科类账号追更        用 B站视频搜索(按发布时间)追更 梗指南/网梗指南 等
  6. 贴吧热议榜              HTML 解析 hottopic 榜单, 补中文社区梗土壤

输出: 雷达站/collectors/meme_YYYYMMDD.md (+ .json)
用法: python meme_radar.py
单路失败不阻断。周一/周四自动化会调用本脚本, 结果供 daily.html hot 板块选材。
"""
import json, os, re, time, datetime, hashlib
import urllib.request, urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.environ.get("MEME_OUT_DIR", os.path.join(BASE, "collectors"))
os.makedirs(OUT_DIR, exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
      "Referer": "https://www.bilibili.com/"}
UA_TIEBA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
            "Referer": "https://tieba.baidu.com/"}

# 梗高发分区
MEME_TNAMES = ("鬼畜", "搞笑", "翻唱", "整活", "配音", "特摄")
# 追更的梗解读合集: (说明, 合集内任一视频aid用于定位)
MEME_SERIES = [
    ("梗外之音(叶小十同学)", 115984031617560),
]
# 梗百科类账号：用视频搜索(按发布时间)追更其最新梗解读
# 覆盖：游戏/电竞/动漫/体育/泛互联网 各圈层梗百科 + 热门梗解读UP
MEME_UP_KEYWORDS = [
    # 泛互联网梗（核心）
    "网梗指南", "梗指南", "梗百科", "梗外之音", "梗知识",
    # 电竞/游戏圈
    "KPL梗指南", "游戏梗指南", "LOL梗指南", "瓦罗兰特梗",
    # 体育圈
    "足球梗指南", "NBA梗",
    # 动漫/二次元
    "动漫梗指南", "二次元梗", "新番梗",
    # 热门梗解读UP（持续产出梗内容的创作者）
    "互联网冲浪笔记", "电子榨菜", "每日一梗", "网络热梗科普",
    "整活bot", "梗图bot", "热梗速递",
]

# ---------- 时效红线 ----------
# 榜单类采集(popular/热搜/每周必看/贴吧)天然只出当期内容，无需门禁；
# 搜索类(梗百科账号)与追更类(合集最新集)不是榜单，必须用发布时间兜底，
# 否则停更账号/停更合集会把几个月前的老视频当"最新"送进词云。
MEME_UP_MAX_AGE_DAYS = 14      # 梗百科搜索结果最大允许天数
SERIES_MAX_AGE_DAYS = 14       # 合集最新一集最大允许天数
MEME_UP_MAX_PAGE = 3           # 冷门关键词最多翻几页找新内容

def _age_days(ts):
    """秒级时间戳 → 距今天数；ts 缺失返回 None（视为不可判定）"""
    try:
        ts = int(ts or 0)
    except Exception:
        return None
    if ts <= 0:
        return None
    return (time.time() - ts) / 86400.0

def get(url, retries=2):
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            return json.load(urllib.request.urlopen(req, timeout=15))
        except Exception:
            if i == retries:
                raise
            time.sleep(1.5)

# ---------- wbi 签名 (每周必看 / 搜索等受保护接口) ----------
_MIXIN = [46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,14,39,
          12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,25,54,21,56,59,6,63,
          57,62,11,36,20,34,44,52]

def wbi_keys():
    j = get("https://api.bilibili.com/x/web-interface/nav")
    img = j["data"]["wbi_img"]["img_url"].rsplit("/", 1)[1].split(".")[0]
    sub = j["data"]["wbi_img"]["sub_url"].rsplit("/", 1)[1].split(".")[0]
    raw = img + sub
    return "".join(raw[i] for i in _MIXIN)[:32]

def wbi_sign(params):
    key = wbi_keys()
    params = dict(params)
    params["wts"] = int(time.time())
    qs = urllib.parse.urlencode(sorted(params.items()))
    params["w_rid"] = hashlib.md5((qs + key).encode()).hexdigest()
    return urllib.parse.urlencode(sorted(params.items()))

# ---------- 采集 ----------
def collect_popular_memes():
    """popular 前100 → 鬼畜/搞笑等分区全收 + 其它分区 view>=80万 的疑似梗标题"""
    out = []
    for pn in (1, 2):
        j = get(f"https://api.bilibili.com/x/web-interface/popular?ps=50&pn={pn}")
        for v in j.get("data", {}).get("list") or []:
            tname = v.get("tname") or ""
            view = v["stat"]["view"]
            is_meme_zone = any(t in tname for t in MEME_TNAMES)
            if is_meme_zone or view >= 800000:
                out.append({
                    "title": v["title"], "tname": tname, "view": view,
                    "url": f"https://www.bilibili.com/video/{v['bvid']}",
                    "pic": v.get("pic", ""),   # 封面图：TOP10 卡片要用，接口本来就返回，别丢
                    "zone": "梗区" if is_meme_zone else "高热",
                    "pubdate": int(v.get("pubdate") or 0),   # 红线①：发布时间戳，供渲染层按5天卡
                })
    return out

def collect_hotwords():
    j = get("https://api.bilibili.com/x/web-interface/search/square?limit=20")
    out = []
    for t in (j.get("data", {}).get("trending", {}) or {}).get("list") or []:
        out.append({"kw": t.get("keyword", ""), "heat": t.get("heat_score", 0)})
    return out

def collect_weekly():
    """每周必看最新一期, wbi 签名; -352 时抛异常由外层跳过"""
    j = get("https://api.bilibili.com/x/web-interface/popular/series/list")
    latest = j["data"]["list"][0]["number"]
    qs = wbi_sign({"number": latest})
    j2 = get(f"https://api.bilibili.com/x/web-interface/popular/series/one?{qs}")
    if j2.get("code") != 0:
        raise RuntimeError(f"weekly code={j2.get('code')}")
    out = []
    for v in j2.get("data", {}).get("list") or []:
        tname = v.get("tname") or ""
        if any(t in tname for t in MEME_TNAMES) or "游戏" in tname:
            out.append({"title": v["title"], "tname": tname,
                        "view": v["stat"]["view"],
                        "pic": v.get("pic", ""),
                        "url": f"https://www.bilibili.com/video/{v['bvid']}",
                        "pubdate": int(v.get("pubdate") or 0)})  # 红线①：发布时间戳
    return out, latest

def collect_series_updates():
    """梗解读合集追更: 最新一集 + 该集高赞热评(挖梗词条)"""
    out = []
    for name, seed_aid in MEME_SERIES:
        j = get(f"https://api.bilibili.com/x/web-interface/view/detail?aid={seed_aid}")
        season = (j.get("data", {}).get("View", {}) or {}).get("ugc_season") or {}
        eps = []
        for sec in season.get("sections") or []:
            eps.extend(sec.get("episodes") or [])
        if not eps:
            continue
        latest = eps[-1]
        bvid = latest["bvid"]
        title = latest["title"]
        # 用 bvid 反查 aid（合集 episodes 里的 aid 字段不可靠）
        jv = get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}")
        aid = jv["data"]["aid"]
        # 时效红线：合集停更时 eps[-1] 仍是几个月前的老集，必须按发布时间卡掉
        age = _age_days(jv["data"].get("pubdate"))
        if age is None or age > SERIES_MAX_AGE_DAYS:
            print(f"  [时效] 合集《{name}》最新集已 {age if age is None else round(age,1)} 天，"
                  f"超过 {SERIES_MAX_AGE_DAYS} 天，跳过")
            continue
        # 高赞热评（-352 风控时退避重试，最多 3 次）
        top = []
        for attempt in range(3):
            try:
                jr = get(f"https://api.bilibili.com/x/v2/reply/main?type=1&oid={aid}&mode=3&ps=10")
                if jr.get("code") == -352:
                    time.sleep(4 * (attempt + 1))
                    continue
                for r in jr.get("data", {}).get("replies") or []:
                    msg = r["content"]["message"].replace("\n", " ")
                    top.append({"like": r["like"], "msg": msg[:120]})
                break
            except Exception:
                time.sleep(2)
        out.append({"series": name, "latest_title": title,
                    "url": f"https://www.bilibili.com/video/{bvid}",
                    "pubdate": int(jv["data"].get("pubdate") or 0),
                    "age_days": round(age, 1),
                    "ep_count": season.get("ep_count", len(eps)),
                    "top_comments": top[:8]})
    return out

def collect_meme_ups():
    """梗百科类账号追更：按发布时间搜其最新梗解读视频

    时效红线：结果按 pubdate 降序，一旦命中超过 MEME_UP_MAX_AGE_DAYS 天的
    条目即停止该关键词（后面只会更老）；冷门关键词页内全是老货时翻下一页，
    最多翻 MEME_UP_MAX_PAGE 页。缺 pubdate 的条目一律丢弃，不赌。
    """
    out = []
    seen = set()
    dropped = 0
    for kw in MEME_UP_KEYWORDS:
        got = 0
        for page in range(1, MEME_UP_MAX_PAGE + 1):
            if got >= 3:
                break
            stale_hit = False
            try:
                qs = wbi_sign({"search_type": "video", "keyword": kw,
                               "order": "pubdate", "page": page})
                j = get(f"https://api.bilibili.com/x/web-interface/search/type?{qs}")
                if j.get("code") != 0:
                    break
                rows = j.get("data", {}).get("result") or []
                if not rows:
                    break
                for r in rows:
                    if got >= 3:
                        break
                    age = _age_days(r.get("pubdate"))
                    if age is None or age > MEME_UP_MAX_AGE_DAYS:
                        # 按发布时间降序，遇到超龄说明这页往后全是老的
                        dropped += 1
                        stale_hit = True
                        break
                    bvid = r.get("bvid")
                    if not bvid or bvid in seen:
                        continue
                    seen.add(bvid)
                    title = re.sub(r"<[^>]+>", "", r.get("title", "")).strip()
                    if not title:
                        continue
                    # 仅保留确实像梗解读的（标题含 梗/解读/科普/百科/知识 或来自已知梗UP）
                    hay = title + kw
                    if not any(k in hay for k in ("梗", "解读", "科普", "百科", "知识", "指南")):
                        continue
                    out.append({
                        "title": title,
                        "bvid": bvid,
                        "view": int(r.get("play", 0) or 0),
                        "pubdate": int(r.get("pubdate") or 0),
                        "age_days": round(age, 1),
                        "url": f"https://www.bilibili.com/video/{bvid}",
                        "up": r.get("author", ""),
                        "kw": kw,
                    })
                    got += 1
            except Exception:
                time.sleep(1)
                break
            if stale_hit:
                break
    if dropped:
        print(f"  [时效] 梗百科搜索丢弃超{MEME_UP_MAX_AGE_DAYS}天/无日期结果 {dropped} 处")
    # 按播放量排序
    out.sort(key=lambda x: -x["view"])
    return out[:12]

def collect_tieba():
    """贴吧热议榜：HTML 解析 hottopic 榜单（接口返回 HTML 而非 JSON）"""
    url = "https://tieba.baidu.com/hottopic/browse/topicList?res_type=1"
    req = urllib.request.Request(url, headers=UA_TIEBA)
    html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "replace")
    # 每条: <a href="...topic_name=URLENCODED" ... class="topic-text">话题</a>
    rows = re.findall(
        r'<a href="(https://tieba\.baidu\.com/hottopic/browse/hottopic\?topic_id=\d+[^"]*?)"'
        r'[^>]*class="topic-text">([^<]+)</a>', html)
    out = []
    seen = set()
    for href, name in rows:
        name = name.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({"name": name, "url": href.split("&topic_name=")[0]})
        if len(out) >= 12:
            break
    return out

def main():
    today = datetime.date.today().strftime("%Y%m%d")
    result, status = {}, {}

    for key, fn in [("popular", collect_popular_memes),
                    ("hotwords", collect_hotwords),
                    ("series", collect_series_updates),
                    ("meme_ups", collect_meme_ups),
                    ("tieba", collect_tieba)]:
        try:
            result[key] = fn()
            status[key] = f"OK({len(result[key])})"
        except Exception as e:
            result[key] = []
            status[key] = f"FAIL {e}"

    try:
        weekly, num = collect_weekly()
        result["weekly"] = weekly
        status["weekly"] = f"OK({len(weekly)}) 第{num}期"
    except Exception as e:
        result["weekly"] = []
        status["weekly"] = f"SKIP {e}"

    # --- 输出 ---
    jpath = os.path.join(OUT_DIR, f"meme_{today}.json")
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    lines = [f"# 梗雷达 {today}", "", "## 状态"]
    lines += [f"- {k}: {v}" for k, v in status.items()]

    lines += ["", "## 一、梗区/高热视频（popular 筛选）"]
    for v in sorted(result["popular"], key=lambda x: -x["view"])[:20]:
        lines.append(f"- [{v['zone']}|{v['tname']}] {v['title']} · {v['view']//10000}万 · {v['url']}")

    lines += ["", "## 二、热搜词（突现怪词=新梗前兆）"]
    for w in result["hotwords"]:
        lines.append(f"- {w['kw']} (heat {w['heat']})")

    if result["weekly"]:
        lines += ["", "## 三、每周必看（梗浓度筛选）"]
        for v in result["weekly"]:
            lines.append(f"- [{v['tname']}] {v['title']} · {v['view']//10000}万 · {v['url']}")

    lines += ["", "## 四、梗解读合集追更 + 热评挖梗"]
    for s in result["series"]:
        lines.append(f"### {s['series']} · 共{s['ep_count']}集")
        lines.append(f"- 最新: {s['latest_title']} · {s['url']}")
        for c in s["top_comments"]:
            lines.append(f"  - 👍{c['like']} {c['msg']}")

    if result["meme_ups"]:
        lines += ["", "## 五、梗百科类账号追更（视频搜索·按发布时间）"]
        for v in result["meme_ups"]:
            vp = f"{v['view']//10000}万" if v["view"] >= 10000 else str(v["view"])
            lines.append(f"- [{v['up'] or v['kw']}] {v['title']} · {vp} · {v['url']}")

    if result["tieba"]:
        lines += ["", "## 六、贴吧热议榜（中文社区梗土壤）"]
        for i, t in enumerate(result["tieba"], 1):
            lines.append(f"- {i}. [{t['name']}]({t['url']})")

    mpath = os.path.join(OUT_DIR, f"meme_{today}.md")
    with open(mpath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print("状态:", status)
    print("输出:", mpath)

if __name__ == "__main__":
    main()
