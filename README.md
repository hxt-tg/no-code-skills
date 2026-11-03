# no-code-skills

Skills that aren't about coding — for everyday thinking, reflection, notes, and publishing.

## Skills

### brain-dump

A guided 15-minute brain dump. It helps you vent, sort what you dumped, rate how much pressure or pull each item has, choose one small next step, and archive the session to local Markdown files.

Use it when you feel overwhelmed, want to clear your head, or want to reflect on the day. The instructions are written in Chinese; responses follow the user's main language.

### note-make

Organize the current conversation into a private Markdown note. The format is checked against a self-hosted Discourse compatibility subset, but the note is written for you, not for an external audience. It chooses one of three structures — command/operation summary, information/research summary, or concept learning — and writes to `.notes/` in the current project by default. Override the directory with `NOTE_MAKE_DIR` or a path in the request.

Credentials are redacted while the note is written; other sensitive details are kept unless you ask to remove them. Re-running on the same topic updates the existing note in place instead of appending an update section. Use `/note-make quiz` (or say "考考我" / "quiz me" in the request) to add an optional learning quiz. Inside tmux, it opens a `note-preview` window: uses `mdcat --ansi` when available and falls back to `less` when `mdcat` is missing; outside tmux it skips. `mdcat` is the only recommended previewer.

Use `/note-make`, or say "把刚才的内容整理成笔记" / "turn this conversation into a note".

### note-push

Push a local Markdown note to a self-hosted Discourse instance. It defaults to a dry-run, creates a new topic or updates an existing topic after title confirmation, always adds the `ai生成` tag plus content tags, inserts a TOC when the note has at least three `##` sections, and keeps a `.note-push.json` cache next to the note. Credentials come from `DISCOURSE_URL`, `DISCOURSE_API_KEY`, and `DISCOURSE_API_USERNAME`; a local `.env` can be loaded with `--env-file .env`.

Use `/note-push path/to/note.md`, or say "把这篇笔记推到 Discourse" / "push this note to Discourse".

## Install

### Claude Code (plugin marketplace)

```text
/plugin marketplace add hxt-tg/no-code-skills
/plugin install no-code-skills@no-code-skills-marketplace
```

### Claude Code (manual)

```bash
git clone https://github.com/hxt-tg/no-code-skills.git
cd no-code-skills
mkdir -p ~/.claude/skills
for skill in skills/*; do
  ln -sfn "$PWD/$skill" ~/.claude/skills/$(basename "$skill")
done
```

### Codex / OpenCode / Pi

```bash
npx skills add hxt-tg/no-code-skills -g

# Or copy from the repository root into the shared agents directory
mkdir -p ~/.agents/skills
cp -R skills/* ~/.agents/skills/
```

## Usage

### brain-dump

Say "我想把脑子里的东西理一理", "brain dump", or "倒一下脑子".

By default, archives are written to:

- `~/Documents/BrainDump/history/DUMP-YYYY-MM-DD.md`
- `~/Documents/BrainDump/FUTURE-PLAN.md`

Set `BRAIN_DUMP_DIR` to change the default directory. A path specified in the conversation takes precedence.

### note-make

Use `/note-make`, or say "把刚才的内容整理成笔记" / "turn this conversation into a note".

Notes are private Markdown files written to `.notes/` in the current project by default. Set `NOTE_MAKE_DIR` to change the default directory; a path specified in the conversation takes precedence. Same-topic notes are updated in place. Credentials are redacted while writing; other details are kept unless you ask to remove them.

To take the optional learning quiz, use `/note-make quiz` or include "考考我" / "quiz me" in the request. Inline MathJax formulas get surrounding spaces automatically when `python3` is available. When run inside tmux, note-make opens a `note-preview` window: `mdcat --ansi` when available, otherwise `less`; outside tmux it skips. `mdcat` is the only recommended previewer.

### note-push

Use `/note-push path/to/note.md`, or say "把这篇笔记推到 Discourse" / "push this note to Discourse".

The skill runs a dry-run first. Confirm the plan, then it creates or updates the topic. Same-title notes are matched with a client-side exact title check; multiple matches require choosing a topic. `ai生成` is mandatory; content tags are suggested and confirmed, and unseen tags require confirmation. TOC is inserted for notes with at least three `##` sections. The cache lives at `.notes/.note-push.json` (or next to the note) and is not a strict source of truth.

Credentials come from `DISCOURSE_URL`, `DISCOURSE_API_KEY`, `DISCOURSE_API_USERNAME`, or `--env-file .env`. Never commit the key. The skill refuses local images, relative links, and likely credentials in the note.

## Privacy and safety

- Archives are local plaintext files. The skill does not send them anywhere.
- `note-make` notes are local plaintext files. Credentials are redacted while writing; review notes before publishing or committing.
- `note-push` sends the selected note content to the configured Discourse instance. Review the dry-run plan before publishing. The skill never prints the API key; the local cache contains topic IDs and tags, not the key.
- This is not therapy or medical advice. If you are in immediate danger, contact local emergency services or someone you trust.
- Do not commit your archive directory to a public repository.

## License

MIT
