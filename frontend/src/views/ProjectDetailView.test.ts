import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { http, HttpResponse } from 'msw'
import { createPinia, disposePinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, RouterView } from 'vue-router'
import type { components } from '../api/schema'
import { createAppRouter } from '../router'
import { useToast } from '../stores/toast'
import { server } from '../test/msw'

const project = {
  id: 8,
  name: 'docs',
  path: '/home/user/repos/docs',
  current_branch: 'main',
  index_status: 'idle',
  indexed_at: null,
  created_at: '2026-01-01T00:00:00Z',
} satisfies components['schemas']['ProjectRead']

type IndexStatus = components['schemas']['ProjectIndexStatus']

const index = '/api/projects/8/index'
const indexedAt = '2026-01-02T00:00:00Z'
const running = {
  status: 'running',
  progress: 42,
  message: 'Scanning repository',
} satisfies IndexStatus
let pinia: ReturnType<typeof createPinia>

enableAutoUnmount(afterEach)
beforeEach(() => {
  pinia = createPinia()
  server.use(http.get('/api/projects/8', () => HttpResponse.json(project)))
})
afterEach(() => {
  disposePinia(pinia)
  vi.useRealTimers()
})

async function render() {
  const router = createAppRouter(createMemoryHistory())
  await router.push('/projects/8')
  const wrapper = mount(RouterView, { global: { plugins: [pinia, router] } })
  return { router, wrapper }
}

function mockStatus(...responses: [IndexStatus, ...IndexStatus[]]) {
  const read = vi.fn()
  server.use(
    http.get(`${index}/status`, () => {
      read()
      const result = responses[0]
      if (responses.length > 1) responses.shift()
      return HttpResponse.json(result)
    }),
  )
  return read
}

async function tick(ms: number) {
  await vi.advanceTimersByTimeAsync(ms)
  await flushPromises()
}

it('shows project details, starts indexing, and polls every second until done', async () => {
  const read = mockStatus({ status: 'idle', progress: 0 }, running, {
    status: 'done',
    progress: 100,
    indexed_at: indexedAt,
  })
  const post = vi.fn()
  server.use(
    http.post(index, () => {
      post()
      return HttpResponse.json(
        { ...running, progress: 0, message: 'Starting indexing' },
        { status: 202 },
      )
    }),
  )
  const { wrapper } = await render()
  expect(wrapper.text()).toContain('Loading project')
  await vi.waitFor(() => expect(wrapper.get('[role="status"]').text()).toBe('Not indexed'))
  expect(wrapper.get('h1').text()).toBe(project.name)
  expect(wrapper.text()).toContain(project.path)
  expect(wrapper.text()).toContain(project.current_branch)
  expect(wrapper.text()).toContain('Never')
  expect(wrapper.get('button').text()).toBe('Index')

  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  await wrapper.get('button').trigger('click')
  await flushPromises()
  expect(post).toHaveBeenCalledTimes(1)
  expect(wrapper.get('[role="status"]').text()).toBe('Starting indexing')
  expect(wrapper.get('button').attributes('disabled')).toBeDefined()

  await tick(999)
  expect(read).toHaveBeenCalledTimes(1)
  await tick(1)
  expect(read).toHaveBeenCalledTimes(2)
  expect(wrapper.get('[role="status"]').text()).toBe(running.message)
  expect(wrapper.get('progress').attributes('value')).toBe('42')

  await tick(1_000)
  expect(read).toHaveBeenCalledTimes(3)
  expect(wrapper.get('[role="status"]').text()).toBe('Indexing complete')
  expect(wrapper.get('time').attributes('datetime')).toBe(indexedAt)
  expect(wrapper.get('button').text()).toBe('Re-index')
  expect(wrapper.get('button').attributes('disabled')).toBeUndefined()
  await tick(3_000)
  expect(read).toHaveBeenCalledTimes(3)
})

it('shows a 409 warning and joins the running job without retrying the POST', async () => {
  const read = mockStatus({ status: 'idle', progress: 0 }, running, {
    status: 'done',
    progress: 100,
    indexed_at: indexedAt,
  })
  const post = vi.fn()
  server.use(
    http.post(index, () => {
      post()
      return HttpResponse.json(
        { error: { code: 'index_already_running', message: 'A job is running' } },
        { status: 409 },
      )
    }),
  )
  const { wrapper } = await render()
  await vi.waitFor(() => expect(wrapper.get('[role="status"]').text()).toBe('Not indexed'))

  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  await wrapper.get('button').trigger('click')
  await flushPromises()
  expect(useToast(pinia).toasts).toMatchObject([
    { kind: 'warning', message: 'Indexing already running' },
  ])
  expect(read).toHaveBeenCalledTimes(2)
  expect(wrapper.get('progress').attributes('value')).toBe('42')
  await tick(1_000)
  expect(read).toHaveBeenCalledTimes(3)
  expect(post).toHaveBeenCalledTimes(1)
  expect(wrapper.get('[role="status"]').text()).toBe('Indexing complete')
})

it('resumes an existing job, shows the failure, and keeps the last successful indexing time', async () => {
  const read = mockStatus(
    { ...running, indexed_at: indexedAt },
    {
      ...running,
      status: 'failed',
      message: 'Indexing failed',
      error: 'Embedding service unavailable',
      indexed_at: indexedAt,
    },
  )
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const { wrapper } = await render()
  await flushPromises()
  expect(wrapper.get('[role="status"]').text()).toBe(running.message)
  await tick(1_000)

  expect(wrapper.get('[role="status"]').text()).toBe('Indexing failed')
  expect(wrapper.get('[role="alert"]').text()).toBe('Embedding service unavailable')
  expect(wrapper.get('time').attributes('datetime')).toBe(indexedAt)
  expect(wrapper.get('button').text()).toBe('Re-index')
  expect(wrapper.get('button').attributes('disabled')).toBeUndefined()
  await tick(3_000)
  expect(read).toHaveBeenCalledTimes(2)
})

it('stops polling when switching projects or leaving the detail view', async () => {
  const read = mockStatus(running)
  const readOther = vi.fn()
  server.use(
    http.get('/api/projects/9', () => HttpResponse.json({ ...project, id: 9, name: 'Other' })),
    http.get('/api/projects/9/index/status', () => {
      readOther()
      return HttpResponse.json(running)
    }),
  )
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const { router, wrapper } = await render()
  await flushPromises()
  expect(read).toHaveBeenCalledTimes(1)

  await router.push('/projects/9')
  await flushPromises()
  expect(wrapper.get('h1').text()).toBe('Other')
  await tick(1_000)
  expect(read).toHaveBeenCalledTimes(1)
  expect(readOther).toHaveBeenCalledTimes(2)

  await router.push('/history')
  await tick(3_000)
  expect(readOther).toHaveBeenCalledTimes(2)
})

it('shows an error when the status check fails and lets the user retry', async () => {
  mockStatus({ status: 'idle', progress: 0 })
  server.use(
    http.get(
      `${index}/status`,
      () =>
        HttpResponse.json(
          { error: { code: 'unavailable', message: 'Status unavailable' } },
          { status: 503 },
        ),
      { once: true },
    ),
  )
  const { wrapper } = await render()
  await vi.waitFor(() => expect(wrapper.get('[role="alert"]').text()).toBe('Status unavailable'))
  expect(useToast(pinia).toasts).toMatchObject([{ kind: 'error', message: 'Status unavailable' }])
  await wrapper.findAll('button')[1]!.trigger('click')
  await vi.waitFor(() => expect(wrapper.get('[role="status"]').text()).toBe('Not indexed'))
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
})
