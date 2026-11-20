<script setup lang="ts">
withDefaults(defineProps<{ label?: string; decorative?: boolean }>(), { label: 'Loading' })
</script>

<template>
  <span
    :role="decorative ? undefined : 'status'"
    :aria-hidden="decorative || undefined"
    class="inline-flex"
  >
    <span
      aria-hidden="true"
      class="size-4 rounded-full border-2 border-current border-r-transparent motion-safe:animate-spin"
    />
    <span v-if="!decorative" class="sr-only">{{ label }}</span>
  </span>
</template>
