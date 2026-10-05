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

declare module 'claude-code' {
  interface PluginState {
    'desk-band': { items: DeskItem[] }
  }
}
