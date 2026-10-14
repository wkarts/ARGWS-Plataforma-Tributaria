<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { ApiClient } from "../api.ts";
import type { InstallationBrand, InstallationBrandInput } from "../types.ts";

const props = defineProps<{ api: ApiClient; brand: InstallationBrand }>();
const emit = defineEmits<{ updated: [brand: InstallationBrand] }>();
const displayName = ref("");
const shortName = ref("");
const primaryColor = ref("");
const secondaryColor = ref("");
const logo = ref<File | null>(null);
const removeLogo = ref(false);
const saving = ref(false);
const error = ref("");
const success = ref("");
const logoName = computed(() => logo.value?.name || "Nenhum arquivo novo selecionado");

watch(() => props.brand, (brand) => {
  displayName.value = brand.display_name;
  shortName.value = brand.short_name;
  primaryColor.value = brand.primary_color;
  secondaryColor.value = brand.secondary_color;
  logo.value = null;
  removeLogo.value = false;
}, { immediate: true });

function chooseLogo(event: Event) {
  const input = event.target as HTMLInputElement;
  logo.value = input.files?.item(0) || null;
  if (logo.value) removeLogo.value = false;
}

async function save() {
  if (saving.value) return;
  error.value = "";
  success.value = "";
  if (logo.value && logo.value.size > 2 * 1024 * 1024) {
    error.value = "O logotipo deve ter até 2 MB.";
    return;
  }
  const payload: InstallationBrandInput = {
    version: props.brand.version,
    display_name: displayName.value.trim(),
    short_name: shortName.value.trim(),
    primary_color: primaryColor.value,
    secondary_color: secondaryColor.value,
    remove_logo: removeLogo.value,
  };
  saving.value = true;
  try {
    const updated = await props.api.saveInstallationBrand(payload, logo.value);
    emit("updated", updated);
    success.value = "Identidade salva para toda a instalação.";
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "Não foi possível salvar a identidade.";
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <section class="surface panel brand-settings">
    <div class="panel-heading">
      <div>
        <div class="eyebrow">IDENTIDADE DA INSTALAÇÃO</div>
        <h2>Marca exibida aos usuários</h2>
      </div>
    </div>
    <p>O nome, as cores e o logotipo valem para toda a instalação. Os CNPJs cadastrados continuam independentes.</p>
    <form class="brand-form" @submit.prevent="save">
      <label for="brand-display-name">Nome público</label>
      <input id="brand-display-name" v-model="displayName" required minlength="2" maxlength="160" autocomplete="off" />
      <label for="brand-short-name">Nome curto</label>
      <input id="brand-short-name" v-model="shortName" required minlength="2" maxlength="40" autocomplete="off" />
      <div class="brand-colors">
        <div><label for="brand-primary">Cor principal</label><input id="brand-primary" v-model="primaryColor" type="color" /></div>
        <div><label for="brand-secondary">Cor secundária</label><input id="brand-secondary" v-model="secondaryColor" type="color" /></div>
      </div>
      <label for="brand-logo">Logotipo PNG, JPEG ou WebP (até 2 MB)</label>
      <input id="brand-logo" type="file" accept="image/png,image/jpeg,image/webp" @change="chooseLogo" />
      <small>{{ logoName }}</small>
      <label v-if="brand.logo_url" class="brand-remove">
        <input v-model="removeLogo" type="checkbox" :disabled="Boolean(logo)" /> Remover logotipo atual
      </label>
      <p v-if="error" role="alert" class="inline-error">{{ error }}</p>
      <p v-if="success" role="status">{{ success }}</p>
      <button type="submit" class="btn primary" :disabled="saving">{{ saving ? "Salvando..." : "Salvar identidade" }}</button>
    </form>
  </section>
</template>

<style scoped>
.brand-settings{max-width:760px}
.brand-form{display:grid;gap:10px;max-width:540px}
.brand-form input:not([type="checkbox"]){max-width:100%;padding:10px;border:1px solid var(--line);border-radius:8px}
.brand-colors{display:flex;gap:22px;flex-wrap:wrap}
.brand-colors>div{display:grid;gap:8px}
.brand-remove{display:flex;align-items:center;gap:8px}
.brand-form button{justify-self:start}
</style>
