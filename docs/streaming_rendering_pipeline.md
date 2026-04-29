# 模型输出全链路：从 API 响应到屏幕渲染

本文档描述 Wyckoff CLI 中 LLM 输出如何经过 Provider 适配、chunk 归一化、TUI 渲染，最终展示给用户的完整技术链路。

---

## 架构概览

```
┌─────────────────┐    normalized chunks     ┌────────────────┐
│  LLM Provider   │ ──────────────────────── │   TUI Agent    │
│ (OpenAI/Gemini/ │   text_delta             │   Loop         │
│  Claude/Fallback)│   thinking_delta         │  (tui.py)      │
└─────────────────┘   tool_calls / usage     └───────┬────────┘
                                                     │
                                              ┌──────▼──────┐
                                              │  ChatLog    │
                                              │ (RichLog)   │
                                              └─────────────┘
```

---

## 1. Provider 适配层

所有 Provider 实现 `cli/providers/base.py` 中的抽象接口：

```python
class LLMProvider(ABC):
    def chat_stream(self, messages, tools) -> Generator[dict, None, None]:
        """流式生成归一化 chunk dict"""
```

### 归一化 chunk 协议

| type | 含义 | 关键字段 |
|------|------|----------|
| `text_delta` | 文本 token | `text: str` |
| `thinking_delta` | 推理链 token (DeepSeek R1 等) | `text: str` |
| `tool_calls` | 流结束时聚合的工具调用 | `tool_calls: list[dict]`, `text: str` |
| `usage` | 永远是最后一个 chunk | `input_tokens`, `output_tokens` |

### OpenAI 兼容 (`cli/providers/openai.py`)

- 解析 `delta.reasoning_content` → `thinking_delta`
- 解析 `delta.content` → `text_delta`
- `delta.tool_calls` 按 `index` 累积到 `tool_map`，流结束后 JSON decode `args_json`，聚合为一次性 `tool_calls` chunk
- 兼容 NVIDIA/kimi 等把 tool_calls 包在 `<tool_call>` XML 标签内的模型（fallback parser）

### Gemini (`cli/providers/gemini.py`)

- `part.text` → `text_delta`
- `part.function_call` → 累积到 `tool_calls[]`（args 已是 dict，无需 JSON 解析）
- 无 `thinking_delta` 支持

### Claude (`cli/providers/claude.py`)

- Anthropic event stream：`content_block_start` / `content_block_delta` / `content_block_stop`
- `text_delta` event → `text_delta` chunk
- `input_json_delta` event → 逐步累积 tool JSON，`content_block_stop` 时 decode
- `message_start` / `message_delta` → 提取 token 用量

### Fallback (`cli/providers/fallback.py`)

多 Provider 自动降级：按优先级逐个尝试，probe 首个 chunk 确认连接成功后 yield 全部 chunk。网络/速率限制/服务端错误触发 fallback。

---

## 2. TUI Agent Loop

入口：`cli/tui.py` → `_run_agent()`，`@work(thread=True)` 在后台线程执行。

### 主循环结构

```
for round_idx in range(MAX_TOOL_ROUNDS=15):
    1. 上下文压缩 (compact_messages)
    2. 流式连接 (最多 3 次重试)
    3. chunk 循环 → 逐行渲染
    4. 流后处理 → thinking 折叠 / tool 执行
    5. 判断是否继续 (有 tool_calls → 下一轮)
```

### 2.1 上下文压缩

每轮开始检查 message 列表是否超过模型 context window 的 25%。超过时调 `compact_messages()` 对头部消息做摘要压缩。压缩本身也通过 `provider.chat_stream()` 完成。

参见 `cli/compaction.py`。

### 2.2 流式文本渲染

```python
for chunk in stream:
    if chunk["type"] == "text_delta":
        text_buf += chunk["text"]
        _stream_line_buf += chunk["text"]
        # 遇到换行，flush 完整行到 ChatLog
        while "\n" in _stream_line_buf:
            line, _stream_line_buf = _stream_line_buf.split("\n", 1)
            _write_stream(Text(line))
```

