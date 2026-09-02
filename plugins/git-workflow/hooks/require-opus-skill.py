#!/usr/bin/env python3
"""Block pr-loop when the session runs below Opus (Opus or Fable pass).

pr-loop merges, pushes and answers reviews without asking again. Its gates are
judgements (is this approval on the current head? is this conflict in a file
the base rewrote?) where a weaker model fails quietly and in a direction that
is hard to undo. Claude Code only: Codex has no hooks, runtime.md carries the
hint there.

The PreToolUse payload for the Skill tool carries `tool_input.skill` and
`transcript_path`, but not the model: it is read from the last assistant entry
of the transcript. Fails CLOSED: an undetermined model blocks.
"""
import json
import pathlib
import sys

PROTECTED = {'pr-loop'}
ALLOWED = ('opus', 'fable')


def session_model(transcript_path):
    if not transcript_path:
        return None
    path = pathlib.Path(transcript_path)
    if not path.is_file():
        return None
    model = None
    with path.open(encoding='utf-8') as transcript:
        for line in transcript:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            found = (entry.get('message') or {}).get('model') or entry.get('model')
            if found:
                model = found
    return model


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except ValueError:
        return 0
    skill = (payload.get('tool_input') or {}).get('skill') or ''
    if skill.split(':')[-1] not in PROTECTED:
        return 0
    model = session_model(payload.get('transcript_path'))
    if model and any(name in model.lower() for name in ALLOWED):
        return 0
    named = model or 'undetermined'
    print(
        f"/{skill} is restricted to Opus or Fable and this session runs on {named}. "
        f"It merges, pushes and answers reviews without asking again, so it is not "
        f"run on a weaker model. Switch the session to Opus or Fable and invoke it "
        f"again, or ask the user which PRs to handle one at a time instead.",
        file=sys.stderr,
    )
    return 2


if __name__ == '__main__':
    sys.exit(main())
