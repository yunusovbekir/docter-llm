<script setup lang="ts">
defineProps<{ title: string; description?: string }>()
</script>

<template>
  <div
    class="space-y-2 rounded-lg border border-dashed border-slate-300 p-6 text-center dark:border-slate-700"
  >
    <h2 class="font-semibold text-slate-900 dark:text-slate-100">{{ title }}</h2>
    <p v-if="description" class="text-sm text-slate-600 dark:text-slate-300">{{ description }}</p>
    <div v-if="$slots.actions" class="pt-2"><slot name="actions" /></div>
  </div>
</template>
