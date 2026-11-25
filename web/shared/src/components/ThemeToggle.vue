<script setup lang="ts">
import { computed } from 'vue'
import { FontAwesomeIcon } from '@fortawesome/vue-fontawesome'
import { faMoon, faSun } from '../icons'
import { useTheme } from '../theme'

const { isDark, toggleTheme } = useTheme()

const label = computed(() => (isDark.value ? '切换到浅色主题' : '切换到深色主题'))
</script>

<template>
  <button
    class="theme-toggle"
    type="button"
    :aria-pressed="isDark"
    :aria-label="label"
    :title="label"
    @click="toggleTheme"
  >
    <FontAwesomeIcon :icon="isDark ? faSun : faMoon" fixed-width />
  </button>
</template>

<style scoped lang="scss">
.theme-toggle {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: 1px solid var(--vigil-color-border);
  border-radius: var(--vigil-radius-sm);
  background: var(--vigil-color-surface);
  color: var(--vigil-color-text-secondary);
  font-size: 15px;
  cursor: pointer;
  transition: color var(--vigil-transition), border-color var(--vigil-transition),
    background var(--vigil-transition), transform var(--vigil-transition);

  &:hover {
    color: var(--vigil-color-primary);
    border-color: var(--vigil-color-border-strong);
    background: var(--vigil-color-surface-alt);
  }

  &:active {
    transform: scale(0.96);
  }
}
</style>
