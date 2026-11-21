import type { Pinia } from 'pinia'
import type { App } from 'vue'
import { ApiError } from './api/client'
import { useToast } from './stores/toast'

const GENERIC_ERROR_MESSAGE = 'Something went wrong. Please try again.'

export function installErrorHandling(app: App, pinia: Pinia) {
  const toast = useToast(pinia)

  function notify(error: unknown) {
    toast.error(error instanceof ApiError ? error.message : GENERIC_ERROR_MESSAGE)
  }

  app.config.errorHandler = (error, _instance, info) => {
    notify(error)
    if (!(error instanceof ApiError)) console.error(info, error)
  }

  function onUnhandledRejection(event: PromiseRejectionEvent) {
    notify(event.reason)
  }

  window.addEventListener('unhandledrejection', onUnhandledRejection)
  app.onUnmount(() => window.removeEventListener('unhandledrejection', onUnhandledRejection))
}
