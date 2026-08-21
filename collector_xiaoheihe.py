# -*- coding: utf-8 -*-
"""collector_xiaoheihe.py — GamePulse 小黑盒「每日热点」热榜采集器

来源：小黑盒（Heybox）游戏社区「每日热点」榜
接口：https://api.xiaoheihe.cn/bbs/app/content_collection/menu?collection_id=68c528455c6766e13a7ce675
  （collection_id 对应「每日热点」合集；该接口为公开 JSON API，无需登录/签名/JS 渲染）

返回 result.links[]：每日热点 TOP20，字段：
  title         帖子标题
  topic_name    游戏分区（影之刃零/Apex 英雄/CS2/Steam/英雄联盟/…）
  view_num      阅读量
  comment_num   评论数
  link_award_num 点赞数
  linkid        帖子 id（拼接落地链接用）
  user          作者 {username, userid, avatar}
  thumb         缩略图
  create_at     发布时间戳（秒）

落地链接（网页版可点击，溯源铁律）：
  https://www.xiaoheihe.cn/app/bbs/link/{linkid}

输出: 雷达站/collectors/xiaoheihe_YYYYMMDD.json
用法: python collector_xiaoheihe.py
      环境变量 XHH_OUT_DIR 可覆盖输出目录（预览锁文件时应急）。
单源失败不阻断，失败时输出 error 标记。
"""
import json, os, datetime, urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.environ.get("XHH_OUT_DIR", os.path.join(BASE, "collectors"))
os.makedirs(OUT_DIR, exist_ok=True)

API_URL = "https://api.xiaoheihe.cn/bbs/app/content_collection/menu?collection_id=68c528455c6766e13a7ce675"
LINK_TMPL = "https://www.xiaoheihe.cn/app/bbs/link/{linkid}"
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
    "Referer": "https://web.xiaoheihe.cn/",
}


def _ts_to_date(ts):
    if not ts:
        return None
    try:
        return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except Exception:
        return None


def fetch_links():
    req = urllib.request.Request(API_URL, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode("utf-8"))
    if data.get("status") != "ok":
        raise RuntimeError("接口返回非 ok: %s" % data.get("msg", "unknown"))
    result = data.get("result", {})
    return result.get("collection_name", "每日热点"), result.get("links", [])


def main():
    try:
        collection_name, links = fetch_links()
    except Exception as e:
        print("状态: {'error': %r}" % repr(e)[:120])
        return

    items = []
    for it in links:
        linkid = it.get("linkid")
        items.append({
            "title": it.get("title", "").strip(),
            "topic": it.get("topic_name", "").strip(),
            "view_num": it.get("view_num"),
            "comment_num": it.get("comment_num"),
            "award_num": it.get("link_award_num"),
            "author": (it.get("user") or {}).get("username", ""),
            "thumb": it.get("thumb", ""),
            "pubdate": _ts_to_date(it.get("create_at")),
            "url": LINK_TMPL.format(linkid=linkid) if linkid else "",
            "linkid": linkid,
        })

    today = datetime.date.today().strftime("%Y%m%d")
    out = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "collection_name": collection_name,
        "source": "小黑盒（Heybox）每日热点",
        "api": API_URL,
        "count": len(items),
        "items": items,
    }
    out_path = os.path.join(OUT_DIR, f"xiaoheihe_{today}.json")
    json.dump(out, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"状态: {{'OK': {len(items)}}}")
    print(f"集合: {collection_name}")
    print(f"输出: {out_path}")

    # 附赠：生成可读的每日浏览清单（Markdown），方便"每日浏览资源库"直接扫一眼
    md_path = os.path.join(OUT_DIR, f"小黑盒每日热点_{today}.md")
    lines = [f"# 小黑盒「每日热点」· {datetime.date.today().strftime('%Y-%m-%d')}", ""]
    lines.append(f"> 来源：小黑盒（Heybox）游戏社区 · 共 {len(items)} 条 · 采集于 {datetime.datetime.now().strftime('%H:%M:%S')}")
    lines.append("")
    lines.append("| # | 分区 | 标题 | 阅读 | 评论 | 赞 |")
    lines.append("|---|------|------|------|------|-----|")
    for i, it in enumerate(items, 1):
        title = it["title"]
        if it["url"]:
            title = f"[{it['title']}]({it['url']})"
        lines.append(f"| {i} | {it['topic']} | {title} | {it['view_num']} | {it['comment_num']} | {it['award_num']} |")
    lines.append("")
    lines.append("---")
    lines.append(f"*接口：`{API_URL}` · 落地链接 `https://www.xiaoheihe.cn/app/bbs/link/{{linkid}}`*")
    io = __import__("io")
    io.open(md_path, "w", encoding="utf-8").write("\n".join(lines))
    print(f"清单: {md_path}")


if __name__ == "__main__":
    main()
