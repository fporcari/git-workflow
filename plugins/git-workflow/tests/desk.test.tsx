import type { On } from 'claude-code'
import { describe, expect, test } from 'claude-code/testing'

import type { DeskItem } from '../types'
import { bareGoAhead, itemsOf, pollHeaders, shownAs, statusLine, transitions, unclosed } from '../hooks/desk'

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
      'PR · pr-loop #1145 aspetta te: digest: 3 pronte',
      'ISSUE · issue-loop #7 finito: 2 PR aperte',
    ])
  })

  test('the status line counts what works and what waits, per kind', () => {
    expect(statusLine([
      item({ status: 'running' }), item({ status: 'needs-input' }),
      item({ tag: 'ISSUE', status: 'queued' }),
    ])).toBe('PR ⏳1 ⏸1 · ISSUE ⏳1')
    expect(statusLine([])).toBeUndefined()
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
    expect(got.drop).toContain('PR pr-loop #1145')
    expect(got.drop).toContain('ISSUE issue-loop #7')
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

  test('a poll with the pane closed says it is in the background', () => {
    expect(pollHeaders('e1', false)).toEqual({ 'If-None-Match': 'e1', 'X-Git-Workflow-Background': '1' })
    expect(pollHeaders(null, true)).toEqual({})
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
