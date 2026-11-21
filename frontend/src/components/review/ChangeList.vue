<script setup lang="ts">
import { useId } from 'vue'
import type { components } from '../../api/schema'
import { useChanges } from '../../stores/changes'
import AppBadge from '../common/AppBadge.vue'
import AppButton from '../common/AppButton.vue'
import AppSpinner from '../common/AppSpinner.vue'

defineEmits<{ retry: [] }>()
const store = useChanges()
const heading = useId()
const statuses = {
  pending: { label: 'Pending', tone: 'warning' },
  approved: { label: 'Approved', tone: 'success' },
  applied: { label: 'Applied', tone: 'success' },
  denied: { label: 'Denied', tone: 'neutral' },
  partial: { label: 'Partial', tone: 'warning' },
  conflict: { label: 'Conflict', tone: 'danger' },
  revised: { label: 'Revised', tone: 'info' },
} as const satisfies Record<components['schemas']['ChangeStatus'], { label: string; tone: string }>
</script>

<template>
  <aside
    :aria-labelledby="heading"
    class="min-w-0 space-y-3 rounded-md border border-slate-200 p-3 dark:border-slate-800"
  >
    <h2 :id="heading" class="text-lg font-semibold">Changes</h2>
    <AppSpinner v-if="store.loading && !store.changeSets.length" label="Loading changes" />
    <div v-if="store.error" class="space-y-2">
      <p role="alert" class="text-sm text-red-700 dark:text-red-300">{{ store.error }}</p>
      <AppButton variant="secondary" @click="$emit('retry')">Retry changes</AppButton>
    </div>
    <p
      v-else-if="!store.loading && !store.changeSets.length"
      class="text-sm text-slate-600 dark:text-slate-400"
    >
      No changes proposed yet.
    </p>
    <ol v-if="store.changeSets.length" :aria-busy="store.loading" class="space-y-4">
      <li v-for="set in store.changeSets" :key="set.id" class="space-y-2">
        <h3 class="flex flex-wrap items-baseline gap-x-2 text-sm">
          <span class="font-semibold">Change set #{{ set.id }}</span>
          <time :datetime="set.created_at" class="text-xs text-slate-600 dark:text-slate-400">
            {{ new Date(set.created_at).toLocaleString() }}
          </time>
        </h3>
        <ul class="space-y-1">
          <li v-for="change in set.changes" :key="change.id">
            <button
              type="button"
              :aria-pressed="store.selectedChangeId === change.id"
              class="block w-full space-y-1 rounded-md border p-2 text-left text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 dark:focus-visible:outline-blue-400"
              :class="
                store.selectedChangeId === change.id
                  ? 'border-blue-600 bg-blue-50 dark:border-blue-400 dark:bg-blue-950'
                  : 'border-slate-200 hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800'
              "
              @click="store.selectedChangeId = change.id"
            >
              <span class="block break-all font-mono">{{ change.file_path }}</span>
              <span class="flex flex-wrap items-center gap-2">
                <AppBadge :tone="statuses[change.status].tone">{{
                  statuses[change.status].label
                }}</AppBadge>
                <span class="text-xs text-slate-600 dark:text-slate-400">
                  {{ change.hunks.length }} {{ change.hunks.length === 1 ? 'hunk' : 'hunks' }}
                </span>
              </span>
            </button>
          </li>
        </ul>
      </li>
    </ol>
  </aside>
</template>
