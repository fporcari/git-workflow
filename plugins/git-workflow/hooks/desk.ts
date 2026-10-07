import type { DeskItem, Tag } from '../types'

type Raw = {
  session?: string
  kind?: string
  status?: string
  label?: string
  via?: string
  n?: number | null
  payload?: { flow?: string; ns?: number[] }
  at?: string
  epoch?: number
  taken_at?: string
  taken_epoch?: number
  running_at?: string
  running_epoch?: number
  closed_at?: string
  report?: string
}

// deskstate.py's budgets: past them the desk itself reads the record as stale
const BUDGET: Record<string, number> = {
  preparing: 1800, queued: 1800, taken: 3600, running: 3600, 'needs-input': 86400,
}
const CLOSED = ['done', 'failed']
const ISSUE_KINDS = ['issue-analyze', 'close']
const ISSUE_FLOWS = ['issue-loop', 'issue-triage']

export const OPEN = Object.keys(BUDGET)
// a loop's numbers past these are on the desk, not in a one-line row
const MAX_NS = 3
const HHMM = /^(\d{2}:\d{2}):\d{2}$/

export function tagOf(raw: Raw): Tag {
  const flow = raw.payload?.flow ?? ''
  return ISSUE_KINDS.includes(raw.kind ?? '') || ISSUE_FLOWS.includes(flow) ? 'ISSUE' : 'PR'
}

function startOf(raw: Raw): number {
  if (raw.status === 'taken') return raw.taken_epoch ?? raw.epoch ?? 0
  if (raw.status === 'running') return raw.running_epoch ?? raw.epoch ?? 0
  return raw.epoch ?? 0
}

function labelOf(raw: Raw): string {
  const flow = raw.payload?.flow
  const ns = raw.payload?.ns ?? []
  const shown = ns.slice(0, MAX_NS).map(n => `#${n}`).join(' ')
  if (flow) return ns.length ? `${flow} ${shown}${ns.length > MAX_NS ? ` +${ns.length - MAX_NS}` : ''}` : flow
  return raw.n != null ? `${raw.kind} #${raw.n}` : raw.label ?? raw.kind ?? '?'
}

function atOf(raw: Raw): string {
  const at = raw.status === 'running' ? raw.running_at ?? raw.at
    : raw.status === 'taken' ? raw.taken_at ?? raw.at
    : CLOSED.includes(raw.status ?? '') ? raw.closed_at ?? raw.at : raw.at
  return (at ?? '').replace(HHMM, '$1')
}

/** The chat-routed requests of one desk state file, live or just closed. */
export function itemsOf(repo: string, state: unknown, nowSec: number): DeskItem[] {
  const ledger = (state as { requests?: Record<string, Raw> } | null)?.requests ?? {}
  const out: DeskItem[] = []
  for (const [key, raw] of Object.entries(ledger)) {
    if (!raw || raw.via !== 'chat-session') continue
    const status = raw.status ?? ''
    const budget = BUDGET[status]
    if (budget !== undefined && nowSec - startOf(raw) > budget) continue
    if (budget === undefined && !CLOSED.includes(status)) continue
    out.push({
      key: `${repo} ${key}`, repo, session: raw.session ?? '', tag: tagOf(raw),
      label: labelOf(raw), status, at: atOf(raw), report: raw.report ?? '',
    })
  }
  return out
}

export const isOpen = (item: DeskItem) => OPEN.includes(item.status)

/** The mod's poll is never somebody using the desk: it neither keeps it alive nor refreshes it. */
export function pollHeaders(etag: string | null | undefined): Record<string, string> {
  const headers: Record<string, string> = etag ? { 'If-None-Match': etag } : {}
  headers['X-Git-Workflow-Background'] = '1'
  return headers
}

/** A repository by its short name, unless another one in view shares it. */
export function projectOf(repo: string, all: string[] = []): string {
  const short = repo.split('/').pop() ?? repo
  return all.some(other => other !== repo && other.split('/').pop() === short) ? repo : short
}

