<template><svg class="scene-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path v-for="(d,index) in (paths[name] || paths.document)" :key="index" :d="d" /></svg></template>
<script setup>
defineProps({name:{type:String,default:'document'}})
const paths={
overview:['M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z'],
company:['M5 21V5l7-2v18 M12 8h7v13 M3 21h18 M8 8v1 M8 13v1 M15 12h1 M15 16h1'],
document:['M6 3h8l4 4v14H6z M14 3v5h4 M9 12h6 M9 16h6'],
search:['M16 16l5 5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0'],
layers:['M12 3 2 8l10 5 10-5z M2 12l10 5 10-5 M2 16l10 5 10-5'],
task:['M9 4h6v4H9z M9 6H5v15h14V6h-4 M8 13l2 2 5-4 M8 18h7'],
experiment:['M9 3h6 M10 3v6L4 19q-1 2 2 2h12q3 0 2-2L14 9V3 M8 15h8'],
report:['M5 3h14v18H5z M9 16v-3 M12 16V8 M15 16v-6'],
motion:['M4 7h12 M2 12h8 M4 17h12 M16 10l4 2-4 2']
}
</script>
