# -*- coding: utf-8 -*-
"""unify_heat.py — 跨板块热度口径统一（自洽校验 (d) 的正解）

问题：daily.html 里「内容TOP10」「梗雷达」「视觉焦点」三处仍直接展示 B 站原始播放量
     （1165万 / 302万播放 …），而词云板块用的是统一热度数 H(0-100)。
     同一个页面两套口径 = 读者无法横向比较，违反红线①②「跨板块一致」。

做法：把这三处的原始播放量换算成 H，H 作主展示，原始量降级为附注/tooltip。

H 公式（与 cross_words.py 完全一致，全站唯一口径）：
    H = round(100 * v / max(v))        # 线性归一，组内最热=100
归一范围（pool）= 读者眼睛会横向比较的那一个列表：
    · 梗雷达：每一条 mu-row（一路采集=一个榜）单独归一
    · 内容TOP10：整份 top10 列表
    · 视觉焦点：整个 vf-grid
tooltip 统一格式：热度数 H=XX ｜ 组内归一（POOL）｜ 原始：NNN万

幂等：已带 H 的槽位会被重新计算覆盖，不会叠加。
非阻断：任一板块解析不到就跳过该板块。
"""
import io, os, re, sys, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.environ.get("UH_TARGET", os.path.join(BASE, "index.html"))

VOL = re.compile(r'(\d+(?:\.\d+)?)\s*万')


def h_of(v, vmax):
    """线性归一到 0-100；最低给 1，避免长尾条目显示 H0 像是坏数据。"""
    if not vmax:
        return 0
    return max(1, round(100 * v / vmax))


def tip(h, pool, raw):
    return f'热度数 H={h} ｜ 组内归一（{pool}）｜ 原始：{raw}'


def section(src, sid):
    # 兼容带附加属性的板块开标签（如 <section id="visual" data-curated="...">），
    # refresh_content.stamp_curated 会给 visual 打 data-curated，写死无属性会静默跳过该板块。
    m = re.search(rf'<section id="{sid}"[^>]*>.*?</section>', src, re.S)
    return m


# ---------------- ① 梗雷达 #hot：每条 mu-row 单独归一 ----------------
def fix_hot(src, report):
    m = section(src, "hot")
    if not m:
        report.append("hot: 未找到板块，跳过")
        return src
    sec = m.group(0)

    def do_row(rm):
        row = rm.group(0)
        lm = (re.search(r'<span class="mu-label[^"]*">(.*?)</span>', row, re.S)
              or re.search(r'<h4 class="mu-block-title">(.*?)<span', row, re.S))
        pool = re.sub(r'<[^>]+>|&#\d+;', '', lm.group(1)).strip() if lm else "梗雷达"
        # 收集本行所有数值型 <em>（<em>泛圈</em> 这类文字标签不参与）
        ems = list(re.finditer(r'<em(?:\s[^>]*)?>(.*?)</em>', row, re.S))
        vals, raws = [], []
        for em in ems:
            # 幂等关键：先去标签，再剥掉上一轮写入的 "H12" 前缀，否则 H100+1165万 会被读成 1001165万
            inner = re.sub(r'<[^>]+>', '', em.group(1))
            inner = re.sub(r'^\s*H\d+\s*', '', inner)
            vm = VOL.search(inner)
            vals.append(float(vm.group(1)) if vm else None)
            raws.append(vm.group(0).replace(' ', '') if vm else '')
        nums = [v for v in vals if v is not None]
        if not nums:
            return row
        vmax = max(nums)
        out, last = [], 0
        for em, v, raw in zip(ems, vals, raws):
            out.append(row[last:em.start()])
            last = em.end()
            if v is None:
                out.append(em.group(0))          # 文字标签原样保留
                continue
            h = h_of(v, vmax)
            out.append(f'<em title="{tip(h, pool, raw)}">H{h}<i>{raw}</i></em>')
            report.append(f"hot/{pool}: {raw} → H{h}")
        out.append(row[last:])
        return "".join(out)

    # 2026-08-05 修复：梗雷达板块早已从 <div class="mu-row"> 改版成 <div class="mu-block ...">，
    # 这条正则匹配不到任何东西 → fix_hot 长期空转，社区风向那几格一直挂着「824万」原始播放量，
    # 自洽校验每天报「跨板块 H 未统一」也就一直修不掉。两种结构都匹配，按块内归一。
    pat_block = r'<div class="mu-block[^"]*"[^>]*>.*?(?=<div class="mu-block|</div>\s*</section>|</div>\s*<p class="note")'
    if re.search(r'<div class="mu-row">', sec):
        new_sec = re.sub(r'<div class="mu-row">.*?(?=<div class="mu-row">|</div>\s*<p class="note")',
                         do_row, sec, flags=re.S)
    else:
        new_sec = re.sub(pat_block, do_row, sec, flags=re.S)
    return src.replace(sec, new_sec)


