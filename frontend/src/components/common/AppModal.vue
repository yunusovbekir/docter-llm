<script setup lang="ts">
import { tabbable } from 'tabbable'
import { onBeforeUnmount, onMounted, useId, useTemplateRef, watch } from 'vue'
import AppButton from './AppButton.vue'

const props = defineProps<{ open: boolean; title: string; description?: string }>()
const emit = defineEmits<{ close: [] }>()
const dialog = useTemplateRef<globalThis.HTMLDialogElement>('dialog')
const titleId = useId()
const descriptionId = useId()

function syncOpen() {
  const element = dialog.value
  if (!element || element.open === props.open) return

  if (props.open) element.showModal()
  else element.close()
}

onMounted(syncOpen)
watch(() => props.open, syncOpen, { flush: 'post' })
onBeforeUnmount(() => {
  if (dialog.value?.open) dialog.value.close()
})

function trapTab(event: globalThis.KeyboardEvent) {
  const element = dialog.value
  if (event.defaultPrevented || !element?.open) return

  const stops = tabbable(element)
  const active = element.ownerDocument.activeElement
  const edge = event.shiftKey ? stops[0] : stops.at(-1)
  if (active !== edge && stops.some((stop) => stop === active)) return

  event.preventDefault()
  const next = event.shiftKey ? stops.at(-1) : stops[0]
  next?.focus()
}
</script>

<template>
  <Teleport to="body">
    <dialog
      ref="dialog"
      :aria-labelledby="titleId"
      :aria-describedby="description ? descriptionId : undefined"
      aria-modal="true"
      class="max-h-[calc(100%-2rem)] w-[calc(100%-2rem)] max-w-lg overflow-y-auto rounded-lg border border-slate-200 bg-white p-6 text-slate-900 shadow-xl backdrop:bg-slate-950/60 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:backdrop:bg-black/75"
      @cancel.prevent="emit('close')"
      @keydown.tab="trapTab"
    >
      <header class="mb-4 flex items-start justify-between gap-4">
        <div>
          <h2 :id="titleId" class="text-lg font-semibold">{{ title }}</h2>
          <p
            v-if="description"
            :id="descriptionId"
            class="mt-1 text-sm text-slate-600 dark:text-slate-300"
          >
            {{ description }}
          </p>
        </div>
        <AppButton variant="ghost" @click="emit('close')">Close</AppButton>
      </header>
      <slot />
      <footer v-if="$slots.footer" class="mt-4"><slot name="footer" /></footer>
    </dialog>
  </Teleport>
</template>
