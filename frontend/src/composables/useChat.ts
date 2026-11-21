import { computed, reactive } from 'vue'
import type { components } from '../api/schema'
import type { ChatMessage, ChatToolCall } from '../types/chat'
import { chatHistory } from './chatHistory'
import { useEventStream } from './useEventStream'

type Session = components['schemas']['SessionDetail']

export function useChat(session: Session) {
  const entries = reactive(chatHistory(session.messages))
  const pending = new Map<string, ChatToolCall>()
  let reply: ChatMessage | undefined
  let nextId = 0
  const key = () => `live-${++nextId}`
  const body: components['schemas']['ChatRequest'] = { message: '' }
  const stream = useEventStream(`/api/sessions/${session.id}/chat`, body)
  stream.onEvent((event) => {
    switch (event.type) {
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
        entries.push({ key: key(), role: event.type, text: event.message })
        break
    }
  })

  const waiting = computed(() => stream.isStreaming.value && entries.at(-1)?.role === 'user')

  async function send(text: string) {
    if (stream.isStreaming.value || !text.trim()) return
    body.message = text.trim()
    entries.push({ key: key(), role: 'user', text: body.message })
    reply = undefined
    await stream.start()
    for (const entry of pending.values()) {
      entry.outcome = { result: 'No tool result was received.', isError: true }
    }
    pending.clear()
  }

  return { entries, waiting, isStreaming: stream.isStreaming, send }
}
