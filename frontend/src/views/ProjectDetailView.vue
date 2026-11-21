<script setup lang="ts">
import { computed } from 'vue'
import AppButton from '../components/common/AppButton.vue'
import AppEmptyState from '../components/common/AppEmptyState.vue'
import AppSpinner from '../components/common/AppSpinner.vue'
import NewSessionForm from '../components/project/NewSessionForm.vue'
import { useIndexStatus } from '../composables/useIndexStatus'
import { useProjectDetail } from '../composables/useProjectDetail'

const props = defineProps<{ id: string }>()
const projectId = computed(() => {
  const id = Number(props.id)
  return /^\d+$/.test(props.id) && Number.isSafeInteger(id) && id > 0 ? id : undefined
})
const { project, loading, error, retry } = useProjectDetail(projectId)
const {
  status,
  loading: loadingStatus,
  starting,
  statusError,
  retry: retryStatus,
  startIndex,
} = useIndexStatus(computed(() => project.value?.id))

const indexedAt = computed(() =>
  status.value ? status.value.indexed_at : project.value?.indexed_at,
)
const messages = {
  idle: 'Not indexed',
  running: 'Indexing in progress',
  done: 'Indexing complete',
  failed: 'Indexing failed',
}
</script>

<template>
  <section class="space-y-4">
    <h1 class="break-words text-xl font-semibold">{{ project?.name ?? 'Project' }}</h1>
    <AppSpinner v-if="loading" label="Loading project" />
    <AppEmptyState v-else-if="error" title="Could not load project" :description="error">
      <template #actions>
        <AppButton variant="secondary" @click="retry">Retry</AppButton>
      </template>
    </AppEmptyState>
    <AppEmptyState
      v-else-if="!project"
      title="Project not found"
      description="Select a registered repository from Projects."
    />
    <template v-else>
      <dl class="space-y-2 text-sm">
        <div>
          <dt class="font-semibold">Repository path</dt>
          <dd class="break-all font-mono">{{ project.path }}</dd>
        </div>
        <div>
          <dt class="font-semibold">Branch</dt>
          <dd class="break-words">{{ project.current_branch ?? 'Unknown' }}</dd>
        </div>
        <div>
          <dt class="font-semibold">Last indexed</dt>
          <dd>
            <time v-if="indexedAt" :datetime="indexedAt">{{
              new Date(indexedAt).toLocaleString()
            }}</time>
            <span v-else>Never</span>
          </dd>
        </div>
      </dl>
      <section aria-label="Indexing" class="space-y-3">
        <AppButton
          :disabled="!status || !!statusError || status.status === 'running'"
          :loading="starting"
          @click="startIndex"
        >
          {{ indexedAt || status?.status === 'done' ? 'Re-index' : 'Index' }}
        </AppButton>
        <AppSpinner v-if="loadingStatus && !status" label="Loading indexing status" />
        <template v-if="status">
          <p role="status" class="break-words text-sm">
            {{ status.message || messages[status.status] }}
          </p>
          <progress
            v-if="status.status !== 'idle'"
            aria-label="Indexing progress"
            :value="status.progress"
            max="100"
            class="block h-3 w-full max-w-md accent-blue-700 dark:accent-blue-400"
          />
          <p
            v-if="status.status === 'failed' && status.error"
            role="alert"
            class="break-words text-sm text-red-700 dark:text-red-300"
          >
            {{ status.error }}
          </p>
        </template>
        <div v-if="statusError" class="space-y-2">
          <p role="alert" class="break-words text-sm text-red-700 dark:text-red-300">
            {{ statusError }}
          </p>
          <AppButton variant="secondary" @click="retryStatus">Retry status check</AppButton>
        </div>
      </section>
      <NewSessionForm
        :key="project.id"
        :project-id="project.id"
        :can-start="status?.status === 'done' && !starting && !statusError"
      />
    </template>
  </section>
</template>
