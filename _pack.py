#!/usr/bin/env python3
"""GamePulse 雷达站完整项目打包脚本"""
import os
import zipfile
import glob
import fnmatch
import json
from datetime import date

BASE = r"C:\Users\shudizhao\WorkBuddy\Claw\雷达站"
OUT = os.path.join(BASE, "gamepulse_handoff.zip")
TODAY = date.today().strftime("%Y%m%d")

# 白名单：精确文件
EXACT_FILES = [
    # HTML 页面
    "daily.html",
    "calendar.html",
    "wordcloud.html",
    "index.html",
    "history.html",
    # 静态资源
    "style.css",
    "favicon.png",
    # 核心数据
    "events.json",
    "wordcloud_terms.json",
    "sources.toml",
    "sources_status.json",
    # 设计/治理
    ".impeccable.md",
    "GAMEPULSE_HANDOFF.md",
]

# 白名单：glob 模式
GLOB_PATTERNS = [
    "*.py",                    # 所有 Python 脚本
]

# 白名单：目录
DIR_PATTERNS = [
    (".workbuddy/masthead_history.json", "masthead_history.json"),
    (f"collectors/meme_{TODAY}.json", f"collectors/meme_{TODAY}.json"),
    (f"collectors/meme_{TODAY}.md", f"collectors/meme_{TODAY}.md"),
    (f"collectors/public_hotlist_{TODAY}.json", f"collectors/public_hotlist_{TODAY}.json"),
    (f"collectors/tgmeng_daily_{TODAY}.json", f"collectors/tgmeng_daily_{TODAY}.json"),
    ("collectors/tgmeng_archive.json", None),
    ("collectors/history_data.json", None),
    ("collectors/wordcloud_terms_20260805.json", None),
    ("collectors/wordcloud_terms_20260804.json", None),
    ("collectors/wordcloud_terms_20260803.json", None),
    ("collectors/wordcloud_terms_20260802.json", None),
    ("collectors/wordcloud_terms_20260731.json", None),
    ("collectors/wordcloud_terms_20260730.json", None),
]

# 从 Desktop 复制的外部文件
EXTERNAL_FILES = [
    (r"C:\Users\shudizhao\Desktop\游戏日报主站-自洽提示词.md", "治理/游戏日报主站-自洽提示词.md"),
]

# 排除模式
EXCLUDE = [
    "*.pyc",
    "__pycache__",
    ".git",
    "_publish*",
    "backup_*",
    "*.zip",
    "daily.html.*",
    "calendar.html.*",
    "wordcloud.html.*",
    "wordcloud_terms_tmp*",
    "wordcloud_tmp*",
    "wordcloud_new*",
    "index.html.*",
    "daily_tmp*",
    "daily_template*",
    "index_test*",
    "commit_msg*",
    "inbox/",
    "_tgmeng_card_preview*",
    "*.tmp",
    "meme_202607*.json",
    "meme_202607*.md",
    "public_hotlist_202607*.json",
]

def should_exclude(name):
    for pat in EXCLUDE:
        if fnmatch.fnmatch(name, pat):
            return True
    return False

def main():
    os.chdir(BASE)
    
    with zipfile.ZipFile(OUT, 'w', zipfile.ZIP_DEFLATED) as zf:
        added = set()
        
        # 1. 精确文件
        for f in EXACT_FILES:
            if os.path.isfile(f) and not should_exclude(f):
                zf.write(f)
                added.add(f)
                print(f"  + {f}")
            else:
                print(f"  ⚠ 缺失: {f}")
        
        # 2. Glob 匹配
        for pat in GLOB_PATTERNS:
            for f in glob.glob(pat, root_dir=BASE):
                if f not in added and not should_exclude(f):
                    zf.write(f)
                    added.add(f)
                    print(f"  + {f}")
        
        # 3. 目录特定文件
        for src, dst in DIR_PATTERNS:
            full = os.path.join(BASE, src)
            if os.path.isfile(full):
                arcname = dst if dst else src
                zf.write(full, arcname)
                added.add(arcname)
                print(f"  + {src} -> {arcname}")
            else:
                print(f"  ⚠ 缺失: {src}")
        
        # 4. 外部文件
        for src, dst in EXTERNAL_FILES:
            if os.path.isfile(src):
                zf.write(src, dst)
                print(f"  + 外部: {dst}")
            else:
                print(f"  ⚠ 缺失外部: {src}")
        
        # 5. 添加 collectors 目录下最新的 meme 和 public_hotlist 文件（近 7 天）
        for pattern in ["collectors/meme_202608*.json", "collectors/meme_202608*.md",
                        "collectors/public_hotlist_202608*.json",
                        "collectors/tgmeng_daily_202608*.json"]:
            for f in sorted(glob.glob(pattern, root_dir=BASE)):
                if f not in added and not should_exclude(os.path.basename(f)):
                    zf.write(f)
                    added.add(f)
                    print(f"  + {f}")
        
        # 6. 项目记忆文件
        memory_dir = os.path.join(BASE, ".workbuddy", "memory")
        for fname in ["MEMORY.md"]:
            fp = os.path.join(memory_dir, fname)
            if os.path.isfile(fp):
                arc = os.path.join(".workbuddy", "memory", fname)
                zf.write(fp, arc)
                print(f"  + memory: {arc}")
        
        # 7. 项目自洽日志（最近 7 天）
        for f in sorted(glob.glob("自洽日志_202608*.md", root_dir=BASE), reverse=True)[:7]:
            if f not in added:
                zf.write(f)
                added.add(f)
                print(f"  + {f}")
    
    size_mb = os.path.getsize(OUT) / (1024 * 1024)
    print(f"\n打包完成: {OUT}")
    print(f"文件数: {len(added)}")
    print(f"大小: {size_mb:.1f} MB")

if __name__ == "__main__":
    main()
