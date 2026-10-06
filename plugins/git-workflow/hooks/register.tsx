import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Card, Desk, DeskItem, View, Wizard, Zoom } from '../types'
import {
  bareGoAhead, deskTarget, isOpen, itemsOf, mentions, statusLine, transitions, waiting, wordOf,
} from './desk'
import type { StateFile } from './desk'
import { drawPane } from './pane'
import type { Handlers } from './pane'
import {
  EMPTY_VIEW, bandLine, currentStep, doubtAction, key, loopAction, moved, optionAction,
  preparedToasts, primary, sectionOf, selected, toggled, wizardStatus,
} from './wizard'
import type { Action } from './wizard'

const shown = atom({ plugin: 'git-workflow', key: 'items' } as const, [] as DeskItem[])
const wizardAtom = atom({ plugin: 'git-workflow', key: 'wizard' } as const, null as Wizard | null)
const deskAtom = atom({ plugin: 'git-workflow', key: 'desk' } as const, null as Desk | null)
const viewAtom = atom({ plugin: 'git-workflow', key: 'view' } as const, EMPTY_VIEW)
const zoomsAtom = atom({ plugin: 'git-workflow', key: 'zooms' } as const, {} as Record<string, Zoom>)

const PANE = 'git-desk'
const TITLE = 'Desk'
const ZOOM_COLUMNS = 110
const POLL_MS = 4000
// a file untouched for this long holds no live request
const STALE_FILE_MS = 26 * 3600 * 1000
const COLOR = { PR: 'magenta', ISSUE: 'green' } as const
// only what the user typed is guarded: a notification or a peer saying "ok" is not an answer
const TYPED = ['composer', 'bridge']
const STATUS_COLOR: Record<string, string> = { 'needs-input': 'yellow', running: 'cyan' }
const LAUNCH = 'Apri il git desk con la skill git-desk e collega questa chat: il pannello /desk ' +
  'del plugin è già aperto qui accanto, non aprire il Browser pane.'

const files: Record<string, StateFile> = {}
const memo: { before: Record<string, string> | null; prepared: Record<string, string>; polling: boolean; etag: string | null } =
  { before: null, prepared: {}, polling: false, etag: null }

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
  const got = await $.http.fetch(url, { headers: etag ? { 'If-None-Match': etag } : {} })
  if (got.status === 304) return { data: null, etag: etag ?? null }
  if (!got.ok) throw new Error(`${url}: HTTP ${got.status}`)
  return { data: JSON.parse(got.text), etag: got.headers?.etag ?? got.headers?.ETag ?? null }
}

/** The desk this chat drives, its token, and its wizard as the server computes it. */
async function readDesk($: EngineInterface, session: string, nowSec: number) {
  const target = deskTarget(files, session, nowSec)
  const known = await read($, deskAtom)
  if (!target) {
    if (known) { await update($, deskAtom, () => null); await update($, wizardAtom, () => null) }
    return
  }
  const base = `http://127.0.0.1:${target.port}`
  try {
    let desk = known
    if (!desk || desk.base !== base) {
      const meta = (await getJSON($, `${base}/api/meta`)).data as { write_token: string; me: string; repo: string }
      desk = { base, repo: meta.repo, token: meta.write_token, me: meta.me, attached: target.attached }
      memo.etag = null
    }
    const fresh = { ...desk, attached: target.attached }
    if (!known || known.base !== fresh.base || known.attached !== fresh.attached)
      await update($, deskAtom, () => fresh)
    const got = await getJSON($, `${base}/api/wizard`, memo.etag)
    memo.etag = got.etag
    if (got.data) {
      const wizard = got.data as Wizard
      for (const line of preparedToasts(memo.prepared, wizard)) $.ui.toast(line, { timeoutMs: 8000 })
      memo.prepared = { pr: wizard.prepare?.pr?.status ?? '', issue: wizard.prepare?.issue?.status ?? '' }
      await update($, wizardAtom, () => wizard)
    }
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
    $.ui.status(statusLine(open, wizardStatus(await read($, wizardAtom))))
  } finally {
    memo.polling = false
  }
}

