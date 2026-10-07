import type { Card, Section, View, Wizard } from '../types'

export type StepDef = { id: string; label: string; done?: string; color?: string }
export type SectionDef = { id: string; label: string; tag: string; steps: StepDef[] }

export const SECTIONS: SectionDef[] = [
  { id: 'review', label: 'Da rivedere', tag: 'DA RIVEDERE', steps: [
    { id: 'approve', label: 'Approvabili', done: 'Approvate', color: 'green' },
    { id: 'changes', label: 'Da respingere', done: 'Respinte', color: 'red' },
    { id: 'doubt', label: 'Dubbie', color: 'yellow' },
    { id: 'done', label: 'Fatto' }] },
  { id: 'mine', label: 'Mie', tag: 'MIE', steps: [
    { id: 'merge', label: 'Da mergiare', color: 'green' },
    { id: 'fix', label: 'Le sistema Claude', color: 'cyan' },
    { id: 'decide', label: 'Da decidere', color: 'yellow' },
    { id: 'waiting', label: 'In attesa' }] },
  { id: 'issue', label: 'Issue', tag: 'ISSUE', steps: [
    { id: 'close', label: 'Da chiudere', color: 'green' },
    { id: 'claude', label: 'Le fa Claude', color: 'cyan' },
    { id: 'decide', label: 'Da decidere', color: 'yellow' },
    { id: 'done', label: 'Fatto' }] },
  { id: 'whose', label: 'A chi tocca', tag: 'A CHI TOCCA', steps: [] },
]

export const EMPTY_VIEW: View = {
  section: 'review', steps: {}, off: [], sel: {}, doubtAt: 0, zoom: null, drafts: {},
}

export const key = (card: Card) => `${card.repo ?? ''}#${card.n}`
export const sectionOf = (id: string) => SECTIONS.find(s => s.id === id) ?? SECTIONS[0]!

export function section(wizard: Wizard | null, id: string): Section | null {
  if (!wizard) return null
  return (wizard as unknown as Record<string, Section | null>)[id] ?? null
}

export function stepRows(wizard: Wizard | null, sectionId: string, step: string): Card[] {
  return section(wizard, sectionId)?.steps.find(s => s.id === step)?.rows ?? []
}

/** The step the section shows: the one the person chose, else the first with work. */
export function currentStep(view: View, wizard: Wizard | null, sectionId = view.section): string | null {
  const def = sectionOf(sectionId)
  if (!def.steps.length) return null
  return view.steps[sectionId] ?? section(wizard, sectionId)?.first ?? def.steps[0]!.id
}

const offKey = (sectionId: string, step: string, card: Card) => `${sectionId}:${step}:${key(card)}`

export const isChecked = (view: View, sectionId: string, step: string, card: Card) =>
  !view.off.includes(offKey(sectionId, step, card)) && !card.sending && !card.sent

export function toggled(view: View, sectionId: string, step: string, card: Card): View {
  const k = offKey(sectionId, step, card)
  return { ...view, off: view.off.includes(k) ? view.off.filter(x => x !== k) : [...view.off, k] }
}

export function checked(view: View, wizard: Wizard | null, sectionId: string, step: string): Card[] {
  return stepRows(wizard, sectionId, step).filter(c => isChecked(view, sectionId, step, c))
}

export function selected(view: View, wizard: Wizard | null): Card | undefined {
  const step = currentStep(view, wizard)
  if (!step) return undefined
  const rows = stepRows(wizard, view.section, step)
  if (view.section === 'review' && step === 'doubt') return rows[Math.min(view.doubtAt, rows.length - 1)]
  return rows.find(c => key(c) === view.sel[`${view.section}:${step}`]) ?? rows[0]
}

export function moved(view: View, wizard: Wizard | null, delta: number): View {
  const step = currentStep(view, wizard)
  if (!step) return view
  const rows = stepRows(wizard, view.section, step)
  if (!rows.length) return view
  if (view.section === 'review' && step === 'doubt')
    return { ...view, doubtAt: Math.max(0, Math.min(rows.length - 1, view.doubtAt + delta)) }
  const at = rows.findIndex(c => key(c) === view.sel[`${view.section}:${step}`])
  const next = rows[Math.max(0, Math.min(rows.length - 1, (at < 0 ? 0 : at) + delta))]!
  return { ...view, sel: { ...view.sel, [`${view.section}:${step}`]: key(next) } }
}

export const draftOf = (view: View, card: Card) => view.drafts[key(card)] ?? card.draft ?? card.body ?? ''

export type Action = { path: string; body: Record<string, unknown>; echo: string; needsChat: boolean }

