import { onScopeDispose, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ApiError } from '../api/client'
import type { components } from '../api/schema'
import { createSession } from '../api/sessions'
import { useToast } from '../stores/toast'

type SessionCreate = components['schemas']['SessionCreate']

// Matches the backend's LanguageTag constraint for translate mode.
const LANGUAGE_TAG = /^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$/

export function useNewSession(projectId: SessionCreate['project_id'], canStart: () => boolean) {
  const mode = ref<SessionCreate['mode']>('add')
  const language = ref('')
  const languageError = ref('')
  const submitting = ref(false)
  const toast = useToast()
  const router = useRouter()
  const controller = new AbortController()
  const { signal } = controller
  onScopeDispose(() => controller.abort())

  watch([mode, language], () => {
    languageError.value = ''
  })

  async function submit() {
    if (!canStart() || submitting.value) return

    const body: SessionCreate = { project_id: projectId, mode: mode.value }
    if (body.mode === 'translate') {
      const target = language.value.trim()
      if (!LANGUAGE_TAG.test(target)) {
        languageError.value = target
          ? 'Enter a language tag such as de or pt-BR.'
          : 'Enter a target language.'
        return
      }
      body.mode_params = { target_language: target }
    }

    submitting.value = true
    try {
      const session = await createSession(body, signal)
      if (!signal.aborted) await router.push(`/sessions/${session.id}`)
    } catch (cause) {
      if (!signal.aborted) {
        toast.error(cause instanceof ApiError ? cause.message : 'Could not start the session.')
      }
    } finally {
      if (!signal.aborted) submitting.value = false
    }
  }

  return { mode, language, languageError, submitting, submit }
}
