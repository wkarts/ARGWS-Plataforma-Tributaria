<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import type { ApiClient } from "../api.ts";
import type { CentralQuery, CentralSummary, Environment } from "../types.ts";
import { formatDate, statusLabel } from "../logic.ts";

const props = defineProps<{
  api: ApiClient;
  companyId: string;
  refreshTrigger: number;
}>();
const emit = defineEmits<{ (e: "openQueries"): void }>();

const summary = ref<CentralSummary | null>(null);
const history = ref<CentralQuery[]>([]);
const cursor = ref<string | null>(null);
const environment = ref<"" | Environment>("");
const loading = ref(false);
const loadingMore = ref(false);
const error = ref("");
let generation = 0;

function message(value: unknown) {
  return value instanceof Error ? value.message : "Ocorreu um erro ao consultar a central.";
}

async function refresh() {
  const current = ++generation;
  loading.value = true;
  error.value = "";
  summary.value = null;
  history.value = [];
  cursor.value = null;
  try {
    const [status, queries] = await Promise.all([
      props.api.centralSummary(props.companyId),
      props.api.centralHistory(props.companyId, undefined, environment.value || undefined),
    ]);
    if (current !== generation) return;
    summary.value = status;
    history.value = queries.items;
    cursor.value = queries.next_cursor;
  } catch (cause) {
    if (current === generation) error.value = message(cause);
  } finally {
    if (current === generation) loading.value = false;
  }
}

async function loadMore() {
  if (!cursor.value || loadingMore.value || loading.value) return;
  const current = generation;
  loadingMore.value = true;
  try {
    const page = await props.api.centralHistory(
      props.companyId, cursor.value, environment.value || undefined,
    );
    if (current !== generation) return;
    const known = new Set(history.value.map((q) => q.id));
    history.value.push(...page.items.filter((q) => !known.has(q.id)));
    cursor.value = page.next_cursor;
  } catch (cause) {
    if (current === generation) error.value = message(cause);
  } finally {
    loadingMore.value = false;
  }
}

watch(() => [props.companyId, props.refreshTrigger] as const, () => { void refresh(); }, { immediate: true });
watch(environment, () => { void refresh(); });
onBeforeUnmount(() => { ++generation; });
</script>

<template>
  <div class="fiscal-command">
    <div v-if="error" class="notice error" role="alert">{{ error }}</div>
    <div class="context-note">
      <span>Dados efetivamente recebidos e armazenados para a empresa ativa. As consultas incrementais não comprovam a cobertura integral da apuração.</span>
    </div>

    <div v-if="loading" class="surface panel">Atualizando indicadores e histórico...</div>
    <template v-else-if="summary">
      <div class="reforma-indicators">
        <div class="surface panel"><span>XMLs armazenados</span><strong>{{ summary.counts.documents }}</strong></div>
        <div class="surface panel"><span>Registros fiscais</span><strong>{{ summary.counts.records }}</strong></div>
        <div class="surface panel"><span>Consultas em andamento</span><strong>{{ summary.counts.active }}</strong></div>
        <div class="surface panel"><span>Consultas com atenção</span><strong>{{ summary.counts.attention }}</strong></div>
        <div class="surface panel"><span>Retornos de ambiente oficial</span><strong>{{ summary.counts.official_snapshots }}</strong></div>
      </div>
      <section class="surface panel">
        <div class="panel-heading">
          <div><div class="eyebrow">AUDITORIA OPERACIONAL</div><h2>Histórico de consultas da empresa</h2></div>
          <button type="button" class="btn secondary" @click="emit('openQueries')">Abrir consultas CBS</button>
        </div>
        <div class="reforma-toolbar">
          <label for="central-environment">Ambiente</label>
          <select id="central-environment" v-model="environment">
            <option value="">Todos</option><option value="production">Produção</option>
            <option value="restricted">Restrito</option><option value="fixture">Dados de teste</option>
          </select>
          <button class="btn secondary" type="button" @click="refresh">Atualizar</button>
        </div>
        <div v-if="!history.length" class="table-empty">Nenhuma consulta encontrada para os filtros selecionados.</div>
        <div v-else class="table-scroll">
          <table>
            <thead><tr><th>Data</th><th>Recurso</th><th>Ambiente</th><th>Situação</th><th>Tíquete</th><th>Integridade</th></tr></thead>
            <tbody>
              <tr v-for="query in history" :key="query.id">
                <td>{{ formatDate(query.created_at) }}</td><td>{{ query.resource }}</td>
                <td>{{ query.environment }}</td><td>{{ statusLabel(query.status) }}</td>
                <td class="reforma-break">{{ query.ticket || "—" }}</td>
                <td class="reforma-break">
                  <code v-if="query.received_sha256" :title="query.received_sha256">{{ query.received_sha256 }}</code>
                  <span v-else>Sem retorno bruto persistido</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="cursor" class="load-more">
          <button class="btn secondary" type="button" :disabled="loadingMore" @click="loadMore">
            {{ loadingMore ? "Carregando..." : "Carregar mais" }}
          </button>
        </div>
        <p class="muted-small">O histórico é uma trilha de operações; não equivale à confirmação de créditos, liquidação de débitos ou declaração homologada.</p>
      </section>
    </template>
  </div>
</template>

<style scoped>
.reforma-indicators{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:16px}
.reforma-indicators .panel{min-width:0;display:grid;gap:10px;padding:18px}
.reforma-indicators span{color:#61768b;font-size:.79rem}
.reforma-indicators strong{color:#183f5b;font-size:1.8rem}
.reforma-toolbar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:14px 0}
.reforma-toolbar label{font-weight:700;color:#344961}
.reforma-toolbar select{border:1px solid #dbe4ee;border-radius:8px;padding:10px;color:#29435c;background:#fff}
.reforma-break{max-width:250px;overflow-wrap:anywhere}
.reforma-break code{font-size:.72rem}
</style>
