# 儿童陪伴智能助手 — API 路由文档

> Base URL: `http://127.0.0.1:8000`

---

## 目录

1. [健康检查](#1-健康检查)
2. [角色管理](#2-角色管理)
3. [对话](#3-对话)
4. [表情分析](#4-表情分析)
5. [TTS 语音合成](#5-tts-语音合成)
6. [ASR 语音识别](#6-asr-语音识别)
7. [对话历史](#7-对话历史)
8. [长期记忆](#8-长期记忆)
9. [测试面板](#9-测试面板)

---

## 1. 健康检查

### `GET /api/health`

**响应**:
```json
{"status": "ok", "version": "2.0.0"}
```

---

## 2. 角色管理

### `GET /api/roles`

列出所有角色名。

**响应**:
```json
{"roles": ["熊大", "哆啦A梦"]}
```

---

### `GET /api/roles/{role_name}`

获取角色详情。

**响应**:
```json
{
  "role_name": "熊大",
  "system_prompt": "你是熊大...",
  "reference_audio_path": "/path/to/audio.wav",
  "voice_id": "child_companion-xxx",
  "voice_provider": "cosyvoice",
  "oss_url": "https://bucket.oss-cn-shanghai.aliyuncs.com/...",
  "target_model": "cosyvoice-v3.5-plus",
  "timestamp": "2026-06-26T12:00:00"
}
```

---

### `POST /api/roles`

创建新角色。

- **Content-Type**: `multipart/form-data`
- **参数**:
  | 字段 | 类型 | 必填 | 说明 |
  |------|------|------|------|
  | `role_name` | string | ✅ | 角色名称 |
  | `voice_provider` | string | ❌ | `cosyvoice` 或 `qwen_tts`，默认 `cosyvoice` |
  | `audio` | file | ❌ | 参考音频；Qwen 支持 WAV/MP3/M4A/MP4 |

**流程**: LLM 生成提示词 → 按 `voice_provider` 创建音色 → 持久化。CosyVoice 会先上传 OSS，Qwen3-TTS 直接提交本地音频。

**响应**: 同 `GET /api/roles/{role_name}`

---

### `DELETE /api/roles/{role_name}`

删除角色及对应云端音色；CosyVoice 角色还会清理 OSS 文件。

**响应**:
```json
{"status": "ok", "message": "角色 '熊大' 已删除"}
```

---

## 3. 对话

### `POST /api/chat`

发送消息，SSE 流式返回。

- **Content-Type**: `application/json`
- **请求体**:
  ```json
  {
    "role_name": "熊大",
    "message": "你好呀！"
  }
  ```

- **响应**: `text/event-stream`（SSE）

**SSE 事件类型**:

| 事件 | 格式 | 说明 |
|------|------|------|
| `text` | `{"type":"text", "data":"增量文本..."}` | 累积的完整响应文本（每3个chunk推送一次） |
| `sentence` | `{"type":"sentence", "data":"完整句子"}` | 检测到的完整句子，前端应调 `/api/tts` 合成语音 |
| `done` | `{"type":"done", "data":"完整回复"}` | 回复完成，已自动持久化到历史 |
| `error` | `{"type":"error", "data":"错误信息"}` | 错误 |

**前端集成流程**:
1. 连接 SSE，监听事件
2. `text` → 实时更新 UI 文本
3. `sentence` → 收集句子，调用 `/api/tts` 合成语音
4. `done` → 收集完所有句子后，调用 `/api/chat/expressions` 获取表情序列，将 TTS 时长填入 `duration_ms`

---

### `DELETE /api/chat/history`

清空对话历史（内存级，与 `/api/history` 不同）。

**响应**:
```json
{"status": "ok", "message": "对话历史已清空"}
```

---

## 4. 表情与动作分析

### `POST /api/chat/expressions`

分析句子情感，返回表情序列 + 身体动作。用于驱动前端 VRM 模型面部表情和骨骼动画。

- **Content-Type**: `application/json`
- **请求体**:
  ```json
  {
    "sentences": ["你好呀！", "今天开心吗？"],
    "available_expressions": ["happy", "sad", "angry", "surprised", "neutral"],
    "available_actions": ["none", "clapping", "goodbye", "jump", "sad", "surprised", "look_around", "angry", "blush", "sleepy", "relax"]
  }
  ```

  | 字段 | 类型 | 必填 | 说明 |
  |------|------|------|------|
  | `sentences` | string[] | ✅ | 待分析的句子列表 |
  | `available_expressions` | string[] | ✅ | VRM 模型支持的表情标签 |
  | `available_actions` | string[] | ❌ | 可用身体动作列表，默认 `["none"]` |

- **响应**:
  ```json
  {
    "expressions": [
      {"sentence_index": 0, "expression": "happy", "intensity": 0.8, "action": "clapping"},
      {"sentence_index": 1, "expression": "surprised", "intensity": 0.6, "action": "none"}
    ]
  }
  ```

  | 字段 | 类型 | 说明 |
  |------|------|------|
  | `sentence_index` | int | 对应输入句子的索引 |
  | `expression` | string | 表情标签（必在 `available_expressions` 中） |
  | `intensity` | float | 表情强度 0.0-1.0（前端二分：≥0.5 为 ON） |
  | `action` | string | 身体动作标签，`"none"` 表示无动作 |

**动作列表**：`angry`, `blush`, `clapping`, `goodbye`, `jump`, `look_around`, `relax`, `sad`, `sleepy`, `surprised`, `none`

**前端集成**：
```
每句到达 → 立即调 API（单句） → expression + action 返回后入队播放
done 时 → 调 API（全量） → 合并修正，不覆盖已有动作
```

---

## 5. TTS 语音合成

### `POST /api/tts`

- **Content-Type**: `application/json`
- **请求体**:
  ```json
  {
    "text": "你好呀小朋友！",
    "role_name": "熊大"
  }
  ```

- **响应**:
  ```json
  {
    "url": "/tts_audio/tts_abc123.wav",
    "duration_ms": 2340,
    "text": "你好呀小朋友！"
  }
  ```

  | 字段 | 说明 |
  |------|------|
  | `url` | 音频访问路径（拼接 Base URL 即可播放） |
  | `duration_ms` | 音频时长毫秒，用于同步表情 |

**注意**: 后端按角色的 `voice_provider` 自动选择 CosyVoice 或 Qwen3-TTS，调用方无需改变 `/api/tts` 请求格式。

---

## 6. ASR 语音识别

### `WS /api/asr/stream`

实时语音主通道。浏览器发送单声道、16 kHz、16-bit PCM 二进制分片，后端将其转发至 DashScope Paraformer，并实时返回中间和最终文本。

**浏览器 -> 服务端控制消息**:

```json
{"type":"start", "language_hints":["zh", "ja", "en"]}
```

开始后发送 PCM 二进制帧；音频结束时发送：

```json
{"type":"stop"}
```

**服务端 -> 浏览器事件**:

| 事件 | 格式 | 说明 |
|------|------|------|
| `ready` | `{"type":"ready"}` | DashScope 任务已启动，可开始发送音频 |
| `partial` | `{"type":"partial","text":"..."}` | 中间识别结果，用于实时字幕 |
| `final` | `{"type":"final","text":"..."}` | VAD 断句后的最终结果 |
| `done` | `{"type":"done","text":"..."}` | 整段语音完成，可提交给对话接口 |
| `error` | `{"type":"error","message":"..."}` | 会话或识别错误 |

连接按浏览器页面复用；单个会话默认最长 120 秒，音频帧最大 64 KiB。需要安装 `websockets` 依赖，并在生产环境配置 `DASHSCOPE_WORKSPACE_ID` 或 `DASHSCOPE_ASR_WS_URL` 使用业务空间专属域名。

---

## 7. 对话历史

### `GET /api/history`

获取所有持久化对话历史。

**响应**:
```json
{
  "history": [
    {
      "timestamp": "2026-06-26T12:00:00",
      "role_name": "熊大",
      "messages": [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "小朋友好！俺是熊大！"}
      ]
    }
  ],
  "total": 1
}
```

---

### `DELETE /api/history`

清空所有持久化对话历史。

---

## 8. 长期记忆

### `GET /api/memory`

获取所有长期记忆。

**响应**:
```json
{
  "memories": [
    {"id": 1, "content": "小朋友叫小明，今年5岁"},
    {"id": 2, "content": "小明最喜欢的动物是恐龙"}
  ],
  "total": 2
}
```

---

### `POST /api/memory/generate`

从对话历史中提取新记忆。由 LLM 自动分析总结。

**响应**:
```json
{
  "memory": "小朋友叫小明，今年5岁，最喜欢恐龙",
  "message": "记忆已生成"
}
```

---

### `DELETE /api/memory`

清空所有长期记忆。

---

## 9. 测试面板

### `GET /test`

返回自包含的 HTML 测试面板，可直接在浏览器中使用所有 API 功能。

**访问**: http://localhost:8000/test

---

## 错误响应格式

所有 API 在出错时返回标准格式：

```json
{"detail": "错误描述信息"}
```

HTTP 状态码：
- `400` — 请求参数错误
- `404` — 资源不存在
- `500` — 服务器内部错误
