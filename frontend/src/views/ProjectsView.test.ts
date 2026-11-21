import { enableAutoUnmount, mount } from '@vue/test-utils'
import { http, HttpResponse } from 'msw'
import { createPinia, disposePinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'
import type { components } from '../api/schema'
import { createAppRouter } from '../router'
import { useToast } from '../stores/toast'
import { server } from '../test/msw'
import ProjectsView from './ProjectsView.vue'

const project = {
  id: 8,
  name: 'docs',
  path: '/home/user/repos/docs',
  current_branch: 'main',
  index_status: 'done',
  indexed_at: '2026-01-01T00:00:00Z',
  created_at: '2026-01-01T00:00:00Z',
} satisfies components['schemas']['ProjectRead']

let pinia: ReturnType<typeof createPinia>
enableAutoUnmount(afterEach)
beforeEach(() => {
  pinia = createPinia()
  server.use(http.get('/api/projects', () => HttpResponse.json([])))
})
afterEach(() => disposePinia(pinia))

async function render() {
  const router = createAppRouter(createMemoryHistory())
  await router.push('/projects')
  const wrapper = mount(ProjectsView, { global: { plugins: [pinia, router] } })

  for (const dialog of document.body.querySelectorAll('dialog')) {
    // jsdom does not implement the native dialog methods.
    dialog.showModal = () => {
      dialog.open = true
    }
    dialog.close = () => {
      dialog.open = false
    }
  }
  return { router, wrapper }
}

function openDialog() {
  const dialog = document.body.querySelector<HTMLDialogElement>('dialog[open]')
  if (!dialog) throw new Error('Expected an open dialog')
  return dialog
}

function clickDialogButton(label: string) {
  const button = Array.from(openDialog().querySelectorAll('button')).find(
    (button) => button.textContent?.trim() === label,
  )
  if (!button) throw new Error(`Dialog button not found: ${label}`)
  button.click()
}

async function startAdd(path = project.path) {
  const { wrapper } = await render()
  await vi.waitFor(() => expect(wrapper.text()).toContain('No projects yet'))
  await wrapper.get('button').trigger('click')
  const dialog = openDialog()
  const input = dialog.querySelector<HTMLInputElement>('input[name="path"]')!
  input.value = path
  input.dispatchEvent(new Event('input', { bubbles: true }))
  dialog.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }))
  return { wrapper, dialog, input }
}

it('loads project cards with repository details and links to the project route', async () => {
  server.use(http.get('/api/projects', () => HttpResponse.json([project])))
  const { wrapper, router } = await render()
  expect(wrapper.get('[role="status"]').text()).toBe('Loading projects')
  await vi.waitFor(() => expect(wrapper.find('article').exists()).toBe(true))

  const card = wrapper.get('article')
  expect(card.text()).toContain(project.path)
  expect(card.text()).toContain('Branch: main')
  expect(card.text()).toContain('Indexed')
  expect(card.get('a').text()).toBe(project.name)
  await card.get('a').trigger('click')
  await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/projects/8'))
})

it('shows a list error and retries into the empty state', async () => {
  server.use(
    http.get(
      '/api/projects',
      () =>
        HttpResponse.json(
          { error: { code: 'unavailable', message: 'Projects are unavailable.' } },
          { status: 503 },
        ),
      { once: true },
    ),
  )
  const { wrapper } = await render()
  await vi.waitFor(() => expect(wrapper.text()).toContain('Projects are unavailable.'))
  expect(useToast(pinia).toasts).toMatchObject([
    { kind: 'error', message: 'Projects are unavailable.' },
  ])
  await wrapper
    .findAll('button')
    .find((button) => button.text() === 'Retry')!
    .trigger('click')
  await vi.waitFor(() => expect(wrapper.text()).toContain('No projects yet'))
})

it('posts the path and shows the newly created project without reloading', async () => {
  const post = vi.fn()
  server.use(
    http.post('/api/projects', async ({ request }) => {
      post(await request.json())
      return HttpResponse.json({ ...project, is_dirty: false, warnings: [] }, { status: 201 })
    }),
  )
  const { wrapper, dialog } = await startAdd()
  await vi.waitFor(() => expect(dialog.open).toBe(false))
  expect(post).toHaveBeenCalledExactlyOnceWith({ path: project.path })
  expect(wrapper.get('article a').text()).toBe(project.name)
  expect(useToast(pinia).toasts).toEqual([])
})

it('keeps the add modal open and displays backend validation beside the input', async () => {
  const message = 'Path must be absolute: docs'
  server.use(
    http.post('/api/projects', () =>
      HttpResponse.json({ error: { code: 'invalid_path', message } }, { status: 400 }),
    ),
  )
  const { dialog, input, wrapper } = await startAdd('docs')
  await vi.waitFor(() => expect(input.getAttribute('aria-invalid')).toBe('true'))
  expect(
    input
      .getAttribute('aria-describedby')
      ?.split(' ')
      .map((id) => document.getElementById(id)?.textContent),
  ).toContain(message)
  expect(input.value).toBe('docs')
  expect(dialog.open).toBe(true)
  expect(wrapper.text()).toContain('No projects yet')
  expect(useToast(pinia).toasts).toMatchObject([{ kind: 'error', message }])
})

it('adds a dirty repository and shows its warning as a toast', async () => {
  const message = 'The working tree has uncommitted changes.'
  server.use(
    http.post('/api/projects', () =>
      HttpResponse.json({ ...project, is_dirty: true, warnings: [message] }, { status: 201 }),
    ),
  )
  const { wrapper, dialog } = await startAdd()
  await vi.waitFor(() => expect(dialog.open).toBe(false))
  expect(wrapper.get('article a').text()).toBe(project.name)
  expect(useToast(pinia).toasts).toMatchObject([{ kind: 'warning', message }])
})

it('removes the project only after confirming that no files will be deleted', async () => {
  const remove = vi.fn()
  server.use(
    http.get('/api/projects', () => HttpResponse.json([project])),
    http.delete('/api/projects/:id', ({ params }) => {
      remove(params.id)
      return new HttpResponse(null, { status: 204 })
    }),
  )
  const { wrapper, router } = await render()
  await vi.waitFor(() => expect(wrapper.find('article').exists()).toBe(true))
  const button = wrapper.get('button[aria-label="Remove docs"]')
  await button.trigger('click')
  const dialog = openDialog()
  expect(dialog.textContent).toContain('It does not delete any files.')
  expect(remove).not.toHaveBeenCalled()
  clickDialogButton('Cancel')
  await vi.waitFor(() => expect(dialog.open).toBe(false))
  expect(remove).not.toHaveBeenCalled()

  await button.trigger('click')
  clickDialogButton('Remove project')
  await vi.waitFor(() => expect(wrapper.text()).toContain('No projects yet'))
  expect(dialog.open).toBe(false)
  expect(remove).toHaveBeenCalledExactlyOnceWith('8')
  expect(router.currentRoute.value.path).toBe('/projects')
})
