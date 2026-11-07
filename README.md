# agent-douyin-downloader

**抖音视频下载器 · 给 AI Agent 用** — Download public Douyin videos through a real headful Chromium. No login, no API signature, no yt-dlp.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![Dependencies](https://img.shields.io/badge/deps-websocket--client-informational.svg)](requirements.txt)

---

## 为什么有这个东西

如果你试过用现有工具下抖音视频，你大概撞过这三堵墙：

| 尝试 | 结果 |
|---|---|
| `yt-dlp <抖音链接>` | `ERROR: Fresh cookies (not necessarily logged in) are needed` |
| 灌入真实浏览器的 27 条 cookie 再试 | **一模一样的报错** |
| 走 `aweme/v1/web/aweme/detail/` API | `403 Blocked by ArgusSecurityPlugin Uifid Not Found` |

**第一个报错会骗人。** 它说"需要新鲜 cookie"，于是你去导 cookie、加 UA、加 Referer、换完整 URL —— 全都没用。真相是：`DouyinIE` 的 API 端点已经跟不上抖音改版，cookie 不是问题。

本项目走的是第三条路，也是唯一还通的路：**用真实浏览器打开视频页，把 `<video>` 元素的 `currentSrc` 读出来。**

抖音的单视频页用 MSE 播放，DOM 里确实只有 `blob:` URL。但 `<video>` 元素本身携带 `currentSrc` —— 一个普通的 CDN 地址。读到它，带上 `Referer` 就能下载。**不需要登录。**

---

## 快速开始

```bash
# 1. 依赖
pip install -r requirements.txt          # websocket-client
sudo apt install -y chromium ffmpeg      # 浏览器 + ffprobe（校验用）

# 2. 下载
python3 dy_dl.py 'https://v.douyin.com/xxxxx/'
```

输出：

```
  [browser] Chrome/131.0.6778.85
  [warmup] loading douyin.com to install visitor cookies
[1/1] https://v.douyin.com/xxxxx/
  [page] 今日立冬旭日临新序冬开万象宁 暖阳当立...
  [page] 576x1024, 3 candidate source(s)
  [download] source 1/3 ok, 821 KB
  [verify] h264 576x1024 7.67s
  [saved] /home/user/douyin/今日立冬旭日临新序冬开万象宁 暖阳当立_576x1024.mp4

1/1 succeeded
```

---

## 工作原理

```
分享链接 (v.douyin.com/xxx)
        │
        ▼
① 预热：在同一个浏览器上下文里先开 https://www.douyin.com/
        │   ← 这一步才下发 ttwid / odin_tt / __ac_nonce / s_v_web_id / __ac_signature
        ▼
② 解析短链 → www.douyin.com/video/<id>
        │
        ▼
③ 打开视频页，轮询 <video> 元素（最长 30 秒）
        │
        ▼
④ 读 currentSrc + 所有 <source> → 2~3 条 CDN 直链
        │
        ▼
⑤ 按 CDN 域名筛选（zjcdn.com / douyinvod.com 优先），多源依次重试下载
        │
        ▼
⑥ ffprobe 校验：有时长、有 video 流才算成功，否则删掉重试
```

### 第 ① 步的顺序不能反

这是整个项目里最容易踩的坑。**先视频页后首页 = 页面空着，零媒体请求。**

抖音把访客身份发在首页响应里。直接打开视频页，浏览器没有 `ttwid`，播放器永远停在「视频数据加载中」，`<video>` 的 `currentSrc` 是空字符串。脚本会一直等到超时，然后报"没抓到视频源"——看起来像内容被删，其实是顺序错了。

`warm_up()` 就是干这个的：先开首页，轮询到 `document.cookie` 里出现 `ttwid` 再继续。

---

## 两种"视频"不要搞混

视频页 DOM 里可能同时存在多个 `.mp4`，**其中一个 1080p 的是 PC 客户端宣传广告**。

| 特征 | 真作品 | 广告（下错文件的经典坑） |
|---|---|---|
| 域名 | `*.zjcdn.com` / `*.douyinvod.com` | 其他域名 |
| 来源 | `<video>` 元素的 `currentSrc` / `<source>` | 页面里的推广 `<a href>` 或隐藏标签 |
| 时长 | 与标题描述一致 | 固定十几秒，内容与标题无关 |

本项目只从 `<video>` 元素取值，并且优先匹配已知 CDN 域名，就是为了避开这个坑。**下完一定看 ffprobe 的时长对不对得上封面。**

---

## 参数

```
python3 dy_dl.py [URL...] [-o DIR] [--json] [--list FILE] [--wait N] [-q]
```

| 参数 | 说明 |
|---|---|
| `URL...` | 一个或多个链接，短链 `v.douyin.com/xxx` 或完整 `douyin.com/video/<id>` 都行 |
| `-o, --outdir` | 输出目录，默认 `~/douyin`（环境变量 `DY_OUTDIR`） |
| `--profile` | 浏览器 profile 目录，默认 `~/.douyin-downloader/browser`（`DY_PROFILE`） |
| `--port` | CDP 端口，默认 `9222`（`DY_CDP_PORT`） |
| `--display` | X display，默认自动探测（`DISPLAY`） |
| `--wait N` | 等播放器出源的秒数，默认 30 |
| `--list FILE` | 从文件读链接，一行一个，`#` 开头为注释 |
| `--json` | 输出结构化 JSON 报告（给 agent 解析用） |
| `-q, --quiet` | 静默，只输出结果 |

### JSON 输出（agent 友好）

```json
{
 "total": 1, "ok": 1, "failed": 0,
 "results": [{
   "input": "https://v.douyin.com/xxxxx/",
   "page": "https://www.douyin.com/video/7705050159461967976",
   "aweme_id": "7705050159461967976",
   "title": "今日立冬旭日临新序...",
   "width": 576, "height": 1024, "duration": 7.67,
   "sources": 3, "codec": "h264",
   "ok": true,
   "path": "/home/user/douyin/今日立冬..._576x1024.mp4",
   "size": 840802
 }]
}
```

---

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 全部成功 |
| 1 | 用法错误 / 浏览器起不来 |
| 2 | 没抓到视频源（已删 / 私密 / 非视频页 / 预热失败） |
| 3 | 所有 CDN 源都下载失败 |
| 4 | ffprobe 拒收（坏文件） |
| 5 | 批量里部分失败 |

---

## 什么情况需要登录

**本项目不处理登录，公开视频也不需要登录。**

| 场景 | 要登录吗 |
|---|---|
| 单个公开视频 | ❌ |
| 博主主页前若干条 | ❌ |
| **主页往下翻 / 批量抓全部作品** | ✅ 抖音会拦，列表返回空 |
| 私密 / 仅粉丝可见 | ✅ |
| 创作者中心数据 | ✅ |

需要登录态时，在同一个浏览器 profile 里手动扫码一次即可 —— profile 是持久化的，登录态会留下来，重启不丢。

---

## 已知限制

1. **必须有 X display**。headless 下抖音播放器不渲染，`<video>` 没有 `currentSrc`。无头服务器用 `xvfb-run -a python3 dy_dl.py ...`。
2. **画质是网页播放器默认码率**（常见 856 kbps / 576×1024）。要更高清得换移动端 UA 或走 `ratio=1080p`，本项目未实现。
3. **串行使用**。多个进程抢同一个浏览器 profile 会互相干扰。
4. **不含批量主页抓取**。那是另一套逻辑（翻列表 + 登录态），不在本项目范围。
5. **依赖抖音前端结构**。哪天它把 `currentSrc` 也藏了，这招就失效 —— 但到目前为止它是唯一还通的路。

---

## 作为 Agent 技能使用

仓库根的 `SKILL.md` 是标准技能定义，可直接放进 Hermes / Claude Code 等 agent 的 skills 目录：

```
skills/media/agent-douyin-downloader/SKILL.md
```

技能里写清了触发条件、执行步骤、验证门槛和**不要做的事**（比如别去试 yt-dlp、别碰 API 签名）。

---

## 相关文档

- [`docs/why-not-yt-dlp.md`](docs/why-not-yt-dlp.md) — 实测归因：为什么 cookie 不是问题
- [`docs/technical-notes.md`](docs/technical-notes.md) — CDP 细节、cookie 下发顺序、CDN 陷阱

---

## License

MIT © 2026

本项目仅用于下载**你有权访问的公开内容**。请遵守抖音的服务条款和当地法律，不要用于批量抓取、二次分发他人作品或任何侵犯版权与隐私的用途。
