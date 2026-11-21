<script setup lang="ts">
import { providerOptions, useSettingsForm } from '../../composables/useSettingsForm'
import { useSettings } from '../../stores/settings'
import AppBadge from '../common/AppBadge.vue'
import AppButton from '../common/AppButton.vue'
import AppCard from '../common/AppCard.vue'
import AppInput from '../common/AppInput.vue'
import AppSelect from '../common/AppSelect.vue'

const store = useSettings()
const { draft, errors, hasKey, removeKey, canSave, toggleKeyRemoval, save } = useSettingsForm()
</script>

<template>
  <AppCard>
    <form novalidate @submit.prevent="save">
      <fieldset :disabled="store.saving" class="space-y-4">
        <AppSelect
          v-model="draft.provider"
          name="provider"
          label="Provider"
          :options="providerOptions"
        />
        <AppInput
          v-if="draft.provider === 'other'"
          v-model="draft.customProvider"
          name="custom_provider"
          label="Provider ID"
          hint="Use the LiteLLM provider ID."
          :error="errors.provider"
          required
        />
        <AppInput v-model="draft.model" name="model" label="Model" :error="errors.model" required />
        <AppInput
          v-model="draft.base_url"
          name="base_url"
          label="Base URL"
          type="url"
          :error="errors.base_url"
          hint="Optional. Leave blank to use the provider default."
        />
        <AppInput
          v-model="draft.embedding_model"
          name="embedding_model"
          label="Embedding model"
          :error="errors.embedding_model"
          required
        />
        <div class="grid gap-4 sm:grid-cols-2">
          <AppInput
            v-model="draft.temperature"
            name="temperature"
            label="Temperature"
            type="number"
            min="0"
            max="2"
            step="any"
            :error="errors.temperature"
            required
          />
          <AppInput
            v-model="draft.max_agent_steps"
            name="max_agent_steps"
            label="Max agent steps"
            type="number"
            min="1"
            max="200"
            :error="errors.max_agent_steps"
            required
          />
        </div>
        <AppInput
          v-model="draft.api_key"
          name="api_key"
          label="API key"
          type="password"
          autocomplete="new-password"
          :disabled="removeKey"
          :hint="
            removeKey
              ? 'The saved key will be removed when you save.'
              : 'Leave blank to keep any existing key for this provider.'
          "
        />
        <div v-if="hasKey" class="flex flex-wrap items-center gap-2">
          <AppBadge tone="success">Key saved</AppBadge>
          <AppButton variant="secondary" @click="toggleKeyRemoval">
            {{ removeKey ? 'Undo removal' : 'Remove key' }}
          </AppButton>
        </div>
        <AppButton type="submit" :disabled="!canSave" :loading="store.saving">
          {{ store.saving ? 'Saving settings' : 'Save' }}
        </AppButton>
      </fieldset>
    </form>
  </AppCard>
</template>
