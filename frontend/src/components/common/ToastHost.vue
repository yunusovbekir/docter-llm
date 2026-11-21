<script setup lang="ts">
import { useToast } from '../../stores/toast'
import AppButton from './AppButton.vue'

const toast = useToast()
const tones = {
  success: 'border-green-600 dark:border-green-400',
  error: 'border-red-600 dark:border-red-400',
  info: 'border-blue-600 dark:border-blue-400',
}
</script>

<template>
  <ul
    aria-live="polite"
    class="pointer-events-none fixed inset-x-4 bottom-4 z-40 flex max-h-[calc(100dvh-2rem)] flex-col gap-2 overflow-y-auto sm:left-auto sm:w-96"
  >
    <li
      v-for="{ id, kind, message } in toast.toasts"
      :key="id"
      :class="tones[kind]"
      class="pointer-events-auto flex shrink-0 items-start justify-between gap-2 rounded-md border-l-4 bg-white p-3 text-sm text-slate-900 shadow-lg dark:bg-slate-800 dark:text-slate-100"
    >
      <p class="min-w-0 break-words">
        <strong class="capitalize">{{ kind }}:</strong> {{ message }}
      </p>
      <AppButton
        variant="ghost"
        :aria-label="`Dismiss notification: ${message}`"
        @click="toast.dismiss(id)"
      >
        Dismiss
      </AppButton>
    </li>
  </ul>
</template>
