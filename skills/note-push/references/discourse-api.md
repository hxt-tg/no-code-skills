# Discourse REST API 参考

本文件是 `note-push` 的实现依据。Discourse 的行为会随版本和站点设置变化；发布前用目标实例的 `/about.json` 和实际请求确认。

## 认证

```http
Api-Key: <api-key>
Api-Username: <username>
Accept: application/json
Content-Type: application/json
```

- 只使用 HTTP header；query/body 里的 `api_key` / `api_username` 自 2020-04 起不再支持。
- 推荐 Single User + Granular key，绑定专用非管理员 bot 用户。
- scope 只能收窄用户权限，不能放大；key 的真实权限 = 被代表用户的权限 ∩ scope。
- 建主题要求目标分类对用户开放 **Create / Reply / See**（API 的 `full`）；只给 Reply 不能建主题。
- `topics:write` 的参数限制不要包含 `topic_id`，否则创建新主题会匹配失败。
- 常见 scopes：`topics:write`、`topics:read`、`posts:edit`、`search:show`、`categories:list`、`tags:list`；更新标题/分类/标签需要 `topics:update`。

## 创建主题与首帖

```http
POST /posts.json
```

```json
{
  "title": "Topic title",
  "raw": "Markdown body",
  "category": 10,
  "tags": ["ai生成", "database"]
}
```

- `title`、`raw` 必需；`category` 在 3.0+ 默认实际必填，必须是数字分类 ID，不接受 slug。
- `tags` 可选；字符串数组在部分版本已弃用但通常仍接受；如失败再尝试 `{id,name}` 对象数组。
- 成功返回 **HTTP 200**，解包后的首帖 JSON：`id`、`topic_id`、`post_number`、`cooked`、`raw`、`topic_slug`、`post_url`。
- HTTP 200 也可能是 `action: "enqueued"`（进入审核队列，没有 `topic_id`）；这不算已发布。
- 同名标题默认被拒绝（422，错误里通常带已有主题链接）；不要把它当成功。

## 按标题查找

```http
GET /search.json?q=<title> in:title&page=1
```

- 没有服务端精确标题查询；`in:title` 是全文/词干/末词前缀搜索。
- 必须客户端按 `topics[].title` 做 NFKC + 空白折叠 + casefold 精确比较。
- 返回顶层 `topics[]` 和 `posts[]`；`posts[].topic_id` 对应主题，`in:title` 下 `posts[].id` 通常是首帖 ID。
- 最多 10 页、默认每页 50 条；CJK、短词、停用词可能漏召回。
- 搜索按 API 用户权限过滤；看不到的私密分类不会出现。
- 搜索失败时不要盲目创建；先解决权限或限流。

## 更新首帖

```http
PUT /posts/{post_id}.json
```

```json
{"post": {"raw": "New Markdown body"}}
```

- 成功返回 `{"post": {...}}`，`cooked` 同步重新生成。
- 内容相同重复 PUT 是 200 no-op，版本不变。
- 普通用户受编辑窗口限制：TL0/1 默认 24 小时，TL2+ 默认 30 天；超时返回 422 `too_late_to_edit`。
- 分类设置 `allow_unlimited_owner_edits_on_first_post=true` 可让首帖正文长期可编辑，但不影响标题/分类/标签。
- 相同内容判断要对 raw 做与 Discourse 一致的空白规范化（至少 `rstrip` + Unicode 空白替换）。

## 更新标题 / 分类 / 标签

```http
PUT /t/{topic_id}.json
```

```json
{"title": "New title", "category_id": 10, "tags": ["ai生成", "database"]}
```

- 参数在**顶层**，不是 `{"topic": {...}}`。
- 只传需要改的字段；默认不要动标题/分类/标签。
- 标题/分类/标签变化**总是**创建 post revision，并消耗编辑额度。
- 标签权限不足时可能静默忽略；应对比响应/实际 tags。

