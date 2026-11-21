import { api, ApiError, unwrap } from './client'
import type { components } from './schema'

export async function createSession(
  body: components['schemas']['SessionCreate'],
  signal: AbortSignal,
) {
  const session = await unwrap(api.POST('/sessions', { body, signal }))
  if (!session) throw new ApiError('invalid_response', 'The API returned no session.', 0)
  return session
}
