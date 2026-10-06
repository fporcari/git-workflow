import type { On } from 'claude-code'
import { describe, expect, test } from 'claude-code/testing'

import type { Card, Desk, View, Wizard } from '../types'
import { deskTarget, statusLine } from '../hooks/desk'
import {
  EMPTY_VIEW, bandLine, doubtAction, preparedToasts, primary, toggled, wizardStatus,
} from '../hooks/wizard'

const card = (n: number, over: Partial<Card> = {}): Card => ({
  n, repo: 'genropy/genropy', label: `#${n}`, title: `titolo ${n}`, author: 'fporcari',
  age: 40, url: `https://github.com/genropy/genropy/pull/${n}`, head: `h${n}`, why: `perché ${n}`,
  ...over,
})

const WIZARD: Wizard = {
  review: {
    count: 4, first: 'approve', pending: [], skipped: [], waiting_author: 0,
    steps: [
      { id: 'approve', rows: [card(1164), card(1163)] },
      { id: 'changes', rows: [card(1142, { stance: 'changes', draft: 'Please split it.' })] },
      { id: 'doubt', rows: [card(1152, { stance: 'doubt', doubt: "l'ordine dei trigger cambia",
                                         lean: 'changes', draft: 'Please pin the order.',
                                         hunk: { path: 'gnrjs/gnrbag.js', header: '@@ -1 +1 @@' } })] },
      { id: 'done', rows: [], summary: {} },
    ],
  },
  mine: { count: 1, first: 'decide', chase: {}, steps: [
    { id: 'merge', rows: [] }, { id: 'fix', rows: [] },
    { id: 'decide', rows: [card(1059, { todo: 'rispondi alla review', ask: 'Pubbliche o solo admin?',
                                         options: ['Solo admin', 'Pubbliche', 'Chiedi a genro'] })] },
    { id: 'waiting', rows: [] }] },
  issue: { count: 5, first: 'claude', pending: [], steps: [
    { id: 'close', rows: [] },
    { id: 'claude', rows: [card(1166, { type: 'DEFECT' }), card(1138, { type: 'DEFECT' })] },
    { id: 'decide', rows: [] }, { id: 'done', rows: [], summary: {} }] },
  whose: [{ who: 'genro', me: true, review: 4, mine: 1, issues_mine: 0, for_claude: 2 },
          { who: 'fporcari', merge: [1130], fix: [], review: [], wait: [], issues: [], total: 1,
            chase: '@fporcari — tocca a te:\nda mergiare: #1130' },
          { who: null, unassigned: 12 }],
  prepare: {
    pr: { status: 'done', phrase: 'preparata stanotte alle 23:10', due: [], landed: [], failed: {} },
    issue: { status: 'done', phrase: 'preparata stanotte alle 23:20', due: [], landed: [], failed: {} },
  },
}
const DESK: Desk = { base: 'http://127.0.0.1:8399', repo: 'genropy/genropy', token: 't', me: 'genro', attached: true }
const PANE = {
  plugin: 'git-workflow', component: 'Pane', requestId: 'git-desk',
  props: { title: 'Desk', isFocused: true, bodyColumns: 80, placement: 'dock',
           scroll: { offset: 0, bodyRows: 40 }, view: {} },
} as const
const BAND = {
  plugin: 'git-workflow', component: 'AbovePrompt',
  props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 120,
           scroll: { offset: 0, bodyRows: 10 }, view: {} },
} as const

type Seed = { wizard?: Wizard | null; desk?: Desk | null; view?: View }

// the engine beneath the mod: the desk's state as the poll left it, the
// desk's server as one answering function, and every prompt it is handed
function seed(on: On, given: Seed, posts: { url: string; body: unknown }[] = [], prompts: string[] = [],
              opened: unknown[] = []) {
  on('state.get', ($, e, next) => {
    if (e.plugin !== 'git-workflow') return next(e)
    const values: Record<string, unknown> = { wizard: given.wizard, desk: given.desk, items: [] }
    if (given.view) values.view = given.view
    return e.key in values ? { value: { value: values[e.key], version: 1 } } : next(e)
  })
  on('http.fetch', ($, e) => {
    if (e.init?.method === 'POST') posts.push({ url: e.url, body: JSON.parse(e.init.body ?? '{}') })
    return { value: { status: 202, ok: true, headers: {}, text: '{"queued": true}' } }
  })
  on('prompt.submit', ($, e) => { prompts.push(e.text); return { text: e.text, context: e.context, origin: e.origin } })
  on('ui.open', ($, e) => { opened.push(e); return { value: { isPlaced: true as const } } })
}

