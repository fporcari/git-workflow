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

export type Preparation = {
  status: string
  phrase: string
  due: (number | string)[]
  landed: (number | string)[]
  failed: Record<string, string>
  report?: string | null
}

export type Desk = { base: string; repo: string; attached: boolean }

export type TodoRow = { repo: string; n: number; title?: string | null; author?: string | null; step: string }

declare module 'claude-code' {
  interface PluginState {
    'git-workflow': {
      items: DeskItem[]
      desk: Desk | null
      notice: string | null
      closed: Record<string, string>
    }
  }
}
