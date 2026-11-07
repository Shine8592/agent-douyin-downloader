# 为什么不用 yt-dlp：一次实测归因

这份文档记录的是一个具体的排查过程。它的价值不在结论，而在**那个会骗人的报错**。

---

## 报错原文

```
$ yt-dlp 'https://v.douyin.com/xxxxx/'
ERROR: [Douyin] 7705050159461967976: Fresh cookies (not necessarily logged in)
are needed; please report this issue on ...
```

关键词：**not necessarily logged in**。

它说的是"需要新鲜 cookie，但不一定要登录"。听起来是个 cookie 问题，于是所有人（包括我）都去搞 cookie。

---

## 实验记录

| # | 做法 | 结果 |
|---|---|---|
| 1 | 直接下 | `Fresh cookies ... are needed` |
| 2 | `curl https://www.douyin.com/` 存 cookie 后下 | 首页响应**没有下发任何 cookie**，仍然失败 |
| 3 | 打 `ttwid.bytedance.com` 注册接口拿 `ttwid` | 拿到 `ttwid`，但域是 `.bytedance.com`，不是 `.douyin.com`，仍然失败 |
| 4 | **从真实 Chromium 导出 27 条 cookie**（`ttwid` / `__ac_nonce` / `__ac_signature` / `s_v_web_id` / `odin_tt` / `passport_csrf_token` …） | **完全相同的报错** |
| 5 | 在 4 的基础上加桌面 UA | 同上 |
| 6 | 在 5 的基础上加 `Referer: https://www.douyin.com/` | 同上 |
| 7 | 换完整 URL（跳过短链跳转）`douyin.com/video/<id>` | 同上 |

**七次尝试，六种变量，报错一字不变。**

---

## 归因

这不是"cookie 不够新鲜"。判断依据：

1. **如果真缺 cookie，加 cookie 就该有变化** —— 报错、状态码、任何东西变了都算。完全没有。
2. **报错文案是硬编码的**，出现在提取器认为响应格式不符合预期的地方，而不是真的在检查 cookie 内容。
3. **同一时间，用真实浏览器打开同一个页面** —— 视频正常播放，`<video>.currentSrc` 直接就是 CDN 直链。

**结论：`DouyinIE` 依赖的 API 端点已经跟不上抖音改版。报错文案误导了方向。**

这是提取器维护的经典困境：站点改版 → 端点返回意料之外的响应 → 代码走到一个"需要 cookie"的兜底分支 → 用户看到的报错和真实原因无关。

---

## 那 API 这条路呢

也堵死了，而且是另一堵墙：

```
GET https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=...
403 Blocked by ArgusSecurityPlugin Uifid Not Found
```

这条路上依次有四道关卡：

| 关卡 | 状态 |
|---|---|
| 出网可达 | ✅ 通 |
| cookie | ✅ 能从真实浏览器拿到全套 |
| 请求签名 `a_bogus` | ✅ 可以在纯 Python 里算出来 |
| **Argus / `uifid` 校验** | ❌ **算对签名也沒用** |

第四关卡的是**浏览器环境指纹**，不是密码学签名。算得出正确的 `a_bogus`，请求仍然 403。

**所以签名这条路不值得走**：它证明"你会算"，但平台要的是"你是那个浏览器"。

---

## 那为什么浏览器路线能通

因为不需要"伪造身份"——**它就是一个真实浏览器**。

```
真实 Chromium 打开首页   → 平台下发访客 cookie（正常访问，无异常）
同一个上下文打开视频页   → 播放器正常初始化，MSE 开始工作
读 <video>.currentSrc    → 拿到 CDN 直链（就是播放器自己在用的那个地址）
带 Referer 下载          → CDN 只校验 Referer/UA，不校验身份
```

**没有任何一步是"绕过"**。拿的就是播放器本来要用的地址。

---

## 给后来者的建议

如果你在为某个平台写提取器，撞到这类报错，按这个顺序判断：

1. **先在真实浏览器里打开同一个页面，确认内容本身可访问** —— 排除内容已删/私密/地区限制。
2. **看页面里有没有 `<video>` / `<img>` 直接携带的源地址** —— 有的话，一切签名和 cookie 都是白费力气。
3. **只有在 2 不成立时**，才去啃 API、签名、风控。
4. **如果加 cookie / 换 UA / 换 Referer 都不能让报错发生任何变化，停止调 cookie** —— 问题不在那一层。

---

## 附：验证成功的样本

```
页面:  https://www.douyin.com/video/<id>
来源:  <video>.currentSrc → https://v5-dy-ov-*.zjcdn.com/.../video/tos/cn/...
下载:  821 KB, HTTP 200, 1.3 秒
校验:  h264 576x1024 30fps, 7.67 秒, aac 音轨
```
