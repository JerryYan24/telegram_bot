# Smart Calendar Assistant

一个面向 Telegram 的智能助手。它使用 OpenAI GPT 模型理解邮件、聊天消息和图片海报中的信息，并自动把解析出的日程写入你的 Google Calendar，同时将待办类事项同步到 Google Tasks。

## 能力
- **聊天记事**：直接在 Telegram 里告诉助手要安排的事项，即可落地为日历事件。
- **智能读信**：把邮件转发到助手邮箱，机器人会轮询收件箱、解析邮件正文并同步到日历。
- **图像理解**：上传活动海报、会议截图等图片，助手会读图提取时间地点再创建日程。
- **统一语义解析**：所有渠道都通过 OpenAI GPT 模型做意图识别与结构化，确保字段规范。
- **批量事件**：一次消息里列出多个行程也没问题，会逐条添加到日历并返回摘要。
- **待办管理**：对“不带具体时间的任务”会落地到 Google Tasks，帮助你区分会议与待办。

## 架构概览
1. Telegram Bot (`python-telegram-bot`) 负责和用户交互。
2. `smart_assistant.OpenAIEventParser` 调用 OpenAI Responses API，将自由文本/图片抽取为结构化事件。
3. `smart_assistant.GoogleCalendarClient` 使用 Google Calendar API 插入事件。
4. 可选的 `EmailEventIngestor` 轮询 IMAP 邮箱，把未读邮件交给助手处理。

## 先决条件
- Python 3.10+
- 一个 Telegram Bot Token
- OpenAI API Key（已启用 GPT-4o/mini 等多模态模型）
- Google Cloud OAuth Client（Desktop）JSON，已启用 Calendar API
- 可选：支持 IMAP 的邮箱账号（建议创建专用“助手邮箱”）

## 安装
```bash
pip install -r requirements.txt
```

## 必需的环境变量
| 变量 | 说明 |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token |
| `OPENAI_API_KEY` | OpenAI API Key |
| `OPENAI_BASE_URL` | （可选）自定义 OpenAI endpoint，未设置则走官方 |
| `OPENAI_TEXT_MODEL` | （可选）自定义文本模型，默认 `gpt-4o-mini` |
| `OPENAI_VISION_MODEL` | （可选）图片解析模型，不填沿用文本模型 |
| `OPENAI_ALLOWED_MODELS` | （可选）允许 `/model` 命令切换的模型列表，逗号或分号分隔 |
| `OPENAI_MODEL_STATE_PATH` | （可选）记住上次选择的模型的保存路径，默认 `model_state.json` |
| `GOOGLE_CLIENT_SECRETS_PATH` | Google OAuth client secret JSON（必需） |
| `GOOGLE_TOKEN_PATH` | OAuth token 保存路径（可选，默认 `google_token.json`） |
| `GOOGLE_CALENDAR_ID` | 目标日历 ID，默认 `primary` |
| `GOOGLE_TASK_LIST_ID` | Google Tasks 列表 ID，默认 `@default` |
| `GOOGLE_DEFAULT_COLOR_ID` | （可选）所有分类都未命中时使用的 `colorId` |
| `ASSISTANT_DEFAULT_TZ` | 默认时区（IANA 格式，默认 `UTC`） |

## 统一配置方式
所有必填和可选项都可以写在 `config.yaml`（或通过 `ASSISTANT_CONFIG_PATH` 指定的路径）里，启动时自动读取；同名环境变量仍可用，并且优先生效。示例：

```yaml
telegram:
  bot_token: "123456789:ABCDEF"

openai:
  api_key: "sk-xxxx"
  base_url: "https://api.openai.com/v1"   # 可替换成自建代理/兼容接口
  text_model: "gpt-4o-mini"
  vision_model: "gpt-4o-mini"
  allowed_models: ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"]
  model_state_path: "model_state.json"

google:
  client_secrets_path: "/abs/path/client_secret.json"
  token_path: "google_token.json"
  calendar_id: "primary"
  task_list_id: "@default"
  category_colors:
    work: "7"        # Peacock
    meeting: "7"
    personal: "5"    # Banana
    family: "2"      # Sage
    travel: "9"      # Blueberry
  default_color_id: ""

assistant:
  default_tz: "Asia/Shanghai"

email:
  imap_host: "imap.example.com"
  username: "bot@example.com"
  password: "app-password"
  folder: "INBOX"
  poll_interval: 60
  use_ssl: true
```

> 想放在自定义路径，请设置 `ASSISTANT_CONFIG_PATH=/path/to/your.yaml`。

