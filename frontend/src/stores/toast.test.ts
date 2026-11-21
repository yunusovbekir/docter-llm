import { createPinia, disposePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { useToast } from './toast'

let pinia: Pinia

beforeEach(() => {
  vi.useFakeTimers()
  pinia = createPinia()
})

afterEach(() => {
  disposePinia(pinia)
  vi.useRealTimers()
})

it('keeps the four most recent toasts in order and cancels the evicted timer', () => {
  const toast = useToast(pinia)

  toast.info('First')
  toast.success('Second')
  toast.error('Third')
  toast.info('Fourth')
  toast.success('Fifth')

  expect(toast.toasts.map(({ kind, message }) => [kind, message])).toEqual([
    ['success', 'Second'],
    ['error', 'Third'],
    ['info', 'Fourth'],
    ['success', 'Fifth'],
  ])
  expect(vi.getTimerCount()).toBe(4)
})

it('dismisses each toast five seconds after it is added', () => {
  const toast = useToast(pinia)

  toast.info('First')
  vi.advanceTimersByTime(1_000)
  toast.info('Second')
  vi.advanceTimersByTime(3_999)
  expect(toast.toasts).toHaveLength(2)

  vi.advanceTimersByTime(1)
  expect(toast.toasts.map(({ message }) => message)).toEqual(['Second'])

  vi.advanceTimersByTime(1_000)
  expect(toast.toasts).toHaveLength(0)
})

it('dismisses only the requested toast and clears its timer', () => {
  const toast = useToast(pinia)

  toast.info('Same message')
  toast.info('Same message')
  const [first, second] = toast.toasts
  toast.dismiss(second.id)

  expect(toast.toasts.map(({ id }) => id)).toEqual([first.id])
  expect(vi.getTimerCount()).toBe(1)
})
