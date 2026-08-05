// 与 WinForms 壳（WebView2）的窗口控制桥。
// 在普通浏览器中开发时 window.chrome.webview 不存在，所有调用静默失效。

const webview = window.chrome?.webview
export const available = typeof webview?.postMessage === 'function'

function send(type) {
  if (!available) return
  webview.postMessage({ type })
}

export const dragStart = () => send('drag')
export const minimize = () => send('minimize')
export const toggleMaximize = () => send('toggle-maximize')
export const close = () => send('close')
export const getState = () => send('get-state')

export function setShellTheme(dark, followsSystem) {
  if (!available) return
  webview.postMessage({ type: 'app-theme', dark: Boolean(dark), followsSystem: Boolean(followsSystem) })
}

// 订阅壳推送的窗口状态（如用户通过 Aero Snap 最大化），返回取消订阅函数。
export function onWindowState(callback) {
  return onMessage('window-state', (data) => callback(Boolean(data.maximized)))
}

// 订阅壳推送的系统深浅色（C# 读取 Windows 主题后推送），返回取消订阅函数。
export function onSystemTheme(callback) {
  return onMessage('system-theme', (data) => callback(Boolean(data.dark)))
}

function onMessage(type, callback) {
  if (!available) return () => {}
  const listener = (event) => {
    try {
      const data = JSON.parse(event.data)
      if (data?.type === type) callback(data)
    } catch {
      // 忽略无法解析的消息。
    }
  }
  webview.addEventListener('message', listener)
  return () => webview.removeEventListener('message', listener)
}
