import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { http, HttpResponse } from 'msw'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { defineComponent, h, nextTick, ref } from 'vue'
import { createMemoryHistory, RouterView } from 'vue-router'
import type { components } from '../api/schema'
import { useEventStream } from '../composables/useEventStream'
import { createAppRouter } from '../router'
import { useChanges } from '../stores/changes'
import { server } from '../test/msw'
import type { StreamEvent } from '../types/events'

vi.mock('../composables/useEventStream', () => ({ useEventStream: vi.fn() }))
const revealLine = vi.hoisted(() => vi.fn())
vi.mock('@guolao/vue-monaco-editor', async () => {
  const { onMounted } = await import('vue')
  return {
    loader: { config: vi.fn() },
    VueMonacoDiffEditor: defineComponent({
      props: {
        original: { type: String, required: true },
        modified: { type: String, required: true },
        language: { type: String, required: true },
        theme: { type: String, required: true },
        options: { type: Object, required: true },
      },
      emits: ['mount'],
      setup(props, { emit }) {
        onMounted(() =>
          emit('mount', {
            getModifiedEditor: () => ({
              getModel: () => ({ getLineCount: () => 100 }),
              revealLineInCenter: revealLine,
            }),
          }),
        )
        return () => h('div', { 'data-monaco': JSON.stringify(props) })
      },
    }),
  }
})
vi.mock('monaco-editor', () => ({
  languages: {
    getLanguages: () => [{ id: 'markdown', extensions: ['.md'] }],
  },
}))

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
  const abort = vi.fn(() => finish?.())
  let listener: ((event: StreamEvent, httpStatus?: number) => void) | undefined
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
      abort,
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
    abort,
    emit: async (event: StreamEvent, httpStatus?: number) => {
      listener?.(event, httpStatus)
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
  server.use(http.get('/api/sessions/17/changes', () => HttpResponse.json([])))
  mockHistory()
})

afterEach(() => {
  localStorage.removeItem('docter.diff-layout')
  document.documentElement.classList.remove('dark')
})

it('cancels the active run, then aborts the stream and unlocks the composer', async () => {
  const cancel = vi.fn()
  let confirmCancel!: () => void
  const confirmation = new Promise<void>((resolve) => {
    confirmCancel = resolve
  })
  server.use(
    http.post('/api/sessions/17/cancel', async () => {
      cancel()
      await confirmation
      return new HttpResponse(null, { status: 204 })
    }),
  )
  const { wrapper, stream } = await render()
  const input = wrapper.get('textarea')
  await input.setValue('Review the docs')
  await wrapper.get('form').trigger('submit')
  expect(input.attributes('disabled')).toBeDefined()
  const stop = wrapper.get('form button[type="button"]')
  expect(stop.text()).toBe('Stop')
  await stop.trigger('click')
  await vi.waitFor(() => expect(cancel).toHaveBeenCalledTimes(1))
  expect(stop.attributes('disabled')).toBeDefined()
  expect(input.attributes('disabled')).toBeDefined()
  expect(stream.abort).not.toHaveBeenCalled()
  confirmCancel()
  await vi.waitFor(() => expect(input.attributes('disabled')).toBeUndefined())
  expect(stream.abort).toHaveBeenCalledTimes(1)
  expect(wrapper.find('form button[type="button"]').exists()).toBe(false)
})

it('blocks sends for a session that is already running on load', async () => {
  server.use(
    http.get('/api/sessions/17', () => HttpResponse.json({ ...session, run_status: 'running' })),
  )
  const { wrapper, stream } = await render()
  expect(wrapper.get('textarea').attributes('disabled')).toBeDefined()
  expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()
  await wrapper.get('form').trigger('submit')
  expect(stream.post).not.toHaveBeenCalled()
})

it('shows the conflict message on an HTTP 409 and removes the rejected optimistic message', async () => {
  const { wrapper, stream } = await render()
  const input = wrapper.get('textarea')
  await input.setValue('Review the docs')
  await wrapper.get('form').trigger('submit')
  const log = wrapper.get('[role="log"]')
  expect(log.text()).toContain('Review the docs')
  await stream.emit({ type: 'error', code: 'http_error', message: 'Conflict' }, 409)
  await stream.finish()
  await flushPromises()
  expect(log.get('[role="alert"]').text()).toBe('Error: A run is already in progress')
  expect(log.text()).not.toContain('Review the docs')
  expect(input.attributes('disabled')).toBeDefined()
})

