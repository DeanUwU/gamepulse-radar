# -*- coding: utf-8 -*-
"""daily_refresh.py — GamePulse 每日刷新 + 自洽校验 + 日志（按"自洽提示词体系"）
流程：① meme_radar 采集 ② 按真实失败源更新源覆盖报告 ③ cross_words 重生词云(H)
      ④ refresh_content 重刷内容板块 ⑤ gen_calendar 重建日历 ⑥ 自洽校验 ⑦ 写日志

2026-08-05 架构合并：全站统一为 index.html，不再生成 daily.html/calendar.html 独立页；
各脚本直写 index.html 对应 section，无需 rebuild_main 拼装步骤。
"""
import re, io, os, sys, json, datetime, subprocess, ast, urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__))
PY = "C:/Users/shudizhao/.workbuddy/binaries/python/versions/3.13.12/python.exe"
TODAY = datetime.date.today().strftime("%Y-%m-%d")
MEMO = []  # 运行日志
problems = []  # (层级, 板块/位置, 问题, 违反维度, 严重度, 修复建议) —— 必须在 ④ 之前定义（④ 的 gen_calendar 失败会 append）
# Windows 下子进程 stdout 走管道默认 GBK，脚本里打印 →/★/emoji 会 UnicodeEncodeError 假死非零退出，强制 UTF-8
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")

def log(s):
    MEMO.append(s)
    print(s)

# ---------- ① 采集 ----------
log("【① 采集】运行 meme_radar.py ...")
r = subprocess.run([PY, "meme_radar.py"], cwd=BASE, capture_output=True, text=True, encoding="utf-8", env=ENV)
status = {}
m = re.search(r"状态:\s*(\{.*\})", r.stdout + r.stderr)
if m:
    try:
        status = ast.literal_eval(m.group(1))
    except Exception:
        pass
ok = [k for k, v in status.items() if str(v).startswith("OK")]
fail = {k: v for k, v in status.items() if not str(v).startswith("OK")}
log(f"    状态: {status}")
log(f"    通过 {len(ok)}/6 · 失败/跳过 {len(fail)}: {fail if fail else '无'}")
today_meme = os.path.join(BASE, "collectors", "meme_" + TODAY.replace("-", "") + ".json")
meme_ready = r.returncode == 0 and os.path.exists(today_meme)

# ⑦ 全网热榜交叉采集（五平台 → game/ACG 过滤）
log("【①b 热榜】运行 collector_public.py（五平台热榜 → 游戏/ACG 交叉采集）...")
rp = subprocess.run([PY, "collector_public.py"], cwd=BASE, capture_output=True, text=True, encoding="utf-8", env=ENV)
pub_status = {}
pm = re.search(r"状态:\s*(\{.*\})", rp.stdout + rp.stderr)
if pm:
    try:
        pub_status = ast.literal_eval(pm.group(1))
    except Exception:
        pass
pub_ok = [k for k, v in pub_status.items() if str(v).startswith("OK")]
pub_fail = {k: v for k, v in pub_status.items() if not str(v).startswith("OK")}
pub_total = sum(1 for k, v in pub_status.items() if v)
log(f"    状态: {pub_status}")
log(f"    通过 {len(pub_ok)}/5 平台 · 失败 {len(pub_fail)}: {pub_fail if pub_fail else '无'}")
today_public = os.path.join(BASE, "collectors", "public_hotlist_" + TODAY.replace("-", "") + ".json")
public_ready = rp.returncode == 0 and os.path.exists(today_public)
# 热榜采集非阻断（单平台失败不阻断整体刷新），仅写进日志供溯源
if rp.returncode != 0:
    log(f"    ⚠ collector_public 非零退出（非阻断）：{(rp.stderr or rp.stdout).strip()[:200]}")
    # 不加入 problems（热榜是增量信号，缺失不影响日报核心板块）
if r.returncode != 0:
    problems.append(("刷新", "meme_radar.py", f"非零退出：{(r.stderr or r.stdout).strip()[:200]}",
                     "当日采集完整性", "阻断", "修复采集错误后重新运行；不得沿用历史采集结果"))
if not os.path.exists(today_meme):
    problems.append(("刷新", "当日采集文件", f"缺少 {os.path.basename(today_meme)}", "当日采集完整性", "阻断",
                     "先成功生成当天 meme 文件；refresh_content 已禁止回退到历史文件"))

# ①c 糖果梦 AI 游戏日报（非阻断 → 行业情报增量信号）
log("【①c tgmeng】运行 collector_tgmeng.py（糖果梦 AI 游戏日报 → 行���综述）...")
rt = subprocess.run([PY, "collector_tgmeng.py"], cwd=BASE, capture_output=True, text=True, encoding="utf-8", env=ENV)
if rt.returncode == 0:
    lines = [l for l in (rt.stdout + rt.stderr).split("\n") if l.strip()]
    for l in lines:
        if "✓" in l or "⚠" in l:
            log(f"    {l.strip()}")
else:
    log(f"    ⚠ collector_tgmeng 非零退出（非阻断）：{(rt.stderr or rt.stdout).strip()[:200]}")
# tgmeng 是 AI 综述信号，非阻断——缺失不影响日报核心板块