快速开始：
```bash
cp config.example.yaml config.yaml
# 或直接编辑 repo 根目录已有的 config.yaml
```

### OAuth 授权（通过 Telegram 完成）
1. 在 Google Cloud Console 为桌面应用创建 OAuth Client，并启用 Calendar API。
2. 下载 client secret JSON，路径填入 `GOOGLE_CLIENT_SECRETS_PATH` 或 `google.client_secrets_path`。
3. 运行 `python jarvis.py` 启动机器人。
4. 在 Telegram 与机器人对话中发送 `/google_auth`，它会回复一条授权链接。
5. 在浏览器里完成 Google 登录并允许访问（同意 Calendar / Tasks 权限）后，复制回调页面的整条链接或 `code=...` 参数。
6. 回到 Telegram，把页面上显示的 code 原样发送过去（直接粘贴或使用 `/google_auth_code <code>` 均可）。收到成功提示后，令牌会写入 `google.token_path`（默认 `google_token.json`）。下次启动会自动复用，无需再次授权；如需中途取消，可以点聊天里附带的“取消本次授权”按钮再次开始。

### 颜色分类
助手会让模型为每个事件打 `category` 标签，并根据以下默认映射写入 Google Calendar `colorId`：

| 分类 | 默认 colorId |
| --- | --- |
| work / meeting / call | 7（Peacock） |
| personal | 5（Banana） |
| family | 2（Sage） |
| travel / trip | 9（Blueberry） |
| study / education | 3（Grape） |
| finance / payment | 8（Graphite） |
| health / medical | 10（Basil） |
| deadline | 11（Tomato） |
| reminder | 1（Lavender） |

如需自定义，把 `google.category_colors` 写成一个字典即可，键为小写分类名，值为 Google `colorId`（字符串 1-11，可用官方色名如 `peacock`/`red` 等）。还可以设置 `google.default_color_id`/`GOOGLE_DEFAULT_COLOR_ID` 作为兜底颜色；若想完全禁用默认映射，可在配置里写 `category_colors: {}`。

### 事件 vs 待办
当用户描述一个没有明确时间/地点、偏向“提醒”或“待办”的事项时，模型会将其标记为 `entry_type="task"` 并写入 `google.task_list_id` 指定的 Google Tasks 列表（默认 `@default`）。涉及具体会议/活动的描述仍会写入 Calendar。这样既能保留会议安排，也能把行动项集中到任务列表里。
> Google Tasks 暂无稳定的任务详情公开链接，因此机器人回复里的待办链接会跳转到 Tasks 官网 `tasks.google.com`，你可以在其中定位到刚创建的条目。

### 切换解析模型
如果你在 `openai.allowed_models`（或 `OPENAI_ALLOWED_MODELS`）里列出了多个模型，可以在 Telegram 里发送 `/model` 查看当前模型和可选列表，再点击按钮或输入 `/model gpt-4o` 之类的命令即时切换。默认情况下会把文本与视觉解析模型一起切换；若在配置里显式设置了 `openai.vision_model`，则图像解析始终使用该模型。最近一次选择会写入 `openai.model_state_path`（默认 `model_state.json`），下次启动自动沿用。

## 邮箱轮询（可选）
启用邮件转日历需额外配置：

| 变量 | 说明 |
| --- | --- |
| `ASSISTANT_IMAP_HOST` | IMAP 服务器地址 |
| `ASSISTANT_EMAIL` | 邮箱账号 |
| `ASSISTANT_EMAIL_PASSWORD` | 邮箱密码或 App Password |
| `ASSISTANT_IMAP_FOLDER` | 读取的文件夹，默认 `INBOX` |
| `ASSISTANT_EMAIL_POLL_INTERVAL` | 轮询间隔秒数，默认 60 |
| `ASSISTANT_IMAP_SSL` | 非 `false` 时使用 SSL |

助手会拉取“未读”邮件，解析成功后自动标记为已读。

## 运行
```bash
python jarvis.py
```

> 首次启动若尚未授权 Google Calendar，请在 Telegram 中发送 `/google_auth` 按提示完成登录，再把页面展示的 code 粘贴给机器人（或使用 `/google_auth_code <code>`）。完成一次后会自动复用本地 `google_token.json`。要切换解析模型，可在对话中发送 `/model` 查看选项并实时更换。

启动后你可以：
1. 在 Telegram 里发送一句自然语言描述（如“明天上午9点和 Alex 开周会”）。
2. 把包含会议邀请的邮件转发到助手邮箱。
3. 上传活动海报图片，让机器人识别其中的事件信息。

每次成功同步后，机器人会回复事件摘要和 Google Calendar 链接，方便你核对。
