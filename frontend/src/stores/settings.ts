import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api, ApiError, unwrap } from '../api/client'
import type { components } from '../api/schema'
import { useToast } from './toast'

type SettingsRead = components['schemas']['SettingsRead']
type SettingsUpdate = components['schemas']['SettingsUpdate']

export const useSettings = defineStore('settings', () => {
  const settings = ref<SettingsRead | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  const loadError = ref('')
  const toast = useToast()

  async function load() {
    if (loading.value || saving.value) return
    loading.value = true
    loadError.value = ''
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
    if (loading.value || saving.value) return
    saving.value = true
    try {
      const updated = await unwrap(api.PUT('/settings', { body }))
      if (!updated) throw new ApiError('invalid_response', 'The API returned no settings.', 0)
      settings.value = updated
      toast.success('Settings saved.')
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : 'Could not save settings.')
    } finally {
      saving.value = false
    }
  }

  return { settings, loading, saving, loadError, load, save }
})
