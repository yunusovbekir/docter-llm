import { defineStore } from 'pinia'
import { onScopeDispose, ref } from 'vue'

type ToastKind = 'success' | 'error' | 'info'
type Toast = { id: number; kind: ToastKind; message: string }

const MAX_TOASTS = 4
const DISMISS_AFTER_MS = 5_000

export const useToast = defineStore('toast', () => {
  const toasts = ref<Toast[]>([])
  const timers = new Map<number, ReturnType<typeof setTimeout>>()
  let nextId = 0

  function dismiss(id: number) {
    clearTimeout(timers.get(id))
    timers.delete(id)
    toasts.value = toasts.value.filter((toast) => toast.id !== id)
  }

  function show(kind: ToastKind, message: string) {
    const oldest = toasts.value[0]
    if (toasts.value.length >= MAX_TOASTS && oldest) dismiss(oldest.id)

    const id = nextId++
    toasts.value.push({ id, kind, message })
    timers.set(
      id,
      setTimeout(() => dismiss(id), DISMISS_AFTER_MS),
    )
  }

  onScopeDispose(() => {
    for (const timer of timers.values()) clearTimeout(timer)
    timers.clear()
  })

  return {
    toasts,
    dismiss,
    success: (message: string) => show('success', message),
    error: (message: string) => show('error', message),
    info: (message: string) => show('info', message),
  }
})
