<script setup lang="ts">
defineProps<{ name: string; size?: number }>();

const paths: Record<string, string> = {
  overview: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
  queries: "M4 5h16 M4 12h16 M4 19h10 M18 16l3 3-3 3",
  credits: "M3 7h18v13H3z M3 10h18 M16 16h2 M7 3h12v4",
  movements: "M4 7h16 M16 3l4 4-4 4 M20 17H4 M8 13l-4 4 4 4",
  audit: "M6 3h9l4 4v14H6z M15 3v5h5 M9 12h7 M9 16h7",
  building:
    "M4 21V5l8-3 8 3v16 M2 21h20 M9 8v2 M15 8v2 M9 13v2 M15 13v2 M10 21v-4h4v4",
  refresh:
    "M20 6v5h-5 M4 18v-5h5 M5.8 9A7 7 0 0 1 18 6l2 5 M4 13l2 5a7 7 0 0 0 12.2-3",
  plus: "M12 5v14 M5 12h14",
  chevron: "M7 10l5 5 5-5",
  arrow: "M5 12h14 M13 6l6 6-6 6",
  logout: "M9 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h4 M16 17l5-5-5-5 M8 12h13",
  lock: "M5 11h14v10H5z M8 11V7a4 4 0 0 1 8 0v4",
  warning: "M12 3 2 21h20z M12 9v5 M12 18h.01",
  check: "M4 12l5 5L20 6",
  clock: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z M12 7v5l3 2",
  close: "M5 5l14 14 M19 5 5 19",
  copy: "M8 8h13v13H8z M16 8V3H3v13h5",
  menu: "M3 6h18 M3 12h18 M3 18h18",
  search: "M11 3a8 8 0 1 0 0 16 8 8 0 0 0 0-16z M17 17l4 4",
  shield: "M12 2 3 6v6c0 5 4 8 9 10 5-2 9-5 9-10V6z M8 12l3 3 5-6",
  info: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20z M12 10v7 M12 7h.01",
};
</script>

<template>
  <svg
    :width="size || 19"
    :height="size || 19"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.8"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path :d="paths[name] || paths.info" />
  </svg>
</template>