/** Posts one desk click: the server routes it to the attached chat, as the page's clicks. */
async function send($: EngineInterface, action: Action | null) {
  const desk = await read($, deskAtom)
  if (!action || !desk) return null
  if (action.needsChat && !desk.attached) {
    $.ui.toast('serve la chat collegata: le azioni pubbliche partono da lì', { timeoutMs: 6000 })
    return null
  }
  try {
    const got = await $.http.fetch(`${desk.base}${action.path}`, {
      method: 'POST', body: JSON.stringify(action.body),
      headers: { 'Content-Type': 'application/json', 'X-Git-Workflow-Token': desk.token } })
    const answer = JSON.parse(got.text || '{}') as { error?: string }
    if (!got.ok) throw new Error(answer.error ?? `HTTP ${got.status}`)
    $.ui.toast(`▶ ${action.echo}`, { timeoutMs: 5000 })
    memo.etag = null
    repoll($)
    return action
  } catch (error) {
    $.ui.toast(`Non partito: ${(error as Error).message}`, { timeoutMs: 8000 })
    return null
  }
}

async function openZoom($: EngineInterface, card: Card | null) {
  if (!card) {
    await update($, viewAtom, v => ({ ...v, zoom: null }))
    await $.ui.open({ id: PANE, title: TITLE })
    return
  }
  await update($, viewAtom, v => ({ ...v, zoom: key(card) }))
  await $.ui.open({ id: PANE, title: TITLE, columns: ZOOM_COLUMNS })
  await fetchZoom($, card)
}

async function fetchZoom($: EngineInterface, card: Card) {
  const desk = await read($, deskAtom)
  if (!desk) return
  try {
    const repo = card.repo ? `?repo=${encodeURIComponent(card.repo)}` : ''
    const zoom = (await getJSON($, `${desk.base}/api/pr/${card.n}/zoom${repo}`)).data as Zoom
    if (zoom) await update($, zoomsAtom, z => ({ ...z, [key(card)]: zoom }))
  } catch {
    // the zoom says it is still reading; the next press asks again
  }
}

function handlers($: EngineInterface, view: View, wizard: Wizard | null): Handlers {
  const change = (fn: (v: View) => View) => { void update($, viewAtom, v => fn(v ?? EMPTY_VIEW)) }
  return {
    section: id => change(v => ({ ...v, section: id, zoom: null })),
    step: id => change(v => ({ ...v, steps: { ...v.steps, [v.section]: id }, doubtAt: 0 })),
    toggle: card => change(v => toggled(v, v.section, currentStep(v, wizard) ?? '', card)),
    pick: card => change(v => ({ ...v, sel: { ...v.sel, [`${v.section}:${currentStep(v, wizard)}`]: key(card) } })),
    move: delta => {
      const next = moved(view, wizard, delta)
      change(() => next)
      const card = selected(next, wizard)
      if (view.zoom && card) void openZoom($, card)
      else if (card?.hunk && view.section === 'review') void fetchZoom($, card)
    },
    zoom: card => { void openZoom($, card) },
    primary: () => { void send($, primary(view, wizard)?.action ?? null) },
    next: () => {
      const def = sectionOf(view.section)
      const step = currentStep(view, wizard)
      if (step === 'done') { change(v => ({ ...v, section: view.section === 'review' ? 'issue' : 'whose' })); return }
      const at = def.steps.findIndex(s => s.id === step)
      const id = def.steps[Math.min(def.steps.length - 1, at + 1)]?.id
      if (id) change(v => ({ ...v, steps: { ...v.steps, [v.section]: id }, doubtAt: 0 }))
    },
    doubt: which => {
      const card = view.zoom
        ? (wizard?.review.steps.flatMap(s => s.rows).find(c => key(c) === view.zoom) ?? null)
        : selected(view, wizard)
      if (card) void send($, doubtAction(view, card, which))
    },
    option: (card, index) => { void send($, optionAction(card, index)) },
    loop: (card, flow) => { void send($, loopAction(card, flow)) },
    copy: text => { void $.ui.copy({ text }).then(r => $.ui.toast(r.isCopied ? 'Copiato' : 'Copia non riuscita')) },
    draft: (card, text) => change(v => ({ ...v, drafts: { ...v.drafts, [key(card)]: text } })),
    launch: () => { void $.prompt.submit({ text: LAUNCH, asUser: true }) },
  }
}