关键设计：
- **逐行刷新**：流式 token 按 `\n` 切分，只渲染完整行，避免 Textual 重绘抖动
- **strip 计数**：每次 `_write_counted()` 记录写入了多少 visual strip，供后续清除用
- **分隔线**：首个 token 到达时写入一条 `───` 分隔线

### 2.3 Thinking 折叠

`thinking_delta` 仅累积到 `thinking_buf`，**不实时渲染**。流结束后折叠为一行：

```
💭 <前 80 字符>… (N 字)
```

设计原因：推理链通常很长且用户不需逐字看，只需知道模型"想了什么"。

### 2.4 Tool 调用展示

当 round 产生 tool_calls 时：

1. **清除已流式输出的文本**：调 `_clear_streamed_block()` 从 ChatLog 中物理删除已写入的行（因为这些是 tool_calls 之前的"思考文本"，模型最终输出在 tool 执行后）
2. **逐个执行 + 状态行**：
   ```
   ✓ 读盘诊断  1.2s        (成功，绿色)
   ✗ 获取持仓  0.8s 错误…  (失败，红色)
   ↗ 后台任务             (后台提交，青色)
   ```
3. **结果写入 messages**：`{"role": "tool", "tool_call_id": ..., "content": ...}` → 下一轮模型可读到

### 2.5 最终渲染

所有 tool 轮完成后（或无 tool_calls 时），最终文本通过 Rich `Markdown()` 渲染：

```python
log.write(Markdown(text_buf))
```

支持标准 Markdown：标题、列表、代码块（语法高亮）、表格、粗体/斜体等。

底部附 token 用量：`↑1234 ↓567 · 3.2s`

---

## 3. Doom-Loop 检测

位于 `cli/loop_guard.py`。

### 算法

```python
DOOM_LOOP_WINDOW = 6      # 滑动窗口大小
DOOM_LOOP_THRESHOLD = 3   # 重复次数阈值

def check_doom_loop(recent_calls, name, args) -> bool:
    fingerprint = (name, hash(json.dumps(args, sort_keys=True)))
    recent_calls.append(fingerprint)
    recent_calls[:] = recent_calls[-DOOM_LOOP_WINDOW:]
    return recent_calls.count(fingerprint) >= DOOM_LOOP_THRESHOLD
```

检测到后：
- 注入错误 tool result：`"doom-loop: 同参数重复调用3次，已中止"`
- 设置 `_doom_break = True`，跳过后续 assistant message 追加（避免空消息导致 API 400）
- TUI 显示黄色警告

---

## 4. Missing-Tool 重试

当用户明确要求某操作（如"看持仓"）但模型跳过了对应 tool 时：

1. `resolve_turn_expectation(messages)` 从最后一条 user message 识别意图
2. `missing_required_tool(expectation, used_tools)` 判断是否缺失
3. 追加一条合成 user message 要求模型重新执行
4. 最多重试 2 次

---

## 5. Headless Agent (`cli/agent.py`)

非 TUI 模式（脚本/测试）使用 `rich.live.Live` 渲染：
- Thinking：实时 live 滚动显示（暗灰斜体，最后 300 字符）
- Text：`Live(Markdown(text_buf))` 实时刷新
- 其他逻辑与 TUI 相同

---

## 6. 关键文件索引

| 文件 | 职责 |
|------|------|
| `cli/providers/base.py` | Provider 抽象接口 |
| `cli/providers/openai.py` | OpenAI 兼容流式适配 |
| `cli/providers/gemini.py` | Gemini 流式适配 |
| `cli/providers/claude.py` | Anthropic 流式适配 |
| `cli/providers/fallback.py` | 多 Provider 自动降级 |
| `cli/tui.py` | TUI 主循环 + 渲染 |
| `cli/agent.py` | Headless agent (Rich Live) |
| `cli/loop_guard.py` | Doom-loop + 重试逻辑 |
| `cli/compaction.py` | 上下文压缩 |
| `cli/tools.py` | Tool 注册 + 执行 + display name |