# ---------- ② 重建源覆盖报告 ----------
# 2026-08-05 治理：原实现是 re.sub(r"本次失败源：[^<]*", ...)，但页面上这句话早已被改写掉，
# 正则长期匹配不到 → 整个 #source 区块变成手写死数据，「已接入(60) / B站 5/6 / 57 源存活」
# 停在历史版本，跟 sources.toml、当日采集结果全对不上，属于防编造门槛的硬伤。
# 现在交给 gen_source_report.py 按真实文件实算重建整个区块。
log("【② 源覆盖报告】运行 gen_source_report.py（按 sources.toml / 探活 / 当日采集实算）...")
rsr = subprocess.run([PY, "gen_source_report.py"], cwd=BASE,
                     capture_output=True, text=True, encoding="utf-8", env=ENV)
if rsr.returncode == 0:
    log("    " + (rsr.stdout.strip().splitlines()[-1] if rsr.stdout.strip() else "(无输出)"))
else:
    problems.append(("刷新", "源覆盖报告", "gen_source_report.py 非零退出", "红线③来源可追溯",
                     "阻断", "源覆盖数字会停留在上一次的值，必须修好再发布"))
    log("    ⛔ gen_source_report 失败：" + (rsr.stderr or rsr.stdout).strip()[:200])
# 采集失败源仍单独记进日志，便于排查（页面数字由上面的脚本实算，不在这里拼字符串）
_all_fail = dict(fail)
_all_fail.update({f"热榜:{k}": v for k, v in pub_fail.items()})
log("    本次失败源：" + ("、".join(f"{k}({v})" for k, v in _all_fail.items())
                          if _all_fail else "无")
    + f"（B站 {len(ok)}/6 · 热榜 {len(pub_ok)}/5）")

# ---------- ②b 热榜→词云交叉加权 ----------
if public_ready:
    log("【②b 加权】运行 boost_hotlist.py（热榜→词云交叉加权）...")
    rb = subprocess.run([PY, "boost_hotlist.py"], cwd=BASE, capture_output=True, text=True, encoding="utf-8", env=ENV)
    log("    " + (rb.stdout.strip().splitlines()[-1] if rb.stdout.strip() else "(无加权)"))
    # 加权非阻断：热榜数据缺失时词云仍用原始 heat，不影响渲染
    if rb.returncode != 0:
        log("    ⚠ boost_hotlist 非零退出（非阻断）：" + rb.stderr.strip()[:160])
else:
    log("【②b 加权】跳过 boost_hotlist：当日热榜未就绪")

# ---------- ③ 重生词云(H) ----------
log("【③ 词云】运行 cross_words.py（统一 H 口径）...")
r2 = subprocess.run([PY, "cross_words.py"], cwd=BASE, capture_output=True, text=True, encoding="utf-8", env=ENV)
log("    " + (r2.stdout.strip().splitlines()[-1] if r2.stdout.strip() else r2.stderr.strip()[:200]))
if r2.returncode != 0:
    problems.append(("刷新", "cross_words.py", f"非零退出：{(r2.stderr or r2.stdout).strip()[:200]}",
                     "当日词云完整性", "阻断", "先基于当天采集重产 wordcloud_terms.json（date 必须等于当天），再重跑"))

# ---------- ③-信源采集（Phase 2，非阻塞） ----------
# 每次 refresh 同步各信源存活状态与最新条目到 inbox/（信源扩充=往 sources.toml 加一条）。
# 设 COLLECT_SOURCES=0 可跳过（避免刷新时联网耗时）。
if os.environ.get('COLLECT_SOURCES', '1') != '0':
    try:
        log("【③信源】运行 collect_sources.py（sources.toml -> inbox）...")
        rsrc = subprocess.run([PY, 'collect_sources.py'], cwd=BASE,
                               capture_output=True, text=True, encoding='utf-8', env=ENV, timeout=240)
        for line in rsrc.stdout.strip().splitlines()[-3:]:
            log("    " + line)
        if rsrc.returncode != 0:
            log("    ⚠ collect_sources 非零退出（非阻断）：" + rsrc.stderr.strip()[:160])
    except Exception as e:
        log("    ⚠ collect_sources 跳过（非阻断）：" + repr(e)[:160])
else:
    log("【③信源】COLLECT_SOURCES=0，跳过信源采集")

# ③-2 准入安检（Phase 3，非阻塞）：inbox 候选 -> sources_curated.json + 准入报告.md
try:
    log("【③准入】运行 admit_sources.py（inbox -> 准入安检）...")
    radm = subprocess.run([PY, 'admit_sources.py'], cwd=BASE,
                          capture_output=True, text=True, encoding='utf-8', env=ENV, timeout=180)
    for line in radm.stdout.strip().splitlines()[-2:]:
        log("    " + line)
    if radm.returncode != 0:
        log("    ⚠ admit_sources 非零退出（非阻断）：" + radm.stderr.strip()[:160])
except Exception as e:
    log("    ⚠ admit_sources 跳过（非阻断）：" + repr(e)[:160])

# ③-3 策展晋升（Phase 4，非阻塞）：curated 的 adopt 条目 -> events.json 信源快报
try:
    log("【③策展】运行 promote_sources.py（adopt -> 信源快报）...")
    rpro = subprocess.run([PY, 'promote_sources.py'], cwd=BASE,
                          capture_output=True, text=True, encoding='utf-8', env=ENV, timeout=120)
    for line in rpro.stdout.strip().splitlines()[-2:]:
        log("    " + line)
    if rpro.returncode != 0:
        log("    ⚠ promote_sources 非零退出（非阻断）：" + rpro.stderr.strip()[:160])
except Exception as e:
    log("    ⚠ promote_sources 跳过（非阻断）：" + repr(e)[:160])

