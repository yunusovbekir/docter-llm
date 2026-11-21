import { enableAutoUnmount, mount } from '@vue/test-utils'
import { http, HttpResponse } from 'msw'
import { createPinia, disposePinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { components } from '../api/schema'
import { useToast } from '../stores/toast'
import { server } from '../test/msw'
import SettingsView from './SettingsView.vue'

const settings = {
  provider: 'openai',
  model: 'gpt-4.1',
  base_url: 'https://example.test/v1',
  embedding_model: 'text-embedding-3-small',
  temperature: 0.3,
  max_agent_steps: 20,
  has_api_key: true,
} satisfies components['schemas']['SettingsRead']

let pinia: ReturnType<typeof createPinia>
enableAutoUnmount(afterEach)
beforeEach(() => {
  pinia = createPinia()
  server.use(http.get('/api/settings', () => HttpResponse.json(settings)))
})
afterEach(() => disposePinia(pinia))

function render() {
  return mount(SettingsView, { global: { plugins: [pinia] } })
}

async function loadedView() {
  const wrapper = render()
  await vi.waitFor(() => expect(wrapper.find('form').exists()).toBe(true))
  return wrapper
}

it('loads settings, shows the saved-key badge, and leaves the password input empty', async () => {
  const wrapper = render()
  expect(wrapper.get('[role="status"]').text()).toBe('Loading settings')
  await vi.waitFor(() => expect(wrapper.text()).toContain('Key saved'))

  expect(wrapper.get<HTMLSelectElement>('select[name="provider"]').element.value).toBe('openai')
  expect(wrapper.findAll('option').map((option) => option.attributes('value'))).toEqual([
    'openai',
    'anthropic',
    'ollama',
    'other',
  ])
  for (const name of [
    'model',
    'base_url',
    'embedding_model',
    'temperature',
    'max_agent_steps',
  ] as const) {
    expect(wrapper.get<HTMLInputElement>(`input[name="${name}"]`).element.value).toBe(
      String(settings[name]),
    )
  }
  expect(wrapper.get<HTMLInputElement>('input[type="password"]').element.value).toBe('')
  expect(wrapper.get<HTMLButtonElement>('button[type="submit"]').element.disabled).toBe(true)
})

it('enables Save only while the form is both changed and valid', async () => {
  const wrapper = await loadedView()
  const save = wrapper.get<HTMLButtonElement>('button[type="submit"]')
  const model = wrapper.get('input[name="model"]')

  await model.setValue('gpt-4.1-mini')
  expect(save.element.disabled).toBe(false)

  for (const [name, invalid] of [
    ['model', ' '],
    ['embedding_model', ''],
    ['base_url', 'ftp://example.test'],
    ['temperature', '2.1'],
    ['temperature', ''],
    ['max_agent_steps', '1.5'],
    ['max_agent_steps', '201'],
  ] as const) {
    const input = wrapper.get(`input[name="${name}"]`)
    const previous = (input.element as HTMLInputElement).value
    await input.setValue(invalid)
    expect(input.attributes('aria-invalid')).toBe('true')
    expect(save.element.disabled).toBe(true)
    await input.setValue(previous)
    expect(save.element.disabled).toBe(false)
  }

  await model.setValue(settings.model)
  expect(save.element.disabled).toBe(true)
})

it('saves just the changed fields, omits a blank key, and shows a success toast', async () => {
  const put = vi.fn()
  server.use(
    http.put('/api/settings', async ({ request }) => {
      put(await request.json())
      return HttpResponse.json({
        ...settings,
        model: 'gpt-4.1-mini',
        base_url: null,
        temperature: 1,
      })
    }),
  )
  const wrapper = await loadedView()
  await wrapper.get('input[name="model"]').setValue(' gpt-4.1-mini ')
  await wrapper.get('input[name="base_url"]').setValue('')
  await wrapper.get('input[name="temperature"]').setValue('1')
  await wrapper.get('form').trigger('submit')

  const toast = useToast(pinia)
  await vi.waitFor(() =>
    expect(toast.toasts).toMatchObject([{ kind: 'success', message: 'Settings saved.' }]),
  )
  expect(put).toHaveBeenCalledExactlyOnceWith({
    model: 'gpt-4.1-mini',
    base_url: null,
    temperature: 1,
  })
  expect(wrapper.get<HTMLInputElement>('input[type="password"]').element.value).toBe('')
  expect(wrapper.get<HTMLButtonElement>('button[type="submit"]').element.disabled).toBe(true)
})

it('writes a new key and clears the password input after saving', async () => {
  const put = vi.fn()
  server.use(
    http.put('/api/settings', async ({ request }) => {
      put(await request.json())
      return HttpResponse.json(settings)
    }),
  )
  const wrapper = await loadedView()
  const key = wrapper.get<HTMLInputElement>('input[type="password"]')
  await key.setValue('test-key')
  await wrapper.get('form').trigger('submit')

  await vi.waitFor(() => expect(key.element.value).toBe(''))
  expect(put).toHaveBeenCalledExactlyOnceWith({ api_key: 'test-key' })
})

it('sends an empty key only when removal is pending at save time', async () => {
  const put = vi.fn()
  server.use(
    http.put('/api/settings', async ({ request }) => {
      put(await request.json())
      return HttpResponse.json({ ...settings, has_api_key: false })
    }),
  )
  const wrapper = await loadedView()
  const remove = wrapper.findAll('button').find((button) => button.text() === 'Remove key')!
  await remove.trigger('click')
  expect(remove.text()).toBe('Undo removal')
  expect(put).not.toHaveBeenCalled()
  await remove.trigger('click')
  expect(wrapper.get<HTMLButtonElement>('button[type="submit"]').element.disabled).toBe(true)

  await remove.trigger('click')
  await wrapper.get('form').trigger('submit')
  await vi.waitFor(() => expect(wrapper.text()).not.toContain('Key saved'))
  expect(put).toHaveBeenCalledExactlyOnceWith({ api_key: '' })
})

it('does not carry key removal over when switching providers', async () => {
  const put = vi.fn()
  server.use(
    http.put('/api/settings', async ({ request }) => {
      put(await request.json())
      return HttpResponse.json({ ...settings, provider: 'azure', has_api_key: false })
    }),
  )
  const wrapper = await loadedView()
  await wrapper
    .findAll('button')
    .find((button) => button.text() === 'Remove key')!
    .trigger('click')
  await wrapper.get('select[name="provider"]').setValue('other')
  expect(wrapper.text()).not.toContain('Key saved')
  expect(wrapper.get<HTMLButtonElement>('button[type="submit"]').element.disabled).toBe(true)
  await wrapper.get('input[name="custom_provider"]').setValue('Azure')
  await wrapper.get('form').trigger('submit')
  await vi.waitFor(() =>
    expect(wrapper.get<HTMLInputElement>('input[name="custom_provider"]').element.value).toBe(
      'azure',
    ),
  )
  expect(put).toHaveBeenCalledExactlyOnceWith({ provider: 'azure' })
})

it('shows a load error and lets the user retry', async () => {
  server.use(
    http.get(
      '/api/settings',
      () =>
        HttpResponse.json(
          { error: { code: 'unavailable', message: 'Settings are unavailable.' } },
          { status: 503 },
        ),
      { once: true },
    ),
  )
  const wrapper = render()
  await vi.waitFor(() => expect(wrapper.text()).toContain('Settings are unavailable.'))
  expect(useToast(pinia).toasts).toMatchObject([
    { kind: 'error', message: 'Settings are unavailable.' },
  ])
  await wrapper.get('button').trigger('click')
  await vi.waitFor(() => expect(wrapper.find('form').exists()).toBe(true))
})

it('reports a failed save without resetting the form', async () => {
  server.use(
    http.put('/api/settings', () =>
      HttpResponse.json(
        { error: { code: 'unavailable', message: 'Settings could not be saved.' } },
        { status: 503 },
      ),
    ),
  )
  const wrapper = await loadedView()
  const model = wrapper.get<HTMLInputElement>('input[name="model"]')
  await model.setValue('gpt-4.1-mini')
  await wrapper.get('form').trigger('submit')

  await vi.waitFor(() =>
    expect(useToast(pinia).toasts).toMatchObject([
      { kind: 'error', message: 'Settings could not be saved.' },
    ]),
  )
  expect(model.element.value).toBe('gpt-4.1-mini')
  expect(wrapper.get<HTMLButtonElement>('button[type="submit"]').element.disabled).toBe(false)
})
