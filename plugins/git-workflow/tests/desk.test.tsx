import type { On } from 'claude-code'
import { describe, expect, test } from 'claude-code/testing'

import type { Desk, DeskItem, Preparation } from '../types'
import {
  bareGoAhead, deskTarget, itemsOf, pollHeaders, projectOf, shownAs, statusLine, transitions, unclosed,
} from '../hooks/desk'

const NOW = 1_800_000_000
const TYPED = { wait: false, origin: { kind: 'composer' } } as const

// the poll reads files a test has none of: the test answers the band's state
// instead, and stands for the engine beneath the prompt and the band
const seed = (on: On, items: DeskItem[]) => {
  on('state.get', ($, e, next) =>
    e.plugin === 'git-workflow' && e.key === 'items' ? { value: { value: items, version: 1 } } : next(e))
  on('prompt.submit', ($, e) => ({ text: e.text, context: e.context, origin: e.origin }))
  on('ui.render', ($, e) => {
    const { Text } = $.ui.resolve(e)
    return <Text>engine</Text>
  })
}

const item = (over: Partial<DeskItem>): DeskItem => ({
  key: 'o/r run:pr-loop', repo: 'o/r', session: 's1', tag: 'PR',
  label: 'pr-loop #1145', status: 'running', at: '22:10', report: '', ...over,
})

describe('reading a desk state file', () => {
  test('keeps the chat-routed requests, tags them, and drops the stale', () => {
    const state = {
      requests: {
        'run:pr-loop': { via: 'chat-session', session: 's1', kind: 'run', status: 'running',
          payload: { flow: 'pr-loop', ns: [1145, 1128] }, running_at: '22:10', running_epoch: NOW - 60 },
        'run:issue-loop': { via: 'chat-session', session: 's1', kind: 'run', status: 'needs-input',
          payload: { flow: 'issue-loop', ns: [7] }, epoch: NOW - 600, report: 'digest: 2 pronte' },
        'issue-analyze:9': { via: 'chat-session', session: 's1', kind: 'issue-analyze', n: 9,
          status: 'taken', taken_epoch: NOW - 7200 },
        'analyze:5': { via: 'agent', kind: 'analyze', n: 5, status: 'queued', epoch: NOW },
      },
    }
    const items = itemsOf('o/r', state, NOW)
    expect(items.map(i => [i.tag, i.label, i.status])).toEqual([
      ['PR', 'pr-loop #1145 #1128', 'running'],
      ['ISSUE', 'issue-loop #7', 'needs-input'],
    ])
  })
})

describe('what interrupts', () => {
  test('a loop that starts waiting for you, or closes, is a toast; progress is not', () => {
    const before = { a: 'running', b: 'taken', c: 'running' }
    const lines = transitions(before, [
      item({ key: 'a', status: 'needs-input', report: 'digest: 3 pronte' }),
      item({ key: 'b', status: 'running' }),
      item({ key: 'c', tag: 'ISSUE', label: 'issue-loop #7', status: 'done', report: '2 PR aperte' }),
      item({ key: 'd', status: 'needs-input' }),
    ])
    expect(lines).toEqual([
      'PR r · pr-loop #1145 aspetta te: digest: 3 pronte',
      'ISSUE r · issue-loop #7 finito: 2 PR aperte',
    ])
  })

  test('the status line counts what works and what waits, per kind', () => {
    expect(statusLine([
      item({ status: 'running' }), item({ status: 'needs-input' }),
      item({ tag: 'ISSUE', status: 'queued' }),
    ])).toBe('PR ⏳1 ⏸1 · ISSUE ⏳1')
    expect(statusLine([])).toBeUndefined()
  })

  test('a repository goes by its short name unless another in view shares it', () => {
    expect(projectOf('genropy/genropy', ['genropy/genropy', 'icond/icond'])).toBe('genropy')
    expect(projectOf('icond/icond', ['icond/icond', 'fork/icond'])).toBe('icond/icond')
  })

  test('only a bare go-ahead is ambiguous', () => {
    expect(bareGoAhead('vai')).toBe(true)
    expect(bareGoAhead(' Tutte vai! ')).toBe(true)
    expect(bareGoAhead('pr vai')).toBe(false)
    expect(bareGoAhead('vai su #1145')).toBe(false)
  })
})