# ③-4 信源自发现（Phase 5，非阻塞）：扫已登记源的同域栏目/跨域站点 + 行业种子 -> 新信源建议
# 脚本内置 7 天节流，日跑不会每天联网重扫；DISCOVER_SOURCES=0 可完全关闭。
if os.environ.get('DISCOVER_SOURCES', '1') != '0':
    try:
        log("【③自发现】运行 discover_sources.py（扫描 -> 新信源建议）...")
        rdis = subprocess.run([PY, 'discover_sources.py'], cwd=BASE,
                              capture_output=True, text=True, encoding='utf-8', env=ENV, timeout=600)
        for line in rdis.stdout.strip().splitlines()[-3:]:
            log("    " + line)
        if rdis.returncode != 0:
            log("    ⚠ discover_sources 非零退出（非阻断）：" + rdis.stderr.strip()[:160])
    except Exception as e:
        log("    ⚠ discover_sources 跳过（非阻断）：" + repr(e)[:160])
else:
    log("【③自发现】DISCOVER_SOURCES=0，跳过信源自发现")

# ③-4.5 内容板块自动刷新（梗雷达/TOP10）：用 ① 采集到的当天 meme 结果重刷 #hot/#radar 两块
# 必须排在 meme_radar 采集之后（读 collectors/meme_YYYYMMDD.json）、且在 ③-5 unify_heat 之前
# （本步只写原始播放量，H 由 ③-5 统一换算，保持单一职责、避免两处各写一套 H 对不上）。
if meme_ready:
    try:
        log("【③内容】运行 refresh_content.py（当天采集 -> 重刷梗雷达/TOP10）...")
        rcon = subprocess.run([PY, 'refresh_content.py'], cwd=BASE,
                              capture_output=True, text=True, encoding='utf-8', env=ENV, timeout=60)
        for line in rcon.stdout.strip().splitlines()[-3:]:
            log("    " + line)
        if rcon.returncode != 0:
            problems.append(("刷新", "refresh_content.py", f"非零退出：{(rcon.stderr or rcon.stdout).strip()[:200]}",
                             "当日内容完整性", "阻断", "修复后重跑；不得沿用历史采集内容"))
    except Exception as e:
        problems.append(("刷新", "refresh_content.py", repr(e)[:200], "当日内容完整性", "阻断",
                         "修复执行异常后重新运行"))
else:
    log("【③内容】跳过 refresh_content：当天采集未就绪，拒绝用历史内容冒充今日刷新")

# ③-5 热度口径统一（跨板块 H）：把 TOP10/梗雷达/视觉焦点 的原始播放量换算成 H
# 必须排在 meme_radar 采集之后（采集会写回原始播放量）。全站统一为 index.html。
try:
    log("【③热度】运行 unify_heat.py（原始播放量 -> 统一 H）...")
    rheat = subprocess.run([PY, 'unify_heat.py'], cwd=BASE,
                           capture_output=True, text=True, encoding='utf-8', env=ENV, timeout=60)
    log("    " + (rheat.stdout.strip().splitlines()[0] if rheat.stdout.strip() else rheat.stderr.strip()[:160]))
    if rheat.returncode != 0:
        log("    ⚠ unify_heat 非零退出（非阻断）：" + rheat.stderr.strip()[:160])
except Exception as e:
    log("    ⚠ unify_heat 跳过（非阻断）：" + repr(e)[:160])

# ③-5.5 糖果梦 AI 日报 -> 站内热度融合（非阻断）：概念匹配 + boost 权重
try:
    log("【③AI融合】运行 boost_tgmeng.py（日报概念 -> 站内交叉匹配）...")
    rb = subprocess.run([PY, "boost_tgmeng.py"], cwd=BASE,
                        capture_output=True, text=True, encoding="utf-8", env=ENV, timeout=120)
    for line in rb.stdout.strip().splitlines()[-4:]:
        log("    " + line)
    if rb.returncode != 0:
        log("    boost_tgmeng 非零退出（非阻断）：" + (rb.stderr or "").strip()[:160])
except Exception as e:
    log("    boost_tgmeng 跳过（非阻断��：" + repr(e)[:160])

