import { createPinia, disposePinia } from 'pinia'
import { expect, it, onTestFinished, vi } from 'vitest'
import { createApp, defineComponent, onMounted } from 'vue'
import { ApiError } from './api/client'
import { installErrorHandling } from './errorHandling'
import { useToast } from './stores/toast'

function mountApp(error?: Error) {
  const app = createApp(
    defineComponent({
      setup() {
        onMounted(() => {
          if (error) throw error
        })
        return () => null
      },
    }),
  )
  const pinia = createPinia()
  app.use(pinia)
  installErrorHandling(app, pinia)
  app.mount(document.createElement('div'))
  let mounted = true
  const unmount = () => {
    if (!mounted) return
    app.unmount()
    mounted = false
  }
  onTestFinished(() => {
    unmount()
    disposePinia(pinia)
  })
  return { toast: useToast(pinia), unmount }
}

function rejectUnhandled(reason: unknown) {
  // jsdom does not expose a PromiseRejectionEvent constructor.
  window.dispatchEvent(Object.assign(new Event('unhandledrejection'), { reason }))
}

it('shows the API message for an uncaught Vue error', () => {
  const { toast } = mountApp(new ApiError('not_found', 'Project not found.', 404))

  expect(toast.toasts).toMatchObject([{ kind: 'error', message: 'Project not found.' }])
})

it('logs unexpected Vue errors but shows only a generic message', () => {
  const log = vi.spyOn(console, 'error').mockImplementation(() => {})
  onTestFinished(() => log.mockRestore())
  const error = new Error('Internal details')
  const { toast } = mountApp(error)

  expect(toast.toasts).toMatchObject([
    { kind: 'error', message: 'Something went wrong. Please try again.' },
  ])
  expect(log).toHaveBeenCalledWith(expect.any(String), error)
})

it('shows the API message for an unhandled rejection and removes the listener on unmount', () => {
  const { toast, unmount } = mountApp()
  rejectUnhandled(new ApiError('network_error', 'Could not reach the API.', 0))

  expect(toast.toasts).toMatchObject([{ kind: 'error', message: 'Could not reach the API.' }])

  unmount()
  rejectUnhandled(new ApiError('not_found', 'Project not found.', 404))
  expect(toast.toasts).toHaveLength(1)
})

it('shows a generic message for unexpected rejection reasons', () => {
  const { toast } = mountApp()
  rejectUnhandled(new Error('Internal details'))

  expect(toast.toasts).toMatchObject([
    { kind: 'error', message: 'Something went wrong. Please try again.' },
  ])
})
