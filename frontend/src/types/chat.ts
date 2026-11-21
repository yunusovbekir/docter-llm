import type { StreamEvent } from './events'

export type ChatMessage = {
  key: string
  role: 'user' | 'assistant'
  text: string
}

type ToolCall = Extract<StreamEvent, { type: 'tool_call' }>
type ToolResult = Extract<StreamEvent, { type: 'tool_result' }>

export type ChatToolCall = {
  key: string
  role: 'tool'
  name: ToolCall['name']
  arguments: unknown
  outcome?: { result: ToolResult['result']; isError: boolean }
}

export type ChatEntry =
  ChatMessage | ChatToolCall | { key: string; role: 'warning' | 'error'; text: string }
