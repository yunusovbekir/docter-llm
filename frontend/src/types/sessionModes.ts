import type { components } from '../api/schema'

export const modeLabels = {
  add: 'Add',
  fix: 'Fix',
  find_missing: 'Find Missing',
  translate: 'Translate',
} satisfies Record<components['schemas']['Mode'], string>
