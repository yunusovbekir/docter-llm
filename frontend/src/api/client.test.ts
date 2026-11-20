import { http, HttpResponse } from 'msw'
import { expect, it } from 'vitest'
import { server } from '../test/msw'
import { api, ApiError, unwrap } from './client'
import type { components } from './schema'

const healthUrl = '/api/health'

it('unwraps successful API data', async () => {
  const body = { status: 'ok', version: '1.0.0' } satisfies components['schemas']['HealthResponse']
  server.use(http.get(healthUrl, () => HttpResponse.json(body)))

  const data = await unwrap(api.GET('/health'))

  expect(data.status).toBe('ok')
  expect(data).toEqual(body)
})

it('unwraps a successful response with no content', async () => {
  server.use(http.delete('/api/projects/1', () => new HttpResponse(null, { status: 204 })))

  await expect(
    unwrap(api.DELETE('/projects/{project_id}', { params: { path: { project_id: 1 } } })),
  ).resolves.toBeUndefined()
})

it('normalizes the backend error envelope', async () => {
  const body = {
    error: { code: 'internal_error', message: 'Internal server error' },
  } satisfies components['schemas']['ErrorResponse']
  server.use(http.get(healthUrl, () => HttpResponse.json(body, { status: 500 })))

  await expect(unwrap(api.GET('/health'))).rejects.toMatchObject({
    name: 'ApiError',
    code: body.error.code,
    message: body.error.message,
    status: 500,
  })
})

it('normalizes an HTTP failure outside the backend error envelope', async () => {
  server.use(http.get(healthUrl, () => HttpResponse.text('Bad gateway', { status: 502 })))

  await expect(unwrap(api.GET('/health'))).rejects.toMatchObject({
    name: 'ApiError',
    code: 'http_error',
    status: 502,
  })
})

it('normalizes a network failure without an HTTP status', async () => {
  const handler = http.get(healthUrl, () => HttpResponse.error())
  server.use(handler)

  const failure = unwrap(api.GET('/health'))

  await expect(failure).rejects.toBeInstanceOf(ApiError)
  await expect(failure).rejects.toMatchObject({
    code: 'network_error',
    message: 'Could not reach the API.',
    status: 0,
  })
  expect(handler.isUsed).toBe(true)
})
