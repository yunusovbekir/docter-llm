<script setup lang="ts">
import type { components } from '../../api/schema'
import AppBadge from '../common/AppBadge.vue'
import AppButton from '../common/AppButton.vue'
import AppCard from '../common/AppCard.vue'

type ProjectRead = components['schemas']['ProjectRead']
defineProps<{ project: ProjectRead; disabled: boolean }>()
const emit = defineEmits<{ remove: [project: ProjectRead] }>()

const status = {
  idle: { label: 'Not indexed', tone: 'neutral' },
  running: { label: 'Indexing', tone: 'info' },
  done: { label: 'Indexed', tone: 'success' },
  failed: { label: 'Index failed', tone: 'danger' },
} as const satisfies Record<ProjectRead['index_status'], { label: string; tone: string }>
</script>

<template>
  <AppCard
    as="article"
    class="relative flex flex-wrap items-center justify-between gap-3 hover:border-blue-500 dark:hover:border-blue-400"
  >
    <div class="min-w-0 space-y-1">
      <h2 class="font-semibold">
        <RouterLink
          :to="`/projects/${project.id}`"
          class="break-words after:absolute after:inset-0 after:rounded-lg focus-visible:outline-none focus-visible:after:outline focus-visible:after:outline-2 focus-visible:after:outline-blue-600 dark:focus-visible:after:outline-blue-400"
        >
          {{ project.name }}
        </RouterLink>
      </h2>
      <p class="break-all text-sm text-slate-600 dark:text-slate-300">{{ project.path }}</p>
      <p class="break-words text-sm text-slate-600 dark:text-slate-300">
        Branch: {{ project.current_branch ?? 'Detached HEAD' }}
      </p>
    </div>
    <div class="flex flex-wrap items-center gap-2">
      <AppBadge :tone="status[project.index_status].tone">
        {{ status[project.index_status].label }}
      </AppBadge>
      <AppButton
        class="relative z-10"
        variant="ghost"
        :disabled="disabled"
        :aria-label="`Remove ${project.name}`"
        @click="emit('remove', project)"
      >
        Remove
      </AppButton>
    </div>
  </AppCard>
</template>