async function showPane($: EngineInterface) {
  const opened = await $.ui.open({ id: PANE, title: TITLE, focus: true })
  repoll($)
  return opened
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const result = await next(e)
    await $.command.register({ name: 'desk', description: 'Open the git desk in a pane beside this chat' })
    await $.tool.register({
      name: 'desk_pane',
      description: 'Open the git desk pane beside this chat (the git-workflow plugin\'s own view of the ' +
        'desk this chat launched). Call it after launching the desk server, instead of opening the URL ' +
        'in the Browser pane.',
    })
    await poll($).catch(() => undefined)
    $.clock.every(POLL_MS, () => repoll($))
    return result
  })

  on('command.run', { command: 'desk' }, async $ => {
    await showPane($)
    if (await read($, deskAtom)) return { text: 'Desk aperto nel pannello · i suoi bottoni arrivano in questa chat.' }
    await $.prompt.submit({ text: LAUNCH, asUser: true })
    return { text: 'Nessun desk aperto: lo avvio in questa chat.' }
  })

  on('tool.call', { tool: 'mcp__git-workflow__desk_pane' }, async $ => {
    const opened = await showPane($)
    const session = await $.session.id()
    return { result: (opened.isPlaced ? 'The desk pane is open beside the chat.'
      : 'The desk pane waits for a wider window; /desk opens it at any width.') +
      ` This chat's session id is ${session}: pass it as --session to chatdesk.py.` }
  })

  on('prompt.submit', async ($, e, next) => {
    if (!TYPED.includes(e.origin?.kind ?? '') || !bareGoAhead(e.text)) return next(e)
    const loops = waiting(await read($, shown))
    const view = (await read($, viewAtom)) ?? EMPTY_VIEW
    const wizard = await read($, wizardAtom)
    const band = bandLine(view, wizard)
    const doubt = band?.doubt && (await read($, deskAtom))?.attached ? band.doubt : null
    const asked = loops.length + (doubt ? 1 : 0)
    if (asked > 1) {
      const which = [...loops.map(i => `${i.tag} ${i.label}`), ...(doubt ? [`DUBBIA #${doubt.n}`] : [])].join(', ')
      return { drop: `Aspettano una risposta in più d'uno: ${which}. Scrivi a quale va (es. "pr vai").` }
    }
    if (loops.length === 1) {
      const only = loops[0]!
      const note = `desk: the one desk request waiting for an answer is ${only.tag} ${only.label} (${only.repo}): this message answers it.`
      return next({ ...e, context: [...(e.context ?? []), note] })
    }
    if (doubt) {
      const sent = await send($, doubtAction(view, doubt, doubt.lean === 'approve' ? 'a' : 'r'))
      const note = sent
        ? `desk: this go-ahead answered the doubt on #${doubt.n} as the desk click ▶ ${sent.echo}; that request reaches this chat through the desk monitor — do not act on it twice.`
        : `desk: this go-ahead could not answer the doubt on #${doubt.n}; nothing was sent.`
      return next({ ...e, context: [...(e.context ?? []), note] })
    }
    return next(e)
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const items = await read($, shown)
    const view = (await read($, viewAtom)) ?? EMPTY_VIEW
    const wizard = await read($, wizardAtom)
    const band = bandLine(view, wizard)
    if (e.props.hasSurvey || (!items.length && !band)) return next(e)
    const { Box, Text, Button } = $.ui.resolve(e)
    const repos = new Set(items.map(i => i.repo))
    const rows = [...items].sort((a, b) =>
      Number(b.status === 'needs-input') - Number(a.status === 'needs-input') || a.at.localeCompare(b.at))
    const act = handlers($, view, wizard)
    const isIssue = view.section === 'issue'
    return (
      <Box flexDirection="column">
        {band ? (
          <Box key="wizard" flexDirection="row" gap={1}>
            <Text color={band.doubt ? 'yellow' : isIssue ? COLOR.ISSUE : COLOR.PR} bold>{band.tag}</Text>
            <Text wrap="truncate-end">{band.text}</Text>
            {band.doubt ? [
              <Button key="band-a" hotkey="a" onPress={() => act.doubt('a')}>Approva</Button>,
              <Button key="band-r" hotkey="r" onPress={() => act.doubt('r')}>Chiedi modifiche</Button>,
              <Button key="band-s" hotkey="s" onPress={() => act.doubt('s')}>Salta</Button>,
            ] : null}
          </Box>) : null}
        {rows.slice(0, Math.max(1, e.props.maxRows - (band ? 2 : 1))).map(item => (
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

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const view = (await read($, viewAtom)) ?? EMPTY_VIEW
    const wizard = await read($, wizardAtom)
    const model = {
      desk: await read($, deskAtom), wizard, view,
      zooms: (await read($, zoomsAtom)) ?? {},
      columns: e.props.bodyColumns, on: handlers($, view, wizard),
    }
    return drawPane($.ui.resolve(e), model)
  })
}

