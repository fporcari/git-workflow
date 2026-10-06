import type { ElementTable, Elements, RenderElement } from 'claude-code'

import type { Card, Desk, Person, View, Wizard, Zoom } from '../types'
import {
  SECTIONS, currentStep, draftOf, isChecked, key, primary, section, sectionOf, selected,
  stepRows,
} from './wizard'

export type Handlers = {
  section: (id: string) => void
  step: (id: string) => void
  toggle: (card: Card) => void
  pick: (card: Card) => void
  move: (delta: number) => void
  zoom: (card: Card | null) => void
  primary: () => void
  next: () => void
  doubt: (which: 'a' | 'r' | 's') => void
  option: (card: Card, index: number) => void
  loop: (card: Card, flow: 'pr-loop' | 'issue-loop') => void
  copy: (text: string) => void
  draft: (card: Card, text: string) => void
  launch: () => void
}

export type Model = {
  desk: Desk | null
  wizard: Wizard | null
  view: View
  zooms: Record<string, Zoom>
  columns: number
  on: Handlers
}

type E = ElementTable
type Field = Elements['terminal']['Input']

// the mobile surface draws no field yet: the text shows, the edit waits for a desk that has one
const fieldOf = (E: E) => ('Input' in E ? (E as { Input: Field }).Input : null)

const NARROW = 60
const LEADS: Record<string, string> = {
  'review:approve': 'Claude le approverebbe: piccole, mirate, con il test. Già spuntate: togli quelle che vuoi leggere tu.',
  'review:changes': 'Claude le respingerebbe. Già spuntate: la motivazione è sotto la riga selezionata.',
  'review:doubt': 'Le uniche su cui serve la tua testa, una alla volta.',
  'mine:merge': 'Approvate, CLEAN, niente in sospeso: pr-loop le mergia secondo le regole di casa.',
  'mine:fix': 'Richieste chiare o riallineamenti: un worktree per PR, in background.',
  'mine:decide': 'Il revisore chiede una scelta che è tua.',
  'mine:waiting': 'La palla è di qualcun altro: il sollecito è pronto da copiare.',
  'issue:close': 'Già risolte da una PR mergiata: si chiudono con il commento che la nomina.',
  'issue:claude': 'Facili, in una fase sola, di nessuno: una PR ciascuna, in background.',
  'issue:decide': 'Domande o lavori lunghi: decidi tu se, chi e come.',
}
const CHIP: Record<string, [string, string]> = {
  approve: ['green', 'approvabile'], changes: ['red', 'da respingere'], doubt: ['yellow', 'dubbia'],
}
const age = (d?: number | null) => (d == null ? '' : `${d}g`)
const name = (c: Card) => c.label ?? `#${c.n}`

function header(E: E, m: Model) {
  const { Box, Text, Button } = E
  return (
    <Box flexDirection="row" gap={1} flexWrap="wrap">
      {SECTIONS.map(s => {
        const x = section(m.wizard, s.id)
        const count = typeof x?.count === 'number' ? ` ${x.count}` : ''
        return (
          <Button key={`sec-${s.id}`} plain dimColor={s.id !== m.view.section}
            onPress={() => m.on.section(s.id)}>{`${s.label}${count}`}</Button>
        )
      })}
      <Text color={m.desk?.attached ? 'green' : undefined} dimColor={!m.desk?.attached}>
        {m.desk?.attached ? '● chat collegata' : '○ chat non collegata'}</Text>
    </Box>
  )
}

function stepper(E: E, m: Model, step: string) {
  const { Box, Button } = E
  const def = sectionOf(m.view.section)
  if (!def.steps.length) return null
  const narrow = m.columns < NARROW
  return (
    <Box flexDirection="row" gap={1} flexWrap="wrap">
      {def.steps.map((s, i) => {
        const rows = s.id === 'done' ? [] : stepRows(m.wizard, m.view.section, s.id)
        const done = rows.length > 0 && rows.every(c => c.sent || c.sending)
        const cur = s.id === step
        const label = narrow && !cur ? '' : ` ${done && s.done ? s.done : s.label}`
        const count = s.id === 'done' ? '' : ` ${rows.length}`
        return (
          <Button key={`step-${s.id}`} plain dimColor={!cur}
            onPress={() => m.on.step(s.id)}>{`${done ? '✓' : i + 1}${label}${count}`}</Button>
        )
      })}
    </Box>
  )
}