describe('the wizard, as the pane reads it', () => {
  test('the primary key counts the checked rows and posts exactly them', () => {
    const view = { ...EMPTY_VIEW }
    expect(primary(view, WIZARD)?.label).toBe('Approva tutte e 2')
    const off = toggled(view, 'review', 'approve', card(1164))
    const main = primary(off, WIZARD)!
    expect(main.label).toBe('Approva le 1 spuntate')
    expect(main.action?.body).toEqual({ event: 'approve',
      items: [{ n: 1163, repo: 'genropy/genropy', head: 'h1163' }] })
    expect(main.action?.needsChat).toBe(true)
  })

  test('R on a doubt sends the leaning text, S needs no chat', () => {
    const doubt = WIZARD.review.steps[2]!.rows[0]!
    expect(doubtAction(EMPTY_VIEW, doubt, 'r')?.body).toEqual({ event: 'changes',
      items: [{ n: 1152, repo: 'genropy/genropy', head: 'h1152', body: 'Please pin the order.' }] })
    expect(doubtAction(EMPTY_VIEW, doubt, 's')?.needsChat).toBe(false)
  })

  test('the status line says what the desk holds and what works', () => {
    expect(wizardStatus(WIZARD)).toEqual({ PR: '✓2 ✕1 ?1', ISSUE: '2 per Claude' })
    expect(statusLine([], wizardStatus(WIZARD))).toBe('PR ✓2 ✕1 ?1 · ISSUE 2 per Claude')
    const reading = { ...WIZARD, prepare: { ...WIZARD.prepare,
      pr: { status: 'running', phrase: 'preparo', due: [1, 2, 3], landed: [1], failed: {} } } }
    expect(wizardStatus(reading).PR).toBe('1/3')
  })

  test('a preparation that ends is one toast; one still going is none', () => {
    expect(preparedToasts({ pr: 'running', issue: 'done' }, WIZARD))
      .toEqual(['Review pronta · 2 approvabili · 1 da respingere · 1 dubbie'])
    expect(preparedToasts({ pr: 'done', issue: 'done' }, WIZARD)).toEqual([])
  })

  test('the band says the step, or the doubt in view', () => {
    expect(bandLine(EMPTY_VIEW, WIZARD)?.text)
      .toBe('passo 1 di 4 · 2 approvabili · poi 1 da respingere, 1 dubbie')
    const doubt = bandLine({ ...EMPTY_VIEW, steps: { review: 'doubt' } }, WIZARD)!
    expect(doubt.tag).toBe('? DUBBIA 1/1')
    expect(doubt.doubt?.n).toBe(1152)
  })

  test('the desk this chat drives is the one it is attached to', () => {
    const files = {
      'genropy__genropy.json': { mtime: 1, state: { desks: { pr: { port: 8399 } },
                                                    chats: { s1: { epoch: 1000 } } } },
      'other__repo.json': { mtime: 9, state: { desks: { pr: { port: 8400 } } } },
      'old__repo.json': { mtime: 10, state: { desks: { pr: { port: 8401, stopped: 'x' } } } },
    }
    expect(deskTarget(files, 's1', 1010)).toEqual({ port: 8399, repo: 'genropy/genropy', attached: true })
    expect(deskTarget(files, 's2', 1010)).toEqual({ port: 8400, repo: 'other/repo', attached: false })
    const listened = { ...files, 'other__repo.json': { mtime: 9, state: { desks: { pr: { port: 8400 } },
                                                                          chats: { 'a-uuid': { epoch: 1005 } } } } }
    expect(deskTarget(listened, 's2', 1010)?.attached).toBe(true)
  })
})

