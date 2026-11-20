<script setup lang="ts">
import AppSpinner from './AppSpinner.vue'

withDefaults(
  defineProps<{
    variant?: 'primary' | 'secondary' | 'danger' | 'ghost'
    type?: 'button' | 'submit' | 'reset'
    loading?: boolean
    disabled?: boolean
  }>(),
  { variant: 'primary', type: 'button' },
)

const emit = defineEmits<{ click: [event: globalThis.MouseEvent] }>()

const variants = {
  primary:
    'bg-blue-700 text-white enabled:hover:bg-blue-800 dark:bg-blue-700 dark:enabled:hover:bg-blue-800',
  secondary:
    'bg-slate-200 text-slate-900 enabled:hover:bg-slate-300 dark:bg-slate-700 dark:text-white dark:enabled:hover:bg-slate-600',
  danger:
    'bg-red-700 text-white enabled:hover:bg-red-800 dark:bg-red-700 dark:enabled:hover:bg-red-800',
  ghost:
    'text-slate-700 enabled:hover:bg-slate-100 dark:text-slate-200 dark:enabled:hover:bg-slate-800',
}
</script>

<template>
  <button
    :type="type"
    :disabled="disabled || loading"
    :aria-busy="loading || undefined"
    :class="variants[variant]"
    class="inline-flex min-h-10 items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-semibold focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 disabled:cursor-not-allowed disabled:opacity-60 dark:focus-visible:outline-blue-400"
    @click="!disabled && !loading && emit('click', $event)"
  >
    <AppSpinner v-if="loading" decorative />
    <slot />
  </button>
</template>