function row(E: E, m: Model, step: string, c: Card, opts: { check?: boolean; chip?: string; cell?: string } = {}) {
  const { Box, Text, Button, Link } = E
  const picked = selected(m.view, m.wizard)
  const isSel = picked && key(picked) === key(c)
  const mark = c.sent ? 'inviata' : c.sending ? 'in invio…' : c.loop ? 'in background' : opts.chip ?? opts.cell ?? ''
  return (
    <Box key={`row-${key(c)}`} flexDirection="row" gap={1}>
      {opts.check
        ? <Button key={`tog-${key(c)}`} plain onPress={() => m.on.toggle(c)}>
            {isChecked(m.view, m.view.section, step, c) ? '[x]' : '[ ]'}</Button>
        : <Text dimColor>{isSel ? '›' : ' '}</Text>}
      <Box flexDirection="column" flexGrow={1} flexShrink={1}>
        <Button key={`pick-${key(c)}`} plain dimColor={!isSel} onPress={() => m.on.pick(c)}>
          {`${name(c)} ${c.title ?? ''}`}</Button>
        <Text dimColor wrap="truncate-end">{c.why ?? c.todo ?? ''}{mark ? `  · ${mark}` : ''}</Text>
      </Box>
      {c.url ? <Link href={c.url} label="↗" /> : null}
    </Box>
  )
}

function field(E: E, m: Model, c: Card) {
  const { Text } = E
  const Input = fieldOf(E)
  if (!Input) return <Text>{draftOf(m.view, c)}</Text>
  return (
    <Input key={`draft-${key(c)}`} value={draftOf(m.view, c)}
      onInput={(text: string) => m.on.draft(c, text)} onSubmit={(text: string) => m.on.draft(c, text)} />
  )
}

function editor(E: E, m: Model, c: Card, labelText: string) {
  const { Box, Text } = E
  return (
    <Box key={`edit-${key(c)}`} flexDirection="column" paddingLeft={4}>
      <Text color="red" bold>{labelText}</Text>
      {field(E, m, c)}
    </Box>
  )
}

function list(E: E, m: Model, step: string, check: boolean,
              below?: (c: Card, isSel: boolean) => RenderElement | null) {
  const { Box } = E
  const rows = stepRows(m.wizard, m.view.section, step)
  const picked = selected(m.view, m.wizard)
  return (
    <Box flexDirection="column">
      {rows.map(c => {
        const isSel = !!picked && key(picked) === key(c)
        return (
          <Box key={`item-${key(c)}`} flexDirection="column">
            {row(E, m, step, c, { check })}
            {below ? below(c, isSel) : null}
          </Box>
        )
      })}
    </Box>
  )
}

function hunkCode(E: E, hunk: Card['hunk']) {
  const { Code, Text } = E
  if (!hunk) return null
  if (!hunk.lines?.length) return <Text dimColor>{hunk.header} · {hunk.error ?? 'il diff non è leggibile qui'}</Text>
  return <Code format="diff" path={hunk.path} source={[hunk.header, ...hunk.lines].join('\n') + '\n'} />
}

function doubtView(E: E, m: Model) {
  const { Box, Text } = E
  const rows = stepRows(m.wizard, 'review', 'doubt')
  const c = selected(m.view, m.wizard)
  if (!c) return <Text dimColor>Nessuna PR dubbia.</Text>
  const at = Math.min(m.view.doubtAt, rows.length - 1)
  const zoom = m.zooms[key(c)]
  const lean = c.lean === 'approve' ? 'approvare' : c.lean === 'changes' ? 'chiedere modifiche' : '—'
  return (
    <Box flexDirection="column" gap={1}>
      <Text dimColor>{`dubbia ${at + 1} di ${rows.length}`}</Text>
      <Box flexDirection="column">
        <Text bold>{`${name(c)} ${c.title ?? ''}`}</Text>
        <Text dimColor>{`${c.author ?? ''} · ${age(c.age)}${c.sent ? ' · inviata' : c.sending ? ' · in invio…' : ''}`}</Text>
      </Box>
      <Box flexDirection="column">
        <Text color="yellow" bold>IL DUBBIO</Text>
        <Text>{c.doubt ?? c.why ?? ''}</Text>
      </Box>
      {c.hunk ? (
        <Box flexDirection="column">
          <Text dimColor bold>{`DOVE · ${c.hunk.path}`}</Text>
          {zoom ? hunkCode(E, zoom.hunk ?? null) : <Text dimColor>leggo il pezzo di diff…</Text>}
        </Box>) : null}
      <Box flexDirection="column">
        <Text dimColor bold>{`CLAUDE PROPENDE PER · ${lean}`}</Text>
        {c.lean === 'changes' ? field(E, m, c) : null}
      </Box>
    </Box>
  )
}

