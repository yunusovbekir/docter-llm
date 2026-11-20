<script setup lang="ts">
withDefaults(defineProps<{ as?: 'div' | 'section' | 'article' }>(), { as: 'div' })
</script>

<template>
  <component
    :is="as"
    class="rounded-lg border border-slate-200 bg-white p-4 text-slate-900 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-100"
  >
    <header v-if="$slots.header" class="mb-3"><slot name="header" /></header>
    <slot />
    <footer v-if="$slots.footer" class="mt-3"><slot name="footer" /></footer>
  </component>
</template>
