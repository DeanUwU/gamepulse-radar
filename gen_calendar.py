#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Phase 1 生成器：读 events.json -> 提取日历 section -> 注入 index.html。
占位符 <<EVT_i>> 用 events[i].anchor 回填，anchor 内 __SRC__ 换回 source_url。

2026-08-05 架构合并：全站统一为 index.html，不再生成独立 calendar.html。
本脚本从 scaffold 生成的完整 HTML 中提取 <section id="cal"> 到信源快报 </table> 的区块，
替换 index.html 中对应内容。
"""
import json, io, os, sys, datetime, re

BASE = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(BASE, "index_v2.html")

doc = json.load(io.open(os.path.join(BASE, 'events.json'), encoding='utf-8'))
scaffold = doc['scaffold']
events = doc['events']
feed_events = doc.get('feed_events', [])

def render(ev):
    return ev['anchor'].replace('__SRC__', ev.get('source_url', ''))

out = scaffold
today_mmdd = datetime.date.today().strftime('%m-%d')
out = re.sub(r'今天（\d{2}-\d{2}）', '今天（%s）' % today_mmdd, out)
retired = []
for i, ev in enumerate(events):
    token = '<<EVT_%d>>' % i
    if token not in out:
        if not (ev.get('anchor') or '').strip():
            retired.append(i)
        else:
            print('WARN: 占位符 %s 未找到，但 anchor 非空 -> events[%d] %s'
                  % (token, i, ev.get('game', '')))
        continue
    out = out.replace(token, render(ev), 1)
if retired:
    print('    已退役节点 %d 个（anchor 空，跳过回填）: %s'
          % (len(retired), ','.join(map(str, retired))))

# Phase 4：信源快报
feed_html = ''.join(render(ev) for ev in feed_events)
if '<<EVT_FEED>>' in out:
    out = out.replace('<<EVT_FEED>>', feed_html, 1)

# 兜底
assert not re.search(r'<<EVT_\d+>>', out), '存在未替换的数字占位符'
assert '<<EVT_FEED>>' not in out, '存在未替换的 <<EVT_FEED>>'
assert '__SRC__' not in out, '存在未替换 __SRC__'

# --- 从完整 HTML 中提取日历区块 ---
# 提取从 <section id="cal"> 到信源快报 </table> 的全部内容
m_cal = re.search(r'(<section id="cal">.*?</section>)', out, re.S)
m_fwd = re.search(r'(<section id="forward">.*?</section>)', out, re.S)
# 信源快报：从 <div style="margin: 到 </table>
m_feed = re.search(
    r'(<div style="margin:\s*\d+px[^"]*信源快报.*?</table>)', out, re.S)

# --- 增强日历 CSS（2026-08-06 放大单元格 + 更多品类覆盖） ---
CAL_ENHANCED_CSS = r"""<style>
/* ===== 日历视觉优化 v3（2026-08-06 放大） ===== */
.cal-grid{border-collapse:collapse;width:100%;table-layout:fixed}
.cal-grid th{font-size:12px;color:var(--sub);font-weight:700;padding:10px 4px;border-bottom:2px solid var(--border);text-transform:uppercase;letter-spacing:.06em}
.cal-grid td{border:1px solid var(--border);vertical-align:top;min-height:110px;padding:8px 9px;font-size:12.5px;background:var(--panel)}
.cal-cell .cd{font-weight:800;color:var(--accent);font-size:13px;margin-bottom:5px;display:inline-block;padding:2px 7px;border-radius:4px;background:rgba(255,92,57,.1)}
.cal-cell.today{background:rgba(255,92,57,.08);box-shadow:inset 0 0 0 2px var(--accent)}
.cal-cell.today .cd{background:var(--accent);color:#fff}
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

cal_section = ""
if m_cal:
    cal_sec = m_cal.group(1)
    # 替换日历 CSS 为增强版
    cal_sec = re.sub(r'<style>.*?</style>', CAL_ENHANCED_CSS, cal_sec, flags=re.S)
    cal_section += cal_sec
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
