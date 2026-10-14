<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import type { ApiClient } from "../api.ts";
import { formatBRL, formatDate } from "../logic.ts";
import type {
  FiscalDocument,
  LocalAgent,
  Reconciliation,
} from "../types.ts";
import AppIcon from "./AppIcon.vue";

type WorkspaceView = "documents" | "agents" | "reconciliation";
const props = defineProps<{
  api: ApiClient;
  companyId: string;
  view: WorkspaceView;
  admin: boolean;
  refreshTrigger: number;
}>();

const documents = ref<FiscalDocument[]>([]);
const nextCursor = ref<string | null>(null);
const agents = ref<LocalAgent[]>([]);
const reconciliation = ref<Reconciliation | null>(null);
const busy = ref(false);
const moreBusy = ref(false);
const mutationBusy = ref(false);
const downloadingId = ref<string | null>(null);
const error = ref("");
const success = ref("");
const agentName = ref("");
const issuedToken = ref<string | null>(null);
const issuedAgentName = ref("");
const revokeCandidate = ref<string | null>(null);
const now = new Date();
const reconciliationPeriod = ref(`${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`);
let generation = 0;

const summaryEntries = computed(() =>
  Object.entries(reconciliation.value?.summary || {}).filter(
    ([, value]) => value !== null && value !== undefined,
  ),
);
const summaryLabels: Record<string, string> = {
  total: "Itens analisados",
  match: "Valores de CBS conferem",
  mismatch: "Divergentes",
  missing: "Sem correspondência",
  ambiguous: "Ambíguos",
  unknown: "Indeterminados",
};
const resultLabels: Record<string, string> = {
  match: "Valores de CBS conferem",
  mismatch: "Divergente",
  missing: "Sem correspondência",
  ambiguous: "Ambíguo",
  unknown: "Indeterminado",
};
const reasonLabels: Record<string, string> = {
  amounts_equal: "Valores de CBS iguais nas duas fontes",
  cbs_amount_differs: "Valores de CBS divergentes",
  local_document_absent: "XML correspondente não recebido",
  local_company_scope_incomplete: "A consulta cobre a raiz CNPJ; verifique XMLs das demais filiais",
  multiple_local_documents: "Mais de um XML para a mesma chave",
  multiple_official_observations: "Mais de um valor oficial para a chave",
  payment_requires_fiscal_extinction_evidence: "Pagamento exige prova da extinção tributária",
  credit_category_requires_independent_review: "Categoria de crédito exige revisão própria",
  document_direction_unknown: "Não foi possível definir entrada ou saída",
  document_direction_requires_review: "Entrada ou saída incompatível com o registro",
  cbs_amount_unavailable: "Valor de CBS indisponível para comparação",
  official_document_or_period_unavailable: "Registro oficial sem chave ou competência identificável",
  official_document_absent_in_covered_period: "Registro oficial ausente em competência com cobertura comprovada",
  official_coverage_unverified: "Cobertura incremental oficial não comprovada",
  local_document_or_period_unavailable: "XML sem chave ou competência identificável",
};

function clearSecret() {
  issuedToken.value = null;
  issuedAgentName.value = "";
}

function message(value: unknown) {
  return value instanceof Error ? value.message : "Ocorreu um erro inesperado.";
}

async function load() {
  const current = ++generation;
  const company = props.companyId;
  const selectedView = props.view;
  busy.value = true;
  error.value = "";
  if (selectedView === "reconciliation" && !/^\d{4}-(0[1-9]|1[0-2])$/.test(reconciliationPeriod.value)) {
    reconciliation.value = null;
    error.value = "Selecione uma competência válida.";
    busy.value = false;
    return;
  }
  try {
    if (selectedView === "documents") {
      const page = await props.api.documents(company);
      if (current !== generation) return;
      documents.value = page.items;
      nextCursor.value = page.next_cursor || null;
    } else if (selectedView === "agents") {
      if (!props.admin) return;
      const page = await props.api.agents(company);
      if (current !== generation) return;
      agents.value = page.items;
    } else {
      const result = await props.api.reconciliation(company, reconciliationPeriod.value);
      if (current !== generation) return;
      reconciliation.value = result;
    }
  } catch (cause) {
    if (current === generation) error.value = message(cause);
  } finally {
    if (current === generation) busy.value = false;
  }
}

