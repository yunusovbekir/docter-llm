import { computed, ref } from 'vue'
import type { components } from '../api/schema'
import { useToast } from '../stores/toast'
import type { ChatMessage } from '../types/chat'
import { useEventStream } from './useEventStream'

type Session = components['schemas']['SessionDetail']

export function useChat(session: Session) {
  const history: ChatMessage[] = []
  for (const { id, role, content } of session.messages) {
    if ((role === 'user' || role === 'assistant') && content) {
      history.push({ key: `saved-${id}`, role, text: content })
    }
  }

  const local = ref<ChatMessage[]>([])
  const reply = ref('')
  let turn = 0
  const body: components['schemas']['ChatRequest'] = { message: '' }
  const stream = useEventStream(`/api/sessions/${session.id}/chat`, body)
  const toast = useToast()
  stream.onEvent((event) => {
    if (event.type === 'token') reply.value += event.text
    else if (event.type === 'error') toast.error(event.message)
  })

  const messages = computed<ChatMessage[]>(() => [
    ...history,
    ...local.value,
    ...(stream.isStreaming.value
      ? [{ key: `assistant-${turn}`, role: 'assistant' as const, text: reply.value }]
      : []),
  ])

  async function send(text: string) {
    if (stream.isStreaming.value || !text.trim()) return
    const current = ++turn
    body.message = text.trim()
    local.value.push({ key: `user-${current}`, role: 'user', text: body.message })
    reply.value = ''
    await stream.start()
    if (reply.value) {
      local.value.push({ key: `assistant-${current}`, role: 'assistant', text: reply.value })
    }
  }

  return { messages, isStreaming: stream.isStreaming, send }
}