const items = (rows: Card[], extra?: (c: Card) => Record<string, unknown>) =>
  rows.map(c => ({ n: c.n, ...(c.repo ? { repo: c.repo } : {}), ...(extra ? extra(c) : {}) }))
const ns = (rows: Card[]) => rows.map(c => `#${c.n}`).join(' ')

/** The step's primary key: its label, and what it posts — the checked rows, as shown. */
export function primary(view: View, wizard: Wizard | null): { label: string; action: Action | null } | null {
  const step = currentStep(view, wizard)
  const s = view.section
  if (!step) return null
  const live = stepRows(wizard, s, step).filter(c => !c.sent && !c.sending)
  const rows = checked(view, wizard, s, step)
  const n = rows.length
  const none = { label: 'Nessuna spuntata', action: null }
  if (s === 'review' && step === 'approve') return n ? {
    label: n === live.length ? `Approva tutte e ${n}` : `Approva le ${n} spuntate`,
    action: { path: '/api/review', echo: `approva ${ns(rows)}`, needsChat: true,
              body: { event: 'approve', items: items(rows, c => ({ head: c.head })) } } } : none
  if (s === 'review' && step === 'changes') return n ? {
    label: n === 1 ? 'Invia la richiesta' : `Invia le ${n} richieste`,
    action: { path: '/api/review', echo: `chiedi modifiche ${ns(rows)}`, needsChat: true,
              body: { event: 'changes', items: items(rows, c => ({ head: c.head, body: draftOf(view, c) })) } } } : none
  if (s === 'review' && step === 'doubt') {
    const card = selected(view, wizard)
    return card ? { label: card.lean === 'approve' ? 'Approva' : 'Chiedi modifiche',
                    action: doubtAction(view, card, card.lean === 'approve' ? 'a' : 'r') } : null
  }
  if (s === 'mine' && step === 'merge') return n ? {
    label: `Mergia le ${n}`,
    action: { path: '/api/run', echo: `pr-loop ${ns(rows)}`, needsChat: false,
              body: { flow: 'pr-loop', items: items(rows), batch: 1 } } } : none
  if (s === 'mine' && step === 'fix') return n ? {
    label: `Falle sistemare (${n})`,
    action: { path: '/api/run', echo: `pr-loop ${ns(rows)} batch=${n}`, needsChat: false,
              body: { flow: 'pr-loop', items: items(rows), batch: n } } } : none
  if (s === 'issue' && step === 'close') return n ? {
    label: n === 1 ? 'Chiudi la issue' : `Chiudi le ${n} issue`,
    action: { path: '/api/close', echo: `chiudi ${ns(rows)}`, needsChat: true,
              body: { items: items(rows, c => ({ body: draftOf(view, c) })) } } } : none
  if (s === 'issue' && step === 'claude') return n ? {
    label: n === 1 ? 'Fai aprire la PR a Claude' : `Fai aprire le ${n} PR a Claude`,
    action: { path: '/api/run', echo: `issue-loop ${ns(rows)} batch=${n}`, needsChat: false,
              body: { flow: 'issue-loop', items: items(rows), batch: n } } } : none
  return null
}

/** A, R or S on a doubt: approve, request changes with the leaning's text, or tomorrow. */
export function doubtAction(view: View, card: Card, which: 'a' | 'r' | 's'): Action | null {
  if (which === 's')
    return { path: '/api/review', echo: `salta #${card.n} a domani`, needsChat: false,
             body: { event: 'skip', items: items([card]) } }
  if (which === 'a')
    return { path: '/api/review', echo: `approva #${card.n}`, needsChat: true,
             body: { event: 'approve', items: items([card], c => ({ head: c.head })) } }
  const text = draftOf(view, card)
  if (!text.trim()) return null
  return { path: '/api/review', echo: `chiedi modifiche #${card.n}`, needsChat: true,
           body: { event: 'changes', items: items([card], c => ({ head: c.head, body: text })) } }
}

export function optionAction(card: Card, index: number): Action | null {
  const text = card.options?.[index]
  if (!text) return null
  return { path: `/api/pr/${card.n}/order`, echo: `pr-loop order #${card.n}: ${text}`, needsChat: false,
           body: { propose: text, instruction: text, ...(card.repo ? { repo: card.repo } : {}) } }
}

export function loopAction(card: Card, flow: 'pr-loop' | 'issue-loop'): Action {
  return { path: '/api/run', echo: `${flow} #${card.n}`, needsChat: false,
           body: { flow, items: items([card]), batch: 1 } }
}

