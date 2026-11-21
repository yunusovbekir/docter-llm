<script setup lang="ts">
import { onBeforeMount } from 'vue'
import AppButton from '../components/common/AppButton.vue'
import AppEmptyState from '../components/common/AppEmptyState.vue'
import AppSpinner from '../components/common/AppSpinner.vue'
import SettingsForm from '../components/settings/SettingsForm.vue'
import { useSettings } from '../stores/settings'

const store = useSettings()
onBeforeMount(() => store.load())
</script>

<template>
  <section class="max-w-xl space-y-4">
    <h1 class="text-xl font-semibold">Settings</h1>
    <AppSpinner v-if="store.loading" label="Loading settings" />
    <SettingsForm v-else-if="store.settings" />
    <AppEmptyState
      v-else
      :title="store.loadError ? 'Could not load settings' : 'No settings available'"
      :description="store.loadError || 'Reload to check for settings.'"
    >
      <template #actions>
        <AppButton variant="secondary" @click="store.load()">Reload settings</AppButton>
      </template>
    </AppEmptyState>
  </section>
</template>