## 修订与版本

| 场景 | 行为 |
| --- | --- |
| raw 改动在 `editing_grace_period`（默认 300 秒）内，且 diff 小于阈值（默认 100 字符，TL2+/staff 400） | 折叠进当前 version，不新建 revision |
| 超过 grace period、diff 较大、不同编辑者、被 flag，或指定 `edit_reason` | version + 1，创建 PostRevision |
| 仅标题/分类/标签变化 | 总是创建新 version |
| 相同 raw 重复 PUT | no-op，不增加 version |

- `PUT /posts/{id}.json` 响应的 `post.version` 可用于确认是否递增。
- `GET /posts/{id}/revisions/{number}.json` 需要查看编辑历史的权限。
- API 不能抑制修订；只能通过“无变化跳过”和“默认不改元数据”减少噪音。

## 长度、编码与限流

- 标题：非 admin 默认 15–255 字符。
- 正文：`max_post_length` 默认 32000 字符，站点可调到 150000；超限 422，不自动截断。
- 推荐 UTF-8 JSON；NUL 会被移除，部分 Unicode 空白会归一化，raw 末尾空白会 `rstrip`。
- 常见错误：400（分类/参数无效）、403（认证、scope 或分类权限）、422（校验、同名、编辑窗口）、429（限流，优先读 `Retry-After` / `extras.wait_seconds`）、503（只读/归档）。
- 默认限流示例：建主题 1 次/15 秒 + 20/天；编辑 30/天；搜索 30/分钟；Admin API key 全站共享 60/分钟。以目标实例为准。

## Tags

- `GET /tags.json` 返回 top tags；需要 `tags:list`。未使用或不在列表里的 tag 可能看不到。
- `/tags/filter/search.json` 和 `/tag/{tag}.json` 可能需要额外 scope；没有时不要把“查不到”当成“一定不存在”。
- 创建/更新 topic 时通过 `tags` 数组设置；缺少 tag 权限可能静默忽略。
- `ai生成` 是 `note-push` 的强制 tag；内容 tags 缺失时先让用户确认再创建。

## TOC（DiscoTOC）

- 在正文最前面插入裸 marker，并确保后面有空行：

```html
<div data-theme-toc="true"> </div>
```

- marker 必须是精确字符串 `data-theme-toc="true"`；单引号/多余空格会被规范化，但不要依赖。
- marker 不要求首行，但建议放在顶部；放 blockquote/代码块/段落中间可能失效。
- DiscoTOC 是 theme component，不是 core plugin；必须在读者使用的 theme 中安装/应用。
- TOC 在客户端从 `cooked` 的 h1–h5 生成，渲染在主题导航；`cooked` 本身不含 TOC HTML。
- `TOC_min_heading` 默认 3，统计 h1–h5；`note-push` 用 ≥3 个 `##` 作为保守触发。

## Markdown 渲染

- 核心支持：标题、列表、引用、围栏代码块、GFM 表格、行内代码、粗体/斜体、删除线、链接、图片。
- 数学公式需要 `discourse_math_enabled=true`（默认关闭）；`cooked` 里是 `<span class="math">` / `<div class="math">` 的 TeX。
- Mermaid 需要官方 theme component；否则只是代码块。
- 原始 HTML 经过 js-xss allowlist；不要依赖任意 HTML。
- 相对链接/本地图片会被 sanitizer 丢弃；图片需要先上传，`upload://` 需要 upload 记录。
- 表格、Math、Mermaid、TOC 的最终视觉需要浏览器确认，API `cooked` 只能验证占位 HTML。

## 官方来源

- https://docs.discourse.org/
- https://meta.discourse.org/t/discourse-rest-api-documentation/22706
- https://meta.discourse.org/t/create-and-configure-an-api-key/230124
- https://meta.discourse.org/t/use-scoped-api-keys/164998
- https://github.com/discourse/DiscoTOC
