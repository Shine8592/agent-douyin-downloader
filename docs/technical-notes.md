# 技术细节

给要改代码或者自己写一套的人看的。README 讲怎么用，这里讲为什么这么写。

---

## 1. 为什么必须是 headful

```
headless=new  →  <video>.currentSrc === ""   ❌
headful (:1)  →  <video>.currentSrc === "https://v5-dy-ov-*.zjcdn.com/..."  ✅
```

抖音的播放器初始化依赖真实的图形上下文。headless 下页面能加载、DOM 能拿到，但播放器不启动 MSE，
`<video>` 元素存在却没有源。

**无头服务器的解法**：

```bash
Xvfb :1 -screen 0 1920x1080x24 &
export DISPLAY=:1
python3 dy_dl.py '<url>'
```

或者一行：`xvfb-run -a python3 dy_dl.py '<url>'`

Xvfb 起来后行为和真桌面一样——这是本方案在纯 CLI 服务器上能跑的关键。

---

## 2. 为什么用 CDP 而不是 `--dump-dom`

`--dump-dom` 是一次性的：进程启动 → 渲染 → 输出 DOM → 退出。

问题在于**时机**。抖音的 `<video>` 元素是异步填充的，`--virtual-time-budget` 是猜测，
猜早了拿到空属性，猜晚了浪费。而且每次都要重新启动浏览器，冷启动 3~5 秒。

CDP 常驻会话的好处：

- 浏览器只启动一次，后续每个视频只开新标签
- 可以**轮询** `currentSrc` 直到有值，而不是猜一个时间点
- 首页预热和视频页加载在**同一个浏览器上下文**里，cookie 自然共享
- 一个会话能连着处理几十个链接

代价是要处理 WebSocket 连接和 target 管理，代码长一点。

---

## 3. 预热：具体在等什么

```python
page.open("https://www.douyin.com/")
while time.time() < deadline:
    if page.js("document.cookie.indexOf('ttwid') >= 0"):
        return True
```

等的是 `ttwid` 这一个 cookie。它出现就说明访客身份已经建立。

抖音在首页下发的相关 cookie：

| 名字 | 作用 |
|---|---|
| `ttwid` | 访客标识，**视频页进播放器前会检查它** |
| `odin_tt` | 设备态 |
| `__ac_nonce` | 风控 nonce |
| `__ac_signature` | 由 nonce 派生 |
| `s_v_web_id` | 浏览器指纹 |
| `passport_csrf_token` | CSRF（未登录也有） |

**只要缺 `ttwid`，视频页就停在「视频数据加载中」**，且不发出任何媒体请求。

不用等全部 cookie。`ttwid` 是最关键的信号，它到了就可以进视频页。

---

## 4. `currentSrc` vs `<source>` vs `blob:`

抖音视频页的三种源，区别很重要：

| 来源 | 长什么样 | 能用吗 |
|---|---|---|
| `<video>.src` | 常常是空或者是 `blob:` | ❌ |
| `<video>.currentSrc` | `https://v5-dy-*.zjcdn.com/.../video/tos/cn/...` | ✅ **这是真地址** |
| `<source>.src` | 2~3 条不同 CDN 节点，同样的 `video_id` | ✅ 备用 |

`currentSrc` 是浏览器**实际正在用**的地址，所以最可靠。`<source>` 里的备用节点用于
主节点超时/失败时重试——它们的 `video_id` 相同，内容一样。

脚本的处理：把 `currentSrc` 插到列表头部，再追加所有 `<source>`，去重后按 CDN 白名单排序。

---

## 5. CDN 白名单为什么必要

视频页 DOM 里可能同时出现多个 `.mp4`。其中一个是 **PC 客户端宣传片**：

- 真实存在，1080p H.264，能正常播放
- 内容与页面标题无关
- 是广告

如果按"页面上第一个 `.mp4`"来抓，大概率拿到它。**用户会觉得"下下来的不是你发的那个"。**

白名单：

```python
GOOD_CDN = ("zjcdn.com", "douyinvod.com", "douyinpic.com",
            "bytecdn.cn", "ixigua.com", "pstatp.com")
```

命中白名单的排前面，其余作为兜底但排在后面。加上"只从 `<video>` 元素取值"这条规则，
基本能杜绝拿到广告片。

---

## 6. ffprobe 校验为什么不能省

三个真实翻车场景：

| 情况 | ffprobe 会看到 |
|---|---|
| 下到错误页 HTML（CDN 拒绝时返回 200 + HTML） | 没有 stream，大小几百字节 |
| 下到广告片 | 有时长，但和标题对不上 |
| 下载中断 | `moov atom not found`，时长读不出来 |

所以验证要两条：

```python
vs = [s for s in streams if s["codec_type"] == "video"]   # 必须有视频流
dur = float(format["duration"])                            # 必须 > 0
```

**第二条（时长对不上）机器判断不了**，得靠人——所以交付前把 ffprobe 输出摆出来，
让用户自己扫一眼时长对不对。

---

## 7. 画质是怎么被决定的

拿到 856 kbps / 576×1024，不是脚本选的，是 URL 里写死的：

```
...&br=856&bt=856&qs=12&...
```

`br`/`bt` 是码率，`qs` 是清晰度档位。这些参数由服务端根据**请求上下文的播放器尺寸和 UA** 决定。

PC 网页播放器在小窗口下请求低码率。想要更高清，现实的路子：

| 方法 | 状态 |
|---|---|
| 加大浏览器窗口 / 提高 `deviceScaleFactor` | 有时有效，不稳定 |
| 换移动端 UA | 可能拿到不同档位，未系统验证 |
| 走 `play` 接口带 `ratio=1080p` | 需要接口还能用，未验证 |
| 从 API 拿 `bit_rate` 列表逐个试 | 需要 API 通，而 API 被 Argus 挡着 |

本项目**没有实现高清**。README 和 SKILL.md 里都明说了，不要假装能拿到。

---

## 8. 错误分类

代码里每个失败都带 `reason` 字段，便于 agent 判断下一步：

| reason | 含义 | 下一步 |
|---|---|---|
| `no_video_source` | 页面没给出源 | 检查预热；确认内容是否公开 |
| `all_sources_failed` | 源拿到了但都下不动 | 重跑（CDN 签名有时效） |
| `ffprobe_rejected` | 下到的不是有效视频 | 检查是否抓到广告/错误页 |
| `exception: ...` | 未预期异常 | 看堆栈 |

有了分类，agent 才能决定"重试"还是"告诉用户这个下不了"，而不是笼统报错。

---

## 9. 并发

**不要并发。** 原因：

- 所有实例共享同一个浏览器 profile
- CDP 的 target 管理不是为多进程设计的
- 同时开多个视频页会互相抢带宽，反而更慢

要批量就串行遍历，一个页面处理完关掉再开下一个。脚本的批量模式就是这么做的。

---

## 10. 已知脆弱点

| 点 | 失效会怎样 | 怎么发现 |
|---|---|---|
| 抖音改播放器，`currentSrc` 不再暴露 | 全部 `no_video_source` | 手动开浏览器看 `<video>` 属性 |
| CDN 开始校验 cookie 而不只是 Referer | `all_sources_failed` | 浏览器里能播但 curl 下不动 |
| 首页不再下发 `ttwid` | 预热失败 | `document.cookie` 里搜不到 |
| 页面结构大改 | 同上 | — |

**这些都是"抖音决定改"的事，不是代码 bug。** 出现时先手动复现确认，再决定要不要修。