async function loadMoreDocuments() {
  if (!nextCursor.value || moreBusy.value) return;
  moreBusy.value = true;
  const current = generation;
  const company = props.companyId;
  try {
    const page = await props.api.documents(company, nextCursor.value);
    if (current !== generation) return;
    const seen = new Set(documents.value.map((item) => item.id));
    documents.value.push(...page.items.filter((item) => !seen.has(item.id)));
    nextCursor.value = page.next_cursor || null;
  } catch (cause) {
    if (current === generation) error.value = message(cause);
  } finally {
    moreBusy.value = false;
  }
}

async function downloadDocument(item: FiscalDocument) {
  if (downloadingId.value) return;
  downloadingId.value = item.id;
  error.value = "";
  try {
    const blob = await props.api.documentRaw(item.id, item.sha256);
    const objectUrl = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    const safeKey = (item.document_key || "").replace(/[^A-Za-z0-9-]/g, "");
    anchor.download = `${safeKey || item.id.replace(/[^A-Za-z0-9-]/g, "")}.xml`;
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 10000);
    success.value = "XML baixado após conferência do SHA-256.";
  } catch (cause) {
    error.value = message(cause);
  } finally {
    downloadingId.value = null;
  }
}

async function issueAgent() {
  const name = agentName.value.trim();
  if (!props.admin || !name || mutationBusy.value) return;
  mutationBusy.value = true;
  error.value = "";
  success.value = "";
  clearSecret();
  try {
    const result = await props.api.createAgent(props.companyId, name);
    if (!result.token) throw new Error("A API não retornou a credencial de emissão única.");
    issuedToken.value = result.token;
    issuedAgentName.value = result.name || name;
    agentName.value = "";
    const page = await props.api.agents(props.companyId);
    agents.value = page.items;
  } catch (cause) {
    error.value = message(cause);
  } finally {
    mutationBusy.value = false;
  }
}

async function revokeAgent(agentId: string) {
  if (!props.admin || mutationBusy.value || revokeCandidate.value !== agentId) return;
  mutationBusy.value = true;
  error.value = "";
  try {
    await props.api.revokeAgent(agentId);
    revokeCandidate.value = null;
    if (issuedToken.value) clearSecret();
    const page = await props.api.agents(props.companyId);
    agents.value = page.items;
    success.value = "Credencial revogada. O serviço local deve receber uma nova credencial para voltar a enviar.";
  } catch (cause) {
    error.value = message(cause);
  } finally {
    mutationBusy.value = false;
  }
}

watch(
  () => [props.companyId, props.view] as const,
  () => {
    clearSecret();
    revokeCandidate.value = null;
    success.value = "";
    documents.value = [];
    agents.value = [];
    reconciliation.value = null;
    nextCursor.value = null;
    void load();
  },
  { immediate: true },
);
watch(
  () => props.refreshTrigger,
  () => {
    clearSecret();
    void load();
  },
);
onBeforeUnmount(() => {
  generation++;
  clearSecret();
});
</script>