# ---------------- ② 内容TOP10 #radar：整份列表归一 ----------------
def fix_radar(src, report):
    m = section(src, "radar")
    if not m:
        report.append("radar: 未找到板块，跳过")
        return src
    sec = m.group(0)
    metas = list(re.finditer(r'<span class="t10-meta"(?:\s[^>]*)?>(.*?)</span>', sec, re.S))
    parsed = []
    for mm in metas:
        inner = mm.group(1)
        # 幂等：已带 H 的先剥掉 H 前缀，用 tooltip 里的原始值复算
        body = re.sub(r'^\s*H\d+\s*·\s*', '', inner)
        vm = VOL.search(inner)
        if not vm:
            # 原始量可能已移进 tooltip
            tm = re.search(r'原始：(\d+(?:\.\d+)?)\s*万', mm.group(0))
            vm2 = float(tm.group(1)) if tm else None
            parsed.append((mm, vm2, body, f'{tm.group(1)}万' if tm else ''))
        else:
            parsed.append((mm, float(vm.group(1)), re.sub(r'^\s*' + re.escape(vm.group(0)) + r'\s*·?\s*', '', body), f'{vm.group(1)}万'))
    nums = [p[1] for p in parsed if p[1] is not None]
    if not nums:
        return src
    vmax = max(nums)
    out, last = [], 0
    for mm, v, desc, raw in parsed:
        out.append(sec[last:mm.start()])
        last = mm.end()
        if v is None:
            out.append(mm.group(0))
            continue
        h = h_of(v, vmax)
        desc = desc.strip(" ·")
        txt = f'H{h} · {desc}' if desc else f'H{h}'
        out.append(f'<span class="t10-meta" title="{tip(h, "内容TOP10", raw)}">{txt}</span>')
        report.append(f"radar: {raw} → H{h}")
    out.append(sec[last:])
    return src.replace(sec, "".join(out))


# ---------------- ③ 视觉焦点 #visual：整个 vf-grid 归一 ----------------
def fix_visual(src, report):
    m = section(src, "visual")
    if not m:
        report.append("visual: 未找到板块，跳过")
        return src
    sec = m.group(0)
    caps = list(re.finditer(r'(<span class="vf-cap">.*?<small)(?:\s[^>]*)?(>)(.*?)(</small>)', sec, re.S))
    parsed = []
    for mm in caps:
        inner = mm.group(3)
        body = re.sub(r'^\s*H\d+\s*·\s*', '', inner)
        vm = re.search(r'(\d+(?:\.\d+)?)\s*万播放', body)
        parsed.append((mm, float(vm.group(1)) if vm else None, body,
                       f'{vm.group(1)}万播放' if vm else ''))
    nums = [p[1] for p in parsed if p[1] is not None]
    if not nums:
        return src
    vmax = max(nums)
    out, last = [], 0
    for mm, v, body, raw in parsed:
        out.append(sec[last:mm.start()])
        last = mm.end()
        if v is None:
            out.append(mm.group(0))
            continue
        h = h_of(v, vmax)
        out.append(f'{mm.group(1)} title="{tip(h, "视觉焦点", raw)}"{mm.group(2)}H{h} · {body}{mm.group(4)}')
        report.append(f"visual: {raw} → H{h}")
    out.append(sec[last:])
    return src.replace(sec, "".join(out))


# ---------------- ④ 图例：告诉读者 H 是什么 ----------------
def add_legend(src):
    note = ('· 热度数 H(0-100)：组内最热=100，原始播放量仅作附注（与词云同一口径）')
    for sid, anchor in (("radar", '内容TOP10'), ("hot", '内容风向与舆情'), ("visual", '今日视觉焦点')):
        m = section(src, sid)
        if not m:
            continue
        sec = m.group(0)
        if 'H(0-100)' in sec:
            continue
        # 在该板块 sec-title 的 <small> 尾部追加说明
        new = re.sub(r'(<div class="sec-title">.*?<small>)(.*?)(</small>)',
                     lambda x: x.group(1) + x.group(2) + ' ' + note + x.group(3),
                     sec, count=1, flags=re.S)
        src = src.replace(sec, new)
    return src


def main():
    src = io.open(TARGET, encoding="utf-8").read()
    before = src
    report = []
    src = fix_hot(src, report)
    src = fix_radar(src, report)
    src = fix_visual(src, report)
    src = add_legend(src)

    # 样式：<em> 内的原始量附注（小一号、半透明），只加一次
    css_path = os.path.join(BASE, "style.css")
    css = io.open(css_path, encoding="utf-8").read()
    if '.mu-links em i' not in css:
        css += '\n.mu-links em i{font-style:normal;font-weight:400;opacity:.5;margin-left:4px}\n'
        io.open(css_path, "w", encoding="utf-8").write(css)
        print("  style.css: 已补 .mu-links em i 原始量附注样式")

    if src == before:
        print("unify_heat: 无变化（可能已统一）")
        return
    # HTML 写入必须走 temp-file + rename，避免预览窗格锁文件导致 PermissionError
    tmp = TARGET + "." + datetime.datetime.now().strftime("%H%M%S%f")
    io.open(tmp, "w", encoding="utf-8").write(src)
    try:
        os.replace(tmp, TARGET)
    except OSError:
        os.remove(TARGET)
        os.rename(tmp, TARGET)
    print(f"unify_heat: 已统一 {len(report)} 处热度口径 -> H(0-100)")
    for line in report[:40]:
        print("  " + line)


if __name__ == "__main__":
    main()