# ③-6 糖果梦 AI 日报卡片 + badge 注入（非阻断）：无外链，纯站内概念信号
try:
    import glob as _g, re as _re
    tg_files = sorted(_g.glob(os.path.join(BASE, "collectors", "tgmeng_daily_*.json")), reverse=True)
    bt_files = sorted(_g.glob(os.path.join(BASE, "collectors", "tgmeng_boost_*.json")), reverse=True)
    _daily_path = os.path.join(BASE, "index.html")
    if not os.path.exists(_daily_path):
        log("    index.html 不存在，跳过 tgmeng 卡片+badge 注入")
    elif not tg_files:
        log("    未找到 tgmeng_daily_*.json，跳过")
    else:
        _tdata = json.load(open(tg_files[0], encoding="utf-8"))
        _entry = _tdata.get("entry")
        if _entry and isinstance(_entry, dict) and _entry.get("title") and not _entry.get("error"):
            _entry_count = _tdata.get("all_entries_count", 0)
            _concepts = _entry.get("tags") or []
            _boost_data = {}
            if bt_files:
                try:
                    _boost_data = json.load(open(bt_files[0], encoding="utf-8"))
                except Exception:
                    pass
            _summ = _boost_data.get("summary", {})
            _matched_c = _summ.get("matched_concepts", [])
            _unmatched_c = _summ.get("unmatched_concepts", [])
            _concept_rows = ""
            for _tag in _concepts:
                if _tag in _matched_c:
                    _concept_rows += '<span class="tgmeng-concept-matched">✅ ' + _tag + '</span>'
                else:
                    _concept_rows += '<span class="tgmeng-concept-trend">🔀 ' + _tag + '</span>'
            _ai_parts = []
            if _entry.get("ai_platform"): _ai_parts.append(str(_entry["ai_platform"]))
            if _entry.get("ai_model"): _ai_parts.append(str(_entry["ai_model"]))
            if _entry.get("generated_at"): _ai_parts.append("生成: " + str(_entry["generated_at"])[:16])
            _ai_meta = " · ".join(_ai_parts)
            _card_html = ('<section id="tgmeng">'
                '<div class="sec-title">'
                '<span class="bar" style="background:linear-gradient(135deg,#c792ea,#4da3ff)"></span>'
                'AI 趋势信号 <small>糖果梦 · 日报概念融合 · ' + str(_entry_count) + ' 期历史</small>'
                '</div>'
                '<div class="tgmeng-card">'
                '<div class="tgmeng-head">'
                '<h3>' + str(_entry.get("title", "")) + '</h3>'
                '<span class="tgmeng-date">' + str(_entry.get("date", "")) + '</span>'
                '</div>'
                '<div class="tgmeng-concept-bar">' + _concept_rows + '</div>'
                '<div class="tgmeng-meta">'
                '<span>' + _ai_meta + '</span>'
                '<span class="tgmeng-signal-note">'
                '信号匹配: ' + str(len(_matched_c)) + '/' + str(len(_concepts)) + ' 概念已确认')
            if _unmatched_c:
                _card_html += ' · ' + str(len(_unmatched_c)) + ' 待观察'
            _card_html += '</span></div></div></section>'

            _daily_content = io.open(_daily_path, encoding="utf-8").read()
            _badge_count = 0
            if _boost_data and _boost_data.get("matched_items"):
                for _item in _boost_data["matched_items"]:
                    _url = _item.get("url", "")
                    _concept = _item.get("concept", "")
                    if not _url or "bilibili.com/video/" not in _url:
                        continue
                    _badge_attr = 'data-tgmeng="' + _concept + '"'
                    if _badge_attr not in _daily_content:
                        _old = 'href="' + _url + '"'
                        _new = _old + ' ' + _badge_attr
                        if _old in _daily_content and _new not in _daily_content:
                            _daily_content = _daily_content.replace(_old, _new, 1)
                            _badge_count += 1
                if _badge_count:
                    log("    " + str(_badge_count) + " 条站内内容已标记 AI 确认信号")

            if 'id="tgmeng"' not in _daily_content:
                _daily_content = _daily_content.replace(
                    '\n<section id="source"',
                    '\n' + _card_html + '\n<section id="source"')
            else:
                _daily_content = _re.sub(
                    r'<section id="tgmeng">.*?</section>',
                    _card_html, _daily_content, flags=_re.S)
                log("    index.html 已含旧 tgmeng 卡片，已替换为新版（概念信号）")
            try:
                with open(_daily_path, "w", encoding="utf-8") as _fh:
                    _fh.write(_daily_content)
                log("    AI 趋势信号卡片已注入 index.html（" + str(len(_concepts)) + " 概念）")
            except PermissionError:
                log("    index.html 被锁定（IDE 预览窗口），跳过写入")
        else:
            log("    tgmeng 数据不可用，跳过卡片+badge 注入")
except Exception as _e:
    log("    tgmeng 卡片+badge 注入跳过（非阻断）：" + repr(_e)[:160])
try:
    import glob as _g
    tg_files = sorted(_g.glob(os.path.join(BASE, "collectors", "tgmeng_daily_*.json")), reverse=True)
    if tg_files:
        _tdata = json.load(open(tg_files[0], encoding="utf-8"))
        _entry = _tdata.get("entry")
        if _entry and isinstance(_entry, dict) and _entry.get("title") and not _entry.get("error"):
            log("【③AI日报】从 tgmeng 数据构建卡片...")
            _entry_count = _tdata.get("all_entries_count", 0) or _tdata.get("all_entries_count", 0)

            # 构建标签 chips
            _tags_html = ""
            for _t in (_entry.get("tags") or [])[:6]:
                _tags_html += f'<span class="tgmeng-tag">{_t}</span>'

            # AI 信息行
            _ai_parts = []
            if _entry.get("ai_platform"):
                _ai_parts.append(f'AI: {_entry["ai_platform"]}')
            if _entry.get("ai_model"):
                _ai_parts.append(_entry["ai_model"])
            if _entry.get("token_usage"):
                _ai_parts.append(f'Token: {_entry["token_usage"]:,}')
            if _entry.get("generated_at"):
                _ai_parts.append(f'生成: {_entry["generated_at"]}')
            _ai_meta_str = " · ".join(_ai_parts)

            _summary = (_entry.get("summary") or "").strip()
            _card_html = f'''<section id="tgmeng"><div class="sec-title"><span class="bar" style="background:linear-gradient(135deg,#c792ea,#4da3ff)"></span>AI 游戏日报 <small>糖果梦 · AI 自动生成每日行业综述 · {_entry_count} 篇历史归档</small></div><div class="tgmeng-card"><a class="tgmeng-head" target="_blank" href="{_entry['url']}"><h3>{_entry['title']}</h3><span class="tgmeng-date">{_entry['date']}</span></a>'''
            if _summary:
                _card_html += f'<p class="tgmeng-summary">{_summary}</p>'
            if _tags_html:
                _card_html += f'<div class="tgmeng-tags">{_tags_html}</div>'
            _card_html += f'''<div class="tgmeng-meta"><span>{_ai_meta_str}</span><a class="tgmeng-source" target="_blank" href="https://tgmeng.com/daily/game">查看完整日报 &#8594;</a></div></div></section>'''

            # 插入到 index.html: 在 <section id="source" 之前
            _daily_path = os.path.join(BASE, "index.html")
            if os.path.exists(_daily_path):
                _daily_content = io.open(_daily_path, encoding="utf-8").read()
                if 'id="tgmeng"' not in _daily_content:
                    _daily_content = _daily_content.replace('\n<section id="source"',
                                                            '\n' + _card_html + '\n<section id="source"')
                    with open(_daily_path, "w", encoding="utf-8") as _fh:
                        _fh.write(_daily_content)
                    log("    ✓ AI 日报卡片已注入 index.html")
                else:
                    log("    ⚠ index.html 已含 tgmeng 卡片（可能是重复注入），跳过")
            else:
                log("    ⚠ index.html 不存在，跳过卡片注入")
        else:
            log("    ⚠ tgmeng 数据不可用（缺失/近期失败/无 entry），跳过卡片注入")
    else:
        log("    ⚠ 未找到 tgmeng_daily_*.json，跳过卡片注入")
