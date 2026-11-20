<script setup lang="ts">
withDefaults(defineProps<{ tone?: 'neutral' | 'info' | 'success' | 'warning' | 'danger' }>(), {
  tone: 'neutral',
})

const tones = {
  neutral: 'bg-slate-100 text-slate-800 dark:bg-slate-800 dark:text-slate-200',
  info: 'bg-blue-100 text-blue-900 dark:bg-blue-950 dark:text-blue-200',
  success: 'bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200',
  warning: 'bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200',
  danger: 'bg-red-100 text-red-900 dark:bg-red-950 dark:text-red-200',
}
</script>

<template>
  <span
    class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium"
    :class="tones[tone]"
  >
    <slot />
  </span>
</template>