describe('the guard on a bare vai', () => {
  test('two loops waiting: the bare vai does not enter', async ($, on) => {
    seed(on, [
      item({ key: 'a', status: 'needs-input' }),
      item({ key: 'b', tag: 'ISSUE', label: 'issue-loop #7', status: 'needs-input' }),
    ])
    const got = await $.prompt.submit({ ...TYPED, text: 'vai' })
    expect(got.drop).toContain('PR r pr-loop #1145')
    expect(got.drop).toContain('ISSUE r issue-loop #7')
  })

  test('one loop waiting: the vai enters, with which one it answers beside it', async ($, on) => {
    seed(on, [item({ key: 'a', status: 'needs-input' }), item({ key: 'b', status: 'running' })])
    const got = await $.prompt.submit({ ...TYPED, text: 'vai' })
    expect(got.text).toBe('vai')
    expect((got.context ?? []).join(' ')).toContain('PR pr-loop #1145')
  })

  test('a notification saying ok is not an answer', async ($, on) => {
    seed(on, [
      item({ key: 'a', status: 'needs-input' }),
      item({ key: 'b', tag: 'ISSUE', status: 'needs-input' }),
    ])
    const got = await $.prompt.submit({ wait: false, origin: { kind: 'task-notification' }, text: 'ok' })
    expect(got.text).toBe('ok')
  })

  test('a named answer always enters untouched', async ($, on) => {
    seed(on, [
      item({ key: 'a', status: 'needs-input' }),
      item({ key: 'b', tag: 'ISSUE', status: 'needs-input' }),
    ])
    const got = await $.prompt.submit({ ...TYPED, text: 'pr vai' })
    expect(got.text).toBe('pr vai')
    expect(got.context).toBeUndefined()
  })
})

describe('the band', () => {
  const BAND = {
    plugin: 'git-workflow', component: 'AbovePrompt',
    props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 120,
             scroll: { offset: 0, bodyRows: 10 }, view: {} },
  } as const

  test('one row per open request, the one waiting for you first', async ($, on) => {
    seed(on, [
      item({ key: 'a', status: 'running', at: '22:00' }),
      item({ key: 'b', tag: 'ISSUE', label: 'issue-loop #7', status: 'needs-input', at: '22:05' }),
    ])
    for (const surface of ['terminal', 'desktop'] as const) {
      const ui = await $.ui.mount({ ...BAND, surface })
      const texts = (await ui.findAll({ type: 'Text' })).map(t => t.text).join('|')
      expect(texts.indexOf('ISSUE')).toBeLessThan(texts.indexOf('PR'))
      expect(texts).toContain('aspetta te dalle 22:05')
      expect(texts).toContain('in background dalle 22:00')
      await ui.unmount()
    }
  })

  test('the mod\'s poll is never a use of the desk', () => {
    expect(pollHeaders('e1')).toEqual({ 'If-None-Match': 'e1', 'X-Git-Workflow-Background': '1' })
    expect(pollHeaders(null)).toEqual({ 'X-Git-Workflow-Background': '1' })
  })

  test('every row names its desk, even with one desk in view', async ($, on) => {
    seed(on, [item({ key: 'a', repo: 'genropy/genropy' })])
    const ui = await $.ui.mount({ ...BAND, surface: 'terminal' })
    const texts = (await ui.findAll({ type: 'Text' })).map(t => t.text).join('|')
    expect(texts).toContain('genropy · pr-loop #1145')
    await ui.unmount()
  })

  test('a closed row stays closed until what it shows changes', () => {
    const running = item({ key: 'a' })
    const closed = { a: shownAs(running) }
    expect(unclosed([running], closed)).toEqual([])
    expect(unclosed([{ ...running, status: 'needs-input' }], closed)).toHaveLength(1)
    expect(unclosed([{ ...running, report: 'PR #1150 aperta' }], closed)).toHaveLength(1)
  })

  test('every row has its close key', async ($, on) => {
    seed(on, [item({ key: 'a' })])
    const ui = await $.ui.mount({ ...BAND, surface: 'desktop' })
    expect(await ui.find({ key: 'close-a' })).toBeDefined()
    await ui.unmount()
  })

  test('nothing open: the band yields', async ($, on) => {
    seed(on, [])
    const ui = await $.ui.mount({ ...BAND, surface: 'terminal' })
    expect(await ui.find({ type: 'Text', text: 'engine' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /aspetta te|in background/ })).toBeUndefined()
    await ui.unmount()
  })
})

const prep = (status: string): Record<string, Preparation> => ({
  pr: { status, phrase: '', due: [], landed: [], failed: {} },
})