function people(E: E, m: Model) {
  const { Box, Text, Button } = E
  const ppl: Person[] = m.wizard?.whose ?? []
  return (
    <Box flexDirection="column">
      {ppl.map((p, i) => {
        if (p.me) return (
          <Text key={`p-${i}`}>{`tu · ${p.review} da rivedere · ${p.mine} mie da muovere · ` +
            `${p.issues_mine} issue tue, ${p.for_claude} le può fare Claude`}</Text>)
        if (p.who == null) return <Text key={`p-${i}`} dimColor>{`nessuno · ${p.unassigned} issue senza un responsabile`}</Text>
        const parts = ([['merge', 'da mergiare'], ['fix', 'da correggere'], ['review', 'review ferme'],
          ['wait', 'in attesa'], ['issues', 'issue senza PR']] as const)
          .map(([k, label]) => [((p[k] as number[] | undefined) ?? []).length, label] as const)
          .filter(([n]) => n).map(([n, label]) => `${n} ${label}`).join(' · ')
        return (
          <Box key={`p-${i}`} flexDirection="row" gap={1}>
            <Text>{`${p.who} · ${parts}`}</Text>
            {p.chase ? <Button key={`copy-${i}`} plain dimColor onPress={() => m.on.copy(p.chase!)}>copia sollecito</Button> : null}
          </Box>
        )
      })}
    </Box>
  )
}

