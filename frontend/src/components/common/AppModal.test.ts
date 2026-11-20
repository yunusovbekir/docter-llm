import { enableAutoUnmount, mount } from '@vue/test-utils'
import { afterEach, expect, it, vi } from 'vitest'
import AppModal from './AppModal.vue'

enableAutoUnmount(afterEach)
afterEach(() => vi.restoreAllMocks())

async function openModal() {
  const host = document.createElement('div')
  const wrapper = mount(AppModal, {
    attachTo: host,
    props: { open: false, title: 'Review changes', description: 'Confirm the proposed patch.' },
    slots: {
      default: '<input aria-label="Comment">',
      footer: '<button id="confirm">Confirm</button><button disabled>Unavailable</button>',
    },
  })
  const dialog = document.body.querySelector('dialog')!
  const closeButton = dialog.querySelector('button')!
  const comment = dialog.querySelector('input')!
  const confirm = dialog.querySelector<HTMLButtonElement>('#confirm')!

  // jsdom does not implement native modal methods or element layout.
  dialog.showModal = vi.fn(() => {
    dialog.open = true
    closeButton.focus()
  })
  dialog.close = vi.fn(() => {
    dialog.open = false
  })
  for (const control of [closeButton, comment, confirm]) {
    const rect = new DOMRect(0, 0, 100, 40)
    vi.spyOn(control, 'getClientRects').mockReturnValue(
      Object.assign([rect], { item: (index: number) => (index === 0 ? rect : null) }),
    )
  }
  await wrapper.setProps({ open: true })
  return { wrapper, host, dialog, closeButton, comment, confirm }
}

function tab(shiftKey = false) {
  return document.activeElement?.dispatchEvent(
    new KeyboardEvent('keydown', { key: 'Tab', shiftKey, bubbles: true, cancelable: true }),
  )
}

it('opens a named, described native modal teleported directly to the body', async () => {
  const { wrapper, host, dialog, closeButton } = await openModal()
  expect(host.querySelector('dialog')).toBeNull()
  expect(dialog.parentElement).toBe(document.body)
  expect(dialog.showModal).toHaveBeenCalledOnce()
  expect(dialog.open).toBe(true)
  expect(document.activeElement).toBe(closeButton)
  expect(document.getElementById(dialog.getAttribute('aria-labelledby')!)?.textContent).toBe(
    'Review changes',
  )
  expect(document.getElementById(dialog.getAttribute('aria-describedby')!)?.textContent).toBe(
    'Confirm the proposed patch.',
  )
  await wrapper.setProps({ open: false })
  expect(dialog.close).toHaveBeenCalledOnce()
  expect(dialog.open).toBe(false)
})

it('wraps Tab and Shift+Tab at the edges but leaves interior navigation to the browser', async () => {
  const { closeButton, comment, confirm } = await openModal()
  expect(tab(true)).toBe(false)
  expect(document.activeElement).toBe(confirm)
  expect(tab()).toBe(false)
  expect(document.activeElement).toBe(closeButton)
  comment.focus()
  expect(tab()).toBe(true)
  expect(tab(true)).toBe(true)
})

it('requests closure on native Escape cancellation and on the close button', async () => {
  const { wrapper, dialog, closeButton } = await openModal()
  // A real Escape key produces the dialog's native cancel event.
  const cancel = new Event('cancel', { cancelable: true })
  expect(dialog.dispatchEvent(cancel)).toBe(false)
  expect(wrapper.emitted('close')).toEqual([[]])
  expect(dialog.open).toBe(true)
  closeButton.click()
  expect(wrapper.emitted('close')).toEqual([[], []])
})
