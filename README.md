# no-code-skills

Skills that aren't about coding — for everyday thinking, reflection, and planning.

## Skills

### brain-dump

A guided 15-minute brain dump. It helps you vent, sort what you dumped, rate how much pressure or pull each item has, choose one small next step, and archive the session to local Markdown files.

Use it when you feel overwhelmed, want to clear your head, or want to reflect on the day. The instructions are written in Chinese; responses follow the user's main language.

## Install

### Claude Code (plugin marketplace)

```text
/plugin marketplace add hxt-tg/no-code-skills
/plugin install no-code-skills@no-code-skills-marketplace
```

### Claude Code (manual)

```bash
git clone https://github.com/hxt-tg/no-code-skills.git
mkdir -p ~/.claude/skills
ln -s "$PWD/no-code-skills/skills/brain-dump" ~/.claude/skills/brain-dump
```

### Codex / OpenCode / Pi

```bash
npx skills add hxt-tg/no-code-skills -g

# Or copy from the repository root into the shared agents directory
mkdir -p ~/.agents/skills
cp -R skills/brain-dump ~/.agents/skills/
```

## Usage

Say "我想把脑子里的东西理一理", "brain dump", or "倒一下脑子".

By default, archives are written to:

- `~/Documents/BrainDump/history/DUMP-YYYY-MM-DD.md`
- `~/Documents/BrainDump/FUTURE-PLAN.md`

Set `BRAIN_DUMP_DIR` to change the default directory. A path specified in the conversation takes precedence.

## Privacy and safety

- Archives are local plaintext files. The skill does not send them anywhere.
- This is not therapy or medical advice. If you are in immediate danger, contact local emergency services or someone you trust.
- Do not commit your archive directory to a public repository.

## License

MIT
