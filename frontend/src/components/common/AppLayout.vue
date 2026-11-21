<script setup lang="ts">
import { RouterLink } from 'vue-router'
import ToastHost from './ToastHost.vue'

const links = [
  { label: 'Projects', to: '/projects' },
  { label: 'History', to: '/history' },
  { label: 'Settings', to: '/settings' },
]
</script>

<template>
  <div
    class="grid min-h-screen grid-cols-[auto_minmax(0,1fr)] grid-rows-[auto_1fr] bg-white text-slate-900 dark:bg-slate-950 dark:text-slate-100"
  >
    <header
      class="col-span-2 border-b border-slate-200 px-4 py-3 font-semibold dark:border-slate-800"
    >
      Docter
    </header>
    <aside
      class="border-r border-slate-200 bg-slate-50 p-2 sm:p-4 dark:border-slate-800 dark:bg-slate-900"
    >
      <nav aria-label="Main navigation" class="space-y-1">
        <RouterLink
          v-for="{ label, to } in links"
          :key="to"
          :to="to"
          class="block rounded px-3 py-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 dark:focus-visible:outline-blue-400"
          active-class="bg-blue-100 font-semibold text-blue-900 dark:bg-blue-900 dark:text-blue-100"
        >
          {{ label }}
        </RouterLink>
      </nav>
    </aside>
    <main class="p-4 sm:p-6">
      <slot />
    </main>
    <ToastHost />
  </div>
</template>
