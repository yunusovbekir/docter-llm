import { computed, onScopeDispose, reactive, ref } from 'vue'
import { api, ApiError, unwrap } from '../api/client'
import type { components } from '../api/schema'
import { useToast } from '../stores/toast'
import type { ChatMessage, ChatToolCall } from '../types/chat'
import { chatHistory } from './chatHistory'
import { useEventStream } from './useEventStream'

type Session = components['schemas']['SessionDetail']
type Comment = components['schemas']['CommentCreate']
type ChangeId = components['schemas']['ChangeRead']['id']

export function useChat(session: Session, reloadChanges: () => void) {
  const entries = reactive(chatHistory(session.messages))
  const pending = new Map<string, ChatToolCall>()
  let reply: ChatMessage | undefined
  let nextId = 0
  const key = () => `live-${++nextId}`
  const chatUrl = `/api/sessions/${session.id}/chat`
  let url = chatUrl
  let body: components['schemas']['ChatRequest'] | Comment = { message: '' }
  const stream = useEventStream(
    () => url,
    () => body,
  )
  const toast = useToast()
  const externalRun = ref(session.run_status === 'running')
  const stopping = ref(false)
  const submitting = ref(false)
  const runActive = computed(() => stream.isStreaming.value || externalRun.value)
  const busy = computed(() => runActive.value || stopping.value || submitting.value)
  const lifetime = new AbortController()
  onScopeDispose(() => lifetime.abort())
  let userKey: string | undefined
  let rejected = false

  stream.onEvent((event, httpStatus) => {
    if (event.type === 'error' && httpStatus && httpStatus >= 400) {
      rejected = true
      if (httpStatus === 409 || url !== chatUrl) {
        const index = entries.findIndex((entry) => entry.key === userKey)
        if (index !== -1) entries.splice(index, 1)
      }
    }
    switch (event.type) {
      case 'change_proposed':
        reloadChanges()
        break
      case 'token':
        if (reply) reply.text += event.text
        else {
          reply = reactive<ChatMessage>({ key: key(), role: 'assistant', text: event.text })
          entries.push(reply)
        }
        break
      case 'tool_call': {
        reply = undefined
        const entry = reactive<ChatToolCall>({
          key: key(),
          role: 'tool',
          name: event.name,
          arguments: event.arguments,
        })
        pending.set(event.id, entry)
        entries.push(entry)
        break
      }
      case 'tool_result': {
        const entry = pending.get(event.id)
        if (!entry) break
        entry.outcome = { result: event.result, isError: event.is_error }
        pending.delete(event.id)
        break
      }
      case 'warning':
      case 'error':
        reply = undefined
        if (event.type === 'error' && httpStatus === 409) {
          externalRun.value = true
          entries.push({ key: key(), role: 'error', text: 'A run is already in progress' })
        } else if (!(stopping.value && event.type === 'error' && event.code === 'network_error')) {
          entries.push({ key: key(), role: event.type, text: event.message })
        }
        break
    }
  })

  const waiting = computed(() => stream.isStreaming.value && entries.at(-1)?.role === 'user')

  async function run(endpoint: string, payload: typeof body, text: string) {
    if (busy.value || !text.trim()) return false
    submitting.value = true
    rejected = false
    url = endpoint
    body = payload
    userKey = key()
    entries.push({ key: userKey, role: 'user', text })
    reply = undefined
    try {
      await stream.start()
      return !rejected
    } finally {
      for (const entry of pending.values()) {
        entry.outcome = { result: 'No tool result was received.', isError: true }
      }
      pending.clear()
      if (endpoint !== chatUrl && !rejected && !lifetime.signal.aborted) reloadChanges()
      submitting.value = false
    }
  }

  function send(text: string) {
    const message = text.trim()
    return run(chatUrl, { message }, message)
  }

  function revise(changeId: ChangeId, comment: Comment) {
    return run(`/api/changes/${changeId}/comment`, comment, comment.text)
  }

  async function stop() {
    if (!runActive.value || stopping.value) return
    stopping.value = true
    try {
      await unwrap(
        api.POST('/sessions/{session_id}/cancel', {
          params: { path: { session_id: session.id } },
          signal: lifetime.signal,
        }),
      )
      if (lifetime.signal.aborted) return
      stream.abort()
      externalRun.value = false
    } catch (cause) {
      if (!lifetime.signal.aborted) {
        toast.error(cause instanceof ApiError ? cause.message : 'Could not stop the run.')
      }
    } finally {
      stopping.value = false
    }
  }

  return { entries, waiting, runActive, busy, stopping, send, revise, stop }
}
