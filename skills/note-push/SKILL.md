---
name: note-push
description: >-
  将本地 Markdown 笔记推送到指定的 self-hosted Discourse 实例。用户输入 /note-push <file.md>，或说「把这篇笔记推到 Discourse」「发布到论坛」「push this note to Discourse」时使用。默认 dry-run，确认后才创建或更新主题；同名标题先确认更新；自动添加 `ai生成` 和内容 tags；≥3 个 `##` 时自动插入 TOC；使用 `.note-push.json` 缓存主题映射。需要 DISCOURSE_URL / DISCOURSE_API_KEY / DISCOURSE_API_USERNAME。
license: MIT
compatibility: >-
  Requires Python 3, network access to the Discourse instance, and
  DISCOURSE_URL / DISCOURSE_API_KEY / DISCOURSE_API_USERNAME. Optional: an
  .env file loaded with --env-file. Works on macOS, Linux, and WSL.
---

# note-push：把 Markdown 笔记推送到 Discourse

把一个 `.md` 笔记发布到 self-hosted Discourse。默认只生成计划，用户确认后才写入。

核心脚本：`scripts/note_push.py`。不要自己重写 API 调用；用脚本的 dry-run、确认、执行流程。

## 前置条件

- 环境变量：`DISCOURSE_URL`、`DISCOURSE_API_KEY`、`DISCOURSE_API_USERNAME`。
- 如果项目里有本地 `.env`，用 `--env-file .env` 加载；不要把 key 写进笔记或提交到仓库。
- 分类：`--category` 或 `DISCOURSE_CATEGORY`，支持数字 ID、slug 或 name。
- API key 推荐 Single User + Granular，绑定专用非管理员 bot 用户；scope：
  - `topics:write`（创建主题）
  - `topics:read`（读取 topic / 首帖）
  - `posts:edit`（更新首帖 raw）
  - `search:show`（按标题查重）
  - `categories:list`（解析分类 slug/name）
  - `tags:list`（读取可见 tags）
  - `topics:update`（仅在用 `--update-title` / `--update-meta` 时需要）
- 目标分类必须对 bot 用户开放 **Create / Reply / See**（API 里的 `full`）；只给 Reply 无法创建主题。

## 执行流程

1. 读取笔记，先跑 dry-run：
   ```bash
   python3 scripts/note_push.py <file.md> --env-file .env --dry-run
   ```
   如果分类不在环境变量里，加 `--category <id|slug|name>`。
2. 检查 plan：
   - `title`：`--title` > 第一个 H1 > 文件名；H1 用作主题标题时会从 raw 删除。
   - `tags`：至少 `ai生成`；内容 tags 从标题/章节提取 3–5 个，含英文字母的一律小写。
   - `toc`：正文有 ≥3 个 `##` 时自动插入 TOC marker。
   - `action`：创建新主题，或更新已有 topic。
   - 如果脚本报多个同名候选，把候选列表给用户，让用户选 `--topic-id`。
3. 如果脚本报 unseen tags（或 plan 里 `unseen_tags` 非空），先问用户是否创建这些 tag；确认后加 `--allow-new-tags` 重跑 dry-run。
4. 把 plan 的标题、分类、tags、TOC、创建/更新目标给用户看，等待确认。
5. 用户确认后加 `--yes` 执行：
   ```bash
   python3 scripts/note_push.py <file.md> --env-file .env --category <id|slug> \
     --tags 'tag1,tag2' --yes
   ```
6. 成功后回复 topic URL、版本和 tag 结果；不要在聊天里重复整篇笔记。

用户明确说“直接发布 / 不用确认”时，可以跳过等待，但仍先跑一次 dry-run 生成 plan，并在回复里给出将要执行的操作。

## 参数

| 参数 | 作用 |
| --- | --- |
| `--instance URL` | 覆盖 `DISCOURSE_URL`（同一账号，只换实例 URL） |
| `--env-file PATH` | 从本地 `.env` 读取凭据；真实环境变量优先 |
| `--category VALUE` | 分类 ID、slug 或 name；创建时必填 |
| `--title TITLE` | 覆盖主题标题 |
| `--new-title TITLE` | 强制用新标题创建新主题；仅在用户拒绝更新同名主题时使用 |
| `--topic-id ID` | 强制更新指定 topic；多个同名候选时使用 |
| `--tags a,b` | 内容 tags；含英文字母的一律小写；`ai生成` 始终自动加入 |
| `--allow-new-tags` | 允许创建 `/tags.json` 里未见过的内容 tags；必须先问用户 |
| `--no-toc` | 不插入 TOC marker |
| `--update-title` | 更新已有主题的标题（会创建 revision） |
| `--update-category` | 更新已有主题的分类（会创建 revision） |
| `--update-meta` | 同时更新标题和分类（会创建 revision） |
| `--dry-run` | 只打印 plan，不写入 |
| `--yes` | 执行 plan；没有它时脚本一律 dry-run |