const count = (wizard: Wizard | null, s: string, step: string) => stepRows(wizard, s, step).length

/** The status line's desk half: `PR ✓8 ✕3 ?4 · ISSUE 5 per Claude`. */
export function wizardStatus(wizard: Wizard | null): Record<'PR' | 'ISSUE', string> {
  if (!wizard) return { PR: '', ISSUE: '' }
  const ok = count(wizard, 'review', 'approve')
  const no = count(wizard, 'review', 'changes')
  const doubt = count(wizard, 'review', 'doubt')
  const reading = wizard.prepare?.pr?.status === 'running'
  const pr = reading
    ? `${wizard.prepare.pr.landed.length + Object.keys(wizard.prepare.pr.failed).length}/${wizard.prepare.pr.due.length}`
    : ok + no + doubt ? `✓${ok} ✕${no} ?${doubt}` : '✓ a zero'
  const claude = count(wizard, 'issue', 'claude')
  const issue = wizard.prepare?.issue?.status === 'running' ? 'in lettura'
    : claude ? `${claude} per Claude` : ''
  return { PR: pr, ISSUE: issue }
}

/** One toast when a preparation that was running ends. */
export function preparedToasts(before: Record<string, string>, wizard: Wizard | null): string[] {
  if (!wizard?.prepare) return []
  const out: string[] = []
  for (const kind of ['pr', 'issue'] as const) {
    const was = before[kind]
    const now = wizard.prepare[kind]?.status
    if (was !== 'running' || now === 'running' || !now) continue
    if (kind === 'pr') {
      out.push(`Review pronta · ${count(wizard, 'review', 'approve')} approvabili · ` +
        `${count(wizard, 'review', 'changes')} da respingere · ${count(wizard, 'review', 'doubt')} dubbie`)
    } else {
      out.push(`Issue pronte · ${count(wizard, 'issue', 'close')} da chiudere · ` +
        `${count(wizard, 'issue', 'claude')} le può fare Claude`)
    }
  }
  return out
}

const SAYS: Record<string, (n: number) => string> = {
  'review:approve': n => `${n} PR che Claude approverebbe aspettano il tuo ok`,
  'review:changes': n => `${n} PR da respingere: rileggi la motivazione e invia`,
  'review:doubt': n => `${n} PR dubbie aspettano il tuo giudizio`,
  'mine:merge': n => `${n} tue PR approvate sono pronte da mergiare`,
  'mine:fix': n => `${n} tue PR le può sistemare Claude`,
  'mine:decide': n => `${n} tue PR aspettano una tua scelta`,
  'mine:waiting': n => `${n} tue PR aspettano qualcun altro`,
  'issue:close': n => `${n} issue già risolte da chiudere`,
  'issue:claude': n => `${n} issue le può fare Claude`,
  'issue:decide': n => `${n} issue aspettano una tua decisione`,
}

/** What the band above the prompt says about the wizard, in one line; nothing when nothing waits or it was closed. */
export function bandLine(view: View, wizard: Wizard | null): { tag: string; text: string; doubt?: Card } | null {
  const line = bandSays(view, wizard)
  return line && line.text !== view.hushed ? line : null
}

function bandSays(view: View, wizard: Wizard | null): { tag: string; text: string; doubt?: Card } | null {
  if (!wizard) return null
  const def = sectionOf(view.section)
  const step = currentStep(view, wizard)
  if (view.section === 'review' && step === 'doubt') {
    const card = selected(view, wizard)
    const rows = stepRows(wizard, 'review', 'doubt')
    if (card) return { tag: `? DUBBIA ${Math.min(view.doubtAt, rows.length - 1) + 1}/${rows.length}`,
                       text: `${card.label ?? `#${card.n}`} ${card.title ?? ''}`, doubt: card }
  }
  const tag = `● ${def.tag}`
  const p = wizard.prepare?.[view.section === 'issue' ? 'issue' : 'pr']
  if (p?.status === 'running') return { tag, text: p.phrase }
  const at = def.steps.findIndex(s => s.id === step)
  const todo = def.steps.slice(Math.max(0, at)).filter(s => SAYS[`${view.section}:${s.id}`] && count(wizard, view.section, s.id))
  const here = todo[0]
  if (!here) return null
  const rest = todo.slice(1).map(s => `${count(wizard, view.section, s.id)} ${s.label.toLowerCase()}`)
  return { tag, text: SAYS[`${view.section}:${here.id}`]!(count(wizard, view.section, here.id)) +
                      (rest.length ? ` · poi ${rest.join(', ')}` : '') }
}