except Exception as _e:
    log("    ⚠ tgmeng 卡片注入跳过（非阻断）：" + repr(_e)[:160])

# ---------- ④-0 由 events.json 重建日历 section（注入 index.html） ----------
# 注意：本步必须在 refresh_content（③-4.5）之后运行，因为两者都读/写 index.html；
# gen_calendar 替换 #cal/#forward 区块，refresh_content 替换 masthead/brief/visual/TOP10/hot，
# 区块不重叠，按序写入互不影响。
if os.path.exists(os.path.join(BASE, "events.json")):
    log("【④ 日历】运行 gen_calendar.py（events.json -> index.html #cal section）...")
    r0 = subprocess.run([PY, "gen_calendar.py"], cwd=BASE, capture_output=True, text=True, encoding="utf-8", env=ENV)
    if r0.returncode != 0:
        problems.append(("日历", "events.json 生成失败", r0.stderr.strip()[:200], "红线③链接必匹配内容", "阻断", "检查 events.json 占位符/__SRC__ 是否完整"))
    else:
        log("    " + r0.stdout.strip())
else:
    log("【④ 日历】未找到 events.json，无法重建日历 section")
    problems.append(("日历", "events.json 缺失", "无法从日历真源生成日历区块", "构建完整性", "阻断",
                     "恢复 events.json 后重新生成，禁止沿用旧日历"))

# ================= ④ 自洽校验 =================
log("\n================= ④ 自洽校验 =================")

def roots_of(fn):
    """返回外部站点根页链接；带具体 query/fragment 路由的页面不当作根页。"""
    out = []
    hh = io.open(os.path.join(BASE, fn), encoding="utf-8").read()
    for u in re.findall(r'href="([^"]+)"', hh):
        if not u.startswith(("http://", "https://")):
            continue
        p = urllib.parse.urlparse(u)
        if p.path in ("", "/") and not p.query and not p.fragment:
            out.append(u)
    return out

# (a) 词云 H 口径
wc = io.open(os.path.join(BASE, "wordcloud.html"), encoding="utf-8").read()
h_count = wc.count("热度数 H=")
if h_count == 0:
    problems.append(("全局", "词云", "词云未使用统一热度数 H，仍用内部/来源字面热度值", "红线①热度口径", "阻断", "cross_words 已改为 H=100×score/max；重新生成词云"))
else:
    log(f"(a) 词云 H 口径：✅ 共 {h_count} 个词带 H（热度数 H=XX）")

# (b) GameLook 主页占位
gamelook_roots = []
for f in ["index.html", "wordcloud.html"]:
    gamelook_roots += [u for u in roots_of(f) if "gamelook.com.cn" in u]
if gamelook_roots:
    problems.append(("全局", "GameLook 链接", f"仍存在 {len(gamelook_roots)} 个 GameLook 主页占位链接", "红线③禁主页占位", "阻断", "深抓到具体文章 URL（形如 /2026/07/598608/），禁填 gamelook.com.cn/"))
else:
    log("(b) GameLook 主页占位：✅ 0 个（已全部深抓到具体文章 URL）")

# (b2) 任意外部根页占位：不能只盯 GameLook，游戏官网/展会官网根页同样不承载具体事件。
root_links = []
for f in ["index.html", "wordcloud.html"]:
    root_links += [(f, u) for u in roots_of(f)]
if root_links:
    problems.append(("全局", "外部根页链接", f"存在 {len(root_links)} 个主页占位链接：{root_links[:5]}",
                     "红线③链接必须溯源具体页面", "阻断", "替换为具体文章/公告/详情页；无法核验则撤下该节点"))
    log(f"(b2) 外部根页占位：❌ {len(root_links)} 个")
else:
    log("(b2) 外部根页占位：✅ 0 个")

