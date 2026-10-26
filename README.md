# 🌙 MoonCheck · 微信小程序版

**晚安成电 · 晚间定位打卡自动化工具（MoonCheck for WeChat Mini App）**

> 你在电脑上登录微信、打开小程序，脚本自动完成剩下的「抓取授权 → 提交打卡 → 结果确认 → 手机通知」。

> ⚠️ **本工具采用「虚拟定位」打卡**：脚本上报的是 `config.json` 里指定的坐标（可设为宿舍或任意位置），而非设备真实定位。

---

## 它能做什么

这是一个配合微信 PC 客户端使用的一键打卡工具：

1. 自动把系统代理切到 mitmproxy；
2. 你在微信里打开「成电智慧学工」小程序 → 进入「晚安成电」打卡页；
3. 脚本从 HTTPS 明文里自动截获 30 分钟有效的一次性 `ticket`；
4. 脚本带 `X-Checkin-Ticket` 提交打卡，并复查结果；
5. 结束自动还原系统代理，通过 Server酱 把结果推到手机。

**你只需要会「登录微信 + 打开小程序」，其余全自动。**

---

## 快速开始

```powershell
# 0) 安装 Python 3.10+ 与依赖
python -m pip install mitmproxy

# 1) 信任 mitmproxy 的 CA 证书（每台电脑首次做一次）
#    运行一次 mitmdump，打开 http://mitm.it 下载证书并安装到「受信任的根证书颁发机构」
#    安装后重启微信（微信会读取系统信任的证书）

# 2) 准备配置
copy config.example.json config.json
notepad config.json          # 填 location（宿舍坐标），可选 notify_sendkey / label

# 3) 先干跑看流程
python checkin.py --dry-run

# 4) 正式打卡（按提示在微信里打开小程序即可）
python checkin.py
```

配好后，**每天双击 `checkin.cmd`** 即可一键打卡，窗口会显示成功/失败与错误提示。

> 打卡窗口为 **21:00–23:30**，其它时间返回 `code=4006`（鉴权正常，只是时间不对）。一天只有一次真实打卡机会。

---

## 命令行选项

| 命令 | 作用 |
| --- | --- |
| `checkin.cmd`（双击） | 一键打卡，弹窗显示成功/失败 |
| `python checkin.py` | 完整流程：切代理 → 抓 ticket → 提交 → 还原 → 通知 |
| `python checkin.py --no-submit` | 只抓 ticket 不提交（验证抓包链路） |
| `python checkin.py --dry-run` | 只打印步骤，不改代理、不启动 mitmproxy |

---

## 工作原理

```
wx.login code ──▶ POST /platform-app/api/auth/check-bind      ──▶ sessionId（学号 sn）
wx.login code ──▶ POST /platform-app/api/auth/checkin-ticket  ──▶ ticket（30 分钟、一次性）
打卡提交        ──▶ POST /daily-checkin/api/student-checkin-record/checkin
                     额外头 X-Checkin-Ticket: <ticket>，会话改用小程序 SESSION
```

关键点：`ticket` **只能由微信客户端生成**（`wx.login` 拿 code 换 ticket），脚本无法凭空构造。
所以脚本不注入微信进程，只靠 mitmproxy 监听本地 HTTPS 明文，把小程序发出的 `check-bind` /
`checkin-ticket` 响应里的 `sessionId` / `ticket` / H5 `SESSION` 落盘到 `out/miniprogram.json`，
再消费它去提交打卡。

---

## 目录结构

```
mooncheck/
├─ README.md               你正在看的这份
├─ LICENSE                 MIT
├─ .gitignore              默认忽略 config.json / out/ / capture/ 等
├─ checkin.cmd             双击一键打卡（Windows，含成功/失败提示）
├─ checkin.py              命令行入口：切代理 → 抓 ticket → 提交 → 还原 → 通知
├─ capture_wanan.py        mitmproxy addon：截获 sessionId / ticket 落盘
├─ wsc_mp.py               小程序会话客户端（读 ticket、校验时效、查询状态）
├─ wsc_checkin.py          打卡接口封装（端点 / 请求头 / 载荷字段 / 安全字段提取）
├─ wsc_http.py             最小 HTTP 客户端（Cookie 罐、302 跟随、零第三方依赖）
└─ config.example.json     配置模板（复制成 config.json 后填写）
```

---

## 配置说明

`config.json` 由 `config.example.json` 复制而来，各字段：

| 字段 | 说明 |
| --- | --- |
| `location` | ★ 必填：要上报的坐标（虚拟定位，可设为宿舍或任意位置）。字段：`latitude` / `longitude` / `reverse_geocode` / `coordinate_system` / `accuracy` / `location_duration_ms` / `network_type` |
| `loc_jitter_m` | 提交时坐标随机抖动半径（米），0 = 不抖动 |
| `user_agent` | 打卡请求的 UA，留空回退默认浏览器 UA |
| `notify_sendkey` | （可选）Server酱 SendKey，留空或保留 `PUT_` 前缀则跳过推送 |
| `label` | （可选）多账号时区分推送标题前缀 |
| `proxy_port` | mitmproxy 监听端口，默认 8080 |
| `mitmdump_path` | （可选）mitmdump 绝对路径，留空自动探测 |

---

## 迁移到其它电脑

拷贝整个目录，装 Python + `pip install mitmproxy`，首次信任 CA 证书并重启微信，填好 `config.json` 即可。
`mitmdump` 路径自动探测（`PATH` → 当前解释器的 `Scripts/`），找不到时在 `config.json` 里写 `mitmdump_path`。

---

## 移植到其它学校 / 其它小程序

思路通用，改三处：

- `capture_wanan.py`：改 `HOST` / `CHECK_BIND` / `CHECKIN_TICKET` / `H5_PREFIX` 为那套小程序的鉴权路径；
- `wsc_checkin.py`：改 `BASE` / 端点 / 请求头 / 载荷字段；
- `checkin.py`：改提交时的票据头字段名与坐标结构。

---

## 路线图（Roadmap）

- ✅ **当前**：半自动——你手动打开小程序一次，脚本自动抓 `ticket` 并提交打卡（虚拟定位）。
- 🚧 **规划中**：开发 **UI 自动化**（模拟点击小程序界面），实现「登录微信 → 打开小程序 → 打卡」全流程自动，彻底免手动。

---

## 安全约定

- `config.json`、`out/`、`capture/` 已在 `.gitignore` 中，**含坐标 / SendKey / 会话票据，切勿提交或外传**；
- `out/miniprogram.json` 里的 `sessionId` / `ticket` 等价于账号通行证，30 分钟有效，用完即弃；
- 脚本只打印安全字段，不打印姓名、手机号、密码、Cookie 值。

---

## 免责声明（请先读）

- 本项目用于**设备/账号持有者本人**的自动打卡；给他人打卡须获得明确授权，**不做代打、不对外分发、不营利**；
- 自动化打卡**可能不符合学校规定**，请自行评估并承担后果；
- 请勿用于绕过服务端安全校验、请勿高频请求（默认每天一次）；
- 本项目按 MIT 协议开源，**作者不对使用后果承担责任**。

---

**License: MIT**
