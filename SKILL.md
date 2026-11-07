---
name: agent-douyin-downloader
description: "Use when downloading a Douyin video without login. Real-browser path, not yt-dlp."
version: 1.0.0
author: Shine
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [douyin, tiktok, video, download, cdp, chromium, china, media]
    category: media
    related_skills: [media-download, blocked-page-recovery]
---

# 抖音视频下载（真实浏览器路径）

## 何时用

- 用户给出抖音分享链接（`v.douyin.com/xxx`）或视频页链接，要求下载
- 用户要求"能下载抖音视频"
- 之前的下载尝试报 `Fresh cookies (not necessarily logged in) are needed` 或 `403 ArgusSecurityPlugin`
- 用户抱怨"下下来的是一段广告/和别人一样的视频"

## 不要做的事（先看这一节，省时间）

| ❌ 不要 | 为什么 |
|---|---|
| 试 yt-dlp 的抖音提取器 | `DouyinIE` 端点已废。报错伪装成"缺 cookie"，灌真 cookie 也无效 |
| 走 `aweme/v1/web/aweme/detail/` | Argus / uifid 校验，403，绕不过 |
| 算 `a_bogus` 签名 | 签名不是身份。算对了照样 403 |
| 从 DOM 里随便抓一个 `.mp4` | 有个 1080p 的是 PC 客户端**宣传广告**，不是作品 |
| 用 headless 浏览器 | 播放器不渲染，`<video>` 没有 `currentSrc` |

## 执行步骤

### 1. 确认环境

```bash
python3 -c "import websocket" || pip install websocket-client
command -v chromium google-chrome chromium-browser   # 任一个
echo $DISPLAY                                          # 必须有值
```

**没有 X display 就先起一个**：`Xvfb :1 -screen 0 1920x1080x24 &` 然后 `export DISPLAY=:1`，
或者整条命令套 `xvfb-run -a`。

### 2. 跑脚本

```bash
python3 dy_dl.py '<抖音链接>' -o <输出目录> --json
```

成功时 JSON 里 `ok: true` 且有 `path`。**用 `path` 的值做事，不要自己拼文件名。**

### 3. 验证（不可跳过）

```bash
ffprobe -v error -show_entries format=duration:stream=codec_name,width,height -of json <path>
```

**必须同时满足**：
- 存在 `video` 流
- `duration > 0` 且与封面/标题描述**大致相符**（差太远 → 下错文件了，很可能是广告）
- 文件大小 > 100 KB

### 4. 交付

先把 ffprobe 的关键行摆出来，再给文件。**不要只说"下载好了"。**

## 关键机制：预热顺序

```
① 先开 https://www.douyin.com/        ← 这一步才下发 ttwid / odin_tt / __ac_nonce
② 等到 document.cookie 出现 ttwid
③ 再开 https://www.douyin.com/video/<id>
④ 读 <video>.currentSrc + 所有 <source>
```

**顺序反了就失败**：直接进视频页 → 没有访客 cookie → 播放器卡在「视频数据加载中」
→ `<video>.currentSrc` 是空串 → 看起来像内容被删，其实是预热没做。

脚本里的 `warm_up()` 已经处理了。自己手写 CDP 流程时别忘。

## 画质

默认拿网页播放器的码率（常见 856 kbps / 576×1024）。这是抖音给 PC 端写死的
（地址里 `br=856&bt=856`），不是脚本的限制。

要更高清需要换移动端 UA 或走 `ratio=1080p` 的 play 接口 —— 本项目**没实现**。
用户如果要求高清，明说这是未实现的部分，不要假装能拿到。

## 需要登录的场景

| 场景 | 登录 |
|---|---|
| 单个公开视频 / 主页前几条 | 不需要 |
| **主页往下翻 / 批量全量** | 需要 |
| 私密 / 仅粉丝可见 | 需要 |
| 创作者中心数据 | 需要 |

需要时：在**同一个浏览器 profile** 里手动扫码一次，登录态留在 profile 目录，长期有效。
脚本用的是持久 profile，不随进程退出清空。

## 失败排查表

| 症状 | 原因 | 处理 |
|---|---|---|
| `no_video_source` | 预热没做 / 内容已删 / 私密 / 非视频页 | 先单独访问首页确认能拿到 ttwid，再重试；仍失败则内容本身不可公开访问 |
| `all_sources_failed` | CDN 拒绝 / 链接过期 | 重新跑一次（CDN 地址有签名时效），或换 `--wait 45` |
| `ffprobe_rejected` | 下到的是错误页 HTML | 检查是不是拿到广告片；确认 `GOOD_CDN` 白名单命中 |
| 浏览器起不来 | 没有 Chromium / 没有 DISPLAY | 见「执行步骤 1」 |
| 超时 | 网络慢或页面要更久 | `--wait 60` |
| 下到的视频和标题不符 | 抓到广告了 | 只从 `<video>` 取源，别从 `<a href>` 或隐藏标签取 |

## 依赖

```
websocket-client   # CDP 通信
chromium / chrome  # 必须有，且必须 headful
ffmpeg (ffprobe)   # 校验
```

## 说明

本项目只用于下载**用户有权访问的公开内容**。批量抓取、二次分发他人作品、
绕过私密/付费内容都不在支持范围。
