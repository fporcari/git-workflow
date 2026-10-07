import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Desk, DeskItem } from '../types'
import {
  bareGoAhead, deskTarget, isOpen, itemsOf, mentions, pollHeaders, projectOf, shownAs,
  statusLine, transitions, unclosed, waiting, wordOf,
} from './desk'
import type { StateFile } from './desk'

const shown = atom({ plugin: 'git-workflow', key: 'items' } as const, [] as DeskItem[])
const deskAtom = atom({ plugin: 'git-workflow', key: 'desk' } as const, null as Desk | null)
const closedAtom = atom({ plugin: 'git-workflow', key: 'closed' } as const, {} as Record<string, string>)

const POLL_MS = 4000
// a file untouched for this long holds no live request
const STALE_FILE_MS = 26 * 3600 * 1000
const COLOR = { PR: 'magenta', ISSUE: 'green' } as const
// only what the user typed is guarded: a notification or a peer saying "ok" is not an answer
const TYPED = ['composer', 'bridge']
const STATUS_COLOR: Record<string, string> = { 'needs-input': 'yellow', running: 'cyan' }
const LAUNCH = 'Apri il git desk con la skill git-desk e collega questa chat.'

const files: Record<string, StateFile> = {}
const memo: { before: Record<string, string> | null; polling: boolean; etag: string | null; base: string | null } =
  { before: null, polling: false, etag: null, base: null }

async function stateDir($: EngineInterface) {
  const configured = await $.env.get('GIT_WORKFLOW_STATE_DIR')
  if (configured) return configured
  const home = await $.env.get('HOME')
  return home ? `${home}/.local/state/git-workflow` : null
}

async function readFiles($: EngineInterface, now: number) {
  const dir = await stateDir($)
  if (!dir || !(await $.fs.exists(dir))) return
  for (const entry of await $.fs.list(dir)) {
    if (entry.kind !== 'file' || !/^[^.].*__.*\.json$/.test(entry.name)) continue
    if (now - entry.mtimeMs > STALE_FILE_MS) { delete files[entry.name]; continue }
    if (files[entry.name]?.mtime === entry.mtimeMs) continue
    try {
      files[entry.name] = { mtime: entry.mtimeMs, state: JSON.parse(await $.fs.read(`${dir}/${entry.name}`)) }
    } catch {
      // a file mid-write: the next poll reads it whole
    }
  }
}

async function getJSON($: EngineInterface, url: string, etag?: string | null) {
  const got = await $.http.fetch(url, { headers: pollHeaders(etag) })
  if (got.status === 304) return { data: null, etag: etag ?? null }
  if (!got.ok) throw new Error(`${url}: HTTP ${got.status}`)
  return { data: JSON.parse(got.text), etag: got.headers?.etag ?? got.headers?.ETag ?? null }
}

/** The desk this chat drives, if it answers. */
async function readDesk($: EngineInterface, session: string, nowSec: number) {
  const target = deskTarget(files, session, nowSec)
  const known = await read($, deskAtom)
  if (!target) {
    if (known) await update($, deskAtom, () => null)
    return
  }
  const base = `http://127.0.0.1:${target.port}`
  if (memo.base !== base) Object.assign(memo, { base, etag: null })
  const fresh = { base, repo: target.repo, attached: target.attached }
  if (!known || known.base !== fresh.base || known.attached !== fresh.attached)
    await update($, deskAtom, () => fresh)
  try {
    memo.etag = (await getJSON($, `${base}/api/todo`, memo.etag)).etag
  } catch {
    // a desk that stopped answering: its registration outlives it until it says it stopped
    await update($, deskAtom, () => null)
    memo.etag = null
  }
}

// a poll that fails is the next poll's to repeat: nothing waits on it
const repoll = ($: EngineInterface) => { poll($).catch(() => undefined) }

async function poll($: EngineInterface) {
  if (memo.polling) return
  memo.polling = true
  try {
    const now = await $.clock.now()
    await readFiles($, now)
    const session = await $.session.id()
    const nowSec = now / 1000
    const attached = Object.values(files).some(f => mentions(f.state, session))
    const items: DeskItem[] = []
    for (const [name, file] of Object.entries(files)) {
      const repo = name.replace(/\.json$/, '').replace('__', '/')
      for (const item of itemsOf(repo, file.state, nowSec))
        if (!attached || item.session === session) items.push(item)
    }
    if (memo.before) for (const line of transitions(memo.before, items)) $.ui.toast(line, { timeoutMs: 8000 })
    memo.before = Object.fromEntries(items.map(i => [i.key, i.status]))
    const open = items.filter(isOpen)
    await update($, shown, () => open)
    await readDesk($, session, nowSec)
    $.ui.status(statusLine(open))
  } finally {
    memo.polling = false
  }
}

