#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Phase 3 准入校验门：对 collect_sources.py 丢进 inbox 的候选链接逐条做质量安检。
规则（对齐"自洽提示词体系"红线③）：
  1) HTTP 200 探活（死链直接剔除）
  2) 域名与游戏匹配（GAME_DOMAIN，防张冠李戴）
  3) 非首页/根页占位、非搜索页、非商店购买页
  4) 尽量是"新闻/公告/赛事"类深链，而非栏目/专题页（栏目页标待复核）
输出 inbox/sources_curated.json（机器用）+ inbox/准入报告.md（人读）。
非阻断：单条失败不影响整体。
"""
import sys, io, os, re, json, datetime, urllib.parse, urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
INBOX = os.path.join(BASE, 'inbox', 'sources_inbox.json')
CURATED = os.path.join(BASE, 'inbox', 'sources_curated.json')
REPORT = os.path.join(BASE, 'inbox', '准入报告.md')
KNOWN = os.path.join(BASE, 'inbox', 'known_rejects.json')   # 已知长期死链，避免天天重复报警
UA = 'Mozilla/5.0 (GamePulse Radar admission-gate)'

# 游戏名 -> 允许的域名片段（可多个：官网 + Steam 官方新闻等第一方渠道）
# 说明：Steam 新闻 RSS 链接形如 store.steampowered.com/news/app/<appid>/view/...，
# 是发行商在 Steam 上发布的**官方公告**，属第一方信源，故列为合法域名。
GAME_DOMAIN = {
    '恋与深空': ['deepspace.papegames'], '原神': ['ys.mihoyo'], '鸣潮': ['kurogames'],
    'LOL手游': ['lolm.qq'], '崩坏星穹铁道': ['sr.mihoyo'], '火影忍者': ['hyrz.qq'],
    '三国志': ['sgz.ejoy'], '三国志·战略版': ['sgz.ejoy'],
    '永劫无间': ['yjwujian', 'store.steampowered.com/news'],
    '萤火突击': ['yhtj.163'], '绝区零': ['zzz.mihoyo'], '晶核': ['coa.nvsgames'],
    '暗黑不朽': ['blizzard.com'], '王者万象棋': ['pvp.qq'], '王者荣耀': ['pvp.qq'],
    'CS2': ['counter-strike', 'store.steampowered.com/news'], '第五人格': ['id5.163'],
    '明日方舟：终末地': ['endfield'], '明日方舟': ['ak.hypergryph'],
    'DOTA2': ['dota2', 'store.steampowered.com/news'], '阴阳师': ['yys.163'],
    '和平精英': ['gp.qq'],
    '三角洲行动': ['df.qq', 'store.steampowered.com/news'],
    'DNF手游': ['dnf.qq'],
    '暗区突围': ['aqtw.qq', 'store.steampowered.com/news'],
    '金铲铲之战': ['jcc.qq'], '无畏契约': ['news.qq.com'],
    # 主机/PC 品类是"多第一方渠道"：Steam 商店/新闻 + Xbox Wire（后续接 PS 再往里加）
    '主机/PC新作': ['steampowered', 'xbox.com'],
    '主机/PC 大作': ['steampowered', 'xbox.com'],
}
# 共享宽域名的游戏（命中只能算"弱匹配"，需人工复核）
SHARED = {}
for _g, _ds in GAME_DOMAIN.items():
    for _d in _ds:
        SHARED[_d] = SHARED.get(_d, 0) + 1
# Steam 新闻域被多游戏共用，但它是按 appid 精确定位的官方公告，不算"张冠李戴"，豁免弱匹配
SHARED['store.steampowered.com/news'] = 1

ARTICLE_HINT = re.compile(
    r'(/news/|/update/|/official/|/gonggao/|/skill/|/cp/|/match/|/sshh7/|/detail'
    r'|/article|/articles/|/content/|/post|/story|/topic/|/blog/'      # 行业媒体常见文章路径
    r'|/\d{4}/\d{2}/|\.shtml$|/\d{6,})', re.I)
SEARCH_RE = re.compile(r'search\.|/s\?|keyword=', re.I)
STORE_RE = re.compile(r'store\.steampowered\.com/app/|/store/|epicgames\.com/store|apps\.apple\.com|ps\.com', re.I)

# 行业源（非特定游戏）相关性闸：标题必须带游戏行业信号，否则只当"待复核"。
# 目的：挡住行业媒体首页混进来的娱乐八卦/硬件广告/栏目名。
GAME_SIGNAL_RE = re.compile(
    r'(《|》|游戏|手游|端游|页游|主机|电竞|赛事|战队|选手|玩家|开发者|厂商|发行'
    r'|上线|定档|发售|预购|公测|内测|删档|开服|停服|版本|更新|资料片|DLC|联动|皮肤'
    r'|Steam|Epic|PS5|PS4|PSN|Xbox|Switch|NS|PC|VR|Demo|试玩|实机|预告'
    r'|腾讯|网易|米哈游|鹰角|库洛|叠纸|完美世界|三七|莉莉丝|育碧|暴雪|动视|任天堂|索尼|世嘉|卡普空|SE\b)',
    re.I)
# 视为"非特定游戏"的行业源标签
INDUSTRY_GAMES = {'全行业', '主机/PC 大作', '主机/PC新作'}

# ---------- 时效红线（第二层：行业情报）----------
# 词云那层是"只认当天"，行业情报做不到那么严（官网公告一周才更一条很正常），
# 所以这里用"最大保鲜期"：能读到发布日期且超过 MAX_AGE_DAYS 的，直接剔除。
# 读不到日期的不硬杀（很多国内官网页面确实不写日期），但会标记 undated，
# 由晋升环节排序时压到有日期的新鲜条目后面 —— 宁可少上，不可上陈货。
MAX_AGE_DAYS = 5  # 用户红线①：全站新闻类内容必须 ≤5 天（原为 30，过松）

def days_ago(iso_date):
    if not iso_date:
        return None
    try:
        d = datetime.date.fromisoformat(str(iso_date)[:10])
    except ValueError:
        return None
    return (datetime.date.today() - d).days


def fetch(url, timeout=10):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': UA})
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, None
    except Exception as e:
        return None, repr(e)[:140]


def _host(u):
    h = urllib.parse.urlparse(u).netloc.lower().split(':')[0]
    return h[4:] if h.startswith('www.') else h


def domain_ok(game, url, src_url=None):
    """域名一致性校验。
    ① 游戏在 GAME_DOMAIN 表内 → 按表比对（防张冠李戴）。
    ② 表外（行业源如"全行业"、新入库游戏）→ 回退为"与该信源注册域名同域"，
       即候选链接必须来自它被抓到的那个站点本身，同样杜绝跨站错配。
    返回 (是否通过, 是否弱匹配需复核)。
    """
    frags = GAME_DOMAIN.get(game)
    p = urllib.parse.urlparse(url)
    netloc, hostpath = p.netloc, p.netloc + p.path
    if frags:
        for f in frags:
            # 含 '/' 的片段按 域名+路径 比对（如 store.steampowered.com/news），否则只比域名
            if (f in hostpath) if '/' in f else (f in netloc):
                return True, (SHARED.get(f, 1) > 1)
        return False, False
    if src_url:
        sh, ch = _host(src_url), _host(url)
        # 同主域即可（允许 news.x.com 与 x.com 互认）
        ok = (ch == sh) or ch.endswith('.' + sh) or sh.endswith('.' + ch)
        return ok, False  # 信源本身经人工登记在 sources.toml，同域深链即视为已溯源
    return False, False


def admit():
    doc = json.load(io.open(INBOX, encoding='utf-8'))
    out_items = []
    cnt = {'adopt': 0, 'review': 0, 'reject': 0, 'stale': 0}
    seen_url = set()          # 全局去重：多个源抓到同一条时只安检一次（省探活）
    dup = 0
    for src in doc.get('sources', []):
        game = src.get('game')
        src_url = src.get('url')
        # 信源身份随条目一路带下去，别让下游靠域名反推。
        # 反面教材：5 个 Steam 官方 RSS 共用 store.steampowered.com 域名，
        # 下游按域名查显示名 → 全被贴成最后登记的那个源（DOTA2 的公告署名"暗区突围"）。
        src_id = src.get('id')
        src_name = src.get('name') or game
        for it in src.get('items', []):
            title, url = it.get('title', ''), it.get('url', '')
            key = url.rstrip('/')
            if key in seen_url:
                dup += 1
                continue
            seen_url.add(key)
            reasons = []
            status, err = fetch(url)
            ok, weak = domain_ok(game, url, src_url)
            path = urllib.parse.urlparse(url).path
            is_root = path in ('', '/')
            is_search = bool(SEARCH_RE.search(url))
            is_store = bool(STORE_RE.search(url))
            is_article = bool(ARTICLE_HINT.search(path))

            if status != 200:
                reasons.append('链接不可达(HTTP %s)' % status)
            if not ok:
                reasons.append('域名与游戏[%s]不符' % game)
            if is_root:
                reasons.append('指向首页/根页(占位链接)')
            if is_search:
                reasons.append('搜索页(红线③禁)')
            if is_store:
                reasons.append('商店购买页(红线③禁)')
            if not is_article and ok and status == 200 and not (is_root or is_search or is_store):
                reasons.append('疑似栏目/专题页(非新闻条目，建议人工挑)')

            # 行业源相关性闸：非特定游戏的信源，标题必须带游戏行业信号
            off_topic = False
            if game in INDUSTRY_GAMES and not GAME_SIGNAL_RE.search(title or ''):
                off_topic = True
                reasons.append('行业源标题无游戏信号（疑似娱乐/硬件/栏目名）')

            # 时效闸：按日期可信度分流
            #   feed/url 来源（RSS 字段、URL 路径）→ 权威，超期直接剔除
            #   text 来源（列表页文本窗口猜的）→ 可能抠到隔壁条目的日期，只降级复核，
            #   免得把当天的新稿误判成陈年旧文（触乐踩过这个坑）
            pub = it.get('pubdate')
            dsrc = it.get('date_src')
            age = it.get('age_days')
            if age is None:
                age = days_ago(pub)
            over = (age is not None and age > MAX_AGE_DAYS)
            stale = over and dsrc in ('feed', 'url')
            stale_soft = over and not stale
            if stale:
                reasons.append('发布日期已 %d 天，超过 %d 天保鲜期（时效红线）' % (age, MAX_AGE_DAYS))
            elif stale_soft:
                reasons.append('疑似陈稿：页面文本显示 %d 天前（日期非权威来源，待人工确认）' % age)

            hard_fail = (status != 200) or (not ok) or is_root or is_search or is_store or stale
            if hard_fail:
                verdict = 'reject'
            elif (not is_article) or weak or off_topic or stale_soft:
                verdict = 'review'
            else:
                verdict = 'adopt'
            cnt[verdict] += 1
            if stale:
                cnt['stale'] += 1
            out_items.append({
                'game': game, 'title': title, 'url': url, 'status': status,
                'src_id': src_id, 'src_name': src_name,
                'pubdate': pub, 'age_days': age, 'date_src': dsrc,
                'verdict': verdict, 'reasons': reasons,
            })
            print('  [%-7s] %-10s %s' % (verdict, game, title[:34]))

    now = datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')

    # 剔除项分"新增/已知"：官网页面上长期存在的死链只在首次发现时报警，之后转为静默存档，
    # 避免自洽日志天天亮红灯导致告警疲劳（告警应对"变化"敏感，而非对"稳态"敏感）。
    try:
        known = json.load(io.open(KNOWN, encoding='utf-8'))
    except Exception:
        known = {}
    rej_urls = [x['url'] for x in out_items if x['verdict'] == 'reject']
    new_rejects = [u for u in rej_urls if u not in known]
    for u in rej_urls:
        known.setdefault(u, now)
    io.open(KNOWN, 'w', encoding='utf-8').write(json.dumps(known, ensure_ascii=False, indent=1))

    curated = {
        'generated_at': now,
        'summary': {'total': len(out_items),
                    'adopt': cnt['adopt'], 'review': cnt['review'], 'reject': cnt['reject'],
                    'stale_rejected': cnt['stale'],
                    'dated': sum(1 for x in out_items if x.get('pubdate')),
                    'new_reject': len(new_rejects)},
        'new_rejects': new_rejects,
        'items': out_items,
    }
    io.open(CURATED, 'w', encoding='utf-8').write(json.dumps(curated, ensure_ascii=False, indent=1))

    # 人读报告
    lines = ['# 信源准入报告（Phase 3 安检门）', '',
             '> 生成时间：%s ｜ 候选 %d 条 → 可采用 %d ｜ 待复核 %d ｜ 剔除 %d' % (
                 now, len(out_items), cnt['adopt'], cnt['review'], cnt['reject']), '']
    for v, label in (('adopt', '✅ 可采用'), ('review', '⚠️ 待复核'), ('reject', '❌ 剔除')):
        grp = [x for x in out_items if x['verdict'] == v]
        if not grp:
            continue
        lines.append('## %s（%d 条）' % (label, len(grp)))
        for x in grp:
            rs = '；'.join(x['reasons']) if x['reasons'] else '全部通过'
            lines.append('- **[%s]** %s — %s  ' % (x['game'], x['title'], x['url']))
            lines.append('  - 安检：%s' % rs)
        lines.append('')
    io.open(REPORT, 'w', encoding='utf-8').write('\n'.join(lines))

    print('\n准入安检完成：候选 %d（跨源重复 %d 条已合并）→ 可采用 %d ｜ 待复核 %d ｜ 剔除 %d'
          '（其中超期 %d ｜ 可判定日期 %d 条）' % (
              len(out_items), dup, cnt['adopt'], cnt['review'], cnt['reject'],
              cnt['stale'], sum(1 for x in out_items if x.get('pubdate'))))
    print('  curated ->', os.path.relpath(CURATED, BASE))
    print('  report  ->', os.path.relpath(REPORT, BASE))
    return curated


if __name__ == '__main__':
    admit()
