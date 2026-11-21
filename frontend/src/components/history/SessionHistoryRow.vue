<script setup lang="ts">
import { RouterLink } from 'vue-router'
import type { components } from '../../api/schema'
import { modeLabels } from '../../types/sessionModes'
import AppBadge from '../common/AppBadge.vue'
import AppButton from '../common/AppButton.vue'

defineProps<{ session: components['schemas']['SessionSummary']; projectName: string }>()
const emit = defineEmits<{ copy: [branch: string]; delete: [] }>()
</script>

<template>
  <article
    class="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 p-4 dark:border-slate-700"
  >
    <div class="min-w-0 space-y-2">
      <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
        <h2 class="break-words font-semibold">{{ projectName }} · Session #{{ session.id }}</h2>
        <AppBadge tone="info">{{ modeLabels[session.mode] }}</AppBadge>
        <time :datetime="session.created_at" class="text-sm text-slate-600 dark:text-slate-300">
          {{ new Date(session.created_at).toLocaleString() }}
        </time>
      </div>
      <div class="flex flex-wrap gap-2">
        <AppBadge tone="warning">Pending: {{ session.change_counts.pending }}</AppBadge>
        <AppBadge tone="success">Applied: {{ session.change_counts.applied }}</AppBadge>
        <AppBadge tone="danger">Denied: {{ session.change_counts.denied }}</AppBadge>
      </div>
      <div v-if="session.branch_name" class="flex flex-wrap items-center gap-x-2 text-sm">
        <span class="min-w-0 break-all"
          >Branch: <code>{{ session.branch_name }}</code></span
        >
        <AppButton
          variant="ghost"
          :aria-label="`Copy branch name for session ${session.id}`"
          @click="emit('copy', session.branch_name)"
        >
          Copy
        </AppButton>
      </div>
      <p v-else class="text-sm text-slate-600 dark:text-slate-300">No branch yet</p>
    </div>
    <div class="flex items-center gap-3">
      <RouterLink
        :to="`/sessions/${session.id}`"
        :aria-label="`Resume session ${session.id}`"
        class="rounded text-sm font-semibold text-blue-700 underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 dark:text-blue-300 dark:focus-visible:outline-blue-400"
      >
        Resume
      </RouterLink>
      <AppButton
        variant="danger"
        :aria-label="`Delete session ${session.id}`"
        @click="emit('delete')"
      >
        Delete
      </AppButton>
    </div>
  </article>
</template>
