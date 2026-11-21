import { createPinia } from 'pinia'
import { createApp } from 'vue'
import { createWebHistory } from 'vue-router'
import App from './App.vue'
import { installErrorHandling } from './errorHandling'
import { createAppRouter } from './router'
import './style.css'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)
installErrorHandling(app, pinia)
app.use(createAppRouter(createWebHistory(import.meta.env.BASE_URL))).mount('#app')
