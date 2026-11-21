import { enableAutoUnmount, mount } from '@vue/test-utils'
import { http, HttpResponse } from 'msw'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { nextTick, ref } from 'vue'
import { createMemoryHistory, RouterView } from 'vue-router'
import type { components } from '../api/schema'
import { useEventStream } from '../composables/useEventStream'
import { createAppRouter } from '../router'
import { useToast } from '../stores/toast'
import { server } from '../test/msw'
import type { StreamEvent } from '../types/events'

vi.mock('../composables/useEventStream', () => ({ useEventStream: vi.fn() }))

const project = {
  id: 8,
  name: 'docs',
  path: '/repos/docs',
  current_branch: 'main',
  index_status: 'done',
  indexed_at: '2026-01-01T00:00:00Z',
  created_at: '2026-01-01T00:00:00Z',
} satisfies components['schemas']['ProjectRead']
const session = {
  id: 17,
  project_id: project.id,
  branch_name: 'docter/docs',
  mode: 'fix',
  mode_params: {},
  run_status: 'idle',
  created_at: project.created_at,
  updated_at: project.created_at,
  messages: [],
} satisfies components['schemas']['SessionDetail']

function message(
  id: number,
  role: string,
  content: string | null,
): components['schemas']['MessageRead'] {
  return {
    id,
    session_id: session.id,
    role,
    content,
    tool_calls: null,
    tool_call_id: null,
    created_at: session.created_at,
  }
}

function mockHistory(...messages: components['schemas']['MessageRead'][]) {
  server.use(http.get('/api/sessions/17', () => HttpResponse.json({ ...session, messages })))
}

function mockStream() {
  const post = vi.fn()
  let listener: ((event: StreamEvent) => void) | undefined
  let finish: (() => void) | undefined
  vi.mocked(useEventStream).mockImplementation((url, body) => {
    const isStreaming = ref(false)
    return {
      isStreaming,
      start: async () => {
        post(url, { ...(body as components['schemas']['ChatRequest']) })
        isStreaming.value = true
        await new Promise<void>((resolve) => {
          finish = resolve
        })
        isStreaming.value = false
      },
      abort: () => finish?.(),
      onEvent: (handler) => {
        listener = handler
        return () => {
          listener = undefined
        }
      },
    }
  })
  return {
    post,
    emit: async (event: StreamEvent) => {
      listener?.(event)
      await nextTick()
    },
    finish: async () => {
      finish?.()
      await Promise.resolve()
      await nextTick()
    },
  }
}

enableAutoUnmount(afterEach)
beforeEach(() => {
  server.use(http.get('/api/projects/8', () => HttpResponse.json(project)))
  mockHistory()
})

async function render() {
  const stream = mockStream()
  const router = createAppRouter(createMemoryHistory())
  await router.push('/sessions/17')
  const pinia = createPinia()
  const wrapper = mount(RouterView, { global: { plugins: [pinia, router] } })
  await vi.waitFor(() => expect(wrapper.find('[role="log"]').exists()).toBe(true))
  return { wrapper, stream, pinia }
}

it('loads session history and shows the project, mode, and branch', async () => {
  mockHistory(
    message(1, 'user', '**Keep this literal**'),
    message(2, 'tool', 'Internal tool output'),
    message(3, 'assistant', null),
    message(4, 'assistant', 'Updated the **README**.'),
  )
  const { wrapper } = await render()
  const header = wrapper.get('header')
  expect(header.get('a').attributes('href')).toBe('/projects/8')
  expect(header.get('a').text()).toBe('docs')
  expect(header.text()).toContain('Fix')
  expect(header.text()).toContain('Branch: docter/docs')
  const items = wrapper.get('[role="log"]').findAll('li')
  expect(items.map((item) => item.text())).toEqual([
    'You**Keep this literal**',
    'AssistantUpdated the README.',
  ])
  expect(items[1]?.get('strong').text()).toBe('README')
})

