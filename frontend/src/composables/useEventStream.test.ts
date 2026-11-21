import { enableAutoUnmount, mount } from '@vue/test-utils'
import { http, HttpResponse } from 'msw'
import { afterEach, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'
import { server } from '../test/msw'
import type { StreamEvent } from '../types/events'
import { useEventStream } from './useEventStream'

const path = '/api/sessions/8/chat'
const url = new URL(path, document.baseURI).href
const body = { message: 'Add documentation' }

enableAutoUnmount(afterEach)
afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

function mountStream() {
  let stream!: ReturnType<typeof useEventStream>
  const events: StreamEvent[] = []
  const wrapper = mount(
    defineComponent({
      setup() {
        stream = useEventStream(url, body)
        stream.onEvent((event) => events.push(event))
        return () => null
      },
    }),
  )
  return { stream, events, wrapper }
}

function sse(data: string) {
  return new HttpResponse(data, { headers: { 'Content-Type': 'text/event-stream; charset=utf-8' } })
}

it('POSTs JSON and normalizes the actual backend SSE payloads in order', async () => {
  const posted = vi.fn()
  server.use(
    http.post(path, async ({ request }) => {
      posted(
        request.headers.get('content-type'),
        request.headers.get('accept'),
        await request.json(),
      )
      return sse(
        [
          ': heartbeat',
          'event: token\ndata: {"type":"token",\ndata: "text":"Hello"}',
          'event: tool_call\ndata: {"id":"call-1","name":"read","arguments":{"path":"README.md"}}',
          'event: tool_result\ndata: {"id":"call-1","name":"read","text":"contents","structured":null,"is_error":false}',
          'event: tool_result\ndata: {"id":"call-2","name":"read","text":"fallback","structured":{"lines":2},"is_error":false}',
          'event: change_proposed\ndata: {"change_id":17,"change":{"file_path":"README.md"}}',
          'event: warning\ndata: {"code":"limit","message":"Limit reached"}',
          'event: error\ndata: {"code":"llm_error","message":"Request failed"}',
          'event: done\ndata: {"type":"done","reason":"error","steps":1}',
          '',
        ].join('\n\n'),
      )
    }),
  )
  const { stream, events } = mountStream()
  const running = stream.start()
  expect(stream.isStreaming.value).toBe(true)
  await stream.start() // An in-flight stream cannot be started twice.
  await running
  expect(posted).toHaveBeenCalledExactlyOnceWith('application/json', 'text/event-stream', body)
  expect(events).toEqual([
    { type: 'token', text: 'Hello' },
    { type: 'tool_call', id: 'call-1', name: 'read', arguments: { path: 'README.md' } },
    { type: 'tool_result', id: 'call-1', name: 'read', result: 'contents', is_error: false },
    { type: 'tool_result', id: 'call-2', name: 'read', result: { lines: 2 }, is_error: false },
    { type: 'change_proposed', change_id: 17, file_path: 'README.md' },
    { type: 'warning', message: 'Limit reached' },
    { type: 'error', code: 'llm_error', message: 'Request failed' },
    { type: 'done' },
  ] satisfies StreamEvent[])
  expect(stream.isStreaming.value).toBe(false)
})

it.each(['abort', 'unmount'] as const)(
  'cancels an active request on %s without emitting an error',
  async (action) => {
    const fetch = vi.spyOn(window, 'fetch').mockImplementation(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => reject(init.signal?.reason))
        }),
    )
    const { stream, events, wrapper } = mountStream()
    const running = stream.start()
    const signal = fetch.mock.calls[0]?.[1]?.signal
    expect(signal?.aborted).toBe(false)
    document.dispatchEvent(new Event('visibilitychange'))
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(signal?.aborted).toBe(false)

    if (action === 'unmount') wrapper.unmount()
    else stream.abort()

    await running
    expect(signal?.aborted).toBe(true)
    expect(stream.isStreaming.value).toBe(false)
    expect(events).toEqual([])
  },
)

it('emits a normalized network error and never retries the POST', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const fetch = vi.spyOn(window, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'))
  const { stream, events } = mountStream()
  await stream.start()
  await vi.advanceTimersByTimeAsync(10_000)
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(events).toEqual([
    { type: 'error', code: 'network_error', message: 'Could not read the event stream.' },
  ])
  expect(stream.isStreaming.value).toBe(false)
})

it('preserves a normalized HTTP error from the API', async () => {
  server.use(
    http.post(path, () =>
      HttpResponse.json(
        { error: { code: 'session_busy', message: 'This session is already running.' } },
        { status: 409 },
      ),
    ),
  )
  const { stream, events } = mountStream()
  await stream.start()
  expect(events).toEqual([
    { type: 'error', code: 'session_busy', message: 'This session is already running.' },
  ])
})

it.each([
  ['event: token\ndata: {"text":42}\n\n', 'invalid_response', 'Invalid token stream event.'],
  ['event: token\ndata: {\n\n', 'invalid_response', 'Invalid token stream event.'],
  [': heartbeat\n\n', 'network_error', 'The event stream closed before it finished.'],
])('reports malformed or truncated streams', async (data, code, message) => {
  server.use(http.post(path, () => sse(data)))
  const { stream, events } = mountStream()
  await stream.start()
  expect(events).toEqual([{ type: 'error', code, message }])
  expect(stream.isStreaming.value).toBe(false)
})
