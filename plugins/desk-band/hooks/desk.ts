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
const ISSUE_KINDS = ['issue-analyze']
const ISSUE_FLOWS = ['issue-loop', 'issue-triage']

export const OPEN = Object.keys(BUDGET)

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
  if (flow) return ns.length ? `${flow} ${ns.map(n => `#${n}`).join(' ')}` : flow
  return raw.n != null ? `${raw.kind} #${raw.n}` : raw.label ?? raw.kind ?? '?'
}

function atOf(raw: Raw): string {
  if (raw.status === 'running') return raw.running_at ?? raw.at ?? ''
  if (raw.status === 'taken') return raw.taken_at ?? raw.at ?? ''
  if (CLOSED.includes(raw.status ?? '')) return raw.closed_at ?? raw.at ?? ''
  return raw.at ?? ''
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
  for (const item of items) {
    const was = before[item.key]
    if (was === undefined || was === item.status) continue
    if (item.status === 'needs-input' || CLOSED.includes(item.status)) {
      const report = item.report ? `: ${short(item.report, 80)}` : ''
      out.push(`${item.tag} · ${item.label} ${wordOf(item.status)}${report}`)
    }
  }
  return out
}

export function statusLine(items: DeskItem[]): string | undefined {
  const open = items.filter(isOpen)
  if (!open.length) return undefined
  const parts: string[] = []
  for (const tag of ['PR', 'ISSUE'] as const) {
    const mine = open.filter(i => i.tag === tag)
    if (!mine.length) continue
    const wait = mine.filter(i => i.status === 'needs-input').length
    const busy = mine.length - wait
    parts.push(`${tag}${busy ? ` ⏳${busy}` : ''}${wait ? ` ⏸${wait}` : ''}`)
  }
  return parts.join(' · ')
}

const GO = /^\s*(vai|ok|okay|si|sì|procedi|tutte vai|vai su tutte|go)\s*[.!]*\s*$/i

export const bareGoAhead = (text: string) => GO.test(text)

export const waiting = (items: DeskItem[]) => items.filter(i => i.status === 'needs-input')
