import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api, ApiError, unwrap } from '../api/client'
import type { components } from '../api/schema'
import { useToast } from './toast'

type ProjectRead = components['schemas']['ProjectRead']
type ProjectCreate = components['schemas']['ProjectCreate']

export const useProjects = defineStore('projects', () => {
  const projects = ref<ProjectRead[]>([])
  const loading = ref(false)
  const creating = ref(false)
  const removing = ref(false)
  const busy = computed(() => loading.value || creating.value || removing.value)
  const loadError = ref('')
  const createError = ref('')
  const removeError = ref('')
  const toast = useToast()

  function notifyError(error: unknown, fallback: string) {
    const message = error instanceof ApiError ? error.message : fallback
    toast.error(message)
    return message
  }

  async function list() {
    if (busy.value) return
    loading.value = true
    loadError.value = ''
    try {
      const result = await unwrap(api.GET('/projects'))
      if (!result) throw new ApiError('invalid_response', 'The API returned no project list.', 0)
      projects.value = result
    } catch (error) {
      loadError.value = notifyError(error, 'Could not load projects.')
    } finally {
      loading.value = false
    }
  }

  async function create(body: ProjectCreate) {
    if (busy.value) return false
    creating.value = true
    createError.value = ''
    try {
      const result = await unwrap(api.POST('/projects', { body }))
      if (!result) throw new ApiError('invalid_response', 'The API returned no project.', 0)
      const { is_dirty, warnings, ...project } = result
      projects.value.push(project)
      if (is_dirty) {
        toast.warning(
          warnings.filter(Boolean).join(' ') || 'The repository has uncommitted changes.',
        )
      }
      return true
    } catch (error) {
      createError.value = notifyError(error, 'Could not add the project.')
      return false
    } finally {
      creating.value = false
    }
  }

  async function remove(id: ProjectRead['id']) {
    if (busy.value) return false
    removing.value = true
    removeError.value = ''
    try {
      await unwrap(api.DELETE('/projects/{project_id}', { params: { path: { project_id: id } } }))
      projects.value = projects.value.filter((project) => project.id !== id)
      return true
    } catch (error) {
      removeError.value = notifyError(error, 'Could not remove the project.')
      return false
    } finally {
      removing.value = false
    }
  }

  return {
    projects,
    loading,
    creating,
    removing,
    busy,
    loadError,
    createError,
    removeError,
    list,
    create,
    remove,
  }
})
