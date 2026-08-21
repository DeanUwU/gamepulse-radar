#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Phase 5 信源自发现：让"信源通讯录"自己长大。

三路发现：
  A) 同域栏目自发现——扫已登记源页面里的同域链接，捞出 /news /gonggao /update /match
     一类**栏目页**（不是单篇文章），作为该游戏的新增/更优信源。
  B) 跨域相关站点——抽出指向站外的链接，剔掉备案/分享/统计/应用商店等噪声，
     余下作为"可能的相关官方站/行业站"候选（低置信，标 maybe）。
  C) 行业追踪器种子——内置一份行业媒体/RSS 种子，**探活自证**：拿不到 200 或
     RSS 解析不出条目的种子一律不建议，绝不臆造。

所有候选一律：200 探活 → 与 sources.toml 已登记 URL/域名去重 → 生成可直接粘贴的
[[sources]] 片段。产出：
  inbox/sources_discovered.json （机器用）
  inbox/新信源建议.md           （人读，含一键复制的 TOML）

用法：
  python discover_sources.py            # 发现并出建议（带 7 天节流）
  python discover_sources.py --force    # 忽略节流立即重跑
  python discover_sources.py --apply    # 把 suggest 级候选直接追加进 sources.toml（自动备份）
非阻断：单条失败不影响整体。
"""
import sys, io, os, re, json, shutil, tomllib, datetime
import urllib.parse, urllib.request
import xml.etree.ElementTree as ET

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, 'sources.toml')
OUT = os.path.join(BASE, 'inbox', 'sources_discovered.json')
REPORT = os.path.join(BASE, 'inbox', '新信源建议.md')
UA = 'Mozilla/5.0 (GamePulse Radar source-discovery)'

TIMEOUT = 8
MAX_SECTION_PER_SOURCE = 2      # 每源最多提 N 个同域栏目候选（按栏目价值打分取 top N）
MAX_EXTERNAL_PER_SOURCE = 2     # 每个已登记源最多提 N 个跨域候选
MAX_PROBE = 90                  # 全局探活上限（控制耗时）
THROTTLE_DAYS = 7               # 节流：N 天内已跑过则跳过

# ---------- 行业追踪器种子（探活自证，活的才建议） ----------
SEEDS = [
    ('全行业', 'GameLook 行业资讯', 'rss', 'https://www.gamelook.com.cn/feed'),
    ('全行业', '机核 GCORES', 'rss', 'https://www.gcores.com/rss'),
    ('全行业', 'Gematsu 新作情报', 'rss', 'https://www.gematsu.com/feed'),
    ('全行业', 'PlayStation Blog 中文', 'rss', 'https://blog.ja.playstation.com/feed/'),
    ('全行业', 'Xbox Wire', 'rss', 'https://news.xbox.com/en-us/feed/'),
    ('全行业', 'Steam 新闻中心', 'news_list', 'https://store.steampowered.com/news/'),
    ('全行业', '游民星空新闻', 'news_list', 'https://www.gamersky.com/news/'),
    ('全行业', '触乐网', 'news_list', 'https://www.chuapp.com/'),
    ('全行业', 'VGtime 游戏时光', 'news_list', 'https://www.vgtime.com/'),
    ('全行业', 'indienova 独立游戏', 'news_list', 'https://indienova.com/indie-game-news/'),
    ('全行业', '17173 网游资讯', 'news_list', 'https://news.17173.com/'),
    ('全行业', 'TapTap 新品', 'static', 'https://www.taptap.cn/'),
]

# ---------- 规则 ----------
# 栏目页特征（新闻/公告/更新/赛事/活动/版本）
SECTION_RE = re.compile(
    r'/(news|newslist|new|gonggao|announce|announcement|notice|update|updates|official'
    r'|article|articles|information|zixun|dongtai|match|matches|esports|event|events'
    r'|activity|patch|version|blog|press|media-?center)(/|$|\.s?html?|\?)', re.I)

# 噪声域名：备案/举报/分享/统计/应用商店/社交入口/浏览器系统/交易平台等，一律不做信源
NOISE_DOMAIN_RE = re.compile(
    r'(beian|miit\.gov|gov\.cn|12377|12318|cyberpolice|police|jubao'
    r'|weibo\.|t\.qq\.com|qzone|connect\.qq|sharer|jq\.qq|wpa\.b\.qq|graph\.qq'
    r'|apple\.com|itunes|play\.google|appstore|9game|yingyongbao|myapp\.com'
    r'|gtimg|gstatic|googleapis|google-analytics|hm\.baidu|cnzz|umeng|doubleclick'
    r'|facebook|twitter\.com|x\.com|instagram|linkedin|discord|reddit|tiktok'
    r'|microsoft\.com|browser\.qq|\.cbg\.|cbg\.163|jiazhang|author\.'      # 系统/浏览器/藏宝阁/家长/作者页
    r'|steamcommunity|aligames|bbs\.|forum\.|tieba'                        # 社区/论坛非官方资讯
    r'|\.jpg|\.png|\.gif|\.mp4|\.apk|\.exe|\.zip|\.pdf)', re.I)

# 分页页（同栏目第 N 页，重复采集无意义）
PAGING_RE = re.compile(r'(index_\d+\.s?html?$|/page/\d+|_\d+\.s?html?$|[?&]page=\d+)', re.I)

# 移动端镜像页（与 PC 版同内容，避免重复登记）
MOBILE_RE = re.compile(r'(indexm\.s?html?$|_m\.s?html?$|/m/|/mobile|/wap)', re.I)

# 栏目价值打分（同一源只留最有信息量的前 N 个）
SECTION_SCORE = [
    (re.compile(r'/(official|gonggao|announce|announcement|notice)', re.I), 100),
    (re.compile(r'/(update|updates|patch|version)', re.I), 90),
    (re.compile(r'/(news|newslist|zixun|dongtai)', re.I), 80),
    (re.compile(r'/(match|matches|esports)', re.I), 70),
    (re.compile(r'/(activity|event|events)', re.I), 60),
    (re.compile(r'/(blog|press|information)', re.I), 50),
    (re.compile(r'/(media|article|articles)', re.I), 40),
]
# 跟踪参数（剥掉后再去重，避免同页因参数不同被当成新源）
TRACK_PARAM_RE = re.compile(r'^(snr|from|from_source|channel|utm_\w+|spm|_t|ADTAG|tdsourcetag)$', re.I)

# 噪声路径：协议/隐私/客服/招聘/下载等非资讯页
NOISE_PATH_RE = re.compile(
    r'(privacy|agreement|protocol|terms|license|about|contact|help|faq|support|kefu'
    r'|recruit|join(us)?|jobs|download|xiazai|login|register|pay|chongzhi|shop|mall'
    r'|sitemap|robots)', re.I)

# 文章详情页（不是栏目，排除）：长数字 ID、多段数字目录（如 /news/1135/0/3996/1.html）
DETAIL_RE = re.compile(r'/\d{6,}(/|$|\.s?html?)|/detail|/article/\d+|(/\d+){3,}', re.I)


def strip_tracking(url):
    """剥掉跟踪参数，避免同一页面因参数不同被误当作新信源。"""
    p = urllib.parse.urlparse(url)
    if not p.query:
        return url
    kept = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
            if not TRACK_PARAM_RE.match(k)]
    q = urllib.parse.urlencode(kept)
    return urllib.parse.urlunparse((p.scheme, p.netloc, p.path, p.params, q, ''))


def section_score(path):
    for rx, sc in SECTION_SCORE:
        if rx.search(path):
            return sc
    return 10


def fetch(url, timeout=TIMEOUT, want_body=True):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': UA})
        r = urllib.request.urlopen(req, timeout=timeout)
        body = r.read() if want_body else b''
        return r.status, body, None
    except Exception as e:
        return None, b'', repr(e)[:140]


def rss_item_count(body):
    try:
        root = ET.fromstring(body)
    except Exception:
        return 0
    n = 0
    for node in root.iter():
        if node.tag.lower().split('}')[-1] in ('item', 'entry'):
            n += 1
    return n


def norm(url):
    """URL 归一化，用于去重：去 www、去跟踪参数、剥 index.html 一类默认文件、去尾斜杠。"""
    p = urllib.parse.urlparse(strip_tracking(url))
    host = p.netloc.lower().split(':')[0]
    if host.startswith('www.'):
        host = host[4:]
    if host.startswith('m.'):          # 移动端镜像视为同一站点
        host = host[2:]
    path = p.path or '/'
    path = re.sub(r'/(index|default|main)\.s?html?$', '/', path, flags=re.I)
    path = path.rstrip('/') or '/'
    return host + path + (('?' + p.query) if p.query else '')


def netloc_of(url):
    h = urllib.parse.urlparse(url).netloc.lower().split(':')[0]
    return h[4:] if h.startswith('www.') else h


def mk_id(url, taken):
    p = urllib.parse.urlparse(url)
    host = netloc_of(url)
    hp = [x for x in re.split(r'[^a-z0-9]+', host) if x and x not in ('com', 'cn', 'net', 'org', 'co')]
    sp = [x for x in re.split(r'[^a-z0-9]+', (p.path or '').lower()) if x and not x.isdigit()]
    cand = '_'.join((hp[:2] + sp[:1]) or ['src'])[:28]
    base_id, i = cand, 2
    while cand in taken:
        cand = '%s%d' % (base_id, i)
        i += 1
    taken.add(cand)
    return cand


def infer_type(url, status, body):
    if re.search(r'(/feed/?$|/rss|\.xml$|/atom)', url, re.I) and status == 200 and rss_item_count(body) > 0:
        return 'rss'
    if SECTION_RE.search(urllib.parse.urlparse(url).path or ''):
        return 'news_list'
    return 'static'


def toml_snippet(sid, game, name, stype, url):
    return ('[[sources]]\nid = "%s"\ngame = "%s"\nname = "%s"\ntype = "%s"\n'
            'url = "%s"\nenabled = true\nmax_items = 5\n') % (sid, game, name, stype, url)


# ---------- 候选采集 ----------
def gather_candidates(cfg):
    """返回未探活的候选列表（已按已登记源去重、已过噪声）。"""
    all_sources = cfg.get('sources', [])
    sources = [s for s in all_sources if s.get('enabled', True)]   # 只扫描启用的源
    # 去重要用**全部**源（含 enabled=false）：停用是人为决定（如 Gematsu 文章页被 CF 拦），
    # 若只按启用源去重，被停用的源会每周被重新"建议"一次，变成永远清不掉的假待办。
    known_url = set(norm(s['url']) for s in all_sources)
    known_host = set(netloc_of(s['url']) for s in all_sources)
    seen = set(known_url)
    cands = []

    for s in sources:
        url, game = s.get('url'), s.get('game')
        status, body, err = fetch(url)
        if status != 200 or not body:
            print('  - 跳过(不可达) %-10s %s' % (game, url[:52]))
            continue
        text = body.decode('utf-8', 'ignore')
        host = netloc_of(url)
        local_seen = set()
        sections, externals = [], []
        for m in re.finditer(r'<a\b[^>]*?href="([^"]+)"', text, re.I):
            href = m.group(1).strip()
            if not href or href.startswith(('javascript:', 'mailto:', 'tel:', '#', 'data:')):
                continue
            if re.search(r'[\s\'"<>{}\[\]$]', href):
                continue
            u = urllib.parse.urljoin(url, href)
            if not u.startswith('http'):
                continue
            u = strip_tracking(u.split('#')[0])
            p = urllib.parse.urlparse(u)
            path = p.path or '/'
            if NOISE_DOMAIN_RE.search(u) or NOISE_PATH_RE.search(path):
                continue
            if PAGING_RE.search(u) or MOBILE_RE.search(path):   # 分页页 / 移动端镜像
                continue
            key = norm(u)
            if key in seen or key in local_seen:
                continue
            h = netloc_of(u)
            if h == host:
                # A) 同域栏目页：必须像栏目、不能像详情页、不能是根页
                if path in ('', '/') or DETAIL_RE.search(path):
                    continue
                if not SECTION_RE.search(path):
                    continue
                local_seen.add(key)
                sections.append({'kind': 'section', 'game': game, 'url': u, 'key': key,
                                 'score': section_score(path), 'has_query': bool(p.query),
                                 'from': s.get('id'), 'from_url': url})
            else:
                # B) 跨域相关站点：只取站点根/栏目，低置信
                if h in known_host or h.replace('m.', '', 1) in known_host:
                    continue
                if DETAIL_RE.search(path):
                    continue
                if path not in ('', '/') and not SECTION_RE.search(path):
                    continue
                local_seen.add(key)
                externals.append({'kind': 'external', 'game': game, 'url': u, 'key': key,
                                  'score': 0, 'has_query': bool(p.query),
                                  'from': s.get('id'), 'from_url': url})
        # 按栏目价值取 top N（官方公告 > 更新 > 新闻 > 赛事 > 活动 ...）
        sections.sort(key=lambda c: -c['score'])
        picked = sections[:MAX_SECTION_PER_SOURCE] + externals[:MAX_EXTERNAL_PER_SOURCE]
        for c in picked:
            seen.add(c.pop('key'))
            cands.append(c)
        print('  · %-12s 同域栏目 %d/%d ｜ 跨域 %d/%d'
              % (s.get('id'), min(len(sections), MAX_SECTION_PER_SOURCE), len(sections),
                 min(len(externals), MAX_EXTERNAL_PER_SOURCE), len(externals)))

    # C) 行业追踪器种子
    for game, name, stype, u in SEEDS:
        key = norm(u)
        if key in seen or netloc_of(u) in known_host:
            continue
        seen.add(key)
        cands.append({'kind': 'seed', 'game': game, 'url': u,
                      'name': name, 'hint_type': stype, 'from': 'seed', 'from_url': ''})
    return cands


# ---------- 探活 + 判级 ----------
def discover(apply_toml=False):
    with open(SRC, 'rb') as f:
        cfg = tomllib.load(f)
    print('【A/B】扫描已登记源，寻找同域栏目与跨域相关站点...')
    cands = gather_candidates(cfg)
    print('【C】加入行业追踪器种子，共 %d 条候选待探活' % len(cands))

    # 优先级：seed > section > external（探活额度有限，先验高价值的）
    order = {'seed': 0, 'section': 1, 'external': 2}
    cands.sort(key=lambda c: order.get(c['kind'], 9))
    cands = cands[:MAX_PROBE]

    taken = set(s.get('id') for s in cfg.get('sources', []))
    items, cnt = [], {'suggest': 0, 'maybe': 0, 'dead': 0}
    for c in cands:
        status, body, err = fetch(c['url'])
        reasons = []
        if status != 200:
            verdict = 'dead'
            reasons.append('探活失败(HTTP %s)' % status)
            stype = c.get('hint_type', 'static')
            sid = ''
            snippet = ''
        else:
            stype = infer_type(c['url'], status, body)
            if c['kind'] == 'seed' and c.get('hint_type') == 'rss' and stype != 'rss':
                verdict = 'maybe'
                reasons.append('种子标称 RSS 但未解析出条目，需人工确认')
            elif c['kind'] == 'section':
                if c.get('has_query'):
                    verdict = 'maybe'
                    reasons.append('栏目页带查询参数，需人工确认是否稳定')
                else:
                    verdict = 'suggest'
                    reasons.append('已登记源[%s]的同域栏目页，可直接入库' % c['from'])
            elif c['kind'] == 'seed':
                verdict = 'suggest'
                reasons.append('行业追踪器种子，探活通过')
            else:
                verdict = 'maybe'
                reasons.append('跨域站点，归属游戏待人工确认')
            name = c.get('name') or '%s·%s' % (c['game'], stype)
            sid = mk_id(c['url'], taken) if verdict == 'suggest' else ''
            snippet = toml_snippet(sid, c['game'], name, stype, c['url']) if sid else ''
        cnt[verdict] = cnt.get(verdict, 0) + 1
        items.append({'kind': c['kind'], 'game': c['game'], 'url': c['url'],
                      'from': c['from'], 'status': status, 'type': stype,
                      'verdict': verdict, 'reasons': reasons,
                      'suggest_id': sid, 'toml': snippet})
        print('  [%-7s] %-6s %-10s %s' % (verdict, c['kind'], c['game'], c['url'][:56]))

    now = datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    doc = {'generated_at': now,
           'summary': {'candidates': len(items), 'suggest': cnt.get('suggest', 0),
                       'maybe': cnt.get('maybe', 0), 'dead': cnt.get('dead', 0),
                       'registered': len(cfg.get('sources', []))},
           'items': items}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    io.open(OUT, 'w', encoding='utf-8').write(json.dumps(doc, ensure_ascii=False, indent=1))

    # ---- 人读报告 ----
    L = ['# 新信源建议（Phase 5 自发现）', '',
         '> 生成时间：%s ｜ 现有登记 %d 源 ｜ 本次候选 %d → 建议入库 %d ｜ 待确认 %d ｜ 探活失败 %d'
         % (now, doc['summary']['registered'], len(items), cnt.get('suggest', 0),
            cnt.get('maybe', 0), cnt.get('dead', 0)), '',
         '扩充信源的动作永远只有一个：**把下面的片段粘进 `sources.toml`**，管线自动消纳。', '']
    grp_s = [x for x in items if x['verdict'] == 'suggest']
    if grp_s:
        L.append('## ✅ 建议入库（%d 条，已 200 探活）' % len(grp_s))
        for x in grp_s:
            L.append('- **[%s]** `%s` — %s  ' % (x['game'], x['type'], x['url']))
            L.append('  - 理由：%s' % '；'.join(x['reasons']))
        L += ['', '### 可直接粘贴的 TOML', '', '```toml']
        for x in grp_s:
            L.append(x['toml'].rstrip())
            L.append('')
        L += ['```', '']
    grp_m = [x for x in items if x['verdict'] == 'maybe']
    if grp_m:
        L.append('## ⚠️ 待人工确认（%d 条）' % len(grp_m))
        for x in grp_m:
            L.append('- [%s] %s — %s' % (x['game'], x['url'], '；'.join(x['reasons'])))
        L.append('')
    grp_d = [x for x in items if x['verdict'] == 'dead']
    if grp_d:
        L.append('## ❌ 探活失败（%d 条，已自动丢弃）' % len(grp_d))
        for x in grp_d:
            L.append('- [%s] %s' % (x['game'], x['url']))
        L.append('')
    io.open(REPORT, 'w', encoding='utf-8').write('\n'.join(L))

    print('\n自发现完成：候选 %d → 建议入库 %d ｜ 待确认 %d ｜ 探活失败 %d'
          % (len(items), cnt.get('suggest', 0), cnt.get('maybe', 0), cnt.get('dead', 0)))
    print('  discovered ->', os.path.relpath(OUT, BASE))
    print('  report     ->', os.path.relpath(REPORT, BASE))

    if apply_toml and grp_s:
        shutil.copyfile(SRC, SRC + '.bak')
        with io.open(SRC, 'a', encoding='utf-8') as f:
            f.write('\n# --- Phase 5 自发现追加 %s ---\n' % now)
            for x in grp_s:
                f.write('\n' + x['toml'])
        print('  已追加 %d 条到 sources.toml（备份 sources.toml.bak）' % len(grp_s))
    return doc


def throttled():
    """7 天内跑过则跳过（daily_refresh 每日调用时避免重复联网）。
    例外：sources.toml 在上次扫描后被改过（说明信源池有变动）→ 立即重扫，
    否则旧建议里已入库的条目会一直挂在待办上误报。"""
    if not os.path.exists(OUT):
        return False
    try:
        d = json.load(io.open(OUT, encoding='utf-8'))
        t = datetime.datetime.strptime(d['generated_at'], '%Y-%m-%dT%H:%M:%S')
        if os.path.getmtime(SRC) > t.timestamp():
            print('自发现：sources.toml 已变更，忽略节流重新扫描')
            return False
        return (datetime.datetime.now() - t).days < THROTTLE_DAYS
    except Exception:
        return False


if __name__ == '__main__':
    args = set(sys.argv[1:])
    if not ('--force' in args or '--apply' in args) and throttled():
        print('自发现：%d 天内已扫描过，本次跳过（--force 可强制重跑）' % THROTTLE_DAYS)
        sys.exit(0)
    discover(apply_toml='--apply' in args)
