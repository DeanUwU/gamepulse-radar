# -*- coding: utf-8 -*-
"""重建 index.html（主站入口）：从 daily/calendar/wordcloud 三个子页抽取内容拼装。
子页改动后运行本脚本即可同步主站。用法：
C:\\Users\\shudizhao\\.workbuddy\\binaries\\python\\envs\\default\\Scripts\\python.exe rebuild_main.py
"""
import re, io, os, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
def rd(f): return io.open(os.path.join(BASE, f), encoding='utf-8').read()
def sections(html):
    return {m.group(1): m.group(0) for m in
            re.finditer(r'<section id="(\w+)"[^>]*>.*?</section>', html, re.DOTALL)}

DAILY_HTML    = os.environ.get("RB_DAILY",    os.path.join(BASE, 'daily.html'))
CALENDAR_HTML = os.environ.get("RB_CALENDAR", os.path.join(BASE, 'calendar.html'))
WC_HTML       = os.environ.get("RB_WC",       os.path.join(BASE, 'wordcloud.html'))

daily = io.open(DAILY_HTML, encoding='utf-8').read()
cal   = io.open(CALENDAR_HTML, encoding='utf-8').read()
wc    = io.open(WC_HTML, encoding='utf-8').read()

dsec = sections(daily)
csec = sections(cal)

# --- 词云概览：抽 wc 样式块 + 词云本体 + 图例（不带 TOP8 详情） ---
m_style = re.search(r'<style>[^<]*?\.wc\{.*?</style>', wc, re.DOTALL)
wc_style = m_style.group(0) if m_style else ''
m_cloud = re.search(r'<div class="wc">.*?</div>\s*<div class="wc-legend">.*?</div>', wc, re.DOTALL)
assert m_cloud, 'wordcloud body not found'
wc_cloud = m_cloud.group(0)

today = datetime.date.today().strftime('%Y-%m-%d')

DAILY_ORDER = ['brief','visual','board','radar','hot','media','rival']
daily_html = '\n'.join(dsec[i] for i in DAILY_ORDER if i in dsec)
source_html = dsec.get('source','')  # 源覆盖报告单独取出，放到页面最底部（日历之后）

# 头图 masthead（daily.html 中 <!--mh-->...<!--/mh--> 区块，含渐变遮罩+GamePulse logo）
m_mh = re.search(r'<!--mh-->.*?<!--/mh-->', daily, re.DOTALL)
masthead = m_mh.group(0) if m_mh else ''

# GamePulse 全站交互脚本：品牌色呼吸 + 光标驱动 3D 透视倾斜（gsap.utils.interpolate）
GSAP_SCRIPT = (
'<script>\n'
'(function(){\n'
'  var root = document.documentElement;\n'
'  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;\n'
'  // 品牌色呼吸（原生 rAF 实现，免 CDN 依赖，离线/沙箱可用）\n'
'  if(!reduce){\n'
'    var cA=[255,92,57], cB=[77,163,255];\n'
'    var t0=performance.now(), dur=3000;\n'
'    function lerp(a,b,t){return a+(b-a)*t;}\n'
'    (function loop(now){\n'
'      var p=(Math.sin((now-t0)/dur*Math.PI*2)+1)/2;\n'
'      root.style.setProperty("--pulse","rgb("+Math.round(lerp(cA[0],cB[0],p))+","+Math.round(lerp(cA[1],cB[1],p))+","+Math.round(lerp(cA[2],cB[2],p))+")");\n'
'      root.style.setProperty("--pulse-glow",(p*14).toFixed(1)+"px");\n'
'      requestAnimationFrame(loop);\n'
'    })(t0);\n'
'  }\n'
'  // 头图 GamePulse logo：全窗口光标驱动倾斜\n'
'  var mlogo = document.querySelector(".mh-logo");\n'
'  if(mlogo){\n'
'    mlogo.style.transformStyle="preserve-3d";\n'
'    if(!reduce){\n'
'      window.addEventListener("mousemove", function(e){\n'
'        var rx=20-(e.clientY/window.innerHeight)*40;\n'
'        var ry=-24+(e.clientX/window.innerWidth)*48;\n'
'        mlogo.style.transform="perspective(600px) rotateX("+rx.toFixed(1)+"deg) rotateY("+ry.toFixed(1)+"deg)";\n'
'      });\n'
'    }\n'
'  }\n'
'  // 卡片光标驱动 3D 透视倾斜\n'
'  if(!reduce){\n'
'    var sels=".card, .vf-card, .hl-item, .mod-card, .top10-item, .fitem";\n'
'    var cards = document.querySelectorAll(sels);\n'
'    for(var i=0;i<cards.length;i++){\n'
'      (function(card){\n'
'        card.style.transformStyle="preserve-3d";\n'
'        card.addEventListener("mousemove", function(e){\n'
'          var r=card.getBoundingClientRect();\n'
'          var px=(e.clientX-r.left)/r.width, py=(e.clientY-r.top)/r.height;\n'
'          var ry=(px-0.5)*24, rx=(0.5-py)*24;\n'
'          card.style.transform="perspective(800px) rotateX("+rx.toFixed(1)+"deg) rotateY("+ry.toFixed(1)+"deg)";\n'
'        });\n'
'        card.addEventListener("mouseleave", function(){\n'
'          card.style.transition="transform .5s ease";\n'
'          card.style.transform="perspective(800px) rotateX(0deg) rotateY(0deg)";\n'
'          setTimeout(function(){card.style.transition="";},500);\n'
'        });\n'
'      })(cards[i]);\n'
'    }\n'
'  }\n'
'})();\n'
'</script>\n'
)

