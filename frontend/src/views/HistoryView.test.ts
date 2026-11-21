import { enableAutoUnmount, mount } from '@vue/test-utils'
import { http, HttpResponse } from 'msw'
import { createPinia, disposePinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'
import type { components } from '../api/schema'
import { createAppRouter } from '../router'
import { useToast } from '../stores/toast'
import { server } from '../test/msw'
import HistoryView from './HistoryView.vue'

const createdAt = '2026-01-10T14:00:00Z'
const first = {
  id: 12,
  project_id: 3,
  mode: 'find_missing',
  mode_params: {},
  branch_name: 'docter/docs-12',
  run_status: 'idle',
  created_at: createdAt,
  updated_at: '2026-01-12T14:00:00Z',
  change_counts: { pending: 2, applied: 3, denied: 1 },
} satisfies components['schemas']['SessionSummary']
const second = {
  ...first,
  id: 17,
  project_id: 5,
  mode: 'translate',
  branch_name: null,
} satisfies components['schemas']['SessionSummary']
const projects = [
  { id: 3, name: 'Docs' },
  { id: 5, name: 'Guide' },
]
let pinia: ReturnType<typeof createPinia>
enableAutoUnmount(afterEach)
beforeEach(() => {
  pinia = createPinia()
  server.use(
    http.get('/api/projects', () => HttpResponse.json(projects)),
    http.get('/api/sessions', () => HttpResponse.json([first, second])),
  )
})
afterEach(() => disposePinia(pinia))

async function render() {
  const router = createAppRouter(createMemoryHistory())
  await router.push('/history')
  const wrapper = mount(HistoryView, { global: { plugins: [pinia, router] } })
  const dialog = document.body.querySelector('dialog')!
  dialog.showModal = () => {
    dialog.open = true
  }
  dialog.close = () => {
    dialog.open = false
  }
  return { wrapper, router, dialog }
}

function clickDialogButton(dialog: HTMLDialogElement, label: string) {
  const button = Array.from(dialog.querySelectorAll('button')).find(
    (button) => button.textContent?.trim() === label,
  )
  if (!button) throw new Error(`Dialog button not found: ${label}`)
  button.click()
}

it('lists session details and resumes the requested session', async () => {
  const { wrapper, router } = await render()
  expect(wrapper.get('[role="status"]').text()).toBe('Loading sessions')
  await vi.waitFor(() => expect(wrapper.findAll('article')).toHaveLength(2))
  const [row, other] = wrapper.findAll('article')
  expect(row!.text()).toContain('Find Missing')
  expect(row!.text()).toContain('Pending: 2')
  expect(row!.text()).toContain('Applied: 3')
  expect(row!.text()).toContain('Denied: 1')
  expect(row!.get('time').attributes('datetime')).toBe(createdAt)
  expect(row!.get('time').text()).toBe(new Date(createdAt).toLocaleString())
  expect(row!.get('code').text()).toBe(first.branch_name)
  expect(other!.text()).toContain('Translate')
  expect(other!.text()).toContain('No branch yet')
  expect(other!.find('button[aria-label^="Copy"]').exists()).toBe(false)

  await row!.get('a').trigger('click')
  await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/sessions/12'))
})

it('sends the selected project ID and omits it when clearing the filter', async () => {
  const list = vi.fn()
  server.use(
    http.get('/api/sessions', ({ request }) => {
      const projectId = new URL(request.url).searchParams.get('project_id')
      list(projectId)
      return HttpResponse.json(projectId === '5' ? [second] : [first, second])
    }),
  )
  const { wrapper } = await render()
  const select = wrapper.get('select')
  await vi.waitFor(() => expect(select.text()).toContain('Guide'))
  await vi.waitFor(() => expect(wrapper.findAll('article')).toHaveLength(2))
  await select.setValue('5')
  await vi.waitFor(() => expect(wrapper.findAll('article')).toHaveLength(1))
  expect(wrapper.get('article').text()).toContain('Guide')
  await select.setValue('')
  await vi.waitFor(() => expect(wrapper.findAll('article')).toHaveLength(2))
  expect(list.mock.calls).toEqual([[null], ['5'], [null]])
})

it('shows a load error and retries into the empty state', async () => {
  const message = 'Session history is unavailable.'
  server.use(
    http.get(
      '/api/sessions',
      () => HttpResponse.json({ error: { code: 'unavailable', message } }, { status: 503 }),
      { once: true },
    ),
    http.get('/api/sessions', () => HttpResponse.json([])),
  )
  const { wrapper } = await render()
  await vi.waitFor(() => expect(wrapper.text()).toContain(message))
  await wrapper.get('section button').trigger('click')
  await vi.waitFor(() => expect(wrapper.text()).toContain('No sessions yet'))
})

it('copies the exact branch name and reports success', async () => {
  const clipboard = { writeText: vi.fn().mockResolvedValue(undefined) }
  const original = Object.getOwnPropertyDescriptor(navigator, 'clipboard')
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: clipboard })
  try {
    const { wrapper } = await render()
    const selector = 'button[aria-label="Copy branch name for session 12"]'
    await vi.waitFor(() => expect(wrapper.find(selector).exists()).toBe(true))
    await wrapper.get(selector).trigger('click')
    expect(clipboard.writeText).toHaveBeenCalledExactlyOnceWith(first.branch_name)
    expect(useToast(pinia).toasts).toMatchObject([
      { kind: 'success', message: 'Branch name copied.' },
    ])
  } finally {
    if (original) Object.defineProperty(navigator, 'clipboard', original)
    else Reflect.deleteProperty(navigator, 'clipboard')
  }
})

it('only deletes after confirming the branch is kept, and removes just that row', async () => {
  const remove = vi.fn()
  server.use(
    http.delete('/api/sessions/:id', ({ params }) => {
      remove(params.id)
      return new HttpResponse(null, { status: 204 })
    }),
  )
  const { wrapper, dialog } = await render()
  const selector = 'button[aria-label="Delete session 12"]'
  await vi.waitFor(() => expect(wrapper.find(selector).exists()).toBe(true))
  await wrapper.get(selector).trigger('click')
  expect(dialog.open).toBe(true)
  expect(dialog.textContent).toContain('The Git branch, if one exists, will be kept.')
  clickDialogButton(dialog, 'Cancel')
  await vi.waitFor(() => expect(dialog.open).toBe(false))
  expect(remove).not.toHaveBeenCalled()

  await wrapper.get(selector).trigger('click')
  clickDialogButton(dialog, 'Delete session')
  await vi.waitFor(() => expect(dialog.open).toBe(false))
  expect(remove).toHaveBeenCalledExactlyOnceWith('12')
  expect(wrapper.get('article').text()).toContain('Session #17')
  expect(wrapper.find(selector).exists()).toBe(false)
})

it('keeps the session and confirmation open if deletion fails', async () => {
  const message = 'The worktree has uncommitted edits.'
  server.use(
    http.delete('/api/sessions/:id', () =>
      HttpResponse.json({ error: { code: 'git_error', message } }, { status: 400 }),
    ),
  )
  const { wrapper, dialog } = await render()
  const selector = 'button[aria-label="Delete session 12"]'
  await vi.waitFor(() => expect(wrapper.find(selector).exists()).toBe(true))
  await wrapper.get(selector).trigger('click')
  clickDialogButton(dialog, 'Delete session')
  await vi.waitFor(() => expect(dialog.querySelector('[role="alert"]')?.textContent).toBe(message))
  expect(dialog.open).toBe(true)
  expect(wrapper.find(selector).exists()).toBe(true)
})
