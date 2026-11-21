import { ref, watch, type Ref } from 'vue'
import { api, ApiError, unwrap } from '../api/client'
import type { components } from '../api/schema'
import { useToast } from '../stores/toast'

type Project = components['schemas']['ProjectRead']

export function useProjectDetail(projectId: Readonly<Ref<Project['id'] | undefined>>) {
  const project = ref<Project>()
  const loading = ref(false)
  const error = ref('')
  const attempt = ref(0)
  const toast = useToast()

  watch(
    [projectId, attempt],
    async ([id], _, onCleanup) => {
      project.value = undefined
      error.value = ''
      loading.value = id !== undefined
      if (id === undefined) return

      const controller = new AbortController()
      onCleanup(() => controller.abort())
      const { signal } = controller

      try {
        const result = await unwrap(
          api.GET('/projects/{project_id}', { params: { path: { project_id: id } }, signal }),
        )
        if (!result) throw new ApiError('invalid_response', 'The API returned no project.', 0)
        if (!signal.aborted) project.value = result
      } catch (cause) {
        if (signal.aborted) return
        const message = cause instanceof ApiError ? cause.message : 'Could not load the project.'
        if (!(cause instanceof ApiError && cause.status === 404)) error.value = message
        toast.error(message)
      } finally {
        if (!signal.aborted) loading.value = false
      }
    },
    { immediate: true },
  )

  return { project, loading, error, retry: () => attempt.value++ }
}