describe('the pane', () => {
  test('one header row, the steps, prechecked rows with their why, one key', async ($, on) => {
    seed(on, { wizard: WIZARD, desk: DESK })
    for (const surface of ['terminal', 'desktop'] as const) {
      const ui = await $.ui.mount({ ...PANE, surface })
      const buttons = (await ui.findAll({ type: 'Button' })).map(b => b.text)
      for (const label of ['Da rivedere 4', 'Mie 1', 'Issue 5', 'A chi tocca'])
        expect(buttons).toContain(label)
      expect(buttons).toContain('1 Approvabili 2')
      expect(buttons.filter(b => b === '[x]')).toHaveLength(2)
      expect(await ui.find({ type: 'Text', text: /perché 1164/ })).toBeDefined()
      expect((await ui.find({ key: 'primary' }))?.text).toBe('Approva tutte e 2')
      expect(await ui.find({ type: 'Link' })).toBeDefined()
      await ui.unmount()
    }
  })

  test('the primary key posts the click to the desk, which routes it to this chat', async ($, on) => {
    const posts: { url: string; body: unknown }[] = []
    seed(on, { wizard: WIZARD, desk: DESK }, posts)
    const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
    await ui.press({ key: 'tog-genropy/genropy#1164' })
    expect((await ui.find({ key: 'primary' }))?.text).toBe('Approva le 1 spuntate')
    await ui.press({ key: 'primary' })
    expect(posts).toEqual([{ url: 'http://127.0.0.1:8399/api/review', body: {
      event: 'approve', items: [{ n: 1163, repo: 'genropy/genropy', head: 'h1163' }] } }])
    await ui.unmount()
  })

  test('without an attached chat a public key does not leave', async ($, on) => {
    const posts: { url: string; body: unknown }[] = []
    seed(on, { wizard: WIZARD, desk: { ...DESK, attached: false } }, posts)
    const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
    expect(await ui.find({ key: 'primary' })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: /serve la chat collegata/ })).toBeDefined()
    await ui.unmount()
  })

  test('a narrow pane keeps the why and collapses the steps to numbers', async ($, on) => {
    seed(on, { wizard: WIZARD, desk: DESK })
    const ui = await $.ui.mount({ ...PANE, surface: 'terminal', props: { ...PANE.props, bodyColumns: 40 } })
    const buttons = (await ui.findAll({ type: 'Button' })).map(b => b.text)
    expect(buttons).toContain('1 Approvabili 2')
    expect(buttons).toContain('2 1')
    expect(await ui.find({ type: 'Text', text: /perché 1163/ })).toBeDefined()
    await ui.unmount()
  })

  test('a doubt: its text, its hunk, its leaning, A R S', async ($, on) => {
    const posts: { url: string; body: unknown }[] = []
    seed(on, { wizard: WIZARD, desk: DESK, view: { ...EMPTY_VIEW, steps: { review: 'doubt' } } }, posts)
    for (const surface of ['terminal', 'desktop', 'mobile'] as const) {
      const ui = await $.ui.mount({ ...PANE, surface })
      expect(await ui.find({ type: 'Text', text: /l'ordine dei trigger cambia/ })).toBeDefined()
      expect(await ui.find({ type: 'Text', text: /CLAUDE PROPENDE PER · chiedere modifiche/ })).toBeDefined()
      expect(await ui.find({ key: 'doubt-s' })).toBeDefined()
      await ui.unmount()
    }
    const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
    await ui.press({ key: 'doubt-r' })
    expect(posts.at(-1)?.body).toEqual({ event: 'changes', items: [
      { n: 1152, repo: 'genropy/genropy', head: 'h1152', body: 'Please pin the order.' }] })
    await ui.unmount()
  })

  test('the same three keys live in the band while the doubt is in view', async ($, on) => {
    const posts: { url: string; body: unknown }[] = []
    seed(on, { wizard: WIZARD, desk: DESK, view: { ...EMPTY_VIEW, steps: { review: 'doubt' } } }, posts)
    for (const surface of ['terminal', 'desktop'] as const) {
      const ui = await $.ui.mount({ ...BAND, surface })
      expect(await ui.find({ type: 'Text', text: '? DUBBIA 1/1' })).toBeDefined()
      await ui.press({ key: 'band-s' })
      await ui.unmount()
    }
    expect(posts.map(p => (p.body as { event: string }).event)).toEqual(['skip', 'skip'])
  })

  test('a bare vai answers the doubt in view, and says so beside the message', async ($, on) => {
    const posts: { url: string; body: unknown }[] = []
    seed(on, { wizard: WIZARD, desk: DESK, view: { ...EMPTY_VIEW, steps: { review: 'doubt' } } }, posts)
    const got = await $.prompt.submit({ wait: false, origin: { kind: 'composer' }, text: 'vai' })
    expect(got.text).toBe('vai')
    expect((got.context ?? []).join(' ')).toContain('#1152')
    expect((posts[0]?.body as { event: string }).event).toBe('changes')
  })

  test('no desk yet: the pane offers to open one in this chat', async ($, on) => {
    const prompts: string[] = []
    seed(on, { wizard: null, desk: null }, [], prompts)
    const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
    await ui.press({ key: 'launch' })
    expect(prompts[0]).toContain('git-desk')
    await ui.unmount()
  })

  test('Mie never offers an approval; a choice is an order with its text', async ($, on) => {
    const posts: { url: string; body: unknown }[] = []
    seed(on, { wizard: WIZARD, desk: DESK, view: { ...EMPTY_VIEW, section: 'mine' } }, posts)
    const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
    expect(await ui.find({ type: 'Button', text: /Approva/ })).toBeUndefined()
    await ui.press({ key: 'opt-1' })
    expect(posts[0]).toEqual({ url: 'http://127.0.0.1:8399/api/pr/1059/order',
      body: { propose: 'Pubbliche', instruction: 'Pubbliche', repo: 'genropy/genropy' } })
    await ui.unmount()
  })

  test('the skill opens the pane with a tool, and learns the session id to attach with', async ($, on) => {
    const opened: unknown[] = []
    seed(on, { wizard: WIZARD, desk: DESK }, [], [], opened)
    on('session.id', () => ({ value: 'the-chat' }))
    const got = await $.tool.call({ tool: 'mcp__git-workflow__desk_pane' })
    expect(String((got as { result?: unknown }).result)).toContain('session id is the-chat')
    expect(opened).toHaveLength(1)
  })

  test('A chi tocca: a chase is one key to copy', async ($, on) => {
    seed(on, { wizard: WIZARD, desk: DESK, view: { ...EMPTY_VIEW, section: 'whose' } })
    const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
    expect(await ui.find({ type: 'Text', text: /fporcari · 1 da mergiare/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /nessuno · 12 issue/ })).toBeDefined()
    expect(await ui.find({ key: 'copy-1' })).toBeDefined()
    await ui.unmount()
  })
})
