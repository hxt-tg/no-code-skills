#!/usr/bin/env python3
"""Push a Markdown note to a self-hosted Discourse instance.

The script is intentionally dry-run-first: without ``--yes`` it prints the
planned action and exits without writing anything.

Environment:
    DISCOURSE_URL           Base URL, e.g. https://forum.example.com
    DISCOURSE_API_KEY       API key (never printed)
    DISCOURSE_API_USERNAME  API username
    DISCOURSE_CATEGORY      Optional default category id/slug/name

A local ``.env`` file can be loaded with ``--env-file .env``; real environment
variables take precedence over values from the file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

MANDATORY_TAG = "ai生成"
TOC_MARKER = '<div data-theme-toc="true"> </div>'
MAX_POST_LENGTH = 32000
TITLE_MIN = 15
TITLE_MAX = 255
CACHE_VERSION = 1

H1_RE = re.compile(r"^ {0,3}#\s+(.+?)\s*#*\s*$")
H2_RE = re.compile(r"^ {0,3}##\s+")
FENCE_RE = re.compile(r"^(```+|~~~+)")
IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
INLINE_CODE_RE = re.compile(r"(`+)(.*?)\1")

SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "API key (sk-...)"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), "GitHub token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{20,}"), "bearer token"),
    (
        re.compile(
            r"(?i)\b(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*['\"]?([A-Za-z0-9_\-]{16,})"
        ),
        "credential assignment",
    ),
]
SAFE_LINK_PREFIXES = ("http://", "https://", "//", "/uploads/", "#", "mailto:")


class PushError(Exception):
    """A user-facing note-push error."""


def parse_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_config(args: argparse.Namespace) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if args.env_file:
        env_path = Path(args.env_file)
        if not env_path.exists():
            raise PushError(f"env file not found: {env_path}")
        values.update(parse_env_file(env_path))

    for key in (
        "DISCOURSE_URL",
        "DISCOURSE_API_KEY",
        "DISCOURSE_API_USERNAME",
        "DISCOURSE_CATEGORY",
    ):
        if os.environ.get(key):
            values[key] = os.environ[key]

    base = (args.instance or values.get("DISCOURSE_URL", "")).rstrip("/")
    api_key = values.get("DISCOURSE_API_KEY", "")
    username = values.get("DISCOURSE_API_USERNAME", "")
    category = args.category or values.get("DISCOURSE_CATEGORY", "")
    if not base:
        raise PushError("missing DISCOURSE_URL (or pass --instance)")
    if not api_key:
        raise PushError("missing DISCOURSE_API_KEY")
    if not username:
        raise PushError("missing DISCOURSE_API_USERNAME")
    return {
        "base": base,
        "api_key": api_key,
        "username": username,
        "category": category,
    }


def normalize_title(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).strip()
    return re.sub(r"\s+", " ", text).casefold()


def clean_title(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def normalize_raw(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(" " if ch.isspace() and ch != "\n" else ch for ch in text)
    return text.rstrip()


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_raw(text).encode("utf-8")).hexdigest()


def iter_content_lines(text: str):
    """Yield (line_number, line, is_code) outside fenced/indented code."""
    fence_char: Optional[str] = None
    fence_len = 0
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip(" \t")
        indent = len(line) - len(stripped)
        if fence_char is not None:
            closing = re.match(r"^(```+|~~~+)\s*$", stripped)
            if (
                closing
                and indent <= 3
                and closing.group(1)[0] == fence_char
                and len(closing.group(1)) >= fence_len
            ):
                fence_char = None
            yield number, line, True
            continue
        opening = re.match(r"^(```+|~~~+)", stripped)
        if opening and indent <= 3:
            fence_char = opening.group(1)[0]
            fence_len = len(opening.group(1))
            yield number, line, True
            continue
        if indent >= 4 or line.startswith("\t"):
            yield number, line, True
            continue
        yield number, line, False


def find_first_h1(text: str) -> Tuple[Optional[int], Optional[str]]:
    for number, line, is_code in iter_content_lines(text):
        if is_code:
            continue
        match = H1_RE.match(line)
        if match:
            return number, match.group(1).strip()
    return None, None


def remove_line_and_following_blank(text: str, line_number: int) -> str:
    lines = text.splitlines(keepends=True)
    index = line_number - 1
    if not (0 <= index < len(lines)):
        return text
    del lines[index]
    if index < len(lines) and lines[index].strip() == "":
        del lines[index]
    return "".join(lines)


def count_h2(text: str) -> int:
    return sum(
        1
        for _, line, is_code in iter_content_lines(text)
        if not is_code and H2_RE.match(line)
    )


def apply_toc(text: str, no_toc: bool) -> Tuple[str, bool]:
    has_marker = TOC_MARKER in text
    h2_count = count_h2(text)
    if no_toc:
        if text.lstrip().startswith(TOC_MARKER):
            rest = text.lstrip()[len(TOC_MARKER) :].lstrip("\n")
            return rest, False
        return text, has_marker
    if text.lstrip().startswith(TOC_MARKER):
        rest = text.lstrip()[len(TOC_MARKER) :].lstrip("\n")
        return TOC_MARKER + "\n\n" + rest, True
    if h2_count >= 3 and not has_marker:
        return TOC_MARKER + "\n\n" + text.lstrip("\n"), True
    return text, has_marker


def strip_link_target(target: str) -> str:
    target = target.strip()
    if target.startswith("<") and target.endswith(">"):
        return target[1:-1].strip()
    return target.split()[0] if target.split() else target


def check_links(text: str) -> List[str]:
    errors: List[str] = []
    for number, line, is_code in iter_content_lines(text):
        if is_code:
            continue
        cleaned = INLINE_CODE_RE.sub("", line)
        for kind, pattern in (("image", IMAGE_RE), ("link", LINK_RE)):
            for match in pattern.finditer(cleaned):
                target = strip_link_target(match.group(1))
                if not target:
                    continue
                if target.startswith(SAFE_LINK_PREFIXES):
                    continue
                errors.append(
                    f"line {number}: {kind} target must be an absolute URL or /uploads/ path: {target[:80]}"
                )
    return errors


def check_secrets(text: str) -> List[str]:
    errors: List[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for pattern, label in SECRET_PATTERNS:
            for match in pattern.finditer(line):
                value = match.group(0)
                if any(
                    marker in value.upper()
                    for marker in ("REDACTED", "PLACEHOLDER", "EXAMPLE", "YOUR_", "XXXX")
                ):
                    continue
                errors.append(f"line {number}: possible {label}; redact it before publishing")
    return errors


def make_request(
    base: str,
    api_key: str,
    username: str,
    method: str,
    path: str,
    payload: Optional[dict] = None,
    query: Optional[dict] = None,
    timeout: int = 60,
) -> Tuple[Optional[int], Dict[str, str], str]:
    url = base + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = None
    headers = {
        "Api-Key": api_key,
        "Api-Username": username,
        "Accept": "application/json",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
            return response.status, dict(response.headers), body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        return exc.code, dict(exc.headers), body
    except Exception as exc:
        return None, {}, str(exc)


def api(
    config: Dict[str, str],
    method: str,
    path: str,
    payload: Optional[dict] = None,
    query: Optional[dict] = None,
    timeout: int = 60,
) -> Tuple[Optional[int], str]:
    status, headers, body = make_request(
        config["base"],
        config["api_key"],
        config["username"],
        method,
        path,
        payload,
        query,
        timeout,
    )
    if status == 429:
        wait = headers.get("Retry-After")
        if not wait:
            try:
                wait = json.loads(body).get("extras", {}).get("wait_seconds")
            except Exception:
                wait = None
        try:
            delay = min(float(wait), 5.0) if wait else 2.0
        except Exception:
            delay = 2.0
        print(f"rate limited; retrying once after {delay:.1f}s")
        time.sleep(delay)
        status, headers, body = make_request(
            config["base"],
            config["api_key"],
            config["username"],
            method,
            path,
            payload,
            query,
            timeout,
        )
    return status, body


def parse_json(body: str):
    try:
        return json.loads(body)
    except Exception:
        return None


def walk_categories(categories):
    for category in categories:
        yield category
        yield from walk_categories(category.get("subcategory_list", []) or [])


def resolve_category(config: Dict[str, str], value: str) -> Tuple[int, str]:
    if value.isdigit():
        return int(value), f"id:{value}"
    status, body = api(config, "GET", "/categories.json")
    if status != 200:
        raise PushError(
            f"cannot list categories (HTTP {status}); pass a numeric category id or grant categories:list"
        )
    data = parse_json(body) or {}
    categories = list(
        walk_categories(data.get("category_list", {}).get("categories", []))
    )
    target = value.casefold()
    matches = [
        category
        for category in categories
        if category.get("slug", "").casefold() == target
        or category.get("name", "").casefold() == target
    ]
    if len(matches) == 1:
        category = matches[0]
        return int(category["id"]), f"{category.get('name')} ({category.get('slug')})"
    if len(matches) > 1:
        candidates = ", ".join(
            f"{c.get('id')}:{c.get('slug')}" for c in matches
        )
        raise PushError(f"category is ambiguous: {candidates}; use a numeric id")
    available = ", ".join(
        f"{c.get('id')}:{c.get('slug')}" for c in categories[:20]
    )
    raise PushError(f"category not found: {value}; available: {available}")


def visible_tags(config: Dict[str, str]) -> Optional[List[str]]:
    status, body = api(config, "GET", "/tags.json")
    if status != 200:
        return None
    data = parse_json(body) or {}
    return [tag.get("name", "") for tag in data.get("tags", []) if tag.get("name")]


def split_tags(value: Optional[str]) -> List[str]:
    if not value:
        return []
    tags: List[str] = []
    seen = set()
    for raw in value.split(","):
        tag = raw.strip().lower()
        if tag and tag.casefold() not in seen:
            tags.append(tag)
            seen.add(tag.casefold())
    return tags


def final_tags(content_tags: List[str]) -> List[str]:
    tags = [MANDATORY_TAG]
    seen = {MANDATORY_TAG.casefold()}
    for tag in content_tags:
        if tag.casefold() not in seen:
            tags.append(tag)
            seen.add(tag.casefold())
    return tags


def check_new_tags(
    config: Dict[str, str],
    content_tags: List[str],
    allow_new_tags: bool,
) -> Tuple[List[str], Optional[List[str]]]:
    content_tags = [
        tag for tag in content_tags if tag.casefold() != MANDATORY_TAG.casefold()
    ]
    if not content_tags:
        return [], None
    visible = visible_tags(config)
    if visible is None:
        if allow_new_tags:
            return [], None
        raise PushError(
            "cannot verify tag existence (tags:list unavailable); confirm tags with the user and rerun with --allow-new-tags"
        )
    visible_names = {name.casefold() for name in visible}
    unseen = [tag for tag in content_tags if tag.casefold() not in visible_names]
    if unseen and not allow_new_tags:
        raise PushError(
            "these tags are not in the visible tag list: "
            + ", ".join(unseen)
            + "; confirm creation with the user and rerun with --allow-new-tags"
        )
    return unseen, visible


def cache_path_for(note: Path) -> Path:
    return note.parent / ".note-push.json"


def load_cache(path: Path) -> dict:
    if not path.exists():
        return {"version": CACHE_VERSION, "instances": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        print(f"warning: ignoring unreadable cache: {path}")
        return {"version": CACHE_VERSION, "instances": {}}
    if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
        print(f"warning: ignoring unsupported cache version: {path}")
        return {"version": CACHE_VERSION, "instances": {}}
    data.setdefault("instances", {})
    return data


def cache_entry(data: dict, base: str, note: Path) -> Optional[dict]:
    return (
        data.get("instances", {})
        .get(base, {})
        .get(str(note.resolve()))
    )


def save_cache(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError as exc:
        print(f"warning: could not write cache {path}: {exc}")


def warn_if_not_ignored(path: Path) -> None:
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", str(path)],
            cwd=str(path.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return
    if result.returncode == 1:
        print(
            f"warning: {path.name} is not gitignored; add it or .notes/ to .gitignore"
        )


def fetch_topic(config: Dict[str, str], topic_id: int) -> Optional[dict]:
    status, body = api(
        config,
        "GET",
        f"/t/{topic_id}.json",
        query={"include_raw": "true"},
    )
    if status != 200:
        if status != 404:
            print(f"warning: cannot read topic {topic_id} (HTTP {status})")
        return None
    return parse_json(body)


def first_post(topic: dict) -> dict:
    posts = topic.get("post_stream", {}).get("posts") or []
    return posts[0] if posts else {}


def search_exact(
    config: Dict[str, str], title: str
) -> Tuple[List[dict], List[dict], Optional[int]]:
    target = normalize_title(title)
    last_status: Optional[int] = None
    for query in (f"{title} in:title", title):
        status, body = api(
            config,
            "GET",
            "/search.json",
            query={"q": query, "page": 1},
        )
        last_status = status
        if status != 200:
            if status == 403:
                raise PushError(
                    "search failed with 403; check search:show scope and API user permissions"
                )
            continue
        data = parse_json(body) or {}
        topics = data.get("topics", []) or []
        posts = data.get("posts", []) or []
        matches = [
            topic for topic in topics if normalize_title(topic.get("title", "")) == target
        ]
        if matches:
            return matches, posts, status
        time.sleep(1)
    return [], [], last_status


def extract_existing_topic_id(body: str) -> Optional[int]:
    match = re.search(r"/t/(?:[^/\"'\s]+/)?(\d+)", body)
    return int(match.group(1)) if match else None


def print_plan(plan: dict) -> None:
    print("note-push plan")
    for key in (
        "note",
        "instance",
        "title",
        "category",
        "tags",
        "toc",
        "action",
        "content_changed",
        "tags_changed",
        "title_changed",
        "category_changed",
        "revision_risk",
        "duplicate_candidates",
        "unseen_tags",
        "cache",
        "content_chars",
    ):
        if key in plan:
            print(f"  {key}: {plan[key]}")
    if plan.get("dry_run"):
        print("dry-run only; rerun with --yes to apply.")


def topic_url(base: str, topic_id: int) -> str:
    return f"{base}/t/{topic_id}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Push a Markdown note to a self-hosted Discourse instance."
    )
    parser.add_argument("note", type=Path, help="Markdown note to publish")
    parser.add_argument("--instance", help="Discourse base URL (overrides DISCOURSE_URL)")
    parser.add_argument("--env-file", help="load KEY=VALUE values from this file")
    parser.add_argument("--category", help="category id, slug, or name")
    parser.add_argument("--title", help="override the topic title")
    parser.add_argument("--new-title", help="force create a new topic with this title")
    parser.add_argument("--topic-id", type=int, help="force update this topic id")
    parser.add_argument("--tags", help="comma-separated content tags, lowercased (ai生成 is always added)")
    parser.add_argument(
        "--allow-new-tags",
        action="store_true",
        help="allow content tags not visible in /tags.json (ask the user first)",
    )
    parser.add_argument("--no-toc", action="store_true", help="do not add the TOC marker")
    parser.add_argument("--update-title", action="store_true", help="update the topic title")
    parser.add_argument("--update-category", action="store_true", help="update the topic category")
    parser.add_argument(
        "--update-meta",
        action="store_true",
        help="update title and category if changed (creates a revision)",
    )
    parser.add_argument("--dry-run", action="store_true", help="print the plan only")
    parser.add_argument("--yes", action="store_true", help="apply the plan")
    args = parser.parse_args()

    if args.yes and args.dry_run:
        print("warning: --dry-run wins; no changes will be written")
    apply = args.yes and not args.dry_run

    note = args.note
    if not note.exists():
        raise PushError(f"note not found: {note}")
    try:
        original = note.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PushError(f"note is not valid UTF-8: {exc}")

    config = load_config(args)
    base = config["base"]

    h1_line, h1_text = find_first_h1(original)
    title_source = args.new_title or args.title or h1_text or note.stem
    title = clean_title(title_source)
    if not title:
        raise PushError("title is empty; pass --title or add an H1")
    if not (TITLE_MIN <= len(title) <= TITLE_MAX):
        raise PushError(
            f"title must be {TITLE_MIN}-{TITLE_MAX} characters; got {len(title)}. "
            "Pass a more descriptive --title if needed."
        )

    raw = original
    if h1_line is not None and normalize_title(h1_text) == normalize_title(title):
        raw = remove_line_and_following_blank(raw, h1_line)
    raw, toc_present = apply_toc(raw, args.no_toc)

    if len(raw) > MAX_POST_LENGTH:
        raise PushError(
            f"raw body is {len(raw)} characters; max_post_length is {MAX_POST_LENGTH}"
        )

    link_errors = check_links(raw)
    secret_errors = check_secrets(raw)
    if link_errors or secret_errors:
        for error in link_errors + secret_errors:
            print(f"error: {error}")
        raise PushError("note failed preflight checks; nothing was published")

    category_id: Optional[int] = None
    category_label = ""
    if config["category"]:
        category_id, category_label = resolve_category(config, config["category"])
    elif args.new_title:
        raise PushError("category is required to create a topic (--category or DISCOURSE_CATEGORY)")

    cache_path = cache_path_for(note)
    cache = load_cache(cache_path)
    cached = cache_entry(cache, base, note) if not args.new_title else None
    topic: Optional[dict] = None
    action = "create"
    target_topic_id: Optional[int] = None
    target_post_id: Optional[int] = None

    if args.new_title:
        action = "create"
        title = clean_title(args.new_title)
    elif args.topic_id:
        action = "update"
        target_topic_id = args.topic_id
        topic = fetch_topic(config, target_topic_id)
        if topic is None:
            raise PushError(f"cannot read topic {target_topic_id}")
    elif cached and cached.get("topic_id"):
        target_topic_id = int(cached["topic_id"])
        topic = fetch_topic(config, target_topic_id)
        if topic is None:
            print(f"cache topic {target_topic_id} is unavailable; falling back to title search")
            target_topic_id = None
            cached = None
        else:
            action = "update"
    if action == "create" and target_topic_id is None and not args.new_title:
        matches, posts, search_status = search_exact(config, title)
        if not matches and search_status != 200:
            raise PushError(
                f"title search failed (HTTP {search_status}); refusing to create without a duplicate check"
            )
        if len(matches) > 1:
            print("multiple topics have this title; choose one and rerun with --topic-id:")
            for item in matches:
                print(
                    f"  id={item.get('id')} title={item.get('title')} "
                    f"category={item.get('category_id')} created={item.get('created_at')}"
                )
            raise PushError("ambiguous title")
        if len(matches) == 1:
            action = "update"
            target_topic_id = int(matches[0]["id"])
            target_post_id = next(
                (
                    int(post["id"])
                    for post in posts
                    if post.get("topic_id") == target_topic_id
                ),
                None,
            )
            topic = fetch_topic(config, target_topic_id)

    if action == "update":
        if target_topic_id is None:
            raise PushError("update target is missing")
        if topic is None:
            topic = fetch_topic(config, target_topic_id)
        if topic is not None:
            first = first_post(topic)
            target_post_id = target_post_id or first.get("id")
            current_title = topic.get("title")
            current_category = topic.get("category_id")
            current_tags = [tag.get("name") for tag in topic.get("tags", []) if tag.get("name")]
        else:
            current_title = cached.get("title") if cached else None
            current_category = cached.get("category_id") if cached else None
            current_tags = cached.get("tags", []) if cached else []
        if not target_post_id and cached:
            target_post_id = cached.get("post_id")
        if not target_post_id:
            raise PushError(f"cannot determine first post id for topic {target_topic_id}")
    else:
        current_title = None
        current_category = None
        current_tags = []

    requested_content_tags = split_tags(args.tags)
    if action == "create":
        unseen, visible = check_new_tags(config, requested_content_tags, args.allow_new_tags)
        tags = final_tags(requested_content_tags)
        current_tags_for_plan = []
    else:
        if args.tags:
            unseen, visible = check_new_tags(config, requested_content_tags, args.allow_new_tags)
            tags = final_tags(requested_content_tags)
        else:
            unseen, visible = [], None
            tags = final_tags([tag for tag in current_tags if tag.casefold() != MANDATORY_TAG.casefold()])
        current_tags_for_plan = current_tags

    if category_id is None and action == "update":
        if current_category:
            category_id = int(current_category)
            category_label = f"current ({category_id})"
        elif not (args.update_category or args.update_meta):
            category_id = None
            category_label = "unchanged (not configured)"

    if action == "create" and category_id is None:
        raise PushError(
            "category is required to create a topic (--category or DISCOURSE_CATEGORY)"
        )
    if (
        action == "update"
        and (args.update_category or args.update_meta)
        and category_id is None
    ):
        raise PushError("--update-category requires a resolved category")

    current_raw = first_post(topic).get("raw") if topic else None
    if action == "update":
        if current_raw is not None:
            content_changed = content_hash(current_raw) != content_hash(raw)
        elif cached and cached.get("content_hash"):
            content_changed = cached["content_hash"] != content_hash(raw)
        else:
            content_changed = True
        tags_changed = {t.casefold() for t in current_tags} != {t.casefold() for t in tags}
        title_changed = bool(
            (args.update_title or args.update_meta)
            and (current_title is None or normalize_title(current_title) != normalize_title(title))
        )
        category_changed = bool(
            (args.update_category or args.update_meta)
            and category_id is not None
            and current_category is not None
            and int(category_id) != int(current_category)
        )
        action_label = f"update topic {target_topic_id} (post {target_post_id})"
        revision_risk = (
            "yes: content/metadata change may create a revision; response version will be reported"
            if content_changed or tags_changed or title_changed or category_changed
            else "no changes; writes will be skipped"
        )
    else:
        content_changed = True
        tags_changed = True
        title_changed = False
        category_changed = False
        action_label = "create new topic"
        revision_risk = "new topic"

    plan = {
        "note": str(note),
        "instance": base,
        "title": title,
        "category": category_label or (str(category_id) if category_id is not None else "missing"),
        "tags": ", ".join(tags),
        "toc": "present" if toc_present else "not present",
        "action": action_label,
        "content_changed": content_changed,
        "tags_changed": tags_changed,
        "title_changed": title_changed,
        "category_changed": category_changed,
        "revision_risk": revision_risk,
        "cache": str(cache_path),
        "content_chars": len(raw),
        "dry_run": not apply,
    }
    if unseen:
        plan["unseen_tags"] = ", ".join(unseen)
    if action == "create" and not requested_content_tags:
        plan["tags"] = f"{MANDATORY_TAG} (no content tags supplied; suggest 3-5 and rerun with --tags)"
    print_plan(plan)
    if not apply:
        return 0

    if action == "update":
        if category_changed and category_id is None:
            raise PushError("--update-category requires a resolved category")
        metadata_payload = {}
        if tags_changed:
            metadata_payload["tags"] = tags
        if title_changed:
            metadata_payload["title"] = title
        if category_changed and category_id is not None:
            metadata_payload["category_id"] = category_id

        if content_changed:
            status, body = api(
                config,
                "PUT",
                f"/posts/{target_post_id}.json",
                {"post": {"raw": raw}},
            )
            print(f"content update: HTTP {status}")
            if status != 200:
                print(body[:800])
                if "too_late_to_edit" in body:
                    print(
                        "hint: the edit window has expired; use an admin/moderator key "
                        "or enable allow_unlimited_owner_edits_on_first_post"
                    )
                raise PushError("content update failed")
            post_data = (parse_json(body) or {}).get("post", {})
            print(f"content version: {post_data.get('version', 'unknown')}")
        else:
            print("content unchanged; skipped PUT")

        if metadata_payload:
            status, body = api(
                config,
                "PUT",
                f"/t/{target_topic_id}.json",
                metadata_payload,
            )
            print(f"metadata update ({', '.join(metadata_payload)}): HTTP {status}")
            if status != 200:
                print(body[:800])
                raise PushError("metadata update failed (this may create a revision)")
        else:
            print("metadata unchanged; skipped PUT")

        refreshed = fetch_topic(config, target_topic_id or 0)
        if refreshed:
            first = first_post(refreshed)
            tags = [tag.get("name") for tag in refreshed.get("tags", []) if tag.get("name")]
            title = refreshed.get("title", title)
            category_id = refreshed.get("category_id", category_id)
            post_version = first.get("version")
            post_url = first.get("post_url") or f"/t/{target_topic_id}"
            raw_after = first.get("raw", raw)
        else:
            post_version = None
            post_url = f"/t/{target_topic_id}"
            raw_after = raw
        entry = {
            "topic_id": target_topic_id,
            "post_id": target_post_id,
            "url": post_url if str(post_url).startswith("http") else base + str(post_url),
            "title": title,
            "category_id": category_id,
            "tags": tags,
            "post_version": post_version,
            "content_hash": content_hash(raw_after),
            "last_pushed_at": datetime.now(timezone.utc).isoformat(),
        }
        cache.setdefault("instances", {}).setdefault(base, {})[str(note.resolve())] = entry
        save_cache(cache_path, cache)
        warn_if_not_ignored(cache_path)
        print(f"updated: {entry['url']}")
        print(f"version: {post_version if post_version is not None else 'unknown'}")
        return 0

    if category_id is None:
        raise PushError("category is required to create a topic")
    status, body = api(
        config,
        "POST",
        "/posts.json",
        {"title": title, "raw": raw, "category": category_id, "tags": tags},
    )
    print(f"create: HTTP {status}")
    if status != 200:
        print(body[:1000])
        existing = extract_existing_topic_id(body) if status == 422 else None
        if existing:
            print(f"existing topic with this title: {topic_url(base, existing)}")
            print("rerun with --topic-id to update it, or --new-title to create a different title")
        raise PushError("create failed")
    data = parse_json(body) or {}
    if data.get("action") == "enqueued" or not data.get("topic_id"):
        print("create was accepted for moderation; no topic was published yet")
        return 1
    topic_id = int(data["topic_id"])
    post_id = int(data.get("id"))
    post_url = data.get("post_url") or f"/t/{topic_id}"
    post_version = data.get("version")
    print(f"created: {post_url if str(post_url).startswith('http') else base + str(post_url)}")
    print(f"topic_id: {topic_id}")
    print(f"post_id: {post_id}")
    if post_version is not None:
        print(f"version: {post_version}")

    refreshed = fetch_topic(config, topic_id)
    actual_tags = tags
    if refreshed:
        actual_tags = [tag.get("name") for tag in refreshed.get("tags", []) if tag.get("name")]
        if {t.casefold() for t in actual_tags} != {t.casefold() for t in tags}:
            print(f"warning: requested tags were not all applied; actual tags: {', '.join(actual_tags)}")
    entry = {
        "topic_id": topic_id,
        "post_id": post_id,
        "url": post_url if str(post_url).startswith("http") else base + str(post_url),
        "title": title,
        "category_id": category_id,
        "tags": actual_tags,
        "post_version": post_version,
        "content_hash": content_hash(raw),
        "last_pushed_at": datetime.now(timezone.utc).isoformat(),
    }
    cache.setdefault("instances", {}).setdefault(base, {})[str(note.resolve())] = entry
    save_cache(cache_path, cache)
    warn_if_not_ignored(cache_path)
    print(f"cache: {cache_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PushError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
