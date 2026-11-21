import { ref, watch } from 'vue'
import type { components } from '../api/schema'
import { useProjects } from '../stores/projects'

type ProjectRead = components['schemas']['ProjectRead']

export function useProjectDialogs() {
  const store = useProjects()
  const addOpen = ref(false)
  const path = ref('')
  const projectToRemove = ref<ProjectRead | null>(null)

  watch(path, () => {
    store.createError = ''
  })

  function openAdd() {
    path.value = ''
    store.createError = ''
    addOpen.value = true
  }

  function closeAdd() {
    if (!store.creating) addOpen.value = false
  }

  async function submitAdd() {
    if (!path.value.trim()) return
    if (await store.create({ path: path.value })) closeAdd()
  }

  function openRemove(project: ProjectRead) {
    store.removeError = ''
    projectToRemove.value = project
  }

  function closeRemove() {
    if (!store.removing) projectToRemove.value = null
  }

  async function confirmRemove() {
    if (projectToRemove.value && (await store.remove(projectToRemove.value.id))) closeRemove()
  }

  return {
    addOpen,
    path,
    projectToRemove,
    openAdd,
    closeAdd,
    submitAdd,
    openRemove,
    closeRemove,
    confirmRemove,
  }
}
