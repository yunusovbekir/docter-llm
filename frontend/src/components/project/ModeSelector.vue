<script setup lang="ts">
import { useId } from 'vue'
import type { components } from '../../api/schema'

type Mode = components['schemas']['Mode']
const mode = defineModel<Mode>({ required: true })
const name = useId()
const modes = {
  add: { label: 'Add', description: 'Write new documentation for your code.' },
  fix: { label: 'Fix', description: 'Correct inaccurate or outdated documentation.' },
  find_missing: { label: 'Find Missing', description: 'Find code that needs documentation.' },
  translate: { label: 'Translate', description: 'Translate existing documentation.' },
} satisfies Record<Mode, { label: string; description: string }>
</script>

<template>
  <fieldset>
    <legend class="mb-2 text-sm font-semibold">Session mode</legend>
    <div class="grid gap-2 sm:grid-cols-2">
      <label v-for="({ label, description }, value) in modes" :key="value" class="block">
        <input
          v-model="mode"
          :name="name"
          :value="value"
          :aria-labelledby="`${name}-${value}-label`"
          :aria-describedby="`${name}-${value}-description`"
          type="radio"
          class="peer sr-only"
        />
        <span
          class="block h-full cursor-pointer rounded-md border border-slate-300 p-3 text-sm hover:border-blue-600 peer-checked:border-blue-600 peer-checked:bg-blue-50 peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-blue-600 peer-disabled:cursor-not-allowed peer-disabled:opacity-60 dark:border-slate-700 dark:hover:border-blue-400 dark:peer-checked:border-blue-400 dark:peer-checked:bg-blue-950 dark:peer-focus-visible:outline-blue-400"
        >
          <span :id="`${name}-${value}-label`" class="block font-semibold">{{ label }}</span>
          <span
            :id="`${name}-${value}-description`"
            class="block text-slate-600 dark:text-slate-300"
          >
            {{ description }}
          </span>
        </span>
      </label>
    </div>
  </fieldset>
</template>
