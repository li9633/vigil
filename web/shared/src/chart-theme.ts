/** 从当前主题的 CSS 变量读取图表色板，切换主题后重新调用即可 */
export interface ChartTheme {
  series: string[]
  axis: string
  grid: string
  text: string
  surface: string
  border: string
  primary: string
}

const FALLBACK = {
  series: ['#2563eb', '#0369a1', '#c77a0a', '#c0264a', '#64748b'],
  axis: '#6f6659',
  grid: 'rgba(36, 29, 20, 0.09)',
  text: '#241d14',
  surface: '#ffffff',
  border: '#e5dfd2',
  primary: '#2563eb',
}

function readVar(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || fallback
}

export function getChartTheme(): ChartTheme {
  return {
    series: FALLBACK.series.map((fallback, index) =>
      readVar(`--vigil-chart-${index + 1}`, fallback),
    ),
    axis: readVar('--vigil-chart-axis', FALLBACK.axis),
    grid: readVar('--vigil-chart-grid', FALLBACK.grid),
    text: readVar('--vigil-color-text', FALLBACK.text),
    surface: readVar('--vigil-color-surface-raised', FALLBACK.surface),
    border: readVar('--vigil-color-border', FALLBACK.border),
    primary: readVar('--vigil-color-primary-solid', FALLBACK.primary),
  }
}
