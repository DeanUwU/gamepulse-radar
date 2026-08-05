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
INDEX_PATH = os.path.join(BASE, "index.html")

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

cal_section = ""
if m_cal:
    cal_section += m_cal.group(1)
if m_fwd:
    cal_section += "\n" + m_fwd.group(1)
if m_feed:
    cal_section += "\n" + m_feed.group(1)

if not cal_section:
    print("ERROR: 未能从 scaffold 中提取日历区块", file=sys.stderr)
    sys.exit(1)

# --- 注入 index.html ---
index_html = io.open(INDEX_PATH, encoding="utf-8").read()

# 找到 index.html 中日历区块的边界：
# 从 <section id="cal"> 开始，到信源快报 </table> 结束
# 后面紧接 <section id="source">
m_cal_start = re.search(r'<section id="cal">', index_html)
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
