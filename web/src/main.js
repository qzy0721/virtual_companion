import { createApp } from 'vue'
import App from './App.vue'
import './styles.css'
import { applyTheme } from './composables/useTheme'

applyTheme()
createApp(App).mount('#app')
