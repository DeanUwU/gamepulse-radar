# GamePulse 部署指南

## GitHub Pages（当前方案 · 公网可访问）

**仓库地址**：`https://github.com/dd2095965283-lgtm/gamepulse-radar`

**部署 URL**：`https://dd2095965283-lgtm.github.io/gamepulse-radar/`

**部署流程**：

### 1. 准备工作
```bash
cd "C:/Users/shudizhao/WorkBuddy/Claw/雷达站"
```
确保 `.gitignore` 排除不必要文件（*.py, collectors/, backup_*/, _publish/ 等）

### 2. 构建站点
运行 `daily_refresh.py` 或单独运行 `rebuild_main.py` 确保 `index.html` 最新。

### 3. 同步到 _publish/
```bash
cp index.html style.css favicon.png daily.html calendar.html wordcloud.html _publish/
```

### 4. 提交推送
```bash
git add _publish/ && git commit -m "刷新 $(date +%Y-%m-%d)" && git push
```

### 5. 验证
- 等待 GitHub Pages 自动构建（通常 1-2 分钟）
- 访问 `https://dd2095965283-lgtm.github.io/gamepulse-radar/` 确认更新

### 自动化部署
`daily_refresh.py` 的最后一步可添加 git add/commit/push，实现每次刷新后自动部署。

---

## CloudStudio（备用方案 · 内网）

**注意**：CloudStudio 返回的 URL 以 `.woa.com` 结尾，是腾讯内网域名，外网（手机非公司网络）无法访问。仅作内网预览用。

### 部署命令
```bash
# 先启动本地服务器（端口 3000）
cd "C:/Users/shudizhao/WorkBuddy/Claw/雷达站"
python -m http.server 3000

# 或使用 workbuddy_cloudstudio_deploy 工具部署 _publish/ 目录
```

### 故障排查
- 400 错误：旧沙箱可能卡住，新建沙箱重新部署
- 访问超时：确认在腾讯内网环境

---

## Netlify（计划中，用于自定义域名）

**用途**：获取不暴露 GitHub 用户名的干净 URL

**方式**：
1. Netlify CLI（`netlify deploy --prod`）
2. Netlify 拖拽部署（`app.netlify.com/drop`）
3. 连接 GitHub 仓库自动部署

**注意**：公司网络下 npm install netlify-cli 极慢，可考虑在个人网络环境安装。

---

## 文件清单（部署必需）

每次部署必须包含：
```
index.html          ← 主站唯一入口
daily.html          ← 日报页
calendar.html        ← 日历页
wordcloud.html       ← 词云页
style.css            ← 全站样式
favicon.png          ← 网站图标
```
