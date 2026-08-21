#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Phase 4 策展入口：把 admit_sources.py 判定为 adopt 的条目，一键晋升进 events.json 的
feed_events（信源快报），由 gen_calendar.py 渲染进 calendar.html 的 <<EVT_FEED>> 区块。
幂等：已晋升的 URL 不会重复添加；<<EVT_FEED>> 区块只注入一次。
非阻断：单条失败不影响整体。
"""
import sys, io, os, re, json, tomllib, datetime, urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__))
EVENTS = os.path.join(BASE, 'events.json')
CURATED = os.path.join(BASE, 'inbox', 'sources_curated.json')
SRC = os.path.join(BASE, 'sources.toml')
MAX_FEED = 40      # 页面上信源快报最多保留条数（超出淘汰最旧的，防止页面无限膨胀）
MAX_PER_GAME = 4   # 单个游戏最多占几条（防止某个更新频繁的官网把快报刷屏）
MAX_PER_IND_SRC = 2   # 行业源按"每家媒体"限额

# ---------- 时效红线（第二层收口）----------
# 之前这里完全不看日期，靠"上游每天采最新"隐性兜底 —— 一旦某个源停更或改版，
# 陈年旧稿就会顺着管道一路进页面，还会长期占着配额坑位。现在统一到三条硬规则：
#   ① 新晋升的条目，有日期且超期 → 不收
#   ② 存量条目，有日期且超期 → 逐日清扫出页
#   ③ 超出 MAX_FEED 时，按"发布日期(缺失则按首次收录日)"从旧到新淘汰，而不是砍列表头
FEED_MAX_AGE_DAYS = 5  # 用户红线①：全站新闻类内容必须 ≤5 天（原 30，过松）


_URL_DATE_PATS = (
    # /20260804/ 、/2026-08-04/
    (re.compile(r'/(20\d{2})[-_]?(\d{2})[-_]?(\d{2})/'), (1, 2, 3)),
    # /2026/08/04/
    (re.compile(r'/(20\d{2})/(\d{2})/(\d{2})/'), (1, 2, 3)),
    # 17173 风格 /content/08042026/xxx.shtml → 月日年
    (re.compile(r'/(\d{2})(\d{2})(20\d{2})/'), (3, 1, 2)),
)


def url_date(u):
    """从 URL 路径里抽真实发布日期。RSS 没给日期时，URL 路径是最可信的第二真值源：
    它由站点自己按发布日生成，比正文文本里随便一个日期靠谱得多。
    抽不出、或抽出的是未来日期 → 返回 None，绝不瞎猜。"""
    if not u:
        return None
    for pat, (yi, mi, di) in _URL_DATE_PATS:
        m = pat.search(u)
        if not m:
            continue
        try:
            d = datetime.date(int(m.group(yi)), int(m.group(mi)), int(m.group(di)))
        except ValueError:
            continue
        if d > datetime.date.today():
            continue
        return d.isoformat()
    return None


def days_ago(iso_date):
    if not iso_date:
        return None
    try:
        d = datetime.date.fromisoformat(str(iso_date)[:10])
    except ValueError:
        return None
    return (datetime.date.today() - d).days


def eff_date(e):
    """条目的有效时间锚：只认真实发布日期 pubdate。
    缺失 pubdate 一律视为'最旧'(1970)，宁可当陈稿清扫，也绝不拿采集日 first_seen 伪装成新鲜。
    （2026-07-31 踩坑：THE FINALS 一文真实发布于 2025-10-30，因无 pubdate 回落到 first_seen=当天，
     被误判为新鲜、违反用户 5 天红线。故取消 first_seen 兜底。）

    2026-08-05 补强：未来日期同样视为'最旧'。正文里的发售日（如"2026年9月17日发售"）
    会被日期抽取器误当成发布日期，这种条目宁可清扫也不能当新鲜稿放上站。"""
    _pd = (e.get('pubdate') or '1970-01-01')[:10]
    try:
        if datetime.date.fromisoformat(_pd) > datetime.date.today():
            return '1970-01-01'
    except ValueError:
        return '1970-01-01'
    return _pd


# ---------- 多源印证簇（2026-08-21 治根因①）----------
# 问题：无发布日期的 RSS 项被一律当陈稿跳过（见 promote 入口时效闸）。
# 但 RSS 只服务最新 N 条，当日采集到的无日期项几乎必是新鲜稿；真正风险是"旧稿丢失日期被当新鲜"。
# 折中：无日期项若属于「多源印证簇」（≥2 家不同信源报道同一话题）即视为当前热点，放行晋升
# （打 date_unverified 标，下次刷新按"无日期"清扫，不长期占坑）。这既救回 GTA6 类全球首发，
# 又挡住单源无日期的低值/陈稿（单源无日期仍跳过，守住"不可上陈货"）。
_CLUSTER_STOP_EN = {
    'the', 'and', 'for', 'with', 'news', 'game', 'games', 'update', 'updates', 'preview',
    'trailer', 'gameplay', 'review', 'best', 'top', 'new', 'what', 'when', 'why', 'how',
    'this', 'that', 'from', 'into', 'your', 'our', 'read', 'more', 'video', 'videos',
    'shows', 'show', 'live', 'now', 'here', 'you', 'are', 'all', 'has', 'have', 'had',
    'was', 'were', 'will', 'about', 'watch', 'via', 'play',
}
_CLUSTER_STOP_CJK = {
    '游戏', '手游', '端游', '玩家', '上线', '发售', '更新', '版本', '官方', '公告', '资讯',
    '新闻', '报道', '曝光', '实机', '预告', '演示', '评测', '专栏', '专题', '首页', '视频',
    '直播', '赛事', '战队', '选手', '电竞', '厂商', '发行', '平台', '社区', '活动', '详情',
    '内容', '最新', '今日', '本周', '本月', '消息', '动态', '进展', '公开', '正式', '情报',
    '速报', '快报', '汇总', '整理',
}

def _title_tokens(title):
    """抽取标题里的「显著 token」：英文 3+ 字母/数字串 + 中文 3 字组（去通用停用词）。

    用 3 字组而非 2 字组：2 字组「采访/难度/选择」等新闻通用词会在几十篇标题里共现，
    把无关稿件误并成一个"27 家印证"的假簇，既会让 QC 闸门误报、又会让单源无日期稿被误放行。
    3 字组（如「对马之魂」「黑神话钟」「gta6」对应的中文专名）几乎只在同 IP 报道里共现，
    聚类精度高得多；代价是 2 字短标题无 3 字组 → 不参与聚类（罕见，可接受）。
    """
    t = (title or '').lower()
    toks = set()
    for m in re.finditer(r'[a-z0-9]{3,}', t):
        tk = m.group(0)
        if tk not in _CLUSTER_STOP_EN:
            toks.add(tk)
    for m in re.finditer(r'[一-鿿]{3,}', t):
        run = m.group(0)
        for i in range(len(run) - 2):
            tg = run[i:i + 3]
            if tg not in _CLUSTER_STOP_CJK:
                toks.add('cj:' + tg)
    return toks

def build_cluster_sizes(adopts):
    """返回 {url: 该 url 所属簇的「不同信源数」}。同簇 = 标题共享至少一个显著 token。"""
    token_map = {}   # token -> [(url, src_id), ...]
    for it in adopts:
        u = it.get('url', '')
        if not u:
            continue
        sid = it.get('src_id') or it.get('src_name') or '?'
        for tk in _title_tokens(it.get('title', '')):
            token_map.setdefault(tk, []).append((u, sid))
    parent = {}
    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    urls = [it.get('url', '') for it in adopts if it.get('url')]
    for lst in token_map.values():
        us = sorted({u for u, _ in lst})
        for i in range(1, len(us)):
            union(us[0], us[i])
    cluster_srcs = {}
    for it in adopts:
        u = it.get('url', '')
        if not u:
            continue
        r = find(u)
        sid = it.get('src_id') or it.get('src_name') or '?'
        cluster_srcs.setdefault(r, set()).add(sid)
    return {u: len(cluster_srcs[find(u)]) for u in urls}


# 行业源（game 不是具体游戏，而是一个大类）。这类 game 值会被多家媒体共用，
# 若还按 game 记配额，等于 9 家行业媒体挤同 4 个坑 —— 新接入的媒体永远排不进去。
# 所以行业源改成"按信源"记配额：每家媒体各自 MAX_PER_IND_SRC 条。
INDUSTRY_GAMES = {'全行业', '主机/PC 大作', '主机/PC新作'}


def quota_key(game, src_id, src_name):
    """行业源按信源计配额，具体游戏按游戏计配额。"""
    if game in INDUSTRY_GAMES:
        return 'SRC:' + (src_id or src_name or game)
    return 'GAME:' + (game or '')


def quota_limit(key):
    return MAX_PER_IND_SRC if key.startswith('SRC:') else MAX_PER_GAME


# 高信号识别：给"登顶 Steam / 正式发售 / 字节新游"这类大稿加权保送，
# 避免被"每家行业媒体最多 2 条"的配额挤掉（2026-07-31 雾影猎人 GameLook 稿被挤掉即此因）。
HIGH_SIGNAL = re.compile(
    r'登顶|畅销榜|热销榜|热度第一|夺冠|夺魁|破圈|爆款|首?发|公测|发售|上线|定档|'
    r'字节新游|新游|亿元|口碑|首日|打破|创新高|引爆|里程碑')
LAUNCH_EXTRA = re.compile(r'登顶|畅销榜|热销榜|夺冠|首发|公测|发售|上线|定档|首日|破圈|爆款')


def priority(item):
    """返回条目重要度分值（越高越该保送）。标题信号 + 新鲜度。

    加新鲜度是为了堵一个洞：光看标题信号，一篇三周前的「XX 登顶热销榜」
    会永远压着今天刚发的普通稿 —— 高信号变成了"占坑护身符"。
    """
    t = item.get('title', '') or ''
    s = 0
    if HIGH_SIGNAL.search(t):
        s += 10
    if LAUNCH_EXTRA.search(t):
        s += 5
    if '字节新游' in t or '新游' in t:
        s += 3
    # 具体游戏名/《》出现，视为有明确指向的游戏事件，略加权
    if re.search(r'《[^》]+》', t):
        s += 2
    # 新鲜度：3 天内 +6，7 天内 +4，14 天内 +2，超 14 天不加
    age = days_ago(item.get('pubdate') or item.get('first_seen'))
    if age is not None:
        if age <= 3:
            s += 6
        elif age <= 7:
            s += 4
        elif age <= 14:
            s += 2
    return s


def _key_pri(e):
    """feed 里已有条目（dict）的配额键 + 优先级，便于再平衡/保送比较。"""
    k = quota_key(e.get('game'), e.get('src_id'), e.get('source_name'))
    return k, priority({'title': e.get('title', ''),
                        'pubdate': e.get('pubdate'),
                        'first_seen': e.get('first_seen')})


def _host(u):
    h = urllib.parse.urlparse(u).netloc.lower().split(':')[0]
    return h[4:] if h.startswith('www.') else h


# 信源显示名：优先按域名匹配（行业源共用 game="全行业"，按 game 会互相覆盖），回退按游戏名
def load_src_names():
    by_host, by_game = {}, {}
    try:
        with open(SRC, 'rb') as f:
            cfg = tomllib.load(f)
        for s in cfg.get('sources', []):
            nm = s.get('name', s.get('game'))
            by_host[_host(s.get('url', ''))] = nm
            by_game.setdefault(s.get('game'), nm)
    except Exception:
        pass
    return by_host, by_game


def pick_name(url, game, by_host, by_game):
    h = _host(url)
    if h in by_host:
        return by_host[h]
    for k, v in by_host.items():          # 允许子域回退（news.x.com -> x.com）
        if k and (h.endswith('.' + k) or k.endswith('.' + h)):
            return v
    return by_game.get(game, (game or '') + ' 官方')


def clean_title(t, n=48):
    t = re.sub(r'\s+', ' ', t or '').strip()
    return t[:n] + ('…' if len(t) > n else '')


def build_anchor(game, title, srcname):
    t = clean_title(title)
    safe_t = t.replace('"', '&quot;')
    safe_game = game.replace('"', '&quot;')
    safe_src = srcname.replace('"', '&quot;')
    return ('<tr><td><b style="color:var(--gold)">%s</b></td>'
            '<td><a target="_blank" href="__SRC__" title="%s（来源：%s）" '
            'style="color:#4da3ff;text-decoration:none">%s</a></td>'
            '<td>%s</td></tr>') % (safe_game, safe_t, safe_src, safe_t, safe_src)


FEED_BLOCK = ('\n<div style="margin:14px 0 6px;font-weight:700;color:var(--gold)">'
             '📰 信源快报（自动收录 · 每日更新）</div>\n'
             '<table class="fw-table"><thead><tr><th>游戏</th>'
             '<th>最新情报「自动收录」</th><th>来源</th></tr></thead>'
             '<tbody><<EVT_FEED>></tbody></table>')


def promote():
    doc = json.load(io.open(EVENTS, encoding='utf-8'))
    feed = doc.setdefault('feed_events', [])
    by_host, by_game = load_src_names()
    existing = {e.get('source_url') for e in feed}
    seq = int(doc.get('meta', {}).get('feed_seq', len(feed)))

    # 读准入结果
    try:
        cur = json.load(io.open(CURATED, encoding='utf-8'))
        adopts = [i for i in cur.get('items', []) if i.get('verdict') == 'adopt']
    except Exception as e:
        print('⚠ 未读到准入结果（inbox/sources_curated.json）：', repr(e)[:120])
        return
    # 2026-08-21 治根因①：预计算「多源印证簇」尺寸（无日期项放行判定用）
    cluster_sizes = build_cluster_sizes(adopts)

    # 存量自愈：早期版本按域名反推显示名，把 5 个共用 store.steampowered.com 的 Steam 源
    # 全贴成了同一个名字（DOTA2 的公告署名"暗区突围"）。这里用准入结果里的真实信源名回填。
    truth = {i.get('url'): (i.get('src_id'), i.get('src_name'))
             for i in cur.get('items', []) if i.get('src_name')}
    # 只用权威日期（RSS 字段 / URL 路径）回填存量，页面文本猜的日期不足以据此清扫
    date_truth = {i.get('url'): i.get('pubdate') for i in cur.get('items', [])
                  if i.get('pubdate') and i.get('date_src') in ('feed', 'url')}
    today = datetime.date.today().isoformat()
    fixed = 0
    redated = 0
    for e in feed:
        t = truth.get(e.get('source_url'))
        if t:
            sid, sname = t
            if e.get('source_name') != sname or not e.get('src_id'):
                e['src_id'] = sid
                e['source_name'] = sname
                e['anchor'] = build_anchor(e.get('game', ''), e.get('title', ''), sname)
                fixed += 1
        # 日期校准（2026-08-05 治理）：原逻辑只在「缺 pubdate」时回填，
        # 于是一旦某轮采集写进了错日期（例如正文文本猜的发售日、或采集当天日期），
        # 后续再怎么跑都纠不回来——今天就抓到 8 条把 08-04 的稿子标成 08-05。
        # 现在改为：只要拿得到权威日期（RSS 字段 / URL 路径），一律以权威日期为准覆盖。
        auth = date_truth.get(e.get('source_url')) or url_date(e.get('source_url', ''))
        if auth and e.get('pubdate') != auth:
            e['pubdate'] = auth
            redated += 1
        if not e.get('pubdate') and not e.get('first_seen'):
            e['first_seen'] = today
    if fixed:
        print('  （存量署名自愈：%d 条来源名按真实信源回填）' % fixed)
    if redated:
        print('  （日期校准：%d 条 pubdate 按权威日期(RSS/URL)覆盖）' % redated)

    # 存量时效清扫：超过保鲜期的，直接请出页面（不占配额坑）
    # 缺失 pubdate 的条目 days_ago 返回 None → 用 9999 兜底，确保'无日期'一律当陈稿清扫；
    # 注意：不能用 `or 9999`，因为 age=0(今天) 时 `0 or 9999` 会误用 9999 把当天新稿错杀。
    def _age(e):
        a = days_ago(e.get('pubdate'))
        return 9999 if a is None else a
    stale = [e for e in feed if _age(e) > FEED_MAX_AGE_DAYS]
    if stale:
        for e in stale:
            _age = days_ago(e.get('pubdate'))
            _age_s = ('%d 天' % _age) if _age is not None else '无发布日期'
            print('  （时效清扫：%s「%s」%s，移出快报）'
                  % (e.get('source_name', ''), (e.get('title') or '')[:22], _age_s))
        feed[:] = [e for e in feed if e not in stale]
        existing = {e.get('source_url') for e in feed}

    from collections import Counter, defaultdict
    # 存量再平衡：每个配额组「按优先级」保留前 N 条（而非仅按时间），
    # 保证高信号稿在存量阶段就不被低优条目挤掉。
    groups = defaultdict(list)
    for idx, e in enumerate(feed):
        k, p = _key_pri(e)
        groups[k].append((idx, p, e))
    keep_idx = []
    for k, items in groups.items():
        lim = quota_limit(k)
        # 优先级降序；同优先级保持原顺序（idx 小优先）
        items.sort(key=lambda x: (-x[1], x[0]))
        for idx, p, e in items[:lim]:
            keep_idx.append(idx)
    keep_idx.sort()
    rebalanced = len(feed) - len(keep_idx)
    if rebalanced:
        feed[:] = [feed[i] for i in keep_idx]
        existing = {e.get('source_url') for e in feed}
        print('  （存量再平衡：按优先级精简 %d 条 · 单游戏 %d / 单家行业媒体 %d）'
              % (rebalanced, MAX_PER_GAME, MAX_PER_IND_SRC))
    per_key = Counter(quota_key(e.get('game'), e.get('src_id'), e.get('source_name')) for e in feed)

    added, quota_skip, stale_skip, unverified_promote = 0, 0, 0, 0
    # 高信号优先处理：先保送重要的，再处理普通稿（普通稿配额满就跳过）
    for it in sorted(adopts, key=lambda x: -priority(x)):
        url = it.get('url', '')
        game = it.get('game', '')
        if not url or url in existing:
            continue
        # 入口时效闸（准入已拦一道，这里是最后一关，防止绕过准入直接塞数据）
        age = it.get('age_days')
        if age is None:
            age = days_ago(it.get('pubdate'))
        if age is None:
            # 2026-08-21 治根因①：无日期项若属「多源印证簇」(≥2 家不同信源报道同一话题)，
            # 视为当前热点放行（打 date_unverified 标，下次刷新按"无日期"清扫，不长期占坑），
            # 救回 GTA6 类全球首发；单源无日期仍跳过（挡住低值/陈稿，守住"不可上陈货"）。
            if cluster_sizes.get(url, 1) >= 2:
                age = 0
                it = dict(it)
                it['date_unverified'] = True
                unverified_promote += 1
            else:
                stale_skip += 1
                continue
        if age > FEED_MAX_AGE_DAYS and it.get('date_src') in ('feed', 'url'):
            stale_skip += 1
            continue
        src_id = it.get('src_id')
        # 显示名优先用准入环节带下来的真实信源名；老数据缺字段时才回退到按域名猜
        srcname = it.get('src_name') or pick_name(url, game, by_host, by_game)
        k = quota_key(game, src_id, srcname)
        p = priority(it)
        if per_key[k] < quota_limit(k):
            # 名额未满，正常晋升
            per_key[k] += 1
        elif p > 0:
            # 配额已满，但这是高信号稿 → 加权保送：若同源存在更低优先级条目则替换之
            low = None
            for e in feed:
                ek, ep = _key_pri(e)
                if ek != k:
                    continue
                if low is None or ep < low[1]:
                    low = (e, ep)
            if low and low[1] < p:
                feed.remove(low[0])
                per_key[k] -= 1
                per_key[k] += 1
                print('  （高信号保送：%s 的「%s」替换同源低优条目）'
                      % (srcname, (it.get('title', '') or '')[:24]))
            else:
                quota_skip += 1
                continue
        else:
            quota_skip += 1
            continue
        seq += 1
        feed.append({
            'id': 'F%03d' % seq,
            'kind': 'feed',
            'game': game,
            'title': it.get('title', ''),
            'source_url': url,
            'source_name': srcname,
            'src_id': src_id,
            'pubdate': it.get('pubdate'),
            'first_seen': today,
            'date_unverified': it.get('date_unverified', False),
            'anchor': build_anchor(game, it.get('title', ''), srcname),
        })
        existing.add(url)
        added += 1

    if added == 0 and not rebalanced and not stale:
        print('无可晋升新条目（adopt 已全部在页，或当前无 adopt）。')
    else:
        # 首次注入 <<EVT_FEED>> 区块（放在前瞻哨表之后）
        sc = doc['scaffold']
        if '<<EVT_FEED>>' not in sc:
            p = sc.find('<<EVT_75>>')
            q = sc.find('</table>', p)
            if q != -1:
                sc = sc[:q + len('</table>')] + FEED_BLOCK + sc[q + len('</table>'):]
                doc['scaffold'] = sc
        # 限流：只保留最新 MAX_FEED 条。
        # 旧写法 del feed[:n] 是"砍列表头"，等于按入库顺序淘汰；
        # 现在按有效日期（发布日 > 首次收录日）从旧到新淘汰，真正的"留新汰旧"。
        dropped = 0
        if len(feed) > MAX_FEED:
            dropped = len(feed) - MAX_FEED
            order = sorted(range(len(feed)), key=lambda i: (eff_date(feed[i]), i))
            drop_set = set(order[:dropped])
            feed[:] = [e for i, e in enumerate(feed) if i not in drop_set]
            doc['feed_events'] = feed
        doc['meta'] = dict(doc.get('meta', {}))
        doc['meta']['feed_count'] = len(feed)
        doc['meta']['feed_seq'] = seq
        if dropped:
            print('  （超出上限 %d，淘汰最旧 %d 条）' % (MAX_FEED, dropped))
        io.open(EVENTS, 'w', encoding='utf-8').write(json.dumps(doc, ensure_ascii=False, indent=1))
        print('晋升 %d 条到信源快报（累计 %d 条）。events.json 已更新。' % (added, len(feed)))
        print('  → 下一步 gen_calendar.py 会把它们渲染进 calendar.html 的「信源快报」区块。')

    if quota_skip:
        print('  （%d 条因配额未晋升，留在准入报告里备查：单游戏 %d / 单家行业媒体 %d）'
              % (quota_skip, MAX_PER_GAME, MAX_PER_IND_SRC))
    if stale_skip:
        print('  （%d 条因超过 %d 天保鲜期未晋升）' % (stale_skip, FEED_MAX_AGE_DAYS))
    if unverified_promote:
        print('  （%d 条无日期但多源印证，照常晋升并标 date_unverified）' % unverified_promote)
    dated = sum(1 for e in feed if e.get('pubdate'))
    print('  时效体检：%d/%d 条可判定发布日期，最旧 %s' % (dated, len(feed),
          min((eff_date(e) for e in feed), default='-')))
    print('当前信源快报条目数：%d' % len(feed))


if __name__ == '__main__':
    promote()
