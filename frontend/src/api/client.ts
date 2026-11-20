import createClient from 'openapi-fetch'
import type { components, paths } from './schema'

// FastAPI already includes /api in its paths; openapi-fetch adds the base URL.
type ApiPaths = {
  [Path in keyof paths as Path extends `/api/${infer Route}` ? `/${Route}` : never]: paths[Path]
}

type ErrorResponse = components['schemas']['ErrorResponse']

export const api = createClient<ApiPaths>({
  baseUrl: '/api',
  fetch: (request) => globalThis.fetch(request),
})

export class ApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status: number,
    options?: ErrorOptions,
  ) {
    super(message, options)
    this.name = 'ApiError'
  }
}

function isErrorResponse(body: unknown): body is ErrorResponse {
  if (typeof body !== 'object' || body === null || !('error' in body)) return false

  const detail = body.error
  return (
    typeof detail === 'object' &&
    detail !== null &&
    'code' in detail &&
    typeof detail.code === 'string' &&
    'message' in detail &&
    typeof detail.message === 'string'
  )
}

export function unwrap<Result extends { response: Response }>(
  request: Promise<Result>,
): Promise<Extract<Result, { data: unknown }>['data']>
export async function unwrap(
  request: Promise<{ response: Response; data?: unknown; error?: unknown }>,
): Promise<unknown> {
  const result = await request.catch((cause: unknown) => {
    if (cause instanceof ApiError) throw cause
    if (cause instanceof SyntaxError) {
      throw new ApiError('invalid_response', 'The API returned an invalid response.', 0, { cause })
    }
    throw new ApiError('network_error', 'Could not reach the API.', 0, { cause })
  })

  if (result.response.ok && 'data' in result) return result.data

  const { status, statusText } = result.response
  if ('error' in result && isErrorResponse(result.error)) {
    const { code, message } = result.error.error
    throw new ApiError(code, message, status)
  }

  throw new ApiError('http_error', statusText || `HTTP ${status}`, status)
}
