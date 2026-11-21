import type { components } from '../api/schema'

type Change = components['schemas']['ChangeRead']
type JsonValue = components['schemas']['JsonValue']

export type StreamEvent =
  | { type: 'token'; text: string }
  | { type: 'tool_call'; id: string; name: string; arguments: Record<string, JsonValue> }
  | { type: 'tool_result'; id: string; name: string; result: JsonValue; is_error: boolean }
  | { type: 'change_proposed'; change_id: Change['id']; file_path: Change['file_path'] }
  | { type: 'warning'; message: string }
  | { type: 'error'; code: string; message: string }
  | { type: 'done' }
