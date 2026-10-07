export type Tag = 'PR' | 'ISSUE'

export type DeskItem = {
  key: string
  repo: string
  session: string
  tag: Tag
  label: string
  status: string
  at: string
  report: string
}

export type Hunk = { path: string; header: string; lines?: string[]; error?: string; missing?: boolean; cut?: boolean }

export type Card = {
  n: number
  repo?: string | null
  label?: string
  title?: string
  author?: string
  age?: number | null
  url?: string
  head?: string | null
  why?: string | null
  labels?: string[]
  stance?: string | null
  draft?: string | null
  doubt?: string | null
  lean?: string | null
  hunk?: Hunk | null
  ask?: string | null
  options?: string[] | null
  todo?: string
  who?: string | null
  type?: string
  size?: string
  body?: string
  fixed_by?: number
  chip?: string
  loop?: string
  sending?: string | boolean
  sent?: string | boolean
}

export type Step = { id: string; rows: Card[]; summary?: Record<string, number[]> }

export type Section = {
  count: number
  steps: Step[]
  first: string
  pending?: Card[]
  skipped?: Card[]
  waiting_author?: number
  chase?: Record<string, string>
}

export type Person = {
  who: string | null
  me?: boolean
  merge?: number[]
  fix?: number[]
  review?: number[] | number
  wait?: number[]
  issues?: number[]
  chase?: string | null
  total?: number
  mine?: number
  issues_mine?: number
  for_claude?: number
  unassigned?: number
}

export type Preparation = { status: string; phrase: string; due: number[]; landed: number[]; failed: Record<string, string> }

export type Wizard = {
  review: Section
  mine: Section
  issue: Section | null
  whose: Person[]
  prepare: Record<'pr' | 'issue', Preparation>
}

export type Zoom = {
  card: Card
  problem?: string | null
  verified?: string[]
  not_verified?: string[]
  timeline?: { on: string; text: string; now?: boolean }[]
  state?: { tests?: string | null; merge?: string | null; conflicts?: string; reviewers?: string[] }
  closes?: number[]
  hunk?: Hunk | null
  stale?: boolean
}

export type View = {
  section: string
  steps: Record<string, string>
  off: string[]
  sel: Record<string, string>
  doubtAt: number
  zoom: string | null
  drafts: Record<string, string>
  hushed?: string
}

export type Desk = { base: string; repo: string; token: string; attached: boolean; me: string }

declare module 'claude-code' {
  interface PluginState {
    'git-workflow': {
      items: DeskItem[]
      wizard: Wizard | null
      desk: Desk | null
      view: View
      zooms: Record<string, Zoom>
    }
  }
}