async function render() {
  const stream = mockStream()
  const router = createAppRouter(createMemoryHistory())
  await router.push('/sessions/17')
  const pinia = createPinia()
  const wrapper = mount(RouterView, { global: { plugins: [pinia, router] } })
  await vi.waitFor(() => expect(wrapper.find('[role="log"]').exists()).toBe(true))
  return { wrapper, stream, changes: useChanges(pinia), router }
}

type ChangeSet = components['schemas']['ChangeSetRead']
type Change = components['schemas']['ChangeRead']

function changeSet(id: number, day: string, ...statuses: Change['status'][]): ChangeSet {
  const created_at = `2026-01-${day}T00:00:00Z`
  return {
    id,
    session_id: session.id,
    run_id: `run-${id}`,
    created_at,
    changes: statuses.map((status, index) => {
      const changeId = id * 10 + index
      return {
        id: changeId,
        change_set_id: id,
        file_path: `docs/${status}.md`,
        patch: '',
        rationale: '',
        status,
        parent_change_id: null,
        created_at,
        original_content: '',
        proposed_content: '',
        hunks: Array.from({ length: index + 1 }, (_, index) => ({
          id: changeId * 10 + index,
          change_id: changeId,
          index,
          header: '@@ -1 +1 @@',
          status,
        })),
        comments: [],
      }
    }),
  }
}

it('groups files newest first and shows each status and hunk count', async () => {
  server.use(
    http.get('/api/sessions/17/changes', () =>
      HttpResponse.json([
        changeSet(2, '01', 'pending', 'applied', 'denied'),
        changeSet(1, '02', 'partial', 'conflict', 'revised'),
      ]),
    ),
  )
  const { wrapper, changes, router } = await render()
  const panel = wrapper.get('aside')
  await vi.waitFor(() => expect(panel.findAll('h3')).toHaveLength(2))
  expect(panel.findAll('h3 span').map((title) => title.text())).toEqual([
    'Change set #1',
    'Change set #2',
  ])
  const rows = panel.findAll('button[aria-pressed]')
  expect(rows.map((row) => row.text())).toEqual([
    'docs/partial.mdPartial1 hunk',
    'docs/conflict.mdConflict2 hunks',
    'docs/revised.mdRevised3 hunks',
    'docs/pending.mdPending1 hunk',
    'docs/applied.mdApplied2 hunks',
    'docs/denied.mdDenied3 hunks',
  ])
  expect(rows[1]!.get('span.bg-red-100').text()).toBe('Conflict')
  await rows[1]!.trigger('click')
  expect(changes.selectedChangeId).toBe(11)
  expect(rows[1]!.attributes('aria-pressed')).toBe('true')
  await rows[0]!.trigger('click')
  expect(changes.selectedChangeId).toBe(10)
  expect(rows[1]!.attributes('aria-pressed')).toBe('false')

  await router.push('/history')
  expect(changes.selectedChangeId).toBeNull()
})

it('shows an empty diff until selection, then passes snapshots and remembers the layout', async () => {
  const set = changeSet(1, '01', 'pending', 'applied')
  Object.assign(set.changes[0]!, {
    original_content: '# Before\n',
    proposed_content: '# After\n',
    file_path: 'docs/README.MD',
  })
  set.changes[1]!.file_path = 'docs/unknown.ext'
  server.use(http.get('/api/sessions/17/changes', () => HttpResponse.json([set])))
  const { wrapper } = await render()
  expect(wrapper.get('h2 + p').text()).toBe('Select a file in Changes to see its diff.')
  expect(wrapper.text()).toContain('No change selected')
  expect(wrapper.find('[data-monaco]').exists()).toBe(false)
  const panel = wrapper.get('aside')
  await vi.waitFor(() => expect(panel.findAll('button[aria-pressed]')).toHaveLength(2))
  const [first, second] = panel.findAll('button[aria-pressed]')
  await first!.trigger('click')
  await vi.waitFor(() => expect(wrapper.find('[data-monaco]').exists()).toBe(true))

  function editorProps() {
    const value = wrapper.get('[data-monaco]').attributes('data-monaco')
    if (value === undefined) throw new Error('Missing mocked Monaco props')
    return JSON.parse(value)
  }
  expect(editorProps()).toMatchObject({
    original: '# Before\n',
    modified: '# After\n',
    language: 'markdown',
    theme: 'vs',
    options: { readOnly: true, originalEditable: false, renderSideBySide: true },
  })
  await wrapper.get('select').setValue('inline')
  expect(editorProps()).toMatchObject({ options: { renderSideBySide: false } })
  expect(localStorage.getItem('docter.diff-layout')).toBe('inline')

  document.documentElement.classList.add('dark')
  await vi.waitFor(() => expect(editorProps()).toMatchObject({ theme: 'vs-dark' }))
  await second!.trigger('click')
  await vi.waitFor(() =>
    expect(editorProps()).toMatchObject({
      language: 'plaintext',
      options: { renderSideBySide: false },
    }),
  )
  expect(wrapper.get('select').element.value).toBe('inline')
})

