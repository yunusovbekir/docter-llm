import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { http, HttpResponse } from 'msw'
import { createPinia, disposePinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { createMemoryHistory } from 'vue-router'
import App from './App.vue'
import { createAppRouter } from './router'
import { useToast } from './stores/toast'
import { server } from './test/msw'

enableAutoUnmount(afterEach)
beforeEach(() => {
  server.use(http.get('/api/projects', () => HttpResponse.json([])))
})

async function renderAt(path: string) {
  const router = createAppRouter(createMemoryHistory())
  await router.push(path)
  await router.isReady()
  const pinia = createPinia()
  const wrapper = mount(App, { global: { plugins: [pinia, router] } })
  return { pinia, router, wrapper }
}

it.each([
  ['/projects', 'Projects'],
  ['/projects/example', 'Project'],
  ['/sessions/example', 'Session'],
  ['/history', 'History'],
  ['/settings', 'Settings'],
])('renders %s in the app shell', async (path, heading) => {
  if (path === '/settings') {
    server.use(http.get('/api/settings', () => new HttpResponse(null, { status: 204 })))
  }
  const { wrapper } = await renderAt(path)

  expect(wrapper.get('header').text()).toBe('Docter')
  expect(wrapper.get('main h1').text()).toBe(heading)
  expect(wrapper.get('main').element.childElementCount).toBe(1)
  if (path === '/settings') {
    await vi.waitFor(() => expect(wrapper.get('main h2').text()).toBe('No settings available'))
  }
})

it('redirects the root to Projects', async () => {
  const { router, wrapper } = await renderAt('/')

  expect(router.currentRoute.value.path).toBe('/projects')
  expect(wrapper.get('main h1').text()).toBe('Projects')
})

it('updates the active sidebar link when navigating', async () => {
  const { wrapper } = await renderAt('/projects')
  const nav = wrapper.get('nav[aria-label="Main navigation"]')

  expect(nav.findAll('a').map((link) => link.text())).toEqual(['Projects', 'History', 'Settings'])
  expect(nav.get('[aria-current="page"]').text()).toBe('Projects')
  expect(nav.get('a[href="/projects"]').classes()).toContain('bg-blue-100')

  await nav.get('a[href="/history"]').trigger('click')
  await flushPromises()

  expect(wrapper.get('main h1').text()).toBe('History')
  expect(nav.get('[aria-current="page"]').text()).toBe('History')
  expect(nav.get('a[href="/history"]').classes()).toContain('bg-blue-100')
  expect(nav.get('a[href="/projects"]').classes()).not.toContain('bg-blue-100')
})

it('keeps Projects highlighted on a project detail page', async () => {
  const { wrapper } = await renderAt('/projects/example')
  const projects = wrapper.get('nav a[href="/projects"]')

  expect(projects.classes()).toContain('bg-blue-100')
  expect(projects.attributes('aria-current')).toBeUndefined()
})

it('renders a 404 for an unknown nested path', async () => {
  const { wrapper } = await renderAt('/projects/example/unknown')

  expect(wrapper.get('main h1').text()).toBe('404: Page not found')
  expect(wrapper.find('nav a.bg-blue-100').exists()).toBe(false)
})

it('stacks and dismisses notifications in the app shell live region', async () => {
  const { pinia, wrapper } = await renderAt('/projects')
  const toast = useToast(pinia)
  const host = wrapper.get('[aria-live="polite"]')
  vi.useFakeTimers()

  try {
    expect(host.findAll('li')).toHaveLength(0)
    toast.success('Project saved.')
    toast.error('The API is unavailable.')
    toast.info('Checking for updates.')
    toast.warning('The repository has uncommitted changes.')
    await nextTick()

    expect(host.findAll('li p').map((item) => item.text())).toEqual([
      'success: Project saved.',
      'error: The API is unavailable.',
      'info: Checking for updates.',
      'warning: The repository has uncommitted changes.',
    ])

    await host
      .get('button[aria-label="Dismiss notification: The API is unavailable."]')
      .trigger('click')
    expect(host.findAll('li')).toHaveLength(3)
    expect(host.text()).not.toContain('The API is unavailable.')
    vi.advanceTimersByTime(5_000)
    await nextTick()
    expect(host.findAll('li')).toHaveLength(0)
  } finally {
    disposePinia(pinia)
    vi.useRealTimers()
  }
})
