# -*- coding: utf-8 -*-
"""
apply_hide.py — 视觉隐藏兜底（2026-08-17 新增）

用途：用户希望「视觉上隐藏」某些臃肿板块/元素，但不要删除数据、不要改动各生成脚本逻辑。
      本脚本作为 daily_refresh.py 流水线的【最后一步】执行，在所有生成脚本跑完后，
      按下方 HIDE_RULES 统一给目标元素打 is-hidden（配合 CSS `section.is-hidden,div.is-hidden{display:none}`）。

设计原则：
  - 只做视觉隐藏（加 class），不删 DOM、不删数据，随时可恢复。
  - 幂等：重复运行不会叠加重复 class。
  - 隐藏清单集中在这一处，想恢复某个板块就删对应条目。

HIDE_RULES 每条：(选择器类型, 匹配串)
  - ("section_id", "#forward")  -> 隐藏 <section id="forward">...</section>
  - ("class", "wc-preview")     -> 隐藏 <div class="wc-preview ...">...</div>（词云32条全量预览）
  - ("zone_head", "版本日历")   -> 隐藏含指定文案的 <div class="zone-head">...</div> 标题栏
"""
import os, re, io

BASE = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.environ.get("AH_INDEX", os.path.join(BASE, "index.html"))

# 隐藏清单（视觉隐藏，不删数据）
HIDE_RULES = [
    ("section_id", "#forward"),       # 前瞻哨（臃肿）
    ("section_id", "#source"),        # 源覆盖报告（臃肿）
    ("section_id", "#cal"),           # 日历正文（标题栏已藏，正文也藏掉，避免半残）
    ("class",     "wc-preview"),      # 词云下方 32 条全量预览（保留上方词云图+图例）
    ("zone_head", "今日日报"),         # 标题栏
    ("zone_head", "版本日历"),         # 标题栏
    ("hide_small", "#podium"),        # 今日焦点 sec-title 内的说明小字（与时效无关）
    ("hide_small", "#media"),         # 行业情报站 sec-title 内的说明小字（与时效无关）
]


def _add_hidden_class(tag_open):
    """给标签加上 is-hidden class；已存在则原样返回。"""
    if "is-hidden" in tag_open:
        return tag_open
    m = re.search(r'class="([^"]*)"', tag_open)
    if m:
        new_cls = (m.group(1) + " is-hidden").strip()
        return tag_open[:m.start(1)] + new_cls + tag_open[m.end(1):]
    # 无 class 属性则补一个
    return tag_open.rstrip(">").rstrip("/").rstrip() + ' class="is-hidden">'


def hide_section(html, sid):
    """隐藏 <section id="sid"> ... </section>（栈平衡找闭合）。"""
    m = re.search(r'<section\s+id="%s"[^>]*>' % re.escape(sid.strip('#')), html)
    if not m:
        return html, False
    start = m.start()
    # 栈平衡找对应 </section>
    depth = 0
    i = m.end()
    while i < len(html):
        nxt = html.find("</section>", i)
        if nxt == -1:
            return html, False
        seg = html[i:nxt]
        depth += len(re.findall(r'<section\b', seg))
        if depth == 0:
            end = nxt + len("</section>")
            break
        depth -= 1
        i = nxt + len("</section>")
    else:
        return html, False
    tag = html[start:start + (m.end() - start)]
    new_tag = _add_hidden_class(tag)
    return html[:start] + new_tag + html[start + (m.end() - start):], True


def hide_class(html, cls):
    """隐藏第一个 <div class="cls..."> ... </div>（栈平衡找闭合）。"""
    m = re.search(r'<div\s+class="[^"]*\b%s\b[^"]*"' % re.escape(cls), html)
    if not m:
        return html, False
    start = m.start()
    # 栈平衡找对应 </div>
    depth = 0
    i = m.end()
    while i < len(html):
        nxt = html.find("</div>", i)
        if nxt == -1:
            return html, False
        seg = html[i:nxt]
        depth += len(re.findall(r'<div\b', seg))
        if depth == 0:
            end = nxt + len("</div>")
            break
        depth -= 1
        i = nxt + len("</div>")
    else:
        return html, False
    tag = html[start:start + (m.end() - start)]
    new_tag = _add_hidden_class(tag)
    return html[:start] + new_tag + html[start + (m.end() - start):], True


def hide_zone_head(html, keyword):
    """隐藏含指定文案的 <div class="zone-head"> ... </div> 标题栏。"""
    m = re.search(r'<div\s+class="zone-head[^"]*">(?:(?!</div>).)*?%s(?:(?!</div>).)*?</div>'
                  % re.escape(keyword), html, re.S)
    if not m:
        return html, False
    tag = m.group(0)
    new_tag = _add_hidden_class(tag)
    return html.replace(tag, new_tag, 1), True


def hide_small(html, sid):
    """隐藏指定 section 的 sec-title 内的 <small>...</small> 说明小字（加 is-hidden，可恢复）。"""
    m = re.search(r'<section\s+id="%s"[^>]*>' % re.escape(sid.strip('#')), html)
    if not m:
        return html, False
    # 在 section 起始后找第一个 sec-title 块
    sec_start = m.end()
    mtitle = re.search(r'<div class="sec-title">.*?</div>', html[sec_start:], re.S)
    if not mtitle:
        return html, False
    title_block = mtitle.group(0)
    abs_start = sec_start + mtitle.start()
    # 在 title 块内找 <small>...</small>
    ms = re.search(r'<small>.*?</small>', title_block, re.S)
    if not ms:
        return html, False
    small = ms.group(0)
    if "is-hidden" in small:
        # 已隐藏，幂等
        return html, True
    new_small = small.replace("<small>", '<small class="is-hidden">', 1)
    new_block = title_block.replace(small, new_small, 1)
    return html[:abs_start] + new_block + html[abs_start + len(title_block):], True


def main():
    if not os.path.exists(INDEX_PATH):
        print("  [apply_hide] index.html 不存在，跳过")
        return 1
    html = io.open(INDEX_PATH, encoding="utf-8").read()
    changed = 0
    for kind, val in HIDE_RULES:
        if kind == "section_id":
            html, ok = hide_section(html, val)
        elif kind == "class":
            html, ok = hide_class(html, val)
        elif kind == "zone_head":
            html, ok = hide_zone_head(html, val)
        elif kind == "hide_small":
            html, ok = hide_small(html, val)
        else:
            ok = False
        if ok:
            changed += 1
            print("  [apply_hide] 已隐藏 %s %s" % (kind, val))
    if changed:
        tmp = INDEX_PATH + ".ah.tmp"
        io.open(tmp, "w", encoding="utf-8").write(html)
        try:
            os.replace(tmp, INDEX_PATH)
        except OSError:
            try:
                os.remove(INDEX_PATH)
                os.replace(tmp, INDEX_PATH)
            except OSError as e:
                print("  [apply_hide] 写入失败（可能被预览窗格锁住）：%s" % e)
                return 1
        print("  [apply_hide] ✓ 共隐藏 %d 处" % changed)
    else:
        print("  [apply_hide] 无新增隐藏（清单项可能已隐藏或不存在）")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