<template>
  <div class="workspace">
    <div v-if="error" class="notice error" role="alert">
      <AppIcon name="warning" :size="17" /><span>{{ error }}</span>
    </div>
    <div v-if="success" class="notice success" role="status">
      <AppIcon name="check" :size="17" /><span>{{ success }}</span>
    </div>

    <template v-if="view === 'documents'">
      <div class="context-note">
        <AppIcon name="shield" :size="18" /><span>
          XMLs recebidos de serviços locais autorizados. A presença de assinatura e o estado de autorização são mostrados separadamente; a lista não substitui a validação fiscal.
        </span>
      </div>
      <section class="surface panel table-panel">
        <div class="panel-heading">
          <div><div class="eyebrow">DOCUMENTOS DA EMPRESA</div><h2>XMLs recebidos</h2></div>
          <span class="small-muted">{{ documents.length }} exibidos</span>
        </div>
        <div v-if="busy" class="table-empty">Carregando XMLs...</div>
        <div v-else-if="!documents.length" class="table-empty">Nenhum XML recebido para esta empresa.</div>
        <div v-else class="table-scroll">
          <table>
            <thead><tr><th>Documento</th><th>Modelo</th><th>Assinatura</th><th>Autorização</th><th>Recebido</th><th>Integridade</th><th><span class="sr-only">Ação</span></th></tr></thead>
            <tbody>
              <tr v-for="item in documents" :key="item.id">
                <td><strong class="document-key">{{ item.document_key || "Sem chave identificável" }}</strong><span class="subcell">{{ item.kind }} · {{ item.id }}</span></td>
                <td>{{ item.model || "—" }}</td>
                <td>{{ item.signature_present ? "Presente" : "Não informada" }}</td>
                <td>{{ item.authorization_status || "Não informada" }}</td>
                <td>{{ formatDate(item.created_at) }}</td>
                <td><code class="document-hash" :title="item.sha256">{{ item.sha256 }}</code></td>
                <td><button class="btn secondary" type="button" :disabled="!!downloadingId" :aria-label="`Baixar XML ${item.document_key || item.id}`" @click="downloadDocument(item)">{{ downloadingId === item.id ? "Verificando..." : "Baixar XML" }}</button></td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="nextCursor" class="load-more"><button class="btn secondary" type="button" :disabled="moreBusy" @click="loadMoreDocuments">{{ moreBusy ? "Carregando..." : "Carregar mais XMLs" }}</button></div>
      </section>
    </template>

    <template v-else-if="view === 'agents'">
      <div class="context-note">
        <AppIcon name="info" :size="18" /><span>
          O serviço local observa uma pasta autorizada pelo cliente e envia XMLs assinados por HTTPS. Configure e guarde a credencial em seu ambiente; ela aparece apenas após a emissão.
        </span>
      </div>
      <section v-if="!admin" class="surface panel">
        <div class="panel-heading"><div><div class="eyebrow">ACESSO RESTRITO</div><h2>Agentes locais</h2></div></div>
        <p>Solicite a emissão ou revogação de credenciais a um administrador.</p>
      </section>
      <template v-else>
        <section class="surface panel agent-panel">
          <div class="panel-heading"><div><div class="eyebrow">ACESSO À COLETA LOCAL</div><h2>Emitir credencial</h2></div><div class="panel-icon"><AppIcon name="lock" /></div></div>
          <p>Identifique a instalação do cliente. Uma credencial diferente deve ser usada para cada serviço local.</p>
          <form class="agent-form" @submit.prevent="issueAgent">
            <div><label for="agent-name">Nome da instalação</label><input id="agent-name" v-model="agentName" type="text" maxlength="120" autocomplete="off" placeholder="Servidor fiscal · unidade 1" required /></div>
            <button class="btn primary" type="submit" :disabled="mutationBusy || !agentName.trim()">{{ mutationBusy ? "Emitindo..." : "Emitir credencial" }}</button>
          </form>
          <div v-if="issuedToken" class="issued-secret" role="status">
            <strong>Credencial de {{ issuedAgentName }} — exibição única</strong>
            <p>Copie agora para a configuração protegida do serviço local. Ao fechar esta área, trocar de empresa ou sair, o console não poderá mostrá-la novamente. Se perdê-la, revogue e emita outra.</p>
            <code>{{ issuedToken }}</code>
            <button class="btn secondary" type="button" @click="clearSecret">Já guardei a credencial · ocultar</button>
          </div>
        </section>
        <section class="surface panel table-panel">
          <div class="panel-heading"><div><div class="eyebrow">INSTALAÇÕES AUTORIZADAS</div><h2>Agentes da empresa</h2></div></div>
          <div v-if="busy" class="table-empty">Carregando agentes...</div>
          <div v-else-if="!agents.length" class="table-empty">Nenhuma credencial emitida para esta empresa.</div>
          <div v-else class="table-scroll">
            <table><thead><tr><th>Instalação</th><th>Emitida em</th><th>Última atividade</th><th>Estado</th><th><span class="sr-only">Ação</span></th></tr></thead>
              <tbody><tr v-for="item in agents" :key="item.id">
                <td><strong>{{ item.name }}</strong><span class="subcell">{{ item.id }}</span></td>
                <td>{{ formatDate(item.created_at) }}</td><td>{{ formatDate(item.last_used_at) }}</td>
                <td>{{ item.revoked_at ? "Revogada" : item.status || "Ativa" }}</td>
                <td>
                  <div v-if="!item.revoked_at && revokeCandidate === item.id" class="revoke-actions"><span>Revogar?</span><button class="btn secondary" type="button" @click="revokeCandidate = null">Cancelar</button><button class="btn danger" type="button" :disabled="mutationBusy" @click="revokeAgent(item.id)">Confirmar</button></div>
                  <button v-else-if="!item.revoked_at" class="btn secondary" type="button" @click="revokeCandidate = item.id">Revogar</button>
                  <span v-else>—</span>
                </td>
              </tr></tbody>
            </table>
          </div>
        </section>
      </template>
    </template>

    <template v-else>
      <div class="context-note">
        <AppIcon name="info" :size="18" /><span>
          Conferência de valores de CBS dos XMLs recebidos com registros oficiais disponíveis para esta empresa. O resultado não valida assinatura, autorização, direito ao crédito, extinção do débito ou fechamento da apuração. Dados incrementais sem cobertura suficiente aparecem como indeterminados.
        </span>
      </div>
      <div class="surface reconciliation-filter">
        <label for="reconciliation-period">Competência</label>
        <input id="reconciliation-period" v-model="reconciliationPeriod" type="month" required @change="load" />
        <span>Compare uma competência de cada vez.</span>
      </div>
      <div v-if="busy" class="surface panel table-empty">Calculando a comparação disponível...</div>
      <template v-else-if="reconciliation">
        <div v-if="!reconciliation.company_scope_complete || reconciliation.unperioded_document_count" class="context-note">
          <AppIcon name="warning" :size="18" /><span>
            <template v-if="!reconciliation.company_scope_complete">Há mais de uma empresa cadastrada para esta raiz CNPJ. A ausência de XML nesta empresa não comprova falta do documento nas demais filiais. </template>
            <template v-if="reconciliation.unperioded_document_count">{{ reconciliation.unperioded_document_count }} XML(s) sem competência identificada ficaram fora deste filtro mensal; examine a lista de documentos.</template>
          </span>
        </div>
        <div v-if="summaryEntries.length" class="reconciliation-summary">
          <div v-for="[label, value] in summaryEntries" :key="label" class="surface reconciliation-stat"><span>{{ summaryLabels[label] || label.replace(/_/g, " ") }}</span><strong>{{ value }}</strong></div>
        </div>
        <section class="surface panel table-panel">
          <div class="panel-heading"><div><div class="eyebrow">CRUZAMENTO DE FONTES</div><h2>Conciliação por documento</h2></div><span class="small-muted">{{ reconciliation.items.length }} resultados</span></div>
          <div v-if="!reconciliation.items.length" class="table-empty">Ainda não há documentos e registros comparáveis nesta empresa.</div>
          <div v-else class="table-scroll"><table><thead><tr><th>Chave do documento</th><th>Resultado</th><th>Motivo</th><th>XML</th><th>Origem oficial</th><th>Crédito a apropriar</th><th>Apropriado</th><th>Utilizado</th></tr></thead><tbody>
            <tr v-for="(item, index) in reconciliation.items" :key="`${item.document_key}-${index}`">
              <td><strong class="document-key">{{ item.document_key || "Sem chave identificável" }}</strong></td>
              <td><span class="status-pill" :class="item.status === 'match' ? 'success' : item.status === 'unknown' ? 'neutral' : 'error'"><span class="status-dot"></span>{{ resultLabels[item.status] || item.status }}</span></td>
              <td>{{ item.reason ? reasonLabels[item.reason] || item.reason : "—" }}</td>
              <td class="amount">{{ formatBRL(item.source_amount) }}</td><td class="amount">{{ formatBRL(item.official_amount) }}</td>
              <td class="amount">{{ formatBRL(item.credit_to_appropriate) }}</td><td class="amount">{{ formatBRL(item.credit_appropriated) }}</td><td class="amount">{{ formatBRL(item.credit_used) }}</td>
            </tr>
          </tbody></table></div>
        </section>
      </template>
    </template>
  </div>
</template>
