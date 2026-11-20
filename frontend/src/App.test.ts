import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { afterEach, expect, it } from 'vitest'
import { createMemoryHistory } from 'vue-router'
import App from './App.vue'
import { createAppRouter } from './router'

enableAutoUnmount(afterEach)

async function renderAt(path: string) {
  const router = createAppRouter(createMemoryHistory())
  await router.push(path)
  await router.isReady()
  const wrapper = mount(App, { global: { plugins: [router] } })
  return { router, wrapper }
}

it.each([
  ['/projects', 'Projects'],
  ['/projects/example', 'Project'],
  ['/sessions/example', 'Session'],
  ['/history', 'History'],
  ['/settings', 'Settings'],
])('renders %s in the app shell', async (path, heading) => {
  const { wrapper } = await renderAt(path)

  expect(wrapper.get('header').text()).toBe('Docter')
  expect(wrapper.get('main > h1').text()).toBe(heading)
  expect(wrapper.get('main').element.childElementCount).toBe(1)
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
