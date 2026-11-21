<script setup lang="ts">
import { computed, onScopeDispose, watch } from 'vue'
import { RouterLink } from 'vue-router'
import type { components } from '../api/schema'
import ChatPanel from '../components/chat/ChatPanel.vue'
import AppBadge from '../components/common/AppBadge.vue'
import AppButton from '../components/common/AppButton.vue'
import AppEmptyState from '../components/common/AppEmptyState.vue'
import AppSpinner from '../components/common/AppSpinner.vue'
import { useSession } from '../stores/session'

const props = defineProps<{ id: string }>()
const store = useSession()
const id = computed(() => {
  const value = Number(props.id)
  return /^\d+$/.test(props.id) && Number.isSafeInteger(value) && value > 0 ? value : undefined
})
const modeLabels = {
  add: 'Add',
  fix: 'Fix',
  find_missing: 'Find Missing',
  translate: 'Translate',
} satisfies Record<components['schemas']['Mode'], string>

function load() {
  if (id.value === undefined) store.clear()
  else void store.load(id.value)
}

watch(id, load, { immediate: true })
onScopeDispose(store.clear)
</script>

<template>
  <section class="space-y-4">
    <header class="space-y-2">
      <h1 class="text-xl font-semibold">Session</h1>
      <div
        v-if="store.session && !store.loading"
        class="flex flex-wrap items-center gap-x-3 gap-y-2 text-sm"
      >
        <RouterLink
          :to="`/projects/${store.session.project_id}`"
          class="break-words rounded text-blue-700 underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 dark:text-blue-300 dark:focus-visible:outline-blue-400"
        >
          {{ store.project?.name ?? `Project #${store.session.project_id}` }}
        </RouterLink>
        <AppBadge tone="info">{{ modeLabels[store.session.mode] }}</AppBadge>
        <span v-if="store.session.branch_name" class="break-all"
          >Branch: {{ store.session.branch_name }}</span
        >
      </div>
    </header>
    <AppSpinner v-if="store.loading" label="Loading session" />
    <AppEmptyState
      v-else-if="store.error"
      title="Could not load session"
      :description="store.error"
    >
      <template #actions>
        <AppButton variant="secondary" @click="load">Retry</AppButton>
      </template>
    </AppEmptyState>
    <ChatPanel v-else-if="store.session" :key="store.session.id" :session="store.session" />
    <AppEmptyState v-else title="Session not found" />
  </section>
</template>