it('renders safe Markdown without inserting scripts or executable links', async () => {
  mockHistory(
    message(
      1,
      'assistant',
      [
        '**Safe Markdown**',
        '<script>window.injected = true</script>',
        '<img src=x onerror="window.injected = true">',
        '[unsafe](javascript:alert%281%29)',
        '[safe](https://example.org/)',
      ].join('\n\n'),
    ),
  )
  const { wrapper } = await render()
  const output = wrapper.get('.assistant-markdown')
  expect(output.get('strong').text()).toBe('Safe Markdown')
  expect(output.find('script, [onerror], [href^="javascript:"]').exists()).toBe(false)
  expect(output.get('a').attributes('href')).toBe('https://example.org/')
})

it('sends on Enter but not Shift+Enter, then appends tokens to the same assistant reply', async () => {
  const { wrapper, stream } = await render()
  const input = wrapper.get('textarea')
  const log = wrapper.get('[role="log"]')
  expect(log.text()).toContain('No messages yet')
  await input.setValue('Line one')
  const newline = new KeyboardEvent('keydown', { key: 'Enter', shiftKey: true, cancelable: true })
  input.element.dispatchEvent(newline)
  expect(newline.defaultPrevented).toBe(false)
  expect(stream.post).not.toHaveBeenCalled()

  await input.setValue('Line one\nLine two')
  await input.trigger('keydown', { key: 'Enter' })
  expect(stream.post).toHaveBeenCalledExactlyOnceWith('/api/sessions/17/chat', {
    message: 'Line one\nLine two',
  })
  expect(log.text()).toContain('Line one\nLine two')
  expect(log.text()).toContain('Waiting for a response')
  expect(wrapper.get('button').attributes('disabled')).toBeDefined()
  await stream.emit({ type: 'token', text: 'Updated ' })
  expect(log.get('.assistant-markdown').text()).toBe('Updated')
  await stream.emit({ type: 'warning', message: 'Not shown here' })
  await stream.emit({ type: 'token', text: '**docs**.' })
  expect(log.findAll('li')).toHaveLength(2)
  expect(log.get('.assistant-markdown').text()).toBe('Updated docs.')
  expect(log.get('strong').text()).toBe('docs')
  expect(log.text()).not.toContain('Not shown here')
  await stream.finish()
  expect(log.get('.assistant-markdown').text()).toBe('Updated docs.')
})

it('follows new tokens only while the reader is near the bottom', async () => {
  const { wrapper, stream } = await render()
  const log = wrapper.get('[role="log"]')
  let height = 300
  Object.defineProperties(log.element, {
    scrollHeight: { get: () => height },
    clientHeight: { value: 100 },
    scrollTop: { writable: true, value: 0 },
  })
  await wrapper.get('textarea').setValue('Review the docs')
  await wrapper.get('form').trigger('submit')
  expect(log.element.scrollTop).toBe(300)

  log.element.scrollTop = 25
  await log.trigger('scroll')
  height = 400
  await stream.emit({ type: 'token', text: 'First' })
  expect(log.element.scrollTop).toBe(25)

  log.element.scrollTop = 284
  await log.trigger('scroll')
  height = 450
  await stream.emit({ type: 'token', text: ' second' })
  expect(log.element.scrollTop).toBe(450)
  await stream.finish()
})

it('reports a failed stream without leaving an empty assistant bubble', async () => {
  const { wrapper, stream, pinia } = await render()
  await wrapper.get('textarea').setValue('Review the docs')
  await wrapper.get('form').trigger('submit')
  await stream.emit({ type: 'error', code: 'network_error', message: 'Connection lost.' })
  await stream.finish()
  expect(useToast(pinia).toasts).toMatchObject([{ kind: 'error', message: 'Connection lost.' }])
  expect(wrapper.get('[role="log"]').findAll('li')).toHaveLength(1)
})
