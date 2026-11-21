<script setup lang="ts">
import { onBeforeMount } from 'vue'
import AppButton from '../components/common/AppButton.vue'
import AppEmptyState from '../components/common/AppEmptyState.vue'
import AppInput from '../components/common/AppInput.vue'
import AppModal from '../components/common/AppModal.vue'
import AppSpinner from '../components/common/AppSpinner.vue'
import ProjectCard from '../components/project/ProjectCard.vue'
import { useProjectDialogs } from '../composables/useProjectDialogs'
import { useProjects } from '../stores/projects'

const store = useProjects()
const {
  addOpen,
  path,
  projectToRemove,
  openAdd,
  closeAdd,
  submitAdd,
  openRemove,
  closeRemove,
  confirmRemove,
} = useProjectDialogs()
onBeforeMount(() => store.list())
</script>

<template>
  <section class="space-y-4">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <h1 class="text-xl font-semibold">Projects</h1>
      <AppButton :disabled="store.busy || !!store.loadError" @click="openAdd">
        Add project
      </AppButton>
    </div>

    <AppSpinner v-if="store.loading" label="Loading projects" />
    <AppEmptyState
      v-else-if="store.loadError"
      title="Could not load projects"
      :description="store.loadError"
    >
      <template #actions>
        <AppButton variant="secondary" @click="store.list()">Retry</AppButton>
      </template>
    </AppEmptyState>
    <AppEmptyState
      v-else-if="!store.projects.length"
      title="No projects yet"
      description="Add a local Git repository to get started."
    />
    <ul v-else class="space-y-3">
      <li v-for="project in store.projects" :key="project.id">
        <ProjectCard :project="project" :disabled="store.busy" @remove="openRemove" />
      </li>
    </ul>

    <AppModal :open="addOpen" title="Add project" @close="closeAdd">
      <form class="space-y-4" @submit.prevent="submitAdd">
        <AppInput
          v-model="path"
          name="path"
          label="Absolute repository path"
          hint="Enter the full path to the root of your local Git repository."
          :error="store.createError || undefined"
          :disabled="store.creating"
          required
          autofocus
        />
        <AppButton type="submit" :disabled="!path.trim()" :loading="store.creating">
          Add project
        </AppButton>
      </form>
    </AppModal>

    <AppModal
      :open="!!projectToRemove"
      title="Remove project?"
      description="This removes the project from Docter. It does not delete any files."
      @close="closeRemove"
    >
      <p v-if="projectToRemove" class="break-words">Remove {{ projectToRemove.name }}?</p>
      <p v-if="store.removeError" role="alert" class="mt-2 text-sm text-red-700 dark:text-red-300">
        {{ store.removeError }}
      </p>
      <template #footer>
        <div class="flex flex-wrap justify-end gap-2">
          <AppButton variant="secondary" :disabled="store.removing" @click="closeRemove">
            Cancel
          </AppButton>
          <AppButton variant="danger" :loading="store.removing" @click="confirmRemove">
            Remove project
          </AppButton>
        </div>
      </template>
    </AppModal>
  </section>
</template>