# 日历"今天"动态高亮（不再静态写死，JS自动匹配当日日期）
CAL_TODAY_JS = (
'<script>(function(){'
'var d=new Date(),day=d.getDate();'
'var cells=document.querySelectorAll(".cal-cell .cd");'
'for(var i=0;i<cells.length;i++){if(parseInt(cells[i].textContent.trim())===day){'
'cells[i].closest(".cal-cell").classList.add("today");break;}}'
'})();</script>\n'
)

out = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>GamePulse · 游戏日报主站</title><link rel="icon" type="image/png" href="favicon.png"><link rel="stylesheet" href="style.css">{wc_style}</head><body>
<header><div class="hbar"><div class="logo">&#127918; GamePulse<em>·雷达</em></div><span class="chip">&#127968; 游戏日报主站</span><span class="chip fresh">{today}</span><nav class="nav-btns"><a class="nav-btn" href="#glance">&#128293; 热词</a><a class="nav-btn" href="#brief">&#128240; 日报</a><a class="nav-btn" href="#cal">&#128197; 日历·前瞻</a><a class="nav-btn" href="daily.html" target="_blank">日报页 &#8599;</a><a class="nav-btn" href="calendar.html" target="_blank">日历页 &#8599;</a><a class="nav-btn" href="wordcloud.html" target="_blank">词云页 &#8599;</a><a class="nav-btn" href="history.html" target="_blank">历史回顾 &#8599;</a></nav></div></header>
{masthead}
<div class="wrap">
<section id="glance"><div class="sec-title"><span class="bar" style="background:var(--green)"></span>今日讨论热词 <small>{today} · 仅当日采集·真实来源溯源</small><a class="fold-link" style="margin-left:auto" href="wordcloud.html" target="_blank">进入词云详情 &#8599;</a></div>
<div class="glance-cloud">{wc_cloud}</div></section>

<div class="zone-head"><h2>&#128240; 今日日报</h2><span class="fold-sub">完整正文 · 8 板块</span><a class="fold-link" href="daily.html" target="_blank">独立页 &#8599;</a></div>
{daily_html}

<div class="zone-head"><h2>&#128197; 版本日历 · 前瞻哨</h2><span class="fold-sub">未来 5 周节点 · 前瞻哨点击展开</span><a class="fold-link" href="calendar.html" target="_blank">独立页 &#8599;</a></div>
{csec.get('cal','')}
{csec.get('forward','')}

{source_html}

<footer>GamePulse · 游戏日报主站 · {today} · 由子页拼装生成（rebuild_main.py），子页改动后重跑一次即可同步</footer></div></div>
{GSAP_SCRIPT}{CAL_TODAY_JS}</body></html>'''

OUT_DIR = os.environ.get("RB_OUT_DIR", BASE)

out_path = os.path.join(OUT_DIR, 'index.html')
_tmp = out_path + "." + datetime.datetime.now().strftime("%H%M%S")
io.open(_tmp, 'w', encoding='utf-8').write(out)
try:
    os.remove(out_path)
except OSError:
    pass
os.rename(_tmp, out_path)
print('rebuilt bytes=', len(out.encode('utf-8')), '| index.html synced')