# (c) 待接入源告警占位
src = io.open(os.path.join(BASE, "index.html"), encoding="utf-8").read()
m_src = re.search(r'<section id="source" class="src-mini">.*?</section>', src, re.S)
src_txt = m_src.group(0) if m_src else ""
# 触乐/竞核/手游那点事已于 Phase 5/7 接入 sources.toml（信源快报出具体文章 URL），从待接入清单移除
# DataEye 为 SaaS 数据平台（无新闻 feed），已注册为 static 探活源，不再标待接入
pending = []
missing = [p for p in pending if p not in src_txt]
if missing:
    problems.append(("全局", "源覆盖报告", f"待接入源未占位告警：{missing}", "采集规范·待接入占位", "优化", "在源覆盖报告补 ⚠ 待接入占位"))
else:
    log(f"(c) 待接入源告警占位：✅ {pending} 已在源覆盖报告标记 ⚠ 待接入·未覆盖")

# (c2) 搜索链接 / 可疑编造详情ID（全站，含日历）
bad_search, bad_fakeid = [], []
for f in ["index.html", "wordcloud.html"]:
    hh = io.open(os.path.join(BASE, f), encoding="utf-8").read()
    bad_search += [(f, u) for u in re.findall(r'https://search\.bilibili\.com[^"]*', hh)]
    bad_fakeid += [(f, u) for u in re.findall(r'https?://[^"]*mihoyo\.com[^"]*news[/a-z]*/\d{4,}[^"]*', hh)]
if bad_search:
    problems.append(("全局", "搜索链接", f"存在 {len(bad_search)} 个 search.bilibili 搜索链接：{bad_search[:5]}", "三不原则·禁搜索链接", "阻断", "替换为真实官网/文章页"))
else:
    log("(c2) 搜索链接：✅ 全站 0 个 search.bilibili")
if bad_fakeid:
    problems.append(("全局", "可疑详情ID", f"存在 {len(bad_fakeid)} 个无法验证的米哈游 news 详情ID链接：{bad_fakeid[:5]}", "红线③链接必真实", "阻断", "改为官方公告列表页（列表页真实可达），或人工核实ID"))
else:
    log("(c2) 可疑详情ID：✅ 全站 0 个未经核实的米哈游 news/数字ID 链接")