## 标题与同名冲突

- 标题优先级：`--title` > 第一个 H1 > 文件名（去 `.md`）。
- 标题长度按 Discourse 默认 15–255 字符本地预检；太短时让用户补一个描述性后缀。
- 如果 H1 被用作主题标题，脚本会从 raw 删除 H1；`--title` 与 H1 不同时保留 H1。
- 脚本先用 `.note-push.json` 缓存找 topic；缓存缺失或失效时按标题搜索。
- 搜索没有服务端精确匹配，脚本会客户端做 NFKC + 空白折叠 + casefold 精确比较。
- 单个同名 topic：默认更新。多个同名：脚本列出候选并要求 `--topic-id`，不要自动选。
- 用户拒绝更新：中止；只有用户明确要求新主题时才用 `--new-title`，不要自动加 `(2)`。
- 创建遇到 422 同名错误时，错误里通常带已有 topic 链接；把它给用户，并建议 `--topic-id` 或 `--new-title`。

## Tags

- 每次发布至少带 `ai生成`；不要询问是否加。
- 所有 tag 里的英文字母一律小写。建议 tag、传入 `--tags`、创建新 tag 前都先转小写，例如 `AI写作` → `ai写作`，`RLHF` → `rlhf`；`ai生成` 已经是小写。
- 创建时如果没有 `--tags`，先根据标题和 `##` 章节建议 3–5 个内容 tags，让用户确认后传入。
- 内容 tag 如果不在 `/tags.json` 的可见列表里，必须先问用户是否创建；确认后加 `--allow-new-tags`。
- 更新时没传 `--tags`：保留原 tags，并确保 `ai生成` 存在；传了 `--tags`：替换内容 tags，但 `ai生成` 始终保留。
- 标签权限不足时 Discourse 可能静默忽略；脚本会对比请求与实际 tags 并提示。

## TOC

- 正文有 ≥3 个 `##` 时，脚本在 H1/正文最前面插入：
  `<div data-theme-toc="true"> </div>` 加一个空行。
- marker 必须单独成行且后面有空行，否则会吞掉紧随其后的标题。
- `--no-toc` 关闭自动插入。
- TOC 由 DiscoTOC theme component 在主题导航客户端渲染；API `cooked` 只能看到 marker，不能验证最终视觉。

## 更新与修订

- 默认只更新首帖 raw；标题/分类/标签默认不动。
- 更新前比较规范化 `content_hash`；内容没变时脚本跳过 PUT。
- 内容变化时接受 revision，脚本会报告 `post.version`。
- `--update-title` / `--update-category` / `--update-meta` 会修改主题元数据，**总是**创建 revision；除非用户明确要求，否则不要加。
- 普通用户的编辑窗口：TL0/1 默认 24 小时，TL2+ 默认 30 天；超时后 `too_late_to_edit`，需要 admin/moderator key 或分类设置 `allow_unlimited_owner_edits_on_first_post`。

## 安全预检

脚本在写入前会拒绝：

- 本地图片或相对链接（`images/a.png`、`./note.md` 等）；只允许 `http(s)://`、`//`、`/uploads/`、`#anchor`。
- 明显的凭据（API key、token、私钥、Bearer token、password/api_key 赋值）；发现时先脱敏。

预检失败不会发布任何内容；把具体行号给用户，修好后再重跑。

## 缓存

- 默认写在笔记同目录的 `.note-push.json`（笔记在 `.notes/` 时就是 `.notes/.note-push.json`）。
- 存 `topic_id` / `post_id` / URL / title / category_id / tags / post_version / content_hash / last_pushed_at`。
- 缓存只是加速和更新提示，不是严格身份；缺失、损坏或 topic 被删时会回退标题搜索。
- 缓存不存 API key。若缓存未加入 `.gitignore`，脚本会提醒；不要自动修改 `.gitignore`。

## 参考

- `references/discourse-api.md`：端点、认证、权限、限流、渲染和修订的细节。
- `scripts/note_push.py --help`：完整参数列表。
