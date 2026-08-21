#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Phase 2 信源采集器：读 sources.toml，逐源抓取并做 200 探活，
按类型(rss/news_list/static)抽取最新条目(带 canonical URL)，写入暂存区。
纯标准库实现，单次失败不阻塞整体；条目进 inbox 供策展时直接采用正确 URL（无需事后补搜）。
"""
import sys, io, os, re, json, tomllib, datetime, urllib.parse, urllib.request, xml.etree.ElementTree as ET

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, 'sources.toml')
INBOX = os.path.join(BASE, 'inbox', 'sources_inbox.json')
STATUS = os.path.join(BASE, 'sources_status.json')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'

# ---------- 抓取 ----------
def fetch(url, timeout=12):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': UA})
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, r.read(), None
    except Exception as e:
        return None, b'', repr(e)[:160]

def abs_url(base, href):
    if not href:
        return None
    if href.startswith(('javascript:', 'mailto:', 'tel:', '#')):
        return None
    return urllib.parse.urljoin(base, href.strip())

def same_netloc(a, b):
    return urllib.parse.urlparse(a).netloc == urllib.parse.urlparse(b).netloc


def decode_body(body_bytes):
    """按页面自报的编码解码，而不是一律当 utf-8。

    踩坑记录：原来写死 decode('utf-8','ignore')，遇到 GBK/GB2312 站点
    （如 gp.qq.com 和平精英官网）中文标题会整片变成乱码，且因为用了 'ignore'
    不会抛异常 —— 又一个"静默失败"：源看起来抓到了条目，实际标题全是垃圾，
    后面准入门再靠标题做相关性判断就全乱套。
    """
    head = body_bytes[:3000]
    enc = None
    m = (re.search(rb'charset=["\']?\s*([A-Za-z0-9_\-]+)', head, re.I) or
         re.search(rb'encoding=["\']\s*([A-Za-z0-9_\-]+)', head, re.I))
    if m:
        enc = m.group(1).decode('ascii', 'ignore').lower()
    # gb2312/gbk 一律用 gb18030 解（超集，能吞下繁体和生僻字，避免半路炸）
    if enc in ('gb2312', 'gbk', 'gb18030'):
        enc = 'gb18030'
    for cand in ([enc] if enc else []) + ['utf-8', 'gb18030']:
        try:
            return body_bytes.decode(cand)
        except (UnicodeDecodeError, LookupError):
            continue
    return body_bytes.decode('utf-8', 'ignore')


# ---------- 发布日期解析（时效红线第二层） ----------
# 全站统一口径：任何进入网站的条目都要能回答"这是哪天发的"。
# 抽取阶段只负责"尽力拿到日期"，不负责淘汰；淘汰交给准入/晋升环节，
# 这样源改版导致日期失效时不会让整条流水线突然断粮，只会在报表里露出来。
_MONTHS = {m: i + 1 for i, m in enumerate(
    ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'])}

def parse_pubdate(text):
    """把各种花式发布时间字符串归一成 'YYYY-MM-DD'，认不出返回 None。

    覆盖：ISO8601(2026-07-29T10:00:00Z)、RFC822(Tue, 29 Jul 2026 10:00:00 +0800)、
    中文(2026年7月29日)、纯数字(20260729)、斜杠(2026/07/29)。
    """
    if not text:
        return None
    t = str(text).strip()
    m = re.search(r'(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})', t)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.search(r'(\d{1,2})\s+([A-Za-z]{3})[a-z]*\s+(20\d{2})', t)
        if m and m.group(2).lower() in _MONTHS:
            d, mo, y = int(m.group(1)), _MONTHS[m.group(2).lower()], int(m.group(3))
        else:
            m = re.search(r'\b(20\d{2})(\d{2})(\d{2})\b', t)
            if not m:
                return None
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        _d = datetime.date(y, mo, d)
    except ValueError:
        return None
    # 2026-08-05 治理：正文里的"将于 2026年9月17日发售"会被误抓成发布日期
    # （《火焰纹章：万缕千丝》实际发布于 08-04，却被记成 09-17）。
    # 新闻不可能发布于未来——未来日期一律判为抓错，返回 None 走"无日期"清扫。
    if _d > datetime.date.today():
        return None
    return _d.isoformat()

def date_from_url(u):
    """从 URL 路径里抠日期：/2026/07/29/xxx.html、/news/20260729_xx.shtml 等。
    国内游戏站十有八九把日期写在路径上，这是没有 RSS 时最靠谱的兜底。
    """
    if not u:
        return None
    path = urllib.parse.urlparse(u).path
    m = re.search(r'/(20\d{2})[-/_]?(\d{2})[-/_]?(\d{2})(?:[/_\-.]|$)', path)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None
    return None

# 日期可信度分级（决定下游是"硬剔除"还是"降级复核"）：
#   feed = RSS/Atom/API 里明确的发布时间字段  → 权威，可据此硬杀
#   url  = 从文章 URL 路径抠出               → 权威，路径日期几乎不会错
#   text = 从列表页 HTML 文本窗口猜的         → 不可靠，可能抠到隔壁条目/侧栏的日期，
#          只能用来"降级复核"，不能直接判死刑
# 踩坑：触乐一篇当天的新文章被页面别处的 2025-11-10 带偏，差点按"263 天陈稿"剔除。
def days_ago(iso_date):
    """'YYYY-MM-DD' → 距今天数；None 进 None 出"""
    if not iso_date:
        return None
    try:
        d = datetime.date.fromisoformat(iso_date)
    except ValueError:
        return None
    return (datetime.date.today() - d).days


# 中文相对时间解析（"40 分钟前" / "1 小时前" / "2 天前" / "昨天"）
# 精度只有天级别——下游准入器用天数做时效判断，分钟/小时级偏差无影响。
_REL_CN = re.compile(r'(\d+)\s*(分钟|小时|天)前|昨天|前天')
def parse_relative_cn(text):
    """中文相对时间 → 'YYYY-MM-DD'；None 表示不识别。"""
    if not text:
        return None
    t = str(text).strip()
    now = datetime.datetime.now()
    m = _REL_CN.search(t)
    if not m:
        return None
    if m.group(0) == '昨天':
        return (now - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
    if m.group(0) == '前天':
        return (now - datetime.timedelta(days=2)).strftime('%Y-%m-%d')
    n = int(m.group(1))
    unit = m.group(2)
    if unit == '分钟':
        return (now - datetime.timedelta(minutes=n)).strftime('%Y-%m-%d')
    elif unit == '小时':
        return (now - datetime.timedelta(hours=n)).strftime('%Y-%m-%d')
    elif unit == '天':
        return (now - datetime.timedelta(days=n)).strftime('%Y-%m-%d')
    return None


# ---------- 条目抽取 ----------
def extract_rss_regex(body_bytes, base, max_items):
    """XML 解析失败时的回退：直接正则抠 <item>/<entry> 里的 title 与 link。
    诱因：部分官方 RSS（如 Steam 新闻）正文含未转义的裸 & 或非法控制字符，
    ElementTree 会整篇解析失败 → 源被误判为"零产出"，且不报错（静默失败）。
    """
    items = []
    text = decode_body(body_bytes)
    for m in re.finditer(r'<(item|entry)\b.*?</\1>', text, re.S | re.I):
        blk = m.group(0)
        mt = re.search(r'<title[^>]*>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</title>', blk, re.S | re.I)
        ml = re.search(r'<link[^>]*\bhref="([^"]+)"', blk, re.I) or \
             re.search(r'<link[^>]*>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</link>', blk, re.S | re.I)
        if not (mt and ml):
            continue
        title = re.sub(r'<[^>]+>', '', mt.group(1))
        title = re.sub(r'\s+', ' ', title).strip()
        u = abs_url(base, ml.group(1).strip())
        # 日期：RSS 用 pubDate，Atom 用 published/updated，都没有就从 URL 抠
        md = re.search(r'<(pubDate|published|updated|dc:date)[^>]*>\s*'
                       r'(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</\1>', blk, re.S | re.I)
        pub = parse_pubdate(md.group(2)) if md else None
        dsrc = 'feed' if pub else None
        if not pub:
            pub = date_from_url(u)
            dsrc = 'url' if pub else None
        if title and u:
            items.append({'title': title[:80], 'url': u, 'pubdate': pub, 'date_src': dsrc})
        if len(items) >= max_items:
            break
    return items


def extract_rss(body_bytes, base, max_items):
    items = []
    try:
        root = ET.fromstring(body_bytes)
    except Exception:
        # 非法 XML → 正则回退，别把"解析器挑食"当成"信源没内容"
        return extract_rss_regex(body_bytes, base, max_items)
    # RSS: <item><title><link> ; Atom: <entry><title><link href>
    for node in root.iter():
        tag = node.tag.lower().split('}')[-1]
        if tag in ('item', 'entry'):
            title = link = raw_date = None
            for c in node:
                ct = c.tag.lower().split('}')[-1]
                if ct == 'title' and title is None:
                    title = (c.text or '').strip()
                elif ct == 'link':
                    if c.get('href'):
                        link = c.get('href')
                    elif c.text and not link:
                        link = c.text.strip()
                elif ct in ('pubdate', 'published', 'updated', 'date') and raw_date is None:
                    raw_date = (c.text or '').strip()
            if title and link:
                u = abs_url(base, link)
                if u:
                    pub, dsrc = parse_pubdate(raw_date), 'feed'
                    if not pub:
                        pub, dsrc = date_from_url(u), 'url'
                    items.append({'title': title, 'url': u, 'pubdate': pub,
                                  'date_src': dsrc if pub else None})
            if len(items) >= max_items:
                break
    # XML 合法但结构非常规（拿不到条目）时，同样回退一次
    return items or extract_rss_regex(body_bytes, base, max_items)

# 中文导航/工具/营销词（命中即视为非新闻条目，直接丢弃）
NAV_RE = re.compile(
    r'(首页|HOME|NEWS|新闻|视频|壁纸|充值|购卡|了解游戏|新手|教学|攻略|专区|社区|客服|'
    r'下载|礼包|活动中心|官方|反馈|反外挂|平台|工具|论坛|商城|直播|媒体|赛事中心|'
    r'世界冠军|高校|城市联赛|季中赛|主播|周边|实体店|门店|招聘|微博|公众号|社群)',
    re.I)
# 英文大写导航令牌（VIDEO / ESPORTS / MATCH ...）
EN_NAV_RE = re.compile(r'(VIDEO|ESPORTS|MATCH|MEDIA|LIVE|FORUM|STORE)', re.I)
# 模板 / JS 泄漏特征（{{item.sTitle}}、item.iNewsId、?.、data[ 等）
TEMPLATE_RE = re.compile(r'(\{\{|\}\}|item\.|\?\.|sTitle|iNewsId|data\[|cover\.|\$\w+)')

def title_junk(title):
    """判定抽取到的链接文字是否为模板/JS 泄漏或导航词（非新闻条目）。"""
    t = re.sub(r'\s+', ' ', title).strip()
    if not t or len(t) < 5:
        return True
    if TEMPLATE_RE.search(t):
        return True
    if re.search(r'[{}<>\$]', t):
        return True
    if EN_NAV_RE.search(t):
        return True
    if NAV_RE.search(t):
        return True
    return False

def href_junk(href):
    """判定链接本身是否为模板/JS 泄漏或碎片（非真实文章 URL）。"""
    if not href:
        return True
    # 模板变量、碎片、JS 拼接（含空格/'/(/)+/`<>` 等都不是正常文章 URL）
    if re.search(r'[\s`\'"<>(){}\[\]]', href):
        return True
    if '$' in href or '#' in href:
        return True
    return False

def extract_news_list(body_bytes, base, max_items):
    items = []
    text = decode_body(body_bytes)
    for m in re.finditer(r'<a\b([^>]*?)href="([^"]+)"([^>]*)>(.*?)</a>', text, re.S | re.I):
        href = m.group(2)
        if href_junk(href):
            continue
        inner = re.sub(r'<[^>]+>', '', m.group(4))
        if title_junk(inner):
            continue
        u = abs_url(base, href)
        if not u:
            continue
        if not same_netloc(u, base):
            continue
        path = urllib.parse.urlparse(u).path
        if path in ('', '/'):
            continue
        if path.endswith(('index.html', 'main.shtml', 'index_1.html')) or '/sub/inner' in path:
            continue
        title = re.sub(r'\s+', ' ', inner).strip()[:80]
        if u.rstrip('/') == base.rstrip('/'):
            continue
        # 日期：优先 URL 路径，其次在 <a> 前后 200 字窗口里找日期文本
        # （新闻列表几乎都是"标题 + 紧邻的日期"这种版式）
        pub, dsrc = date_from_url(u), 'url'
        if not pub:
            # 版式差异大，按"离标题多近"分三级找：先 <a> 标签自身属性（title/data-date），
            # 再往后 350 字（"标题 2026-07-29" 这种最常见），最后往前 250 字（日期在标题左侧）。
            dsrc = 'text'
            for win in (m.group(1) + m.group(3) + m.group(4),
                        text[m.end():m.end() + 350],
                        text[max(0, m.start() - 250):m.start()]):
                pub = parse_pubdate(re.sub(r'<[^>]+>', ' ', win))
                if pub:
                    break
        items.append({'title': title, 'url': u, 'pubdate': pub,
                      'date_src': dsrc if pub else None})
        if len(items) >= max_items:
            break
    # 去重（同 url 只留第一条）
    seen, uniq = set(), []
    for it in items:
        if it['url'] not in seen:
            seen.add(it['url']); uniq.append(it)
    return uniq[:max_items]

# ---------- JSON API 抽取 ----------
# 支持两种响应格式：
#   1) 扁平数组：[{"title": "...", "url": "..."}, ...]（原先行为，向后兼容）
#   2) 嵌套对象：{"data":{"article_list":[...]}}（通过 data_path 导航）
#
# sources.toml 可选字段：
#   data_path  = "data.article_list"  # 点号分隔的 JSON 导航路径（嵌套格式用）
#   url_prefix = "https://..."        # URL 前缀（url_field 为纯数字 ID 时拼接用）
#   url_field  = "id"                 # 覆盖默认 "url" 字段名（与 url_prefix 配合）
#   title_field= "title"              # 覆盖默认 "title" 字段名
#
# 日期自动探测：先试常规字段(pubdate/date/time 等)，再试中文相对时间(add_time/"X 分钟前")���
# 最后回落到 URL 路径日期。额外字段（views/synopsis/source 等）保留在 item 的 extra 字典里
# 供下游使用。
def extract_json_api(body_bytes, base, max_items,
                     data_path=None, url_prefix=None, url_field=None, title_field=None):
    items = []
    try:
        data = json.loads(body_bytes)
    except (json.JSONDecodeError, ValueError):
        return items

    # 1) 确定文章数组
    arr = None
    if data_path:
        # 嵌套导航："data.article_list" → data["data"]["article_list"]
        cur = data
        for part in data_path.split('.'):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                cur = None; break
        if isinstance(cur, list):
            arr = cur
    elif isinstance(data, list):
        arr = data  # 扁平数组（向后兼容）
    if not arr:
        return items

    # 2) 逐条抽取
    for entry in arr[:max_items * 3]:
        if not isinstance(entry, dict):
            continue
        # 标题
        tkey = title_field or 'title'
        title = str(entry.get(tkey) or '').strip()
        if not title or len(title) < 2:
            continue

        # URL：优先 url_field + url_prefix 拼接，否则从 "url" 字段取，最后用 base 兜底
        u = None
        if url_prefix and url_field:
            raw_id = entry.get(url_field)
            if raw_id is not None:
                u = url_prefix.rstrip('/') + '/' + str(raw_id)
        if not u:
            u = str(entry.get('url') or entry.get('link') or '').strip()
        if not u:
            continue
        if not u.startswith(('http://', 'https://')):
            u = abs_url(base, u)
        if not u:
            continue

        # 日��：常规字段 → 中文相对时间 → URL 路径
        pub, dsrc = None, None
        for k in ('pubdate', 'pubDate', 'date', 'time', 'publish_time', 'publishTime',
                  'created_at', 'createTime', 'sPubTime', 'update_time', 'add_time'):
            if entry.get(k):
                raw = str(entry[k])
                pub = parse_pubdate(raw) or parse_relative_cn(raw)
                if pub:
                    dsrc = 'feed'; break
        if not pub:
            pub, dsrc = date_from_url(u), 'url'

        item = {'title': title[:80], 'url': u, 'pubdate': pub,
                'date_src': dsrc if pub else None}
        # 保留额外字段（浏览量/摘要/来源 等）供下游使用
        extra = {}
        for ek in ('views', 'synopsis', 'from', 'source', 'summary'):
            if entry.get(ek) is not None:
                extra[ek] = entry[ek]
        if extra:
            item['extra'] = extra
        items.append(item)

    # 去重
    seen, uniq = set(), []
    for it in items:
        if it['url'] not in seen:
            seen.add(it['url']); uniq.append(it)
    return uniq[:max_items]


# ---------- 主流程 ----------
def collect(only=None):
    with open(SRC, 'rb') as f:
        cfg = tomllib.load(f)
    sources = [s for s in cfg.get('sources', []) if s.get('enabled', True)]
    if only:
        sources = [s for s in sources if s.get('id') == only]

    out_sources = []
    alive = 0
    items_total = 0
    dated_total = 0
    for s in sources:
        sid, game, name = s.get('id'), s.get('game'), s.get('name')
        url, stype, mx = s.get('url'), s.get('type', 'static'), int(s.get('max_items', 5))
        status, body, err = fetch(url)
        rec = {'id': sid, 'game': game, 'name': name, 'type': stype,
               'url': url, 'status': status, 'error': err, 'items': []}
        if status == 200:
            alive += 1
            if stype == 'rss':
                rec['items'] = extract_rss(body, url, mx)
                rec['extraction'] = 'rss'
            elif stype == 'news_list':
                rec['items'] = extract_news_list(body, url, mx)
                # 200 但抽不到条目 → 多为 JS 渲染页，标记需人工策展
                rec['extraction'] = 'extracted' if rec['items'] else 'js_rendered (manual curation)'
            elif stype == 'json_api':
                rec['items'] = extract_json_api(body, url, mx,
                    data_path=s.get('data_path'),
                    url_prefix=s.get('url_prefix'),
                    url_field=s.get('url_field'),
                    title_field=s.get('title_field'))
                rec['extraction'] = 'json_api' if rec['items'] else 'json_api (empty)'
            else:  # static: 仅探活，不抽条目
                rec['extraction'] = 'liveness_only'
            items_total += len(rec['items'])
            # 每条都补 age_days，让下游（准入/晋升）不用各自再解析一遍日期
            for it in rec['items']:
                it['age_days'] = days_ago(it.get('pubdate'))
            dated = sum(1 for it in rec['items'] if it.get('pubdate'))
            rec['dated_items'] = dated
            dated_total += dated
        out_sources.append(rec)
        print('  %-12s %-10s status=%-5s items=%d 有日期=%d  %s'
              % (sid, game, status, len(rec['items']), rec.get('dated_items', 0), url[:40]))

    now = datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    doc = {
        'fetched_at': now,
        'summary': {
            'sources_total': len(out_sources),
            'sources_alive': alive,
            'items_total': items_total,
            'items_dated': dated_total,
        },
        'sources': out_sources,
    }
    os.makedirs(os.path.dirname(INBOX), exist_ok=True)
    io.open(INBOX, 'w', encoding='utf-8').write(json.dumps(doc, ensure_ascii=False, indent=1))
    # 轻量状态文件（供仪表盘/自洽校验读取）
    st = [{'id': s['id'], 'game': s['game'], 'status': s['status'],
           'last_fetched': now, 'item_count': len(s['items'])} for s in out_sources]
    io.open(STATUS, 'w', encoding='utf-8').write(json.dumps(st, ensure_ascii=False, indent=1))
    print('\n采集完成：%d 源 / %d 存活 / %d 条候选 / %d 条带发布日期(%.0f%%)'
          % (len(out_sources), alive, items_total, dated_total,
             100.0 * dated_total / items_total if items_total else 0))
    print('  inbox ->', os.path.relpath(INBOX, BASE))
    print('  status->', os.path.relpath(STATUS, BASE))
    return doc

if __name__ == '__main__':
    only = sys.argv[1] if len(sys.argv) > 1 else None
    collect(only)