function done(E: E, m: Model) {
  const { Box, Text } = E
  const x = section(m.wizard, m.view.section)
  const sum = x?.steps.find(s => s.id === 'done')?.summary ?? {}
  const line = (label: string, ns?: number[]) =>
    ns?.length ? <Text key={label}>{`${ns.length} ${label} · ${ns.map(n => `#${n}`).join(' ')}`}</Text> : null
  return (
    <Box flexDirection="column" gap={1}>
      <Text dimColor bold>OGGI</Text>
      {m.view.section === 'review'
        ? [line('approvate', sum.approve), line('richieste di modifica', sum.changes), line('saltate a domani', sum.skip)]
        : [line('chiuse', sum.close)]}
      <Text dimColor bold>A CHI TOCCA ADESSO</Text>
      {people(E, m)}
    </Box>
  )
}

function prepare(E: E, m: Model) {
  const { Box, Text } = E
  const r = section(m.wizard, 'review')
  const p = m.wizard?.prepare?.pr
  const ready = (r?.steps ?? []).filter(s => s.id !== 'done')
    .flatMap(s => s.rows.map(c => ({ c, chip: CHIP[s.id] })))
  return (
    <Box flexDirection="column" gap={1}>
      <Text>{ready.length ? 'Puoi già cominciare: ' : ''}{p?.phrase ?? ''}</Text>
      <Box flexDirection="column">
        {ready.map(({ c, chip }) => (
          <Text key={`prep-${key(c)}`} color={chip?.[0]} wrap="truncate-end">
            {`${(chip?.[1] ?? '').padEnd(14)}${name(c)} ${c.title ?? ''}`}</Text>
        ))}
        {(r?.pending ?? []).map(c => (
          <Text key={`prep-${key(c)}`} dimColor wrap="truncate-end">{`${(c.chip ?? 'in coda').padEnd(14)}${name(c)} ${c.title ?? ''}`}</Text>
        ))}
      </Box>
    </Box>
  )
}

function decide(E: E, m: Model, step: string) {
  const { Box, Text, Button } = E
  return list(E, m, step, false, (c, isSel) => isSel ? (
    <Box key={`dec-${key(c)}`} flexDirection="column" paddingLeft={2}>
      {c.ask ? <Text color="yellow">{`IL REVISORE CHIEDE · ${c.ask}`}</Text> : <Text color="yellow">{c.todo ?? ''}</Text>}
      {c.why ? <Text dimColor>{`CLAUDE · ${c.why}`}</Text> : null}
      <Box flexDirection="row" gap={1} flexWrap="wrap">
        {c.options?.length
          ? c.options.map((o, i) => (
            <Button key={`opt-${i}`} hotkey={String(i + 1)} variant={i === 0 ? 'primary' : undefined}
              onPress={() => m.on.option(c, i)}>{o}</Button>))
          : <Button key="loop-one" variant="primary"
              onPress={() => m.on.loop(c, m.view.section === 'issue' ? 'issue-loop' : 'pr-loop')}>Lavorala in chat</Button>}
      </Box>
    </Box>) : null)
}

function waiting(E: E, m: Model) {
  const { Box, Text, Button } = E
  const rows = stepRows(m.wizard, 'mine', 'waiting')
  const chase = section(m.wizard, 'mine')?.chase ?? {}
  const who: Record<string, Card[]> = {}
  rows.forEach(c => { (who[c.who ?? '?'] ??= []).push(c) })
  return (
    <Box flexDirection="column" gap={1}>
      {Object.entries(who).map(([w, cs]) => (
        <Box key={`w-${w}`} flexDirection="column">
          <Box flexDirection="row" gap={1}>
            <Text bold>{`@${w} · ${cs.length} PR`}</Text>
            {chase[w] ? <Button key={`chase-${w}`} plain dimColor onPress={() => m.on.copy(chase[w]!)}>copia sollecito</Button> : null}
          </Box>
          {cs.map(c => <Text key={`wt-${key(c)}`} dimColor wrap="truncate-end">{`${name(c)} ${c.title ?? ''}`}</Text>)}
        </Box>
      ))}
    </Box>
  )
}

function body(E: E, m: Model, step: string | null) {
  const { Text } = E
  const s = m.view.section
  if (s === 'whose') return people(E, m)
  if (!step) return null
  if (step === 'prepare') return prepare(E, m)
  if (step === 'done') return done(E, m)
  if (s === 'review' && step === 'doubt') return doubtView(E, m)
  if (s === 'mine' && step === 'decide') return decide(E, m, step)
  if (s === 'issue' && step === 'decide') return decide(E, m, step)
  if (s === 'mine' && step === 'waiting') return waiting(E, m)
  if (!stepRows(m.wizard, s, step).length) return <Text dimColor>Niente in questo passo, per ora.</Text>
  if (s === 'review' && step === 'changes')
    return list(E, m, step, true, (c, isSel) => (isSel ? editor(E, m, c, 'MOTIVAZIONE') : null))
  if (s === 'issue' && step === 'close')
    return list(E, m, step, true, (c, isSel) => (isSel ? editor(E, m, c, 'COMMENTO DI CHIUSURA') : null))
  return list(E, m, step, true)
}

function footer(E: E, m: Model, step: string | null) {
  const { Box, Text, Button } = E
  const main = primary(m.view, m.wizard)
  const doubt = m.view.section === 'review' && step === 'doubt'
  const blocked = !!main?.action?.needsChat && !m.desk?.attached
  return (
    <Box flexDirection="column">
      <Box flexDirection="row" gap={1} flexWrap="wrap">
        {doubt ? [
          <Button key="doubt-a" hotkey="a" variant={selected(m.view, m.wizard)?.lean === 'approve' ? 'primary' : undefined}
            onPress={() => m.on.doubt('a')}>Approva</Button>,
          <Button key="doubt-r" hotkey="r" variant={selected(m.view, m.wizard)?.lean === 'changes' ? 'primary' : undefined}
            onPress={() => m.on.doubt('r')}>Chiedi modifiche</Button>,
          <Button key="doubt-s" hotkey="s" onPress={() => m.on.doubt('s')}>Salta a domani</Button>,
        ] : main?.action && !blocked
          ? <Button key="primary" variant="primary" autoFocus onPress={m.on.primary}>{main.label}</Button>
          : main ? <Text dimColor>{main.label}</Text> : null}
        {step === 'done'
          ? <Button key="next" onPress={m.on.next}>{m.view.section === 'review' ? 'Passa alle issue' : 'Passa a chi tocca'}</Button>
          : step && m.view.section !== 'whose' ? <Button key="next" plain dimColor onPress={m.on.next}>salta il passo</Button> : null}
      </Box>
      {blocked || (doubt && !m.desk?.attached)
        ? <Text dimColor>serve la chat collegata: le azioni pubbliche partono da lì</Text>
        : m.view.section === 'review' && step === 'approve' ? <Text dimColor>il merge resta a chi ha aperto la PR</Text> : null}
    </Box>
  )
}

function keys(E: E, m: Model) {
  const { Box, Button } = E
  return (
    <Box flexDirection="row" gap={1} flexWrap="wrap">
      <Button key="key-j" plain dimColor hotkey="j" onPress={() => m.on.move(1)}>giù</Button>
      <Button key="key-k" plain dimColor hotkey="k" onPress={() => m.on.move(-1)}>su</Button>
      <Button key="key-x" plain dimColor hotkey="x" onPress={() => { const c = selected(m.view, m.wizard); if (c) m.on.toggle(c) }}>spunta</Button>
      <Button key="key-z" plain dimColor hotkey="z" onPress={() => m.on.zoom(selected(m.view, m.wizard) ?? null)}>tutta la situazione</Button>
    </Box>
  )
}

function zoomView(E: E, m: Model) {
  const { Box, Text, Button, Link } = E
  const rows = stepRows(m.wizard, m.view.section, currentStep(m.view, m.wizard) ?? '')
  const c = rows.find(x => key(x) === m.view.zoom) ?? m.zooms[m.view.zoom!]?.card
  const z = m.zooms[m.view.zoom!]
  if (!c) return <Text dimColor>Questa PR non è più in questo passo.</Text>
  const st = z?.state ?? {}
  return (
    <Box flexDirection="column" gap={1}>
      <Box flexDirection="row" gap={1}>
        <Button key="unzoom" hotkey="z" onPress={() => m.on.zoom(null)}>‹ Indietro</Button>
        <Button key="zoom-j" plain dimColor hotkey="j" onPress={() => m.on.move(1)}>PR dopo</Button>
        <Button key="zoom-k" plain dimColor hotkey="k" onPress={() => m.on.move(-1)}>PR prima</Button>
        {c.url ? <Link href={c.url} label="Apri su GitHub" /> : null}
      </Box>
      <Box flexDirection="column">
        <Text bold>{`${name(c)} ${c.title ?? ''}`}</Text>
        <Text dimColor>{`${c.author ?? ''} · aperta ${c.age ?? '?'} giorni fa`}</Text>
      </Box>
      {!z ? <Text dimColor>Leggo la situazione…</Text> : [
        <Box key="brief" flexDirection="column"><Text dimColor bold>IN BREVE</Text><Text>{z.problem ?? c.why ?? 'Nessuna analisi ancora.'}</Text></Box>,
        c.doubt ? <Box key="why" flexDirection="column"><Text color="yellow" bold>PERCHÉ È DUBBIA</Text><Text>{c.doubt}</Text></Box> : null,
        z.hunk ? <Box key="hunk" flexDirection="column"><Text dimColor bold>{`DOVE · ${z.hunk.path}`}</Text>{hunkCode(E, z.hunk)}</Box> : null,
        <Box key="checked" flexDirection="column"><Text dimColor bold>COSA HA VERIFICATO CLAUDE</Text>
          {(z.verified ?? []).map((v, i) => <Text key={`v-${i}`} color="green">{`✓ ${v}`}</Text>)}
          {(z.not_verified ?? []).map((v, i) => <Text key={`nv-${i}`} color="yellow">{`? non verificato: ${v}`}</Text>)}</Box>,
        <Box key="story" flexDirection="column"><Text dimColor bold>LA STORIA</Text>
          {(z.timeline ?? []).map((t, i) => <Text key={`t-${i}`} color={t.now ? 'yellow' : undefined}>{`${t.on}  ${t.text}`}</Text>)}</Box>,
        <Box key="state" flexDirection="column"><Text dimColor bold>STATO</Text>
          <Text>{`test ${st.tests === 'SUCCESS' ? 'verdi' : st.tests ?? 'non letti'} · merge ${st.merge ?? '—'} · conflitti ${st.conflicts ?? '—'}`}</Text>
          <Text>{`revisori ${(st.reviewers ?? []).join(', ') || 'nessuno'}`}</Text>
          {(z.closes ?? []).length ? <Text>{`collegate ${(z.closes ?? []).map(n => `#${n}`).join(' ')}`}</Text> : null}</Box>,
      ]}
      {m.view.section === 'review' && ['approve', 'changes', 'doubt'].includes(currentStep(m.view, m.wizard) ?? '')
        ? footer(E, { ...m, view: { ...m.view, steps: { ...m.view.steps, review: 'doubt' } } }, 'doubt')
        : null}
    </Box>
  )
}

/** The whole pane: header, steps, the step's rows, its keys. */
export function drawPane(E: E, m: Model) {
  const { Box, Text, Button } = E
  if (!m.desk) return (
    <Box flexDirection="column" gap={1}>
      <Text bold>Git desk</Text>
      <Text dimColor>Nessun desk aperto per questo repository.</Text>
      <Button key="launch" variant="primary" autoFocus onPress={m.on.launch}>Apri il desk</Button>
    </Box>
  )
  if (!m.wizard) return <Text dimColor>Leggo il desk…</Text>
  if (m.view.zoom) return zoomView(E, m)
  const step = currentStep(m.view, m.wizard)
  const lead = step ? LEADS[`${m.view.section}:${step}`] : 'Per ogni persona, le PR e le issue in cui la prossima mossa è sua.'
  return (
    <Box flexDirection="column" gap={1}>
      {header(E, m)}
      {step ? stepper(E, m, step) : null}
      {lead ? <Text dimColor>{lead}</Text> : null}
      {body(E, m, step)}
      {footer(E, m, step)}
      {m.view.section !== 'whose' ? keys(E, m) : null}
    </Box>
  )
}
