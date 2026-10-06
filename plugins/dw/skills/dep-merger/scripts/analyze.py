#!/usr/bin/env python3
"""Find what the integration branch carries that the production branch lacks, unit by unit.

    analyze.py --base master --head develop --scratch <dir> --out analysis.json

A unit is one first-parent commit of HEAD not reachable from BASE: a merged PR, a
squashed PR or a direct commit. Each gets a content status against the base tree, so a
change already carried over by cherry-pick or by a twin PR is recognised. The missing
units are then cherry-picked in order in a throwaway worktree, to find what conflicts
alone but applies in sequence, and to prove the list is complete.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

SCHEMA_LINE = re.compile(r'^\+.*\.(column|table)\(')
UPGRADE_PATH = re.compile(r'/lib/upgrades/')
MODEL_PATH = re.compile(r'/model/')


def run(cmd, check=True, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if check and r.returncode:
        sys.exit(f"{' '.join(cmd)}: {r.stderr.strip()}")
    return r


def git(*args, **kw):
    return run(['git', *args], **kw)


def gh_json(*args):
    r = run(['gh', *args], check=False)
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None


def pr_number(subject):
    m = re.match(r'Merge pull request #(\d+)', subject) or re.search(r'\(#(\d+)\)\s*$', subject)
    return int(m.group(1)) if m else None


def applies(patch, env, *flags):
    return run(['git', 'apply', '--cached', '--check', *flags], check=False,
               input=patch, env=env).returncode == 0


def schema_hints(patch):
    hints, current = [], None
    for line in patch.splitlines():
        if line.startswith('+++ b/'):
            current = line[6:]
            if UPGRADE_PATH.search(current):
                hints.append(f'upgrade {current}')
        elif current and MODEL_PATH.search(current) and SCHEMA_LINE.match(line):
            hints.append(line[1:].strip()[:120])
    return hints


def trial(base_ref, head_ref, units, scratch):
    wt = os.path.join(scratch, 'dep-merger-trial')
    git('worktree', 'add', '-q', '--detach', wt, base_ref)
    try:
        for u in units:
            args = ['cherry-pick', '--allow-empty', '-x'] + (['-m', '1'] if u['merge'] else []) + [u['sha']]
            if git(*args, check=False, cwd=wt).returncode:
                u['trial'] = 'conflict: ' + ' '.join(
                    git('diff', '--name-only', '--diff-filter=U', cwd=wt).stdout.split())
                git('cherry-pick', '--abort', check=False, cwd=wt)
            else:
                u['trial'] = 'ok'
        return git('diff', '--name-only', 'HEAD', head_ref, cwd=wt).stdout.split()
    finally:
        git('worktree', 'remove', '--force', wt, check=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default='master')
    ap.add_argument('--head', default='develop')
    ap.add_argument('--remote', default='origin')
    ap.add_argument('--scratch', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()

    base_ref, head_ref = f'{a.remote}/{a.base}', f'{a.remote}/{a.head}'
    git('fetch', a.remote, '--prune', '-q')
    repo = (gh_json('repo', 'view', '--json', 'nameWithOwner') or {}).get('nameWithOwner', '')
    picked = set(re.findall(r'cherry picked from commit ([0-9a-f]{40})',
                            git('log', base_ref, '--format=%b').stdout))
    lines = git('log', '--reverse', '--first-parent', '--format=%H%x1f%P%x1f%an%x1f%s',
                f'{base_ref}..{head_ref}').stdout.splitlines()

    env = dict(os.environ, GIT_INDEX_FILE=os.path.join(a.scratch, 'dep-merger.index'))
    units, order = [], []
    for line in lines:
        sha, parents, author, subject = line.split('\x1f')
        parents = parents.split()
        merge = len(parents) > 1
        order.append(sha)
        if merge and git('merge-base', '--is-ancestor', parents[1], base_ref, check=False).returncode == 0:
            continue
        git('read-tree', base_ref, env=env)
        patch = git('diff', '--binary', f'{sha}^1', sha).stdout
        if sha in picked:
            status, alone = 'in_base', 'cherry-picked'
        elif not patch.strip():
            status, alone = 'empty', ''
        elif applies(patch, env, '-R'):
            status, alone = 'in_base', 'same content'
        elif applies(patch, env):
            status, alone = 'missing', 'clean'
        elif applies(patch, env, '-3'):
            status, alone = 'missing', '3way'
        else:
            status, alone = 'unclear', 'conflict'
        u = dict(sha=sha, short=sha[:8], merge=merge, git_author=author, subject=subject,
                 pr=pr_number(subject), status=status, alone=alone,
                 files=git('diff', '--name-only', f'{sha}^1', sha).stdout.split(),
                 schema=schema_hints(patch))
        info = gh_json('pr', 'view', str(u['pr']), '--json',
                       'author,title,body,closingIssuesReferences,headRefName') if u['pr'] else None
        if info:
            issues = {i['number'] for i in info.get('closingIssuesReferences', [])}
            issues |= {int(n) for n in re.findall(r'(?i)(?:fixes|closes|resolves) #(\d+)', info.get('body') or '')}
            u.update(author=info['author']['login'], title=info['title'],
                     branch=info['headRefName'], issues=sorted(issues))
        else:
            u.update(author=author, title=subject, branch='', issues=[])
        units.append(u)

    pending = [u for u in units if u['status'] in ('missing', 'unclear')]
    residual = trial(base_ref, head_ref, pending, a.scratch) if pending else []
    for i, u in enumerate(pending):
        if u['alone'] != 'clean':
            u['maybe_requires'] = [p['pr'] or p['short'] for p in pending[:i]
                                   if set(p['files']) & set(u['files'])]

    base_prs = gh_json('pr', 'list', '--base', a.base, '--state', 'all', '--limit', '60',
                       '--json', 'number,title,author,state,headRefName') or []
    open_on_head = gh_json('pr', 'list', '--base', a.head, '--state', 'open',
                           '--json', 'number,title,author') or []
    with open(a.out, 'w') as f:
        json.dump(dict(repo=repo, base=a.base, head=a.head, remote=a.remote,
                       date=datetime.date.today().isoformat(), order=order, units=units,
                       residual_diff=residual, base_prs=base_prs, open_on_head=open_on_head),
                  f, indent=1, ensure_ascii=False)
    for u in units:
        ref = f"#{u['pr']}" if u['pr'] else 'direct'
        print(f"{u['short']} {u['status']:8} {u['alone']:13} {u.get('trial', ''):10} "
              f"{ref:7} {u['author']:16} {u['title'][:60]}")
    print(f"\nresidual diff after the trial: {residual or 'none, the trial tree equals ' + head_ref}")


if __name__ == '__main__':
    main()