/** What a closed row was showing: a new status or report brings it back. */
export const shownAs = (item: DeskItem) => `${item.status}|${item.report}`

export const unclosed = (items: DeskItem[], closed: Record<string, string> = {}) =>
  items.filter(i => closed[i.key] !== shownAs(i))

export function mentions(state: unknown, session: string): boolean {
  const s = state as { chats?: Record<string, unknown>; requests?: Record<string, Raw> } | null
  if (!s || !session) return false
  if (s.chats && session in s.chats) return true
  return Object.values(s.requests ?? {}).some(r => r?.session === session)
}

const WORD: Record<string, string> = {
  preparing: 'il desk prepara', queued: 'in coda', taken: 'in chat ora',
  running: 'in background', 'needs-input': 'aspetta te',
  done: 'finito', failed: 'non riuscito',
}

export const wordOf = (status: string) => WORD[status] ?? status

const short = (text: string, max: number) =>
  text.length > max ? `${text.slice(0, max - 1)}…` : text

/** One line per transition worth a toast: a loop now waits for you, or closed. */
export function transitions(before: Record<string, string>, items: DeskItem[]): string[] {
  const out: string[] = []
  const repos = items.map(i => i.repo)
  for (const item of items) {
    const was = before[item.key]
    if (was === undefined || was === item.status) continue
    if (item.status === 'needs-input' || CLOSED.includes(item.status)) {
      const report = item.report ? `: ${short(item.report, 80)}` : ''
      out.push(`${item.tag} ${projectOf(item.repo, repos)} · ${item.label} ${wordOf(item.status)}${report}`)
    }
  }
  return out
}

/** `PR ⏳1 ⏸1 · ISSUE ⏳1`: the loops at work and the ones waiting for you, per kind. */
export function statusLine(items: DeskItem[]): string | undefined {
  const open = items.filter(isOpen)
  const parts: string[] = []
  for (const tag of ['PR', 'ISSUE'] as const) {
    const mine = open.filter(i => i.tag === tag)
    const wait = mine.filter(i => i.status === 'needs-input').length
    const busy = mine.length - wait
    const words = [busy ? `⏳${busy}` : '', wait ? `⏸${wait}` : ''].filter(Boolean)
    if (words.length) parts.push(`${tag} ${words.join(' ')}`)
  }
  return parts.length ? parts.join(' · ') : undefined
}

const GO = /^\s*(vai|ok|okay|si|sì|procedi|tutte vai|vai su tutte|go)\s*[.!]*\s*$/i

export const bareGoAhead = (text: string) => GO.test(text)

export const waiting = (items: DeskItem[]) => items.filter(i => i.status === 'needs-input')

// deskstate.CHAT_STALE: a heartbeat older than this is no chat
const CHAT_STALE = 45

type Mark = { port?: number; stopped?: string }
type Chat = { epoch?: number }
export type StateFile = { mtime: number; state: unknown }
export type Target = { port: number; repo: string; attached: boolean }

export const repoOf = (fileName: string) => fileName.replace(/\.json$/, '').replace('__', '/')

/** The desk this chat drives: the one it is attached to, else the one touched last.
 *  `attached`: some chat listens to it, so the server has a chat to route a click to. */
export function deskTarget(files: Record<string, StateFile>, session: string, nowSec: number): Target | null {
  const found: (Target & { mine: boolean; mtime: number })[] = []
  for (const [fileName, file] of Object.entries(files)) {
    const s = file.state as { desks?: { pr?: Mark }; chats?: Record<string, Chat> } | null
    const mark = s?.desks?.pr
    if (!mark?.port || mark.stopped) continue
    const chats = s?.chats ?? {}
    found.push({ port: mark.port, repo: repoOf(fileName), mtime: file.mtime, mine: session in chats,
                 attached: Object.values(chats).some(chat => nowSec - (chat.epoch ?? 0) <= CHAT_STALE) })
  }
  found.sort((a, b) => Number(b.mine) - Number(a.mine) || b.mtime - a.mtime)
  const best = found[0]
  return best ? { port: best.port, repo: best.repo, attached: best.attached } : null
}
