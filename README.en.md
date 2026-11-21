# opencode-go-relay

English · [中文](README.md)

[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![tests](https://github.com/a1271981054/opencode-go-relay/actions/workflows/ci.yml/badge.svg)](https://github.com/a1271981054/opencode-go-relay/actions/workflows/ci.yml)

Use **OpenCode Go** models from any OpenAI- or Anthropic-compatible client — WorkBuddy, Cherry Studio, LobeChat, NextChat, Open WebUI, or your own scripts. One file, Python standard library only.

## Why this exists

The Go inference gateway requires an `x-opencode-session` header on every request, and rejects requests **before** authentication when it is missing:

```json
{"type":"error","error":{"type":"MissingSessionID",
 "message":"Request is missing x-opencode-session and cannot be routed efficiently."}}
```

Most clients only give you a URL field and an API key field, with no way to add a header. This relay listens on localhost, adds the header, and forwards the request:

```
client ──► http://127.0.0.1:8787/v1/chat/completions ──► https://opencode.ai/inference/go/openai/v1/chat/completions
                 adds x-opencode-session, passes Authorization through untouched
```

## Features

- **Real streaming** — SSE chunks are forwarded as they arrive, never buffered (covered by a test)
- **Stable sessions** — `ses_...` derived from system prompt + first user message, so the gateway can reuse its prompt cache
- **No secrets at rest** — your `Authorization` header is forwarded as-is; nothing is stored or logged. `api-key` / `x-api-key` are accepted and converted to Bearer
- **Multiple protocols** — Chat Completions, Responses, Anthropic Messages, Gemini paths
- **Localhost only** by default, strips `opencode-go/` model prefixes

## Quick start

```bash
git clone https://github.com/a1271981054/opencode-go-relay.git
cd opencode-go-relay
python3 relay.py
```

```bash
curl -s http://127.0.0.1:8787/__relay/health
curl -s http://127.0.0.1:8787/v1/models | head -c 200
```

Get a key at <https://opencode.ai/console> → account menu → **Account** → **Personal API keys**. The key must belong to a workspace subscribed to Go, otherwise you get `401 Invalid credential`.

## Configure your client

| Field | Value |
| --- | --- |
| Endpoint | `http://127.0.0.1:8787/v1/chat/completions` |
| Base URL | `http://127.0.0.1:8787/v1` |
| API key | `oc_sk_...` |
| Model | `glm-5.3` (bare ID) |
| Tool calling | on |
| Reasoning | on |
| Custom protocol | off |
| Input / output tokens | 256K / 64K |

Anthropic-style clients (Claude Code and friends): point them at `http://127.0.0.1:8787` and they will hit `/v1/messages`.

## Models

All Go models work; popular picks:

| Model ID | Context | Vision | Go monthly usage |
| --- | --- | --- | --- |
| `glm-5.3` | 1M | ❌ | $500 |
| `deepseek-v4.1-flash` | 1M | ✅ | $100 |
| `glm-5.3-flash` | 1M | ✅ | $60 |
| `minimax-m3` | 1M | ✅ | $60 |
| `kimi-k2.7-code` | 256K | ✅ | $60 |
| `qwen3.7-plus` | 1M | ✅ | $60 |
| `kimi-k3` | 1M | ✅ | $15 |
| `deepseek-v4-pro` | 1M | ❌ | $15 |

Limits: the 5-hour window may use 20% of the monthly allowance; the weekly window resets Monday 00:00 UTC.

## Run it permanently

```bash
./start.command              # macOS, double-clickable
./install-autostart.command  # macOS launchd, keeps it alive
```

Linux: copy the systemd user unit from the Chinese README. Windows: `python relay.py` in the background, or a scheduled task.

## Options

| Flag | Default | Meaning |
| --- | --- | --- |
| `--host` | `127.0.0.1` | Bind address. Do not use `0.0.0.0` — there is no auth |
| `--port` | `8787` | Change it if the port is taken |
| `--upstream` | `https://opencode.ai` | Rarely needs changing |
| `--timeout` | `900` | Upstream timeout in seconds |
| `--no-log` | off | Disable `relay.log` |

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `400 MissingSessionID` | The request bypassed the relay, or the relay is not running |
| `401 Invalid credential` | Wrong key, or the key's workspace has no Go subscription |
| `403` | `*-free` models are OpenCode-only; or region restrictions |
| `429` | Allowance exhausted for the 5-hour or weekly window |
| `502` | Cannot reach opencode.ai — make sure the relay process uses your proxy too |

## Caveats

- **Not an official client.** OpenCode validates Claude Code, Codex, Hermes and Pi — not this relay. Requests route and bill correctly, but you are responsible for complying with the terms of service.
- **Localhost only.** The relay is unauthenticated; never expose it publicly.
- **Free models are blocked** outside OpenCode.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## License

[MIT](LICENSE)
