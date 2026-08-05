// 主题元数据：5 套配色（紫藤/初音/樱/抹茶/星夜）。
// 明暗由主题决定：仅星夜为深色（对应壳的 Mica 深色模式），其余为浅色。
// 实际颜色定义在 styles.css（:root 亮色基础 = 紫藤，[data-theme="dark"] = 星夜，
// [data-theme="light"][data-palette="*"] 为其余亮色覆盖），此处仅提供 UI 展示与逻辑所需字段。

export const PALETTES = [
  {
    key: 'wisteria',
    name: '紫藤',
    dark: false,
    base: '#f6f7fb',
    accent: '#7c8cff',
    emotion: '#ff7fa3',
    desc: '默认 · 紫藤花语',
  },
  {
    key: 'hatsune',
    name: '初音',
    dark: false,
    base: '#eff9f8',
    accent: '#39c5bb',
    emotion: '#ff7d9c',
    desc: '39 祭 · 青绿葱姬',
  },
  {
    key: 'sakura',
    name: '樱',
    dark: false,
    base: '#fdf3f5',
    accent: '#e78bab',
    emotion: '#ff8fa3',
    desc: '花见 · 全粉系',
  },
  {
    key: 'matcha',
    name: '抹茶',
    dark: false,
    base: '#f5f8f1',
    accent: '#7fae7e',
    emotion: '#f2a65a',
    desc: '喫茶 · 抹茶杏酱',
  },
  {
    key: 'starry',
    name: '星夜',
    dark: true,
    base: '#191a28',
    accent: '#9aa5ff',
    emotion: '#ff86a8',
    desc: '夜话 · 深夜陪伴',
  },
]

export function getPalette(key) {
  return PALETTES.find((palette) => palette.key === key) ?? PALETTES[0]
}
