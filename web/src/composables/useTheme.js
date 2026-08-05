// 主题管理：5 套配色（紫藤/初音/樱/抹茶/星夜），明暗由主题决定（仅星夜为深色）。
// 偏好存 localStorage，通过 html[data-theme]/html[data-palette] 应用（配合 styles.css 变量），
// 并同步通知壳（C# Mica 深色模式与加载页配色）。

import { getPalette, PALETTES } from '../themes'
import { setShellTheme } from './useWindowBridge'

const STORAGE_KEY = 'vc-theme'
const DEFAULT_THEME = 'wisteria'

export function getThemePreference() {
  const saved = localStorage.getItem(STORAGE_KEY)
  // 兼容旧值：亮/跟随系统 → 紫藤，暗 → 星夜
  if (saved === 'light' || saved === 'system') return DEFAULT_THEME
  if (saved === 'dark') return 'starry'
  return PALETTES.some((palette) => palette.key === saved) ? saved : DEFAULT_THEME
}

export function applyTheme() {
  const palette = getPalette(getThemePreference())
  document.documentElement.dataset.theme = palette.dark ? 'dark' : 'light'
  document.documentElement.dataset.palette = palette.key
  setShellTheme(palette.dark, false)
}

export function setThemePreference(preference) {
  localStorage.setItem(STORAGE_KEY, preference)
  applyTheme()
}
