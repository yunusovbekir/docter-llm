<script setup lang="ts">
import { useId } from 'vue'
import type { components } from '../../api/schema'
import { useNewSession } from '../../composables/useNewSession'
import AppButton from '../common/AppButton.vue'
import AppInput from '../common/AppInput.vue'
import ModeSelector from './ModeSelector.vue'

const props = defineProps<{
  projectId: components['schemas']['ProjectRead']['id']
  canStart: boolean
}>()
const heading = useId()
const tooltip = useId()
const { mode, language, languageError, submitting, submit } = useNewSession(
  props.projectId,
  () => props.canStart,
)
</script>

<template>
  <section
    :aria-labelledby="heading"
    class="space-y-3 border-t border-slate-200 pt-4 dark:border-slate-800"
  >
    <h2 :id="heading" class="text-lg font-semibold">New session</h2>
    <form class="space-y-3" novalidate @submit.prevent="submit">
      <fieldset :disabled="submitting" class="space-y-3">
        <ModeSelector v-model="mode" />
        <AppInput
          v-if="mode === 'translate'"
          v-model="language"
          name="target-language"
          label="Target language"
          hint="Use a language tag such as de or pt-BR."
          :error="languageError || undefined"
          required
        />
      </fieldset>
      <div
        class="group relative inline-block rounded-md focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 dark:focus-visible:outline-blue-400"
        :tabindex="canStart ? undefined : 0"
        :aria-describedby="canStart ? undefined : tooltip"
      >
        <AppButton type="submit" :disabled="!canStart" :loading="submitting">
          Start session
        </AppButton>
        <span
          v-if="!canStart"
          :id="tooltip"
          role="tooltip"
          class="invisible absolute left-0 top-full z-10 w-max max-w-52 rounded bg-slate-900 p-2 text-sm text-white group-hover:visible group-focus:visible dark:bg-slate-200 dark:text-slate-950"
        >
          Indexing must finish before you can start a session.
        </span>
      </div>
    </form>
  </section>
</template>
