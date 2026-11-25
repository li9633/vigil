/** 复制文本：优先使用 Clipboard API，HTTP 等非安全上下文回退到 execCommand。 */
export async function copyText(value: string): Promise<boolean> {
  if (!value) return false
  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value)
      return true
    } catch {
      // 非 HTTPS 或权限被拒时继续走回退方案
    }
  }
  return legacyCopy(value)
}

function legacyCopy(value: string): boolean {
  if (typeof document === 'undefined') return false
  const textarea = document.createElement('textarea')
  textarea.value = value
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.top = '-1000px'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)
  let ok = false
  try {
    textarea.select()
    textarea.setSelectionRange(0, value.length)
    ok = document.execCommand('copy')
  } catch {
    ok = false
  } finally {
    document.body.removeChild(textarea)
  }
  return ok
}