it('reloads on a proposed change without dropping the current selection', async () => {
  const sets = [changeSet(1, '01', 'pending')]
  const load = vi.fn(() => HttpResponse.json(sets))
  server.use(http.get('/api/sessions/17/changes', load))
  const { wrapper, stream, changes } = await render()
  const panel = wrapper.get('aside')
  await vi.waitFor(() => expect(panel.text()).toContain('docs/pending.md'))
  await panel.get('button[aria-pressed]').trigger('click')
  await wrapper.get('textarea').setValue('Update the docs')
  await wrapper.get('form').trigger('submit')
  sets.push(changeSet(2, '02', 'applied'))
  await stream.emit({ type: 'change_proposed', change_id: 20, file_path: 'docs/applied.md' })
  await vi.waitFor(() => expect(panel.text()).toContain('docs/applied.md'))
  expect(load).toHaveBeenCalledTimes(2)
  expect(changes.selectedChangeId).toBe(10)
  expect(panel.get('[aria-pressed="true"]').text()).toContain('docs/pending.md')
  await stream.finish()
})

function mockReview(action: 'approve' | 'deny' | 'approve-all', status: Change['status']) {
  const set = changeSet(1, '01', 'pending')
  const change = set.changes[0]!
  const readChanges = vi.fn(() => HttpResponse.json([set]))
  const readSession = vi.fn(() =>
    HttpResponse.json({
      ...session,
      branch_name: change.status === 'pending' ? null : 'docter/review',
    }),
  )
  const post = vi.fn(async ({ request }: { request: Request }) => {
    expect(await request.text()).toBe('')
    change.status = status
    return HttpResponse.json(action === 'approve-all' ? [change] : change)
  })
  server.use(
    http.get('/api/sessions/17/changes', readChanges),
    http.get('/api/sessions/17', readSession),
    http.post(
      action === 'approve-all'
        ? '/api/sessions/17/changes/approve-all'
        : `/api/changes/10/${action}`,
      post,
    ),
  )
  return { readChanges, readSession, post }
}

it.each(['approve', 'deny'] as const)(
  'sends only selected pending hunk indices to %s and jumps to a hunk',
  async (action) => {
    revealLine.mockClear()
    const set = changeSet(1, '01', 'partial')
    const change = set.changes[0]!
    const base = change.hunks[0]!
    change.hunks = [
      { ...base, index: 0, status: 'applied' },
      { ...base, id: base.id + 1, index: 1, status: 'pending', header: '@@ -7,2 +12,3 @@' },
      { ...base, id: base.id + 2, index: 2, status: 'denied' },
      { ...base, id: base.id + 3, index: 3, status: 'pending', header: '@@ -25,2 +30,0 @@' },
    ]
    const payload = vi.fn()
    server.use(
      http.get('/api/sessions/17/changes', () => HttpResponse.json([set])),
      http.post(`/api/changes/10/${action}`, async ({ request }) => {
        payload(await request.json())
        for (const hunk of change.hunks) {
          if (hunk.status === 'pending') hunk.status = action === 'approve' ? 'applied' : 'denied'
        }
        return HttpResponse.json(change)
      }),
    )
    const { wrapper } = await render()
    await vi.waitFor(() => expect(wrapper.find('aside [aria-pressed]').exists()).toBe(true))
    await wrapper.get('aside [aria-pressed]').trigger('click')
    const list = wrapper.get('fieldset')
    const boxes = list.findAll<HTMLInputElement>('input[type="checkbox"]')
    expect(boxes.map(({ element }) => element.disabled)).toEqual([true, false, true, false])
    const submit = list
      .findAll('button')
      .find((button) => button.text().toLowerCase() === `${action} selected`)!
    expect(submit.element.disabled).toBe(true)
    if (action === 'approve') {
      const jump = list.findAll('button').find((button) => button.text() === '@@ -7,2 +12,3 @@')!
      await vi.waitFor(() => expect(jump.element.disabled).toBe(false))
      await jump.trigger('click')
      expect(revealLine).toHaveBeenCalledExactlyOnceWith(12)
    }
    await boxes[3]!.setValue(true)
    await boxes[1]!.setValue(true)
    await submit.trigger('click')
    await vi.waitFor(() => expect(payload).toHaveBeenCalledExactlyOnceWith({ hunks: [1, 3] }))
    await vi.waitFor(() =>
      expect(boxes.every(({ element }) => element.disabled && !element.checked)).toBe(true),
    )
  },
)

