import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { DeskItem } from '../types'
import {
  bareGoAhead, isOpen, itemsOf, mentions, statusLine, transitions, waiting, wordOf,
} from './desk'

const shown = atom({ plugin: 'desk-band', key: 'items' } as const, [] as DeskItem[])

const POLL_MS = 4000
// a file untouched for this long holds no live request
const STALE_FILE_MS = 26 * 3600 * 1000
const COLOR = { PR: 'magenta', ISSUE: 'green' } as const
// only what the user typed is guarded: a notification or a peer saying "ok" is not an answer
const TYPED = ['composer', 'bridge']
const STATUS_COLOR: Record<string, string> = { 'needs-input': 'yellow', running: 'cyan' }

const files: Record<string, { mtime: number; state: unknown }> = {}
const memo: { before: Record<string, string> | null; polling: boolean } = { before: null, polling: false }

async function poll($: EngineInterface) {
  if (memo.polling) return
  memo.polling = true
  try {
    const home = await $.env.get('HOME')
    if (!home) return
    const dir = `${home}/.local/state/git-workflow`
    if (!(await $.fs.exists(dir))) return
    const now = await $.clock.now()
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
    $.ui.status(statusLine(open))
    await update($, shown, () => open)
  } finally {
    memo.polling = false
  }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const result = await next(e)
    await poll($)
    $.clock.every(POLL_MS, () => { void poll($) })
    return result
  })

  on('prompt.submit', async ($, e, next) => {
    if (!TYPED.includes(e.origin?.kind ?? '') || !bareGoAhead(e.text)) return next(e)
    const open = waiting(await read($, shown))
    if (open.length > 1) {
      const which = open.map(i => `${i.tag} ${i.label}`).join(', ')
      return { drop: `Aspettano una risposta in due: ${which}. Scrivi a quale va (es. "pr vai").` }
    }
    if (open.length === 1) {
      const only = open[0]!
      const note = `desk-band: the one desk request waiting for an answer is ${only.tag} ${only.label} (${only.repo}): this message answers it.`
      return next({ ...e, context: [...(e.context ?? []), note] })
    }
    return next(e)
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const items = await read($, shown)
    if (e.props.hasSurvey || !items.length) return next(e)
    const { Box, Text } = $.ui.resolve(e)
    const repos = new Set(items.map(i => i.repo))
    const rows = [...items].sort((a, b) =>
      Number(b.status === 'needs-input') - Number(a.status === 'needs-input') || a.at.localeCompare(b.at))
    return (
      <Box flexDirection="column">
        {rows.slice(0, Math.max(1, e.props.maxRows - 1)).map(item => (
          <Box key={item.key}>
            <Text color={COLOR[item.tag]} bold>● {item.tag === 'PR' ? 'PR   ' : 'ISSUE'} </Text>
            <Text wrap="truncate-end">
              {repos.size > 1 ? `${item.repo} · ` : ''}{item.label} · </Text>
            <Text color={STATUS_COLOR[item.status]} bold={item.status === 'needs-input'}>
              {wordOf(item.status)}{item.at ? ` dalle ${item.at}` : ''}</Text>
            {item.report ? <Text dimColor wrap="truncate-end"> · {item.report}</Text> : null}
          </Box>
        ))}
      </Box>
    )
  })
}
