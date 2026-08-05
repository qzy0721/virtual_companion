import { createApp } from 'vue'
import PetApp from './PetApp.vue'
import './styles.css'
import { applyTheme } from './composables/useTheme'

applyTheme()
createApp(PetApp).mount('#app')
