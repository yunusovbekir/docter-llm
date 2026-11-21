import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api, ApiError, unwrap } from '../api/client'
import type { components } from '../api/schema'
import { useToast } from './toast'

type SettingsRead = components['schemas']['SettingsRead']
type SettingsUpdate = components['schemas']['SettingsUpdate']
type ConnectionTestOutcome =
  { status: 'success'; latencyMs: number | null } | { status: 'error'; message: string }

export const useSettings = defineStore('settings', () => {
  const settings = ref<SettingsRead | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  const loadError = ref('')
  const testing = ref(false)
  const connectionTest = ref<ConnectionTestOutcome | null>(null)
  const canTest = computed(
    () =>
      !loading.value &&
      !saving.value &&
      !testing.value &&
      (settings.value?.has_api_key === true || settings.value?.provider === 'ollama'),
  )
  const toast = useToast()

  async function load() {
    if (loading.value || saving.value || testing.value) return
    loading.value = true
    loadError.value = ''
    connectionTest.value = null
    try {
      settings.value = (await unwrap(api.GET('/settings'))) ?? null
    } catch (error) {
      settings.value = null
      loadError.value = error instanceof ApiError ? error.message : 'Could not load settings.'
      toast.error(loadError.value)
    } finally {
      loading.value = false
    }
  }

  async function save(body: SettingsUpdate) {
    if (loading.value || saving.value || testing.value) return
    saving.value = true
    try {
      const updated = await unwrap(api.PUT('/settings', { body }))
      if (!updated) throw new ApiError('invalid_response', 'The API returned no settings.', 0)
      settings.value = updated
      connectionTest.value = null
      toast.success('Settings saved.')
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : 'Could not save settings.')
    } finally {
      saving.value = false
    }
  }

  async function testConnection() {
    if (!canTest.value) return
    testing.value = true
    connectionTest.value = null
    try {
      const result = await unwrap(api.POST('/settings/test'))
      if (!result) throw new ApiError('invalid_response', 'The API returned no test result.', 0)
      if (!result.ok) {
        throw new ApiError('connection_test_failed', result.error || 'Connection test failed.', 200)
      }
      connectionTest.value = { status: 'success', latencyMs: result.latency_ms }
    } catch (error) {
      const message = error instanceof ApiError ? error.message : 'Connection test failed.'
      connectionTest.value = { status: 'error', message }
      toast.error(message)
    } finally {
      testing.value = false
    }
  }

  return {
    settings,
    loading,
    saving,
    loadError,
    testing,
    connectionTest,
    canTest,
    load,
    save,
    testConnection,
  }
})
