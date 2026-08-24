# -*- coding: utf-8 -*-
"""backfill_pubdate.py — 给 events.json 的 events[] 回填新闻原始发布时间 pubdate。

背景（2026-08-24 时效治理第三层）：
  日历定档节点 events[] 大多没有 pubdate（只有 date_start 未来发售日），
  导致红线无法校验"这条新闻本身是几号发的"。本脚本按 source_url 类型自动抓取
  新闻原始发布时间，能拿到就写回 pubdate，拿不到留 null 并在报告标注原因。

抓取规则（只信强信号，绝不猜"页面最旧日期"——2026-08-24 教训）：
  1. B站视频链接 → api.bilibili.com/x/web-interface/view 取 data.pubdate
     （unix 秒，+8h 转北京时间）。
  2. 媒体/官网文章 → 抓 HTML，按优先级取：
       a. <meta> og:article:published_time / article:published_time
       b. JSON-LD "datePublished" / "dateCreated"
       c. itemprop="datePublished" 的 content/datetime
       d. 中文"发布时间/发布日期/时间："强信号
  3. 抓取失败 / 无 meta / 列表页首页 → 留 null，标注原因。

用法:
  python backfill_pubdate.py            # 干跑：只出报告，不写回 events.json
  python backfill_pubdate.py --write    # 报告 + 写回 pubdate（只填当前为 null 的）
"""
import json, os, re, sys, io, datetime, argparse, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = os.path.dirname(os.path.abspath(__file__))
DOC = os.path.join(BASE, "events.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
HDRS = {"User-Agent": UA}
TIMEOUT = 12


def _to_iso(s):
    """把各种日期字串统一成 YYYY-MM-DD；失败返回 None。"""
    if not s:
        return None
    s = str(s).strip()
    m = re.search(r"(20\d{2})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(20\d{2})[/.](\d{1,2})[/.](\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日?", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return None


def _fetch(url, timeout=TIMEOUT):
    host = urllib.parse.urlparse(url).netloc
    headers = {**HDRS, "Referer": f"https://{host}/"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "ignore")


def _url_date(url):
    """从 URL 内嵌日期提取发布时间（官网公告 URL 惯例，强信号）。
    优先 8 位 YYYYMMDD，其次 /YYYY/MM/DD/ 路径段。
    边界收紧：日期前后必须是分隔符 / _ - .、大写字母（腾讯新闻 a/YYYYMMDD Axxx）或串首尾，
    排除 a20240906main 这类"字母紧贴日期"的页面编码。"""
    m = re.search(r"(?:[/_\-]|^)(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?=[/_\-.]|[A-Z]|$)", url)
    if m:
        y, mm, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime.date(y, mm, dd).strftime("%Y-%m-%d")
        except ValueError:
            pass
    m = re.search(r"/(20\d{2})/(\d{1,2})/(\d{1,2})(?:/|\.|$)", url)
    if m:
        y, mm, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime.date(y, mm, dd).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def _sanity(d):
    """pubdate 合理性校验：未来日期或超老(>400 天)判为误判（首页/专题页/列表页的
    站点级 meta 常带无关旧日期，页面编码 a20240906main 也可能漏进），返回 None 剔除。"""
    if not d:
        return None
    try:
        dd = datetime.date.fromisoformat(str(d)[:10])
    except ValueError:
        return None
    today = datetime.date.today()
    if dd > today:
        return None  # 未来日期 = 误判
    if (today - dd).days > 400:
        return None  # 超老 = 疑似站点级 meta / 页面编码误判
    return dd.strftime("%Y-%m-%d")


def _bilibili_pubdate(url):
    m = re.search(r"/(BV[0-9A-Za-z]+)", url)
    if not m:
        return None
    bvid = m.group(1)
    api = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    try:
        req = urllib.request.Request(api, headers={**HDRS, "Referer": "https://www.bilibili.com"})
        raw = urllib.request.urlopen(req, timeout=TIMEOUT).read().decode("utf-8", "ignore")
        data = json.loads(raw)
        if data.get("code") == 0:
            pub = data["data"].get("pubdate")
            if pub:
                tz = datetime.timezone(datetime.timedelta(hours=8))
                return datetime.datetime.fromtimestamp(pub, tz=tz).strftime("%Y-%m-%d")
    except Exception:
        pass
    return None


def _extract_pubdate(html):
    if not html:
        return None
    # a. meta article:published_time（property/name 两种，content 位置两种）
    for pat in (
        r'<meta[^>]+(?:property|name)=["\'](?:og:)?article:published_time["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:)?article:published_time["\']',
    ):
        m = re.search(pat, html, re.I)
        if m:
            d = _to_iso(m.group(1))
            if d:
                return d
    # b. JSON-LD datePublished / dateCreated
    for key in ("datePublished", "dateCreated"):
        for m in re.finditer(r'"' + key + r'"\s*:\s*"([^"]+)"', html):
            d = _to_iso(m.group(1))
            if d:
                return d
    # c. itemprop=datePublished（content/datetime 与 itemprop 顺序两种）
    for pat in (
        r'itemprop=["\']datePublished["\'][^>]*(?:content|datetime)=["\']([^"\']+)["\']',
        r'(?:content|datetime)=["\']([^"\']+)["\'][^>]*itemprop=["\']datePublished["\']',
    ):
        m = re.search(pat, html, re.I)
        if m:
            d = _to_iso(m.group(1))
            if d:
                return d
    # d. 中文强信号：发布时间/发布日期/时间 后跟日期
    for m in re.finditer(r'(?:发布时间|发布日期|发表时间|时间)\s*[:：]?\s*([0-9]{4}[-/年.][0-9]{1,2}[-/月.][0-9]{1,2}日?)', html):
        d = _to_iso(m.group(1))
        if d:
            return d
    return None


def _suspect_listing(url):
    """疑似列表页/首页（拿不到单条发布时间是必然的）。"""
    p = urllib.parse.urlparse(url)
    path = (p.path or "").strip("/")
    if not path:
        return True
    segs = [s for s in path.split("/") if s]
    # 首页/列表页特征：结尾是 news/main/index 等，或路径段很少
    if segs[-1].lower() in ("news", "index", "index.html", "main"):
        return True
    return False


def _resolve(e):
    """返回 (id, pubdate_or_None, reason)。所有 pubdate 一律过 _sanity 合理性校验。"""
    eid = e.get("id")
    u = (e.get("source_url") or "").strip()
    if not u or u == "#":
        return eid, None, "无链接"
    raw = None
    reason = ""
    if "bilibili.com/video/" in u:
        try:
            raw = _bilibili_pubdate(u)
            reason = "ok" if raw else "B站API无pubdate"
        except Exception as ex:
            return eid, None, f"B站API异常 {repr(ex)[:60]}"
    elif not u.startswith(("http://", "https://")):
        return eid, None, "非http链接"
    else:
        # URL 内嵌日期（官网公告 URL 惯例，强信号，优先于 HTML meta，反爬页面也能拿到）
        raw = _url_date(u)
        if raw:
            reason = "ok(url日期)"
        else:
            try:
                html = _fetch(u)
            except Exception as ex:
                return eid, None, f"抓取失败 {repr(ex)[:60]}"
            raw = _extract_pubdate(html)
            if raw:
                reason = "ok"
            elif _suspect_listing(u):
                reason = "列表页/首页无单条meta"
            else:
                reason = "无发布时间meta"
    d = _sanity(raw)
    if d:
        return eid, d, reason
    if raw:
        return eid, None, f"可疑日期已剔除({raw})"
    return eid, None, reason


def resolve_url(url):
    """对外公共入口：解析单个 URL 的新闻原始发布时间。返回 (pubdate_or_None, reason)。
    供 collector_news_events / promote_release_dates 在生成节点时源头回填 pubdate。"""
    _, pub, reason = _resolve({"id": "-", "source_url": url})
    return pub, reason


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="写回 pubdate（只填当前为 null 的）")
    args = ap.parse_args()

    ev = json.load(io.open(DOC, encoding="utf-8"))
    events = ev.get("events", [])
    todo = [e for e in events if not e.get("pubdate")]
    print(f"events[] 共 {len(events)} 条，其中缺 pubdate 待回填 {len(todo)} 条")
    if not todo:
        return 0

    results = {}
    ok = fail = 0
    fail_detail = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(_resolve, e): e for e in todo}
        for i, f in enumerate(as_completed(futs), 1):
            eid, pub, reason = f.result()
            results[eid] = pub
            if pub:
                ok += 1
            else:
                fail += 1
                fail_detail.append((eid, reason,
                                    next((e.get("title", "")[:24] for e in todo if e.get("id") == eid), ""),
                                    next((e.get("source_url", "") for e in todo if e.get("id") == eid), "")))
            if i % 20 == 0 or i == len(todo):
                print(f"  进度 {i}/{len(todo)}  成功 {ok}  失败 {fail}")

    # 写回
    if args.write:
        for e in events:
            if not e.get("pubdate") and e.get("id") in results and results[e.get("id")]:
                e["pubdate"] = results[e.get("id")]
        json.dump(ev, io.open(DOC, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"✅ 已写回 events.json：新增 pubdate {ok} 条")
    else:
        print("（干跑模式，未写回。加 --write 才落盘）")

    # 报告
    print("\n" + "=" * 64)
    print(f"回填结果：成功 {ok} / 失败 {fail}")
    if fail_detail:
        print("\n--- 拿不到的清单（供决策：换深链 或 判时效未知）---")
        for eid, reason, title, url in sorted(fail_detail, key=lambda x: x[1]):
            print(f"[{reason}] {eid}  {title}")
            print(f"        {url}")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