it.each([
  ['approve', 'applied', 'Applied'],
  ['deny', 'denied', 'Denied'],
  ['approve', 'conflict', 'Conflict'],
] as const)(
  'handles %s with a resulting %s status and reloads both resources',
  async (action, status, label) => {
    const { readChanges, readSession, post } = mockReview(action, status)
    const { wrapper, changes } = await render()
    const panel = wrapper.get('aside')
    await vi.waitFor(() => expect(panel.find('button[aria-pressed]').exists()).toBe(true))
    await panel.get('button[aria-pressed]').trigger('click')
    await wrapper.get('textarea').setValue('Keep this draft')
    const bar = wrapper.get('[aria-label="Review selected change"]')
    const button = bar.findAll('button').find((button) => button.text().toLowerCase() === action)!
    await button.trigger('click')

    await vi.waitFor(() => {
      expect(panel.get('button[aria-pressed]').text()).toContain(label)
      expect(wrapper.get('header').text()).toContain('Branch: docter/review')
    })
    expect(post).toHaveBeenCalledTimes(1)
    expect(readChanges).toHaveBeenCalledTimes(2)
    expect(readSession).toHaveBeenCalledTimes(2)
    expect(changes.selectedChangeId).toBe(10)
    expect(wrapper.get('textarea').element.value).toBe('Keep this draft')
    if (status === 'conflict') {
      expect(bar.get('[role="alert"]').text()).toContain('The patch could not be applied')
    } else {
      expect(bar.findAll('button').every((button) => button.element.disabled)).toBe(true)
    }
  },
)

it('requires confirmation before approving all pending changes, then reloads both resources', async () => {
  const { readChanges, readSession, post } = mockReview('approve-all', 'applied')
  const { wrapper } = await render()
  const panel = wrapper.get('aside')
  await vi.waitFor(() => expect(panel.find('button[aria-pressed]').exists()).toBe(true))
  const dialog = document.querySelector('dialog')!
  dialog.showModal = () => {
    dialog.open = true
  }
  dialog.close = () => {
    dialog.open = false
  }
  const clickDialog = (label: string) =>
    [...dialog.querySelectorAll('button')]
      .find((button) => button.textContent?.trim() === label)!
      .click()

  const approveAll = panel
    .findAll('button')
    .find((button) => button.text() === 'Approve all pending')!
  await approveAll.trigger('click')
  expect(dialog.open).toBe(true)
  expect(post).not.toHaveBeenCalled()
  clickDialog('Cancel')
  await nextTick()
  expect(dialog.open).toBe(false)
  expect(post).not.toHaveBeenCalled()

  await approveAll.trigger('click')
  clickDialog('Approve all pending')
  await vi.waitFor(() => {
    expect(panel.get('button[aria-pressed]').text()).toContain('Applied')
    expect(wrapper.get('header').text()).toContain('Branch: docter/review')
  })
  expect(post).toHaveBeenCalledTimes(1)
  expect(readChanges).toHaveBeenCalledTimes(2)
  expect(readSession).toHaveBeenCalledTimes(2)
  expect(dialog.open).toBe(false)
})

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
  await stream.emit({ type: 'token', text: '**docs**.' })
  expect(log.findAll('li')).toHaveLength(2)
  expect(log.get('.assistant-markdown').text()).toBe('Updated docs.')
  expect(log.get('strong').text()).toBe('docs')
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

