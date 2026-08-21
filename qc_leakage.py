#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""反向质检闸门（2026-08-21 治本）：每日刷新后比对「全采集数据」与「最终日报」的差集，
自动告警「高信号却没进日报」的漏采事件。挂到 daily_refresh.py 末尾，非阻断。

治的是哪类问题：GTA6 泄露这类全球级事件，明明被多个国际源采到了（admit_sources 判为 adopt），
却因为「无发布日期」被 promote_sources 当陈稿跳过，最终没进信源快报/头条——
而此前没有任何环节会回头查「采到了却没上」。本闸门就是那个回头查。

比对对象：
  采集池 = inbox/sources_curated.json 的 adopt + review 条目（行业媒体已通过安检、或仅待复核的候选）
  日报面 = events.json 的 feed_events（信源快报）+ index.html 全文（头条/TOP10/情报站/词云/日历）
漏采判定：某候选不在日报面里，且信号达到阈值（多源印证≥2 家 / 强事件词命中） → 告警。
  review 项（待复核被卡）若带强游戏事件信号也告警 —— 否则"真突发被 off_topic 误判"会绕过本闸门。
输出：inbox/qc_leakage.json（漏采清单 + 原因）+ stdout 摘要。
"""
import io, os, re, json, sys, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
INBOX = os.path.join(BASE, 'inbox')
CURATED = os.path.join(INBOX, 'sources_curated.json')
EVENTS = os.path.join(BASE, 'events.json')
INDEX = os.path.join(BASE, 'index.html')
OUT = os.path.join(INBOX, 'qc_leakage.json')

# 强事件词（命中即视为"大事件/突发"，单源也值得关注）
STRONG_EVENT = re.compile(
    r'泄露|泄漏|leak|官宣|公布|实机|演示|首发|发售|定档|公测|上线|登顶|夺冠|夺魁|'
    r'破圈|爆款|里程碑|打破|创新高|引爆|停服|关服|停运|裁员|收购|合并|独占|延期|跳票|'
    r'announce|reveal|trailer|gameplay|launch|release|shutdown|layoff|acqui', re.I)

# 复用 promote_sources 的多源印证聚类（避免两套逻辑漂移）
sys.path.insert(0, BASE)
from promote_sources import build_cluster_sizes, _title_tokens


def _report_text():
    """拼出"日报面"全文：信源快报 + 日历 events[] + index.html。"""
    parts = []
    try:
        ev = json.load(io.open(EVENTS, encoding='utf-8'))
        for e in ev.get('feed_events', []):
            parts.append((e.get('title') or '') + ' ' + (e.get('source_name') or ''))
        for e in ev.get('events', []):
            parts.append((e.get('title') or '') + ' ' + (e.get('game') or ''))
    except Exception:
        pass
    try:
        parts.append(io.open(INDEX, encoding='utf-8').read())
    except Exception:
        pass
    return '\n'.join(parts).lower()


def _covered(adopt, report_lower):
    """adopt 是否已被日报覆盖：标题里的显著 token 任一出现在日报全文即视为已收录。

    容错：CJK token 在 _title_tokens 里带 'cj:' 前缀，比对时去掉前缀再查原始中文。
    宁可误判为"已覆盖"（少报），不要漏报 —— 复核成本低，漏采代价高。
    """
    toks = _title_tokens(adopt.get('title', ''))
    if not toks:
        return (adopt.get('url', '') or '').lower() in report_lower
    for tk in toks:
        m = tk[3:] if tk.startswith('cj:') else tk
        if m and m in report_lower:
            return True
    return False


def qc():
    try:
        cur = json.load(io.open(CURATED, encoding='utf-8'))
        # 池 = adopt + review（review 被 off_topic 卡住的"真突发"也要查，否则绕过本闸门）
        items = [i for i in cur.get('items', [])
                 if i.get('verdict') in ('adopt', 'review')]
    except Exception as e:
        print('⚠ qc_leakage：未读到准入结果（%s），跳过' % repr(e)[:120])
        return
    if not items:
        print('反向质检闸门：无候选，跳过')
        return

    report_lower = _report_text()
    cluster = build_cluster_sizes(items)

    leaks = []
    for it in items:
        if _covered(it, report_lower):
            continue
        url = it.get('url', '')
        n_src = cluster.get(url, 1)
        strong = bool(STRONG_EVENT.search(it.get('title', '')))
        score = 0
        if n_src >= 2:
            score += 2
        if n_src >= 3:
            score += 1
        if strong:
            score += 2
        # 阈值：多源印证(≥2) 或 强事件词 任一即告警（宁告警不漏报）
        if score < 2 and not strong:
            continue
        reasons = []
        if n_src >= 2:
            reasons.append('多源印证(%d家)' % n_src)
        if strong:
            reasons.append('强事件词')
        if it.get('verdict') == 'review':
            reasons.append('准入待复核被卡')
        leaks.append({
            'title': it.get('title', ''),
            'url': url,
            'game': it.get('game', ''),
            'src_name': it.get('src_name', ''),
            'verdict': it.get('verdict'),
            'pubdate': it.get('pubdate'),
            'date_unverified': it.get('date_unverified', False),
            'n_src_cluster': n_src,
            'signal': score,
            'reasons': reasons,
        })

    doc = {
        'generated_at': datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
        'adopt_total': sum(1 for i in items if i.get('verdict') == 'adopt'),
        'review_total': sum(1 for i in items if i.get('verdict') == 'review'),
        'leak_total': len(leaks),
        'leaks': leaks,
    }
    io.open(OUT, 'w', encoding='utf-8').write(json.dumps(doc, ensure_ascii=False, indent=1))

    if leaks:
        print('⚠ 反向质检闸门：发现 %d 条「高信号却未进日报」的潜在漏采：' % len(leaks))
        for L in leaks[:12]:
            print('   - [%s] %s — %s（%s）' % (L['game'], L['title'][:36], L['src_name'], '/'.join(L['reasons'])))
        if len(leaks) > 12:
            print('   … 其余 %d 条见 inbox/qc_leakage.json' % (len(leaks) - 12))
    else:
        print('✅ 反向质检闸门：高信号候选均已进日报，无漏采')


if __name__ == '__main__':
    qc()
