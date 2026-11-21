# opencode-go-relay

[English](README.en.md) · 中文

[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![tests](https://github.com/a1271981054/opencode-go-relay/actions/workflows/ci.yml/badge.svg)](https://github.com/a1271981054/opencode-go-relay/actions/workflows/ci.yml)

让 **OpenCode Go** 的模型能在任意 OpenAI / Anthropic 兼容客户端里直接用 —— WorkBuddy、Cherry Studio、LobeChat、NextChat、Open WebUI、你自己的脚本都行。

单文件、纯 Python 标准库、无依赖。

## 它解决什么问题

OpenCode Go 的推理网关强制要求每个请求带 `x-opencode-session`：

```http
POST https://opencode.ai/inference/go/openai/v1/chat/completions
```

不带这个头，请求在**鉴权之前**就被拒：

```json
{"type":"error","error":{"type":"MissingSessionID",
 "message":"Request is missing x-opencode-session and cannot be routed efficiently."}}
```

而大多数客户端的「添加模型」只有两个格子：接口地址 + API Key，没有填请求头的地方。于是这个中转在本机起一个 OpenAI 兼容接口，补上这个头再转发：

```
客户端 ──► http://127.0.0.1:8787/v1/chat/completions ──► https://opencode.ai/inference/go/openai/v1/chat/completions
                    补 x-opencode-session，Authorization 原样透传
```

## 特性

- **流式透传**：SSE 逐块转发，不攒齐再吐（有测试守着）
- **稳定 session**：按「system + 首条用户消息」哈希出固定 `ses_...`，同一段对话命中网关的 prompt 缓存
- **Key 不落地**：客户端发来的 `Authorization` 原样转发，中转不存、不记日志；也接受 `api-key` / `x-api-key` 并转成 Bearer
- **多种协议**：OpenAI Chat Completions、OpenAI Responses、Anthropic Messages、Gemini 路径都能映射
- **只监听 127.0.0.1**：默认不对外，本机自用
- 顺手把 `opencode-go/glm-5.3` 这种带前缀的模型名剥成裸 ID

## 快速开始

```bash
git clone https://github.com/a1271981054/opencode-go-relay.git
cd opencode-go-relay
python3 relay.py
```

看到这行就成功了：

```
relay 1.0.0 listening on http://127.0.0.1:8787
```

自检（不需要 key）：

```bash
curl -s http://127.0.0.1:8787/__relay/health
curl -s http://127.0.0.1:8787/v1/models | head -c 200   # 应该返回 Go 的模型列表
```

API Key 从哪里来：<https://opencode.ai/console> → 账号菜单 → **Account** → **Personal API keys** → Add API key。
**必须选订阅了 Go 的 workspace**，否则调用会返回 `401 Invalid credential`。

## 客户端怎么填

以 WorkBuddy 的「设置 → 模型 → 添加模型」为例：

| 字段 | 填什么 |
| --- | --- |
| 接口地址 | `http://127.0.0.1:8787/v1/chat/completions` |
| API Key | `oc_sk_...` |
| 模型名称 | `glm-5.3`（裸 ID，换模型只改这一格） |
| 工具调用 | ✅ 勾上，否则 Agent 不会调工具 |
| 图片输入 | 看模型，见下表 |
| 思考模式 | ✅ 勾上 |
| 自定义协议 | ❌ 不勾（走的就是 OpenAI Chat Completions） |
| 输入 / 输出 token | 256K / 64K（或留空用默认） |

其他客户端同理：要 **base URL** 的填 `http://127.0.0.1:8787/v1`，要 **完整 endpoint** 的填 `http://127.0.0.1:8787/v1/chat/completions`。
Anthropic 协议客户端（Claude Code 那类）填 `http://127.0.0.1:8787`，走 `/v1/messages`。

## 模型 ID

31 个 Go 模型都能用，常用这几个（数据来自 `GET /v1/models`）：

| 模型 ID | 名称 | 上下文 | 图片 | Go 月额度 |
| --- | --- | --- | --- | --- |
| `glm-5.3` | GLM-5.3 | 1M | ❌ | $500 |
| `deepseek-v4.1-flash` | DeepSeek V4.1 Flash | 1M | ✅ | $100 |
| `glm-5.3-flash` | GLM-5.3-Flash | 1M | ✅ | $60 |
| `minimax-m3` | MiniMax-M3 | 1M | ✅ | $60 |
| `kimi-k2.7-code` | Kimi K2.7 Code | 256K | ✅ | $60 |
| `qwen3.7-plus` | Qwen3.7 Plus | 1M | ✅ | $60 |
| `kimi-k3` | Kimi K3 | 1M | ✅ | $15 |
| `deepseek-v4-pro` | DeepSeek V4 Pro | 1M | ❌ | $15 |

额度规则：5 小时窗口最多用掉月额度的 20%，周额度每周一 00:00 UTC 重置。
用完了可以开 Extra Usage 走 Console 余额。

## 常驻运行

**macOS**

```bash
./start.command                        # 双击也行，关窗口就停
./install-autostart.command            # 装成 launchd 开机自启（崩溃自动拉起）
```

取消自启：

```bash
launchctl bootout gui/$(id -u)/com.opencode-go-relay
rm ~/Library/LaunchAgents/com.opencode-go-relay.plist
```

**Linux**（systemd user unit）

```ini
# ~/.config/systemd/user/opencode-go-relay.service
[Unit]
Description=OpenCode Go relay

[Service]
ExecStart=%h/opencode-go-relay/run.sh
Restart=always

[Install]
WantedBy=default.target
```

```bash
systemctl --user enable --now opencode-go-relay
```

**Windows**：`python relay.py` 挂后台，或丢进任务计划程序。

## 参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--host` | `127.0.0.1` | 监听地址，别改成 `0.0.0.0`（没有鉴权） |
| `--port` | `8787` | 端口被占就换一个，客户端里的 URL 跟着改 |
| `--upstream` | `https://opencode.ai` | 上游，一般不用动 |
| `--timeout` | `900` | 上游超时秒数 |
| `--no-log` | 关 | 不写 `relay.log` |

日志每次请求一行，只有路径、状态码、模型、耗时、字节数，**不含 Key、不含请求体**。

## 排错

| 现象 | 原因 / 处理 |
| --- | --- |
| `400 MissingSessionID` | 没走中转，或中转没起、端口不对 |
| `401 Invalid credential` | Key 错 / 不属于订阅 Go 的 workspace / 复制漏字符 |
| `403` | 免费模型（`*-free`）只在 OpenCode 内可用，第三方一律 403；或地区被隐私设置限制 |
| `429` / `opencode.allowance` | 5 小时或每周额度用完 |
| `502 relay_upstream_unreachable` | 本机连不上 opencode.ai；开了代理的话，中转进程也要走代理 |
| 客户端提示连接失败 | `curl http://127.0.0.1:8787/__relay/health` 先确认中转活着 |

国内用 `deepseek-v4.1-flash` / `deepseek-v4-pro` 时，需要在 Console → Settings → Privacy → Regions 选 **Global**，否则会被地区策略拦。

## 已知边界

- **不是官方客户端。** OpenCode 文档里列为「已验证」的是 Claude Code、Codex、Hermes、Pi 等，本项目不在名单内。它能正常路由、正常计费，但属于「能用不保证」，请自行确认是否符合服务条款，别拿它跑异常流量。
- **只在本机用。** 中转没有任何鉴权，谁都能用你机器上的端口；不要 `--host 0.0.0.0`，更不要暴露到公网。
- **免费模型走不通。** Go 里的 `*-free` 模型只允许在 OpenCode 内部调用。

## 开发

```bash
python3 -m unittest discover -s tests -v
```

测试会起一个假上游，验证补头、路径映射、session 稳定性，以及流式是否真的逐块到达（不是整体缓冲）。

## License

[MIT](LICENSE)