it('shows warnings and errors inline without leaving an empty assistant bubble', async () => {
  const { wrapper, stream } = await render()
  await wrapper.get('textarea').setValue('Review the docs')
  await wrapper.get('form').trigger('submit')
  await stream.emit({ type: 'warning', message: 'Limited context.' })
  await stream.emit({ type: 'error', code: 'network_error', message: 'Connection lost.' })
  await stream.finish()
  const log = wrapper.get('[role="log"]')
  expect(log.findAll('li').map((item) => item.text())).toEqual([
    'YouReview the docs',
    'Warning: Limited context.',
    'Error: Connection lost.',
  ])
  expect(log.get('[role="status"]').classes()).toContain('bg-amber-50')
  expect(log.get('[role="alert"]').classes()).toContain('bg-red-50')
})

it('pairs out-of-order tool results by ID and keeps the cards at their call positions', async () => {
  const { wrapper, stream } = await render()
  await wrapper.get('textarea').setValue('Review the docs')
  await wrapper.get('form').trigger('submit')
  await stream.emit({ type: 'token', text: 'Checking files.' })
  await stream.emit({ type: 'tool_call', id: 'a', name: 'read', arguments: { path: 'a' } })
  await stream.emit({ type: 'tool_call', id: 'b', name: 'read', arguments: { path: 'b' } })
  const log = wrapper.get('[role="log"]')
  expect(log.findAll('summary').map((summary) => summary.text())).toEqual([
    'read Running',
    'read Running',
  ])
  await stream.emit({
    type: 'tool_result',
    id: 'b',
    name: 'read',
    result: 'Not found',
    is_error: true,
  })
  await stream.emit({
    type: 'tool_result',
    id: 'a',
    name: 'read',
    result: { lines: 2 },
    is_error: false,
  })
  await stream.emit({ type: 'token', text: 'Finished.' })
  await stream.finish()

  expect(
    log.findAll('li').map((item) => item.element.firstElementChild?.textContent?.trim()),
  ).toEqual([
    'YouReview the docs',
    'AssistantChecking files.',
    expect.stringContaining('read OK'),
    expect.stringContaining('read Error'),
    'AssistantFinished.',
  ])
  const [ok, error] = log.findAll('details')
  expect(ok?.findAll('pre').map((pre) => pre.text())).toEqual([
    '{\n  "path": "a"\n}',
    '{\n  "lines": 2\n}',
  ])
  expect(error?.findAll('pre').map((pre) => pre.text())).toEqual([
    '{\n  "path": "b"\n}',
    'Not found',
  ])
  expect(error?.get('summary span:last-child').classes()).toContain('bg-red-100')
})

it('renders saved tool calls with their replies and lets the reader expand and truncate long results', async () => {
  const result = 'x'.repeat(2_000) + ' end'
  mockHistory(
    {
      ...message(1, 'assistant', null),
      tool_calls: [
        { id: 'a', type: 'function', function: { name: 'read', arguments: '{"path":"a"}' } },
        { id: 'b', type: 'function', function: { name: 'read', arguments: '{"path":"b"}' } },
      ],
    },
    { ...message(2, 'tool', 'Tool error: Not found'), tool_call_id: 'b' },
    { ...message(3, 'tool', result), tool_call_id: 'a' },
  )
  const { wrapper } = await render()
  const [ok, error] = wrapper.findAll<HTMLDetailsElement>('details')
  expect(ok?.element.open).toBe(false)
  ok?.get('summary').element.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  expect(ok?.element.open).toBe(true)
  expect(ok?.get('summary').text()).toBe('read OK')
  expect(ok?.get('pre').text()).toBe('{\n  "path": "a"\n}')
  expect(ok?.get('pre:last-child').text()).toBe(result.slice(0, 2_000))
  expect(ok?.get('button').text()).toBe('Show more')
  await ok?.get('button').trigger('click')
  expect(ok?.get('pre:last-child').text()).toBe(result)
  await ok?.get('button').trigger('click')
  expect(ok?.get('pre:last-child').text()).toBe(result.slice(0, 2_000))
  ok?.get('summary').element.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  expect(ok?.element.open).toBe(false)
  expect(error?.get('summary').text()).toBe('read Error')
  expect(error?.get('pre:last-child').text()).toBe('Not found')
})
