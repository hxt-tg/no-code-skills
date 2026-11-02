#!/usr/bin/env python3
"""Normalize spacing around inline MathJax formulas in Markdown notes.

The note-make skill writes notes for a self-hosted Discourse instance. Inline
math such as ``$\\text{A}$`` renders reliably only when it is separated from
adjacent characters by whitespace. This script adds the missing spaces while
leaving code blocks, inline code, link destinations, bare URLs, escaped
dollars, and display math untouched.

Only ``$...$`` pairs whose body contains a LaTeX command (a backslash followed
by letters) are touched. This keeps currency such as ``$5`` and ``$10`` safe.
Simple formulas without a backslash (``$x$``) are left to the writing rule.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

LATEX_COMMAND = re.compile(r"\\[A-Za-z]+")
URL_RE = re.compile(r"(?:https?|ftp)://[^\s<>()]+")
AUTOLINK_RE = re.compile(r"<[^>\n]+>")
EMPHASIS = {"*", "_"}


def is_escaped(text: str, index: int) -> bool:
    """Return True when text[index] is preceded by an odd number of backslashes."""
    count = 0
    j = index - 1
    while j >= 0 and text[j] == "\\":
        count += 1
        j -= 1
    return count % 2 == 1


def code_mask(text: str) -> List[bool]:
    """Mark characters that belong to inline code spans."""
    mask = [False] * len(text)
    i = 0
    while i < len(text):
        if text[i] != "`":
            i += 1
            continue
        j = i
        while j < len(text) and text[j] == "`":
            j += 1
        run = j - i
        k = j
        end: Optional[int] = None
        while k < len(text):
            if text[k] == "`":
                m = k
                while m < len(text) and text[m] == "`":
                    m += 1
                if m - k == run:
                    end = m
                    break
                k = m
            else:
                k += 1
        if end is None:
            i = j
        else:
            for p in range(i, end):
                mask[p] = True
            i = end
    return mask


def protected_ranges(text: str, mask: List[bool]) -> List[Tuple[int, int]]:
    """Find link destinations, bare URLs, and autolinks that must not change."""
    ranges: List[Tuple[int, int]] = []

    # Markdown inline link/image destinations: ]( ... )
    i = 0
    while i < len(text) - 1:
        if (
            text[i] == "]"
            and text[i + 1] == "("
            and not mask[i]
            and not mask[i + 1]
        ):
            depth = 1
            j = i + 2
            while j < len(text) and depth > 0:
                if not mask[j]:
                    if text[j] == "(":
                        depth += 1
                    elif text[j] == ")":
                        depth -= 1
                j += 1
            if depth == 0:
                ranges.append((i + 1, j))
                i = j
                continue
        i += 1

    for pattern in (URL_RE, AUTOLINK_RE):
        for match in pattern.finditer(text):
            ranges.append((match.start(), match.end()))

    return ranges


def is_protected(index: int, ranges: List[Tuple[int, int]]) -> bool:
    return any(start <= index < end for start, end in ranges)


def display_delimiters(
    text: str, mask: List[bool], ranges: List[Tuple[int, int]]
) -> List[int]:
    """Return positions of unescaped ``$$`` delimiters outside code/URLs."""
    positions: List[int] = []
    i = 0
    while i < len(text) - 1:
        if (
            text[i] == "$"
            and text[i + 1] == "$"
            and not mask[i]
            and not mask[i + 1]
            and not is_protected(i, ranges)
            and not is_escaped(text, i)
        ):
            positions.append(i)
            i += 2
        else:
            i += 1
    return positions


def find_formulas(
    text: str, mask: List[bool], ranges: List[Tuple[int, int]]
) -> List[Tuple[int, int]]:
    """Pair sequential ``$`` delimiters and keep formulas with LaTeX commands."""
    pairs: List[Tuple[int, int]] = []
    i = 0
    n = len(text)
    while i < n:
        if (
            text[i] != "$"
            or mask[i]
            or is_protected(i, ranges)
            or is_escaped(text, i)
        ):
            i += 1
            continue
        if i + 1 < n and text[i + 1] == "$":
            i += 2
            continue

        j = i + 1
        close: Optional[int] = None
        while j < n:
            if (
                text[j] == "$"
                and not mask[j]
                and not is_protected(j, ranges)
                and not is_escaped(text, j)
            ):
                if j + 1 < n and text[j + 1] == "$":
                    break
                close = j
                break
            j += 1

        if close is None:
            i += 1
            continue
        pairs.append((i, close))
        i = close + 1

    return [
        (open_index, close_index)
        for open_index, close_index in pairs
        if LATEX_COMMAND.search(text[open_index + 1 : close_index])
    ]


def process_line(text: str, line_number: int, warnings: List[str]) -> str:
    mask = code_mask(text)
    ranges = protected_ranges(text, mask)
    formulas = find_formulas(text, mask, ranges)
    if not formulas:
        return text

    insertions: List[int] = []
    n = len(text)
    warned_emphasis = False
    for open_index, close_index in formulas:
        if open_index > 0 and not text[open_index - 1].isspace():
            if text[open_index - 1] in EMPHASIS:
                if not warned_emphasis:
                    warnings.append(
                        f"line {line_number}: skipped formula next to emphasis delimiter; fix manually"
                    )
                    warned_emphasis = True
            else:
                insertions.append(open_index)
        if close_index + 1 < n and not text[close_index + 1].isspace():
            if text[close_index + 1] in EMPHASIS:
                if not warned_emphasis:
                    warnings.append(
                        f"line {line_number}: skipped formula next to emphasis delimiter; fix manually"
                    )
                    warned_emphasis = True
            else:
                insertions.append(close_index + 1)

    if not insertions:
        return text

    chars = list(text)
    for position in sorted(set(insertions), reverse=True):
        chars.insert(position, " ")
    return "".join(chars)


def process_file(
    path: Path, dry_run: bool = False
) -> Tuple[int, List[int], List[str]]:
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    output: List[str] = []
    changed_lines: List[int] = []
    warnings: List[str] = []
    fence_char: Optional[str] = None
    fence_length = 0
    in_display_math = False

    for line_number, raw_line in enumerate(lines, start=1):
        newline = ""
        line = raw_line
        if line.endswith("\r\n"):
            line, newline = line[:-2], "\r\n"
        elif line.endswith("\n"):
            line, newline = line[:-1], "\n"

        stripped = line.lstrip(" \t")
        indent = len(line) - len(stripped)

        if in_display_math:
            mask = code_mask(line)
            ranges = protected_ranges(line, mask)
            if display_delimiters(line, mask, ranges):
                in_display_math = False
            output.append(raw_line)
            continue

        if fence_char is not None:
            closing = re.match(r"^(```+|~~~+)\s*$", stripped)
            if (
                closing
                and indent <= 3
                and closing.group(1)[0] == fence_char
                and len(closing.group(1)) >= fence_length
            ):
                fence_char = None
            output.append(raw_line)
            continue

        # Indented code is not used by the skill, but a line indented four
        # spaces is code even when it starts with a fence marker.
        if indent >= 4 or line.startswith("\t"):
            output.append(raw_line)
            continue

        opening = re.match(r"^(```+|~~~+)", stripped)
        if opening:
            fence_char = opening.group(1)[0]
            fence_length = len(opening.group(1))
            output.append(raw_line)
            continue

        mask = code_mask(line)
        ranges = protected_ranges(line, mask)
        delimiters = display_delimiters(line, mask, ranges)
        if delimiters:
            if len(delimiters) % 2 == 1:
                in_display_math = True
            output.append(raw_line)
            continue

        new_line = process_line(line, line_number, warnings)
        if new_line != line:
            changed_lines.append(line_number)
        output.append(new_line + newline)

    if changed_lines and not dry_run:
        path.write_text("".join(output), encoding="utf-8")

    return len(changed_lines), changed_lines, warnings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add spaces around inline MathJax formulas in Markdown notes."
    )
    parser.add_argument("files", nargs="+", type=Path, help="Markdown files to process")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report changes without writing",
    )
    args = parser.parse_args()

    exit_code = 0
    for path in args.files:
        try:
            count, changed_lines, warnings = process_file(path, dry_run=args.dry_run)
        except OSError as exc:
            print(f"{path}: {exc}", file=sys.stderr)
            exit_code = 1
            continue

        if count:
            verb = "would change" if args.dry_run else "changed"
            line_list = ", ".join(str(number) for number in changed_lines)
            print(f"{path}: {verb} {count} line(s): {line_list}")
        else:
            print(f"{path}: no changes")

        for warning in warnings:
            print(f"{path}: {warning}", file=sys.stderr)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