/** The page in the Browser pane where the host has one; otherwise its link. */
async function openDesk($: EngineInterface, desk: Desk) {
  const url = `${desk.base}/`
  try {
    const got = await $.mcp.call('Claude_Browser', 'preview_start', { url })
    if (!got.isError) return `Desk di ${desk.repo}: lo apro nel Browser pane.`
  } catch {
    // no Browser pane on this host: the link instead
  }
  return `Desk di ${desk.repo}: ${url}`
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const result = await next(e)
    await $.command.register({ name: 'desk', description: 'Open the git desk of this chat in the Browser pane' })
    await $.tool.register({
      name: 'desk_open',
      description: 'Call it right after launching the git desk server: it answers this chat\'s session id ' +
        'to attach with, and the desk page to open now in the Browser pane.',
    })
    await poll($).catch(() => undefined)
    $.clock.every(POLL_MS, () => repoll($))
    return result
  })

  on('command.run', { command: 'desk' }, async $ => {
    const desk = await read($, deskAtom)
    if (!desk) return { text: 'Nessun desk aperto: lo avvio in questa chat.', context: [LAUNCH] }
    return { text: await openDesk($, desk) }
  })

  on('tool.call', { tool: 'mcp__git-workflow__desk_open' }, async $ => {
    const session = await $.session.id()
    const attach = ` This chat's session id is ${session}: pass it as --session to chatdesk.py.`
    await poll($).catch(() => undefined)
    const desk = await read($, deskAtom)
    if (!desk) {
      return { result: 'No desk answers yet: its server is still binding the port. Call this tool again in a ' +
        'second.' + attach }
    }
    return { result: `The desk of ${desk.repo} is up: open ${desk.base}/ in the Browser pane now ` +
      '(preview_start with that url), also while its triage runs, which the page shows at the bottom; a host ' +
      'without one gets the link.' + attach }
  })

  on('prompt.submit', async ($, e, next) => {
    if (!TYPED.includes(e.origin?.kind ?? '') || !bareGoAhead(e.text)) return next(e)
    const loops = waiting(await read($, shown))
    const repos = loops.map(i => i.repo)
    const name = (i: DeskItem) => `${i.tag} ${projectOf(i.repo, repos)} ${i.label}`
    if (loops.length > 1) {
      return { drop: `Aspettano una risposta in più d'uno: ${loops.map(name).join(', ')}. Scrivi a quale va (es. "pr vai").` }
    }
    if (loops.length === 1) {
      const only = loops[0]!
      const note = `desk: the one desk request waiting for an answer is ${only.tag} ${only.label} (${only.repo}): this message answers it.`
      return next({ ...e, context: [...(e.context ?? []), note] })
    }
    return next(e)
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const open = await read($, shown)
    const closed = (await read($, closedAtom)) ?? {}
    const items = unclosed(open, closed)
    if (e.props.hasSurvey || !items.length) return next(e)
    const { Box, Text, Button } = $.ui.resolve(e)
    const repos = items.map(i => i.repo)
    const rows = [...items].sort((a, b) =>
      Number(b.status === 'needs-input') - Number(a.status === 'needs-input') || a.at.localeCompare(b.at))
    const close = (item: DeskItem) => {
      void update($, closedAtom, c => Object.fromEntries([
        ...Object.entries(c ?? {}).filter(([k]) => open.some(i => i.key === k)), [item.key, shownAs(item)]]))
    }
    return (
      <Box flexDirection="column">
        {rows.slice(0, Math.max(1, e.props.maxRows - 1)).map(item => (
          <Box key={item.key} flexDirection="row">
            <Box flexDirection="row" flexGrow={1} flexShrink={1}>
              <Text color={COLOR[item.tag]} bold>● {item.tag === 'PR' ? 'PR   ' : 'ISSUE'} </Text>
              <Text wrap="truncate-end">{`${projectOf(item.repo, repos)} · ${item.label} · `}</Text>
              <Text color={STATUS_COLOR[item.status]} bold={item.status === 'needs-input'}>
                {wordOf(item.status)}{item.at ? ` dalle ${item.at}` : ''}</Text>
              {item.report ? <Text dimColor wrap="truncate-end"> · {item.report}</Text> : null}
            </Box>
            <Button key={`close-${item.key}`} role="dismiss" plain dimColor onPress={() => close(item)}>✕</Button>
          </Box>
        ))}
      </Box>
    )
  })
}