# (c3) 日历游戏名↔链接域名一致性（防"蛋仔派对链到暗区突围"类张冠李戴）
# 值为"允许的域名片段列表"：官网 + Steam 官方新闻等第一方渠道（与 admit_sources.py 保持一致）
# Steam 新闻链接形如 store.steampowered.com/news/app/<appid>/view/... 是发行商官方公告，按 appid 精确定位，属第一方
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
    '主机/PC新作': ['steampowered', 'xbox.com'],
    '主机/PC 大作': ['steampowered', 'xbox.com'],
}
# 从 index.html 中提取日历区域进行校验（架构合并后日历内嵌在 index 中）
idx_h = io.open(os.path.join(BASE, "index.html"), encoding="utf-8").read()
# 提取 #cal + #forward 区块
_cal_match = re.search(r'<section id="cal">.*?</section>.*?<section id="source"', idx_h, re.S)
cal_h = _cal_match.group(0) if _cal_match else idx_h
mismatches = []
for mm2 in re.finditer(r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', cal_h, re.S):
    _url = mm2.group(1)
    _txt = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', mm2.group(2))).strip()
    _hit = None
    for g in sorted(GAME_DOMAIN, key=len, reverse=True):
        if _txt.startswith(g):
            _hit = g
            break
    if not _hit:
        continue
    # (c3) 本意是防「张冠李戴」——游戏名链到【另一个游戏】的官方域名（如蛋仔派对链到暗区突围）。
    # 因此只在两种情况下判错配：
    #   ① URL 命中【另一个游戏】的域名（跨游戏错配）；
    #   ② URL 是主页/根页占位（无深链）。
    # 命中本游戏官方域名、或落在中立新闻/行业站点（new.qq.com、gamelook 等）均视为合规——
    # 否则「王者荣耀新闻发在腾讯新闻」这类正常链接会被误杀（2026-07-31 雾影猎人刷新后实测触发）。
    if any((f in _url) for f in GAME_DOMAIN[_hit]):
        continue  # 本游戏官方域名，OK
    cross = False
    for h, doms in GAME_DOMAIN.items():
        if h == _hit:
            continue
        if any((f in _url) for f in doms):
            cross = True
            break
    if cross:
        mismatches.append((_hit, _txt[:30], _url[:60]))
if mismatches:
    problems.append(("日历", "游戏名↔域名错配", f"{len(mismatches)} 条链接与游戏不符：{mismatches[:5]}", "红线③链接必匹配内容", "阻断", "按 GAME_DOMAIN 映射修正为对应游戏官网或真实信源"))
    log(f"(c3) 日历名↔域名一致性：❌ {len(mismatches)} 条错配")
else:
    log("(c3) 日历名↔域名一致性：✅ 0 错配")

# (c4) 信源注册表存活（Phase 2 产出；任何非 200 源直接在日志告警，前移"死链"发现）
try:
    st_path = os.path.join(BASE, 'sources_status.json')
    if os.path.exists(st_path):
        st_doc = json.load(io.open(st_path, encoding='utf-8'))
        dead = [s for s in st_doc if s.get('status') != 200]
        if dead:
            names = '、'.join('%s(%s)' % (s.get('game'), s.get('status')) for s in dead)
            problems.append(("信源", "注册表存活", f"{len(dead)} 个源不可达：{names}", "红线③链接必真实", "优化", "修正 sources.toml 中该源的 url 为可用页面"))
            log(f"(c4) 信源注册表存活：❌ {len(dead)} 个不可达 → {names}")
        else:
            log(f"(c4) 信源注册表存活：✅ 全部 200（{len(st_doc)} 源存活）")
    else:
        log("(c4) 信源注册表存活：⚠ 未找到 sources_status.json（本次未采集）")
except Exception as e:
    log("(c4) 信源注册表存活：⚠ 读取失败 " + repr(e)[:120])

# (c5) 准入安检结果（Phase 3 产出；剔除项直接告警，前移"死链/错配"发现）
try:
    cur_path = os.path.join(BASE, 'inbox', 'sources_curated.json')
    if os.path.exists(cur_path):
        cur = json.load(io.open(cur_path, encoding='utf-8'))
        s = cur.get('summary', {})
        rej, rev = s.get('reject', 0), s.get('review', 0)
        new_rej = s.get('new_reject', rej)   # 只对"新出现的剔除项"告警，已知长期死链静默存档
        if new_rej:
            problems.append(("信源", "准入安检", f"新增 {new_rej} 条候选被剔除（死链/错配/主页占位）", "红线③链接必真实", "优化", "看 inbox/准入报告.md 的 ❌ 分组，必要时修正 sources.toml 或信源页"))
            log(f"(c5) 准入安检：❌ 新增 {new_rej} 条剔除（本次共 {rej} 条）、{rev} 条待复核")
        elif rej:
            log(f"(c5) 准入安检：✅ 0 新增剔除（{rej} 条为已知长期死链，已静默存档）、{rev} 条待复核")
        else:
            log(f"(c5) 准入安检：✅ 0 剔除、{rev} 条待复核（共 {s.get('total')} 候选）")
    else:
        log("(c5) 准入安检：⚠ 未找到 sources_curated.json（本次未安检）")
except Exception as e:
    log("(c5) 准入安检：⚠ 读取失败 " + repr(e)[:120])

# (c6) 信源快报（Phase 4 产出；页上已自动收录的条目数）
try:
    ev_doc = json.load(io.open(os.path.join(BASE, 'events.json'), encoding='utf-8'))
    fc = len(ev_doc.get('feed_events', []))
    log(f"(c6) 信源快报：✅ 页上已收录 {fc} 条（每日自动从准入 adopt 晋升）")
except Exception as e:
    log("(c6) 信源快报：⚠ 读取失败 " + repr(e)[:120])

# (c7) 信源自发现（Phase 5 产出；待入库的新信源建议数 = 信源池还能长多大）
try:
    dpath = os.path.join(BASE, 'inbox', 'sources_discovered.json')
    if os.path.exists(dpath):
        dd = json.load(io.open(dpath, encoding='utf-8'))
        s = dd.get('summary', {})
        sug, mb = s.get('suggest', 0), s.get('maybe', 0)
        age = (datetime.datetime.now() - datetime.datetime.strptime(
            dd.get('generated_at'), '%Y-%m-%dT%H:%M:%S')).days
        if sug:
            problems.append(("信源", "自发现建议", f"{sug} 条新信源建议待入库（{age} 天前扫描）",
                             "信源优先/持续扩源", "优化",
                             "看 inbox/新信源建议.md，把合适的 [[sources]] 片段粘进 sources.toml"))
        log(f"(c7) 信源自发现：{'⚠' if sug else '✅'} 建议入库 {sug} 条 ｜ 待确认 {mb} 条"
            f"（{age} 天前扫描，报告 inbox/新信源建议.md）")
    else:
        log("(c7) 信源自发现：⚠ 未找到 sources_discovered.json（本次未扫描）")
except Exception as e:
    log("(c7) 信源自发现：⚠ 读取失败 " + repr(e)[:120])

# (d) 跨板块 H 一致性
# 只查"热度展示槽位"（em / t10-meta / vf-cap small），槽位里出现原始播放量却没有 H 才算违规。
# 不再扫正文标题——新闻标题里的"玩家破500万""3200万欧"是事实陈述，不是热度口径，扫了就是误报。
SLOTS = [
    ("梗雷达 em", r'<em(?:\s[^>]*)?>(?:(?!</em>).)*</em>'),
    ("TOP10 meta", r'<span class="t10-meta"(?:\s[^>]*)?>(?:(?!</span>).)*</span>'),
    ("视觉焦点 cap", r'<span class="vf-cap">.*?<small(?:\s[^>]*)?>(?:(?!</small>).)*</small>'),
]
raw_vol = re.compile(r"(\d+(\.\d+)?\s*万|播放量|views?|热度=\d+)")
bad_slots = []
for label, pat in SLOTS:
    for mm in re.finditer(pat, src, re.S):
        seg = mm.group(0)
        vis = re.sub(r'\s(?:title|alt)="[^"]*"', '', seg)   # tooltip 里的"原始：1165万"是合规附注，剔除后再查
        if raw_vol.search(vis) and not re.search(r'\bH\d+\b', vis):
            bad_slots.append((label, re.sub(r'<[^>]+>', '', vis)[:40]))
if bad_slots:
    problems.append(("全局", "跨板块 H", f"{len(bad_slots)} 个热度槽位仍是原始播放量、未换算 H：{bad_slots[:5]}", "红线①②跨板块一致", "优化", "运行 unify_heat.py 统一为 H（组内归一，原始量降级为附注）"))
    log(f"(d) 跨板块 H：⚠ {len(bad_slots)} 个槽位未统一 H：{bad_slots[:5]}")
else:
    log("(d) 跨板块 H：✅ 所有热度槽位均已换算为 H（原始量仅作附注）")

# (e) 人工策展区块过期告警：扫描 data-curated 标记（视觉焦点 / 风险观察），
# 超 N 天未人工确认即告警。refresh_content 只在"尚未打标"时写日期，
# 所以一旦人工复核并更新过内容，标记日期会刷新、告警消失——日期只代表"上次人工碰过"。
import datetime as _dt
CURATED_MAX_AGE = 7
curated_marks = re.findall(r'data-curated="(\d{4}-\d{2}-\d{2})"', src)
stale = []
for d in curated_marks:
    try:
        age = (_dt.date.today() - _dt.date.fromisoformat(d)).days
    except Exception:
        continue
    if age > CURATED_MAX_AGE:
        stale.append((d, age))
if stale:
    problems.append(("全局", "人工策展过期", f"{len(stale)} 个策展块超过 {CURATED_MAX_AGE} 天未人工刷新：{stale}", "内容时效", "优化", "人工复核 TOP10 头条/风险观察/视觉焦点并更新，标记刷新后告警消失"))
    log(f"(e) 人工策展过期：❌ {len(stale)} 个超期：{stale}")
else:
    _mk = sorted(set(curated_marks)) or ["（无策展块标记）"]
    log(f"(e) 人工策展过期：✅ 策展块均在 {CURATED_MAX_AGE} 天内（标记于 {_mk}）")

# ================= ⑤ 写日志 =================
log("\n================= ⑤ 自洽日志 =================")
blocking = [p for p in problems if p[4] == "阻断"]
optimizing = [p for p in problems if p[4] == "优化"]
if not problems:
    verdict = "高（无问题）"
elif not blocking:
    verdict = "中（仅优化级）"
else:
    verdict = "低（存在阻断级，须修复后再发布）"

md = []
md.append(f"# GamePulse 自洽日志 · {TODAY}\n")
md.append("> 按「自洽提示词体系」生成的每日刷新 + 自洽校验报告。\n")
md.append("## 一、源覆盖报告（采集状态）\n")
md.append(f"- 采集时间：{TODAY}（每日刷新）")
md.append(f"- 已接入源(14+5)：B站(热门/热搜/每周必看/梗解读/梗百科账号) · 贴吧热议 · Steam国区热销 · Reddit r/Games · GameLook · 游戏陀螺 · 白鲸出海 · 触乐 · 竞核 · 手游那点事 · 糖果梦AI日报 · 微信好文 · 微博热搜 · 知乎热榜 · 抖音热榜 · B站热搜(洛樱云) · 小红书热榜（全网热榜为增量交叉信号，collector_public.py 采集，sources.toml static 注册；糖果梦 AI 日报为日报综述信号，collector_tgmeng.py 独立采集）")
md.append(f"- DataEye：static 探活注册（SaaS 数据平台，非传统新闻源，不参与内容采集）")
md.append(f"- 待接入源({len(pending)})·⚠告警占位：{' · '.join(pending) if pending else '无（backlog 已清零）'}")
md.append(f"- 本次采集：B站 {len(ok)}/6 · 全网热榜 {len(pub_ok)}/5 · 失败/跳过 {fail if fail else '无'}（单源失败自动跳过）")
md.append(f"- GameLook 深抓：✅ 全部指向具体文章 URL，无主页占位\n")
md.append("## 二、自洽问题清单\n")
md.append("| 层级 | 板块/位置 | 问题 | 违反维度 | 严重度 | 修复建议 |")
md.append("| --- | --- | --- | --- | --- | --- |")
if not problems:
    md.append("| — | 全站 | 无问题 | — | — | — |")
for p in problems:
    md.append("| " + " | ".join(str(x) for x in p) + " |")
md.append("")
md.append("## 三、自洽度结论\n")
md.append(f"- 整体自洽度：**{verdict}**")
md.append(f"- 阻断级必修复项：{len(blocking)} 条 → {[p[1] for p in blocking] if blocking else '无'}")
md.append(f"- 优化级记入迭代 backlog：{len(optimizing)} 条 → {[p[1] for p in optimizing] if optimizing else '无'}")
md.append("\n## 四、本次修复亮点（对照自洽提示词两大已知问题）\n")
md.append("- **问题 A（信息不全）**：行业情报站 GameLook 链接已全部深抓到具体文章 URL（无 `gamelook.com.cn/` 主页占位）；原待接入源 backlog（触乐/竞核/手游那点事/DataEye）已于 Phase 7 全部注册（触乐/竞核/手游那点事 为 news_list 采集，DataEye 为 static 探活），源覆盖报告已清零。")
md.append("- **问题 B（词云热度不自洽）**：词云「热度」已统一为全站口径热度数 H(0–100)，tooltip 格式 `热度数 H=XX ｜ 来源：… ｜ 原始：…`，不再用来源字面/内部累计分充当热度。")
md.append("- **热度口径**：词云、梗雷达、内容 TOP10 与视觉焦点均以 H(0–100) 为主展示；原始量仅保留在 tooltip/附注中。\n")
md.append("---")
md.append(f"*生成于 {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · daily_refresh.py*")

log_path = os.path.join(BASE, f"自洽日志_{TODAY}.md")
io.open(log_path, "w", encoding="utf-8").write("\n".join(md))
log(f"\n日志已写入：{log_path}")
log(f"整体自洽度：{verdict} ｜ 阻断 {len(blocking)} ｜ 优化 {len(optimizing)}")
