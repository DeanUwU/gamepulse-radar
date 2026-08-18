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
lo = today - datetime.timedelta(days=WINDOW)
hi = today + datetime.timedelta(days=WINDOW)

# 网格起止：窗口两端所在周的周一 / 周日
def monday(d):
    return d - datetime.timedelta(days=d.weekday())
def sunday(d):
    return d + datetime.timedelta(days=6 - d.weekday())
grid_start = monday(lo)
grid_end = sunday(hi)

# 按日期归集事件（区间事件只放 date_start 那天，不展开）
ev_by_day = {}
MAX_PER_CELL = int(os.environ.get("GC_MAX_PER_CELL", "8"))  # 每格最多显示条数
for ev in events:
    ds = parse_date(ev.get('date_start'))
    if not ds:
        continue
    # 只放起始日；区间事件不展开到后续每天
    if grid_start <= ds <= grid_end:
        ev_by_day.setdefault(ds, []).append(ev)
    # date_end 不再展开（用户要求：只看开始时间）

# 生成动态月历表格
def build_calendar():
    rows = ['<table class="cal-grid"><thead><tr>'
            '<th>一</th><th>二</th><th>三</th><th>四</th><th>五</th><th>六</th><th>日</th>'
            '</tr></thead><tbody>']
    cur = grid_start
    while cur <= grid_end:
        rows.append('<tr>')
        for _ in range(7):
            in_win = lo <= cur <= hi
            cls = 'cal-cell'
            if cur == today:
                cls += ' today'
            if not in_win:
                cls += ' cal-cell-out'  # 窗口外灰显
            cd = '%02d-%02d' % (cur.month, cur.day)
            cell = '<td class="%s"><div class="cd" data-d="%s">%d</div>' % (cls, cd, cur.day)
            day_evs = ev_by_day.get(cur, [])
            if day_evs:
                shown = day_evs[:MAX_PER_CELL]
                for ev in shown:
                    cell += render(ev)
                overflow = len(day_evs) - MAX_PER_CELL
                if overflow > 0:
                    cell += ('<span class="cal-more" title="还有 %d 条">+%d</span>'
                             % (overflow, overflow))
            cell += '</td>'
            rows.append(cell)
            cur += datetime.timedelta(days=1)
        rows.append('</tr>')
    rows.append('</tbody></table>')
    return ''.join(rows)

cal_table = build_calendar()

# hero 文案动态化
span_lo = lo.strftime('%m-%d')
span_hi = hi.strftime('%m-%d')
hero_note = ('未来约 %d 周关键节点（%s ~ %s）：新角色 / 新版本 / 联动 / 活动 / 维护 / 前瞻。'
             '虚线边框 = 前瞻情报（未官宣，需跟踪）。日历每日自动滚动更新。'
             % (round(WINDOW * 2 / 7), span_lo, span_hi))

# 构造新的 #cal section（保留 scaffold 的 section 外壳 + 增强 CSS + 动态表格）
CAL_ENHANCED_CSS = r"""<style>
/* ===== 日历视觉优化 v4（2026-08-18 动态滚动） ===== */
.cal-grid{border-collapse:collapse;width:100%;table-layout:fixed}
.cal-grid th{font-size:12px;color:var(--sub);font-weight:700;padding:10px 4px;border-bottom:2px solid var(--border);text-transform:uppercase;letter-spacing:.06em}
.cal-grid td{border:1px solid var(--border);vertical-align:top;min-height:110px;padding:8px 9px;font-size:12.5px;background:var(--panel)}
.cal-cell .cd{font-weight:800;color:var(--accent);font-size:13px;margin-bottom:5px;display:inline-block;padding:2px 7px;border-radius:4px;background:rgba(255,92,57,.1)}
.cal-cell.today{background:rgba(255,92,57,.08);box-shadow:inset 0 0 0 2px var(--accent)}
.cal-cell.today .cd{background:var(--accent);color:#fff}
.cal-cell-out{opacity:.4;background:var(--panel2)}
.cal-ev{display:block;margin-bottom:4px;padding:5px 8px;border-radius:7px;background:linear-gradient(135deg,var(--panel2),rgba(77,163,255,.04));
  border-left:3px solid var(--blue);color:var(--txt);font-size:11.5px;line-height:1.5;
  white-space:normal;overflow:hidden;text-overflow:ellipsis;transition:all .15s}
.cal-ev:hover{text-decoration:none;color:#fff;background:linear-gradient(135deg,rgba(77,163,255,.15),var(--panel2));border-left-color:var(--gold);transform:translateX(2px)}
.cal-ev b{color:var(--gold);font-weight:700}
.cal-ev.guess{border-left-style:dashed;opacity:.78;background:repeating-linear-gradient(-45deg,var(--panel2),var(--panel2) 3px,rgba(255,176,32,.03) 3px,rgba(255,176,32,.03) 6px)}
.cal-legend{margin-top:10px;font-size:11px;color:var(--sub)}
.cal-legend span{display:inline-block;margin-right:12px}
.cal-legend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px;vertical-align:middle}
@media(max-width:640px){.cal-grid td{min-height:90px;padding:5px 6px;font-size:11px}.cal-ev{font-size:10px;padding:3px 5px}.cal-cell .cd{font-size:11px}}
</style>"""

# 注意：CAL_ENHANCED_CSS 内含字面 %（如 rgba 透明度），不能用 % 格式化，改用拼接
cal_section_new = (
    '<section id="cal">'
    + CAL_ENHANCED_CSS
    + '<div class="hero"><h1>版本日历 · 前瞻哨</h1><p>' + hero_note + '</p></div>'
    + cal_table
    + '<div class="cal-legend"><span><i style="background:var(--blue)"></i>官方已官宣</span>'
    + '<span><i style="background:var(--gold);opacity:.6"></i>前瞻情报（虚线边）</span>'
    + '<span style="opacity:.5">灰显=窗口外（±' + str(WINDOW) + '天）</span></div>'
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
