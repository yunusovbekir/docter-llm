import type { components } from '../api/schema'
import type { ChatEntry, ChatToolCall } from '../types/chat'

const TOOL_ERROR_PREFIX = 'Tool error: '

function parseJson(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch (error) {
    if (error instanceof SyntaxError) return text
    throw error
  }
}

export function chatHistory(messages: components['schemas']['MessageRead'][]): ChatEntry[] {
  const entries: ChatEntry[] = []
  const pending = new Map<string, ChatToolCall>()
  for (const { id, role, content, tool_calls, tool_call_id } of messages) {
    if ((role === 'user' || role === 'assistant') && content) {
      entries.push({ key: `saved-${id}`, role, text: content })
    }
    if (role === 'assistant') {
      for (const [index, call] of (tool_calls ?? []).entries()) {
        const fn = call.function
        if (
          typeof call.id !== 'string' ||
          typeof fn !== 'object' ||
          fn === null ||
          !('name' in fn) ||
          typeof fn.name !== 'string' ||
          !('arguments' in fn)
        )
          continue

        const entry: ChatToolCall = {
          key: `saved-${id}-tool-${index}`,
          role: 'tool',
          name: fn.name,
          arguments: typeof fn.arguments === 'string' ? parseJson(fn.arguments) : fn.arguments,
        }
        pending.set(call.id, entry)
        entries.push(entry)
      }
    } else if (role === 'tool' && tool_call_id) {
      const entry = pending.get(tool_call_id)
      if (!entry) continue
      // Persisted tool messages encode failure in their text rather than a flag.
      const isError = content?.startsWith(TOOL_ERROR_PREFIX) ?? false
      entry.outcome = {
        result:
          content === null
            ? null
            : parseJson(isError ? content.slice(TOOL_ERROR_PREFIX.length) : content),
        isError,
      }
      pending.delete(tool_call_id)
    }
  }
  for (const entry of pending.values()) {
    entry.outcome = { result: 'No tool result was saved.', isError: true }
  }
  return entries
}