describe('the desk this chat drives', () => {
  test('the desk this chat drives is the one it is attached to', () => {
    const files = {
      'genropy__genropy.json': { mtime: 1, state: { desks: { pr: { port: 8399 } },
                                                    chats: { s1: { epoch: 1000 } } } },
      'other__repo.json': { mtime: 9, state: { desks: { pr: { port: 8400 } } } },
      'old__repo.json': { mtime: 10, state: { desks: { pr: { port: 8401, stopped: 'x' } } } },
    }
    expect(deskTarget(files, 's1', 1010)).toEqual({ port: 8399, repo: 'genropy/genropy', attached: true })
    expect(deskTarget(files, 's2', 1010)).toEqual({ port: 8400, repo: 'other/repo', attached: false })
  })
})

const DESK: Desk = { base: 'http://127.0.0.1:8399', repo: 'genropy/genropy', attached: true }
const BAND_PROPS = {
  plugin: 'git-workflow', component: 'AbovePrompt',
  props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 120,
           scroll: { offset: 0, bodyRows: 10 }, view: {} },
} as const

// the desk as the poll left it, its server answering /api/state, the Browser
// pane as one answering function, and every prompt the engine is handed
function deskSeed(on: On, desk: Desk | null, prepare: Record<string, Preparation> | null,
                  opened: unknown[], browser = true) {
  on('state.get', ($, e, next) => {
    if (e.plugin !== 'git-workflow') return next(e)
    const values: Record<string, unknown> = { desk, items: [] }
    return e.key in values ? { value: { value: values[e.key], version: 1 } } : next(e)
  })
  on('mcp.call', ($, e) => {
    opened.push(e)
    return { value: { content: [], isError: !browser } }
  })
  on('http.fetch', ($, e) => e.url.endsWith('/api/state') && prepare
    ? { value: { status: 200, ok: true, headers: {}, text: JSON.stringify({ prepare }) } }
    : { value: { status: 304, ok: false, headers: {}, text: '' } })
  on('session.id', () => ({ value: 'the-chat' }))
  on('ui.render', ($, e) => {
    const { Text } = $.ui.resolve(e)
    return <Text>engine</Text>
  })
}

describe('opening the desk', () => {
  test('while the boot triages, the skill opens the page all the same', async ($, on) => {
    deskSeed(on, DESK, prep('running'), [])
    const got = await $.tool.call({ tool: 'mcp__git-workflow__desk_open' })
    const text = String((got as { result?: unknown }).result)
    expect(text).toContain('open http://127.0.0.1:8399/ in the Browser pane now')
    expect(text).toContain('also while its triage runs')
    expect(text).toContain('session id is the-chat')
    const ui = await $.ui.mount({ ...BAND_PROPS, surface: 'terminal' })
    expect(await ui.find({ type: 'Text', text: 'engine' })).toBeDefined()
    await ui.unmount()
  })

  test('no desk answers yet: the skill is told to ask again', async ($, on) => {
    deskSeed(on, null, null, [])
    const got = await $.tool.call({ tool: 'mcp__git-workflow__desk_open' })
    const text = String((got as { result?: unknown }).result)
    expect(text).toContain('Call this tool again in a second')
    expect(text).toContain('session id is the-chat')
  })

  test('a ready desk: the skill opens its page, with the session id to attach', async ($, on) => {
    deskSeed(on, DESK, prep('done'), [])
    const got = await $.tool.call({ tool: 'mcp__git-workflow__desk_open' })
    const text = String((got as { result?: unknown }).result)
    expect(text).toContain('open http://127.0.0.1:8399/ in the Browser pane')
    expect(text).toContain('session id is the-chat')
  })

  test('/desk opens the page in the Browser pane', async ($, on) => {
    const opened: unknown[] = []
    deskSeed(on, DESK, null, opened)
    await $.command.run({ command: 'desk', args: '' })
    expect(opened).toHaveLength(1)
  })

  test('a host without a Browser pane gets the link', async ($, on) => {
    deskSeed(on, DESK, null, [], false)
    const got = await $.command.run({ command: 'desk', args: '' })
    expect(got.text).toBe('Desk di genropy/genropy: http://127.0.0.1:8399/')
  })

  test('no desk: /desk asks the chat to launch it', async ($, on) => {
    deskSeed(on, null, null, [])
    const got = await $.command.run({ command: 'desk', args: '' })
    expect(got.context?.join(' ')).toContain('skill git-desk')
  })

  test('no notice above the prompt: with no loop open the band yields', async ($, on) => {
    deskSeed(on, DESK, null, [])
    const ui = await $.ui.mount({ ...BAND_PROPS, surface: 'desktop' })
    expect(await ui.find({ type: 'Text', text: 'engine' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /DESK/ })).toBeUndefined()
    await ui.unmount()
  })
})
