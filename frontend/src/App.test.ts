import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import App from './App.vue'

it('renders only the Docter heading', () => {
  const wrapper = mount(App)

  expect(wrapper.get('h1').text()).toBe('Docter')
  expect(wrapper.text()).toBe('Docter')
})
