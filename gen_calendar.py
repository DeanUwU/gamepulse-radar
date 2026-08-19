#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Phase 1 生成器：读 events.json -> 提取日历 section -> 注入 index.html。

2026-08-18 改造：日历表格不再用 scaffold 写死的 07-27~08-30 月历，
改为按今天 ±WINDOW 天【动态生成】月历网格（跟着今天滚动，零手工维护）。
events[] 必须有 date_start/date_end（由 migrate_event_dates.py 抽取）。

占位符 <<EVT_i>> / <<EVT_FEED>> 逻辑保留（forward/feed 段仍用 scaffold），
但 #cal 主体表格改为动态生成。
"""
import json, io, os, sys, datetime, re

BASE = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.environ.get("GC_INDEX",
                            os.path.join(BASE, "index.html"))
WINDOW = int(os.environ.get("GC_WINDOW", "30"))  # ±30 天（信息量更全）

doc = json.load(io.open(os.path.join(BASE, 'events.json'), encoding='utf-8'))
scaffold = doc['scaffold']
events = doc['events']
feed_events = doc.get('feed_events', [])

def render(ev):
    return ev['anchor'].replace('__SRC__', ev.get('source_url', ''))

def parse_date(s):
    if not s:
        return None
    try:
        y, m, d = map(int, s.split('-'))
        return datetime.date(y, m, d)
    except (ValueError, AttributeError):
        return None

today = datetime.date.today()
lo = today  # 只保留今天起未来，不看过去（省空间）
hi = today + datetime.timedelta(days=WINDOW)

# 按日期归集事件（区间事件只放 date_start 那天，不展开）
ev_by_day = {}
MAX_PER_CELL = int(os.environ.get("GC_MAX_PER_CELL", "8"))  # 每日最多显示条数
for ev in events:
    ds = parse_date(ev.get('date_start'))
    if not ds:
        continue
    # 只放起始日；区间事件不展开到后续每天
    if today <= ds <= hi:
        ev_by_day.setdefault(ds, []).append(ev)
    # date_end 不展开（用户要求：只看开始时间）

# 生成「未来动作日程表」（紧凑单列：一行一事件，日期合并，取代月历与卡片堆叠）
_WEEK = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
# 类型推断：从标题/动作文字里的关键词判定类型，映射到左侧色点（信息编码）
# 顺序即优先级：版本 > 联动 > 赛事 > 公测 > 发售（版本类关键词须先于「上线/发售」判断）
_TYPE_RULES = [
    (('版本', '卡池', '角色', '复刻', '资料片'), 'ver', '版本'),
    (('联动', '联名'), 'col', '联动'),
    (('赛事', '联赛', '总决赛', '淘汰赛', '杯赛'), 'evt', '赛事'),
    (('公测', '内测', '测试服', 'Beta'), 'beta', '公测'),
    (('发售', '上线', '登陆', '推出', '正式版', '重制', '双发', '同日'), 'rel', '发售'),
]
_TYPE_COLOR = {'rel': '#4da3ff', 'ver': '#c792ea', 'col': '#ff5c39',
               'evt': '#3fd68f', 'beta': '#ffb020', '': '#8b949e'}

def _strip_game(title, game):
    """去掉 title 里重复的游戏名前缀（collector 曾生成 '鸣潮：鸣潮：...'）。"""
    t = (title or '').strip()
    if game and t.startswith(game + '：'):
        t = t[len(game) + 1:].strip()
    if game and t.startswith(game + ':'):
        t = t[len(game) + 1:].strip()
    return t

def _ev_type(ev):
    """从事件文字推断类型，返回 (type_key, 中文标签)。"""
    text = (ev.get('title') or '') + ' ' + (ev.get('source_name') or '')
    game = ev.get('game') or ''
    # 「主机/PC新作」默认就是发售（除非文字含联动/版本/赛事等强信号）
    if game == '主机/PC新作':
        for kw in ('联动', '版本', '赛事', '卡池', '公测', '角色'):
            if kw in text:
                break
        else:
            return 'rel', '发售'
    for kws, key, label in _TYPE_RULES:
        for kw in kws:
            if kw in text:
                return key, label
    return '', '其他'

def _ev_text(ev):
    """事件动作文字（纯文本，去标签、去游戏名前缀）。"""
    t = _strip_game(ev.get('title'), ev.get('game'))
    t = re.sub(r'<[^>]+>', '', t)
    return t.strip()

def _day_label(d):
    """日期 → 人性化标签：今天 / 明天 / 后天 / 周X(月-日)。"""
    delta = (d - today).days
    if delta == 0:
        return '今天'
    if delta == 1:
        return '明天'
    if delta == 2:
        return '后天'
    return '%s %02d-%02d' % (_WEEK[d.weekday()], d.month, d.day)

def build_timeline():
    """紧凑日程表：一行一事件，同一天日期合并，空天不占行。"""
    days = sorted([d for d in ev_by_day if d >= today])
    if not days:
        return '<div class="tl-empty">未来 30 天暂无已确认的游戏动作，等待每日检索刷新。</div>'
    rows = ['<div class="cal-agenda">']
    for d in days:
        day_evs = ev_by_day[d]
        shown = day_evs[:MAX_PER_CELL]
        overflow = len(day_evs) - MAX_PER_CELL
        is_today = (d == today)
        label = _day_label(d)
        for idx, ev in enumerate(shown):
            # 同一天第一条显示日期，其余日期用占位（visibility:hidden 防 flex 塌陷）
            if idx == 0:
                date_html = '<span class="ag-date%s">%s</span>' % (
                    ' ag-today' if is_today else '', label)
            else:
                date_html = '<span class="ag-date ag-date-dim">·</span>'
            game = ev.get('game', '')
            text = _ev_text(ev)
            tkey, tlabel = _ev_type(ev)
            color = _TYPE_COLOR.get(tkey, _TYPE_COLOR[''])
            row_cls = 'ag-row ag-today-row' if is_today else 'ag-row'
            rows.append(
                '<a class="%s" target="_blank" href="%s" title="%s">%s'
                '<span class="ag-body">'
                '<span class="ag-game">%s</span>'
                '<span class="ag-sep">·</span>'
                '<span class="ag-text">%s</span>'
                '</span>'
                '<span class="ag-type" style="--c:%s"><i></i>%s</span></a>'
                % (row_cls, ev.get('source_url', ''), text,
                   date_html, game, text, color, tlabel))
        if overflow > 0:
            rows.append('<div class="ag-more">+%d 条同日事件</div>' % overflow)
    rows.append('</div>')
    return ''.join(rows)

cal_table = build_timeline()

# hero 文案动态化
span_lo = today.strftime('%m-%d')
span_hi = hi.strftime('%m-%d')
future_cnt = sum(len(ev_by_day[d]) for d in ev_by_day if d >= today)
hero_note = ('未来 %d 天（%s ~ %s）· %d 个游戏动作，点条目直达原文。'
             % (WINDOW, span_lo, span_hi, future_cnt))

# 构造新的 #cal section（紧凑日程表：HTML+结构，样式已外移到 style.css）
cal_section_new = (
    '<section id="cal">'
    + '<div class="hero"><h1>版本日历 · 未来动作</h1><p>' + hero_note + '</p></div>'
    + cal_table
    + '<div class="cal-legend"><span><i style="background:#4da3ff"></i>发售</span>'
    + '<span><i style="background:#c792ea"></i>版本</span>'
    + '<span><i style="background:#ff5c39"></i>联动</span>'
    + '<span><i style="background:#3fd68f"></i>赛事</span>'
    + '<span><i style="background:#ffb020"></i>公测</span></div>'
    + '</section>'
)

# --- 提取 forward / feed 段（仍来自 scaffold 占位符回填） ---
out = scaffold
today_mmdd = today.strftime('%m-%d')
out = re.sub(r'今天（\d{2}-\d{2}）', '今天（%s）' % today_mmdd, out)
retired = []
for i, ev in enumerate(events):
    token = '<<EVT_%d>>' % i
    if token not in out:
        if not (ev.get('anchor') or '').strip():
            retired.append(i)
        # forward/feed 占位符找不到是正常的（已在动态日历里处理过的跳过）
        continue
    out = out.replace(token, render(ev), 1)
if retired:
    print('    已退役节点 %d 个（anchor 空，跳过回填）: %s'
          % (len(retired), ','.join(map(str, retired))))

# Phase 4：信源快报
feed_html = ''.join(render(ev) for ev in feed_events)
if '<<EVT_FEED>>' in out:
    out = out.replace('<<EVT_FEED>>', feed_html, 1)

# 兜底（动态日历不再有 <<EVT_i>> 在 #cal 内，但 forward 段可能有）
# 2026-08-18 修复：events 增删会导致索引前移，scaffold 旧月历里写死的 <<EVT_i>>（如 94~99）
# 可能超出 events 数组范围。旧月历已被动态日历 cal_section_new 完全取代，
# 这些残留占位符直接清空即可，不应触发断言失败。
out = re.sub(r'<<EVT_\d+>>', '', out)
out = re.sub(r'<<EVT_FEED>>', '', out)
out = out.replace('__SRC__', '')

# --- 提取 forward / feed 区块（从 scaffold 回填后的 out） ---
m_fwd = re.search(r'(<section id="forward">.*?</section>)', out, re.S)
m_feed = re.search(
    r'(<div style="margin:\s*\d+px[^"]*信源快报.*?</table>)', out, re.S)

# --- 拼装最终日历区块：动态 #cal + forward + feed ---
cal_section = cal_section_new
if m_fwd:
    cal_section += "\n" + m_fwd.group(1)
if m_feed:
    cal_section += "\n" + m_feed.group(1)

if not cal_section:
    print("ERROR: 未能从 scaffold 中提取日历区块", file=sys.stderr)
    sys.exit(1)

# --- 动态覆盖游戏列表（2026-08-06：从 events 实算，不写死） ---
active_games = []
seen_gs = set()
for ev in events:
    g = ev.get("game", "")
    if g and g not in seen_gs and ev.get("anchor", "").strip():
        seen_gs.add(g)
        active_games.append(g)
skip_note = {"全行业", "主机/PC 大作", "主机/PC新作", "ChinaJoy 2026", "和平精英 / 王者荣耀", "蛋仔派对 × 永劫无间"}
core_games = [g for g in active_games if g not in skip_note]
extra_cats = [g for g in active_games if g in skip_note]
game_list_str = " / ".join(core_games[:35])
if len(core_games) > 35:
    game_list_str += f" + {len(core_games) - 35} 款"
if extra_cats:
    game_list_str += "；" + " / ".join(extra_cats)
coverage_note = (
    f'覆盖：{game_list_str}。只保留：新版本 / 新角色 / 联动 / 前瞻 / 大型活动 / 电竞赛事。'
    '实线=官方已官宣，<b>虚线边=前瞻情报（待官方确认）</b>。')
cal_section = re.sub(
    r'覆盖：.*?(?=。只保留)', coverage_note,
    cal_section, flags=re.S)

# --- 注入 index.html ---
index_html = io.open(INDEX_PATH, encoding="utf-8").read()

# 找到 index.html 中日历区块的边界：
# 从 <section id="cal"> 开始，到信源快报 </table> 结束
# 后面紧接 <section id="source">
m_cal_start = re.search(r'<section id="cal"', index_html)
m_source_start = re.search(r'<section id="source"', index_html)

if not m_cal_start:
    print("ERROR: index.html 中未找到 <section id=\"cal\">", file=sys.stderr)
    sys.exit(1)

cal_start_pos = m_cal_start.start()
if m_source_start:
    cal_end_pos = m_source_start.start()
else:
    # 如果找不到 #source，尝试找 footer
    m_footer = re.search(r'<footer>', index_html)
    if m_footer:
        cal_end_pos = m_footer.start()
    else:
        print("ERROR: 无法确定日历区块结束位置", file=sys.stderr)
        sys.exit(1)

# 替换：zone-head 之后的空白也一并清理
new_html = index_html[:cal_start_pos] + cal_section + "\n" + index_html[cal_end_pos:]

# 写临时文件再替换，防止预览窗格锁住
_tmp = INDEX_PATH + "." + str(int(datetime.datetime.now().timestamp()))
io.open(_tmp, 'w', encoding='utf-8').write(new_html)
try:
    os.replace(_tmp, INDEX_PATH)
except OSError:
    try:
        os.remove(INDEX_PATH)
        os.rename(_tmp, INDEX_PATH)
    except OSError:
        print('WARN: cannot overwrite', INDEX_PATH, '(locked), content in', _tmp)
        sys.exit(0)

print('日历区块已注入 index.html（%d bytes）' % len(cal_section))
print('已退役节点 %d 个' % len(retired))
