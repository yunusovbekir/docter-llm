import {
  EventStreamContentType,
  fetchEventSource,
  type EventSourceMessage,
} from '@microsoft/fetch-event-source'
import { onScopeDispose, readonly, ref } from 'vue'
import { ApiError, normalizeHttpError } from '../api/client'
import type { StreamEvent } from '../types/events'

const eventNames = {
  token: true,
  tool_call: true,
  tool_result: true,
  change_proposed: true,
  warning: true,
  error: true,
  done: true,
} satisfies Record<StreamEvent['type'], true>

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function parseEvent({ event, data }: EventSourceMessage): StreamEvent | undefined {
  // Ignore heartbeats and event kinds this client does not consume.
  if (!Object.hasOwn(eventNames, event)) return

  const invalid = new ApiError('invalid_response', `Invalid ${event} stream event.`, 0)
  let payload: unknown
  try {
    payload = JSON.parse(data)
  } catch (cause) {
    if (cause instanceof SyntaxError) throw invalid
    throw cause
  }
  if (!isRecord(payload)) throw invalid

  const { id, name, message } = payload
  switch (event) {
    case 'token':
      if (typeof payload.text === 'string') return { type: event, text: payload.text }
      break
    case 'tool_call':
      if (typeof id === 'string' && typeof name === 'string' && isRecord(payload.arguments)) {
        return { type: event, id, name, arguments: payload.arguments }
      }
      break
    case 'tool_result': {
      const result = 'result' in payload ? payload.result : (payload.structured ?? payload.text)
      if (
        typeof id === 'string' &&
        typeof name === 'string' &&
        typeof payload.is_error === 'boolean' &&
        result !== undefined
      ) {
        return { type: event, id, name, result, is_error: payload.is_error }
      }
      break
    }
    case 'change_proposed': {
      const { change_id } = payload
      const file_path =
        payload.file_path ?? (isRecord(payload.change) ? payload.change.file_path : undefined)
      if (
        typeof change_id === 'number' &&
        Number.isSafeInteger(change_id) &&
        typeof file_path === 'string'
      ) {
        return { type: event, change_id, file_path }
      }
      break
    }
    case 'warning':
      if (typeof message === 'string') return { type: event, message }
      break
    case 'error':
      if (typeof payload.code === 'string' && typeof message === 'string') {
        return { type: event, code: payload.code, message }
      }
      break
    case 'done':
      return { type: event }
  }
  throw invalid
}

type Listener = (event: StreamEvent) => void

export function useEventStream(url: string, body: unknown) {
  const isStreaming = ref(false)
  const listeners = new Set<Listener>()
  let active: AbortController | undefined
  let disposed = false

  function onEvent(listener: Listener) {
    if (!disposed) listeners.add(listener)
    return () => {
      listeners.delete(listener)
    }
  }

  function abort() {
    const request = active
    active = undefined
    isStreaming.value = false
    request?.abort()
  }

  onScopeDispose(() => {
    disposed = true
    abort()
    listeners.clear()
  })

  function emit(request: AbortController, event: StreamEvent) {
    for (const listener of listeners) {
      if (active !== request) break
      listener(event)
    }
  }

  async function start() {
    if (active || disposed) return
    const request = new AbortController()
    active = request
    isStreaming.value = true
    let done = false

    try {
      let json: string | undefined
      try {
        json = JSON.stringify(body)
      } catch (cause) {
        throw new ApiError('invalid_request', 'The stream body is not valid JSON.', 0, { cause })
      }
      if (json === undefined)
        throw new ApiError('invalid_request', 'The stream requires a JSON body.', 0)

      await fetchEventSource(url, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: json,
        signal: request.signal,
        // Prevent visibility changes from reconnecting and replaying this POST.
        openWhenHidden: true,
        async onopen(response) {
          if (!response.ok) {
            let payload: unknown
            if (response.headers.get('content-type')?.includes('application/json')) {
              try {
                payload = await response.json()
              } catch (cause) {
                if (!(cause instanceof SyntaxError)) throw cause
              }
            }
            throw normalizeHttpError(response, payload)
          }
          const contentType = response.headers.get('content-type')?.split(';')[0]?.trim()
          if (contentType !== EventStreamContentType || !response.body) {
            throw new ApiError(
              'invalid_response',
              'The API did not return an event stream.',
              response.status,
            )
          }
        },
        onmessage(message) {
          if (active !== request || done) return
          const event = parseEvent(message)
          if (!event) return
          done = event.type === 'done'
          emit(request, event)
        },
        onclose() {
          if (!done)
            throw new ApiError('network_error', 'The event stream closed before it finished.', 0)
        },
        onerror(error: unknown) {
          throw error
        },
      })
    } catch (cause) {
      if (active === request) {
        const error: StreamEvent =
          cause instanceof ApiError
            ? { type: 'error', code: cause.code, message: cause.message }
            : { type: 'error', code: 'network_error', message: 'Could not read the event stream.' }
        emit(request, error)
      }
    } finally {
      if (active === request) {
        active = undefined
        isStreaming.value = false
      }
    }
  }

  return { start, abort, isStreaming: readonly(isStreaming), onEvent }
}
