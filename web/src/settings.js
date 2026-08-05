import { createApp } from 'vue'
import SettingsApp from './SettingsApp.vue'
import './styles.css'
import { applyTheme } from './composables/useTheme'

document.body.classList.add('settings-body')
applyTheme()
createApp(SettingsApp).mount('#app')
