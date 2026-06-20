<template>
  <div class="metric-card">
    <span>{{ title }}</span>
    <strong>{{ value }}</strong>
    <p>{{ hint }}</p>
  </div>
</template>

<script setup lang="ts">
defineProps<{ title: string; value: string | number; hint: string }>()
</script>
