<script setup lang="ts">
import AppButton from '../components/common/AppButton.vue'
import AppEmptyState from '../components/common/AppEmptyState.vue'
import AppModal from '../components/common/AppModal.vue'
import AppSelect from '../components/common/AppSelect.vue'
import AppSpinner from '../components/common/AppSpinner.vue'
import SessionHistoryRow from '../components/history/SessionHistoryRow.vue'
import { useSessionHistory } from '../composables/useSessionHistory'

const {
  projects,
  projectFilter,
  projectNames,
  projectOptions,
  sessions,
  loading,
  loadError,
  load,
  sessionToDelete,
  deleting,
  deleteError,
  openDelete,
  closeDelete,
  confirmDelete,
  copyBranch,
} = useSessionHistory()
</script>

<template>
  <section class="space-y-4">
    <h1 class="text-xl font-semibold">History</h1>
    <div class="max-w-sm">
      <AppSelect
        v-model="projectFilter"
        label="Filter by project"
        :options="projectOptions"
        :disabled="projects.loading || deleting"
        :hint="projects.loading ? 'Loading projects…' : undefined"
        :error="projects.loadError || undefined"
      />
      <AppButton v-if="projects.loadError" variant="secondary" @click="projects.list()">
        Retry loading projects
      </AppButton>
    </div>

    <AppSpinner v-if="loading" label="Loading sessions" />
    <AppEmptyState v-else-if="loadError" title="Could not load sessions" :description="loadError">
      <template #actions>
        <AppButton variant="secondary" @click="load">Retry</AppButton>
      </template>
    </AppEmptyState>
    <AppEmptyState
      v-else-if="!sessions.length"
      :title="projectFilter ? 'No sessions for this project' : 'No sessions yet'"
    />
    <ul v-else class="space-y-3">
      <li v-for="session in sessions" :key="session.id">
        <SessionHistoryRow
          :session="session"
          :project-name="projectNames.get(session.project_id) ?? `Project #${session.project_id}`"
          @copy="copyBranch"
          @delete="openDelete(session)"
        />
      </li>
    </ul>

    <AppModal
      :open="!!sessionToDelete"
      title="Delete session?"
      description="This permanently deletes the session history. The Git branch, if one exists, will be kept."
      @close="closeDelete"
    >
      <p v-if="sessionToDelete">Delete session #{{ sessionToDelete.id }}?</p>
      <p v-if="deleteError" role="alert" class="mt-2 text-sm text-red-700 dark:text-red-300">
        {{ deleteError }}
      </p>
      <template #footer>
        <div class="flex flex-wrap justify-end gap-2">
          <AppButton variant="secondary" :disabled="deleting" @click="closeDelete"
            >Cancel</AppButton
          >
          <AppButton variant="danger" :loading="deleting" @click="confirmDelete">
            Delete session
          </AppButton>
        </div>
      </template>
    </AppModal>
  </section>
</template>
