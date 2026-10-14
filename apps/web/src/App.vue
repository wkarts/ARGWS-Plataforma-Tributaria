<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import AppIcon from "./components/AppIcon.vue";
import FiscalWorkspace from "./components/FiscalWorkspace.vue";
import FinanceControls from "./components/FinanceControls.vue";
import { ApiClient, ApiError } from "./api.ts";
import {
  canQuery,
  classifyCredit,
  formatBRL,
  formatDate,
  isActive,
  isFailed,
  prettyJson,
  RESOURCE_LABELS,
  statusKind,
  statusLabel,
} from "./logic.ts";
import type {
  Capabilities,
  Company,
  Coverage,
  Environment,
  FiscalRecord,
  Query,
  Resource,
  User,
} from "./types.ts";
import { RESOURCES } from "./types.ts";

type View = "overview" | "queries" | "credits" | "movements" | "audit" | "documents" | "agents" | "reconciliation" | "finance_controls";
type CreditView = "government_reported" | "presumed_stock" | "unclassified";
const navigation: { id: View; label: string; icon: string; hint: string }[] = [
  { id: "overview", label: "Visão geral", icon: "overview", hint: "Operação" },
  { id: "queries", label: "Consultas CBS", icon: "queries", hint: "Tíquetes" },
  { id: "credits", label: "Créditos", icon: "credits", hint: "Apropriação" },
  {
    id: "movements",
    label: "Movimentações",
    icon: "movements",
    hint: "Débitos e pagamentos",
  },
  {
    id: "audit",
    label: "Rastreabilidade",
    icon: "audit",
    hint: "Fontes e retornos",
  },
  { id: "documents", label: "XML fiscais", icon: "audit", hint: "Documentos recebidos" },
  { id: "reconciliation", label: "Conciliação", icon: "search", hint: "Conferência" },
  { id: "finance_controls", label: "Controles manuais", icon: "credits", hint: "Estoque e parcelas" },
  { id: "agents", label: "Agentes locais", icon: "shield", hint: "Coleta no cliente" },
];
const environmentLabels: Record<Environment, string> = {
  fixture: "Fixture · simulação",
  restricted: "Restrito · homologação",
  production: "Produção",
};

const token = ref<string | null>(sessionStorage.getItem("rt_access_token"));
const username = ref("");
const password = ref("");
const user = ref<User | null>(null);
const companies = ref<Company[]>([]);
const companyId = ref("");
const capabilities = ref<Capabilities | null>(null);
const coverage = ref<Coverage | null>(null);
const coverageError = ref("");
const loadingCoverage = ref(false);
const queries = ref<Query[]>([]);
const records = ref<FiscalRecord[]>([]);
const nextCursor = ref<string | null>(null);
const nextQueryCursor = ref<string | null>(null);
const selectedQuery = ref<Query | null>(null);
const selectedRecord = ref<FiscalRecord | null>(null);
const view = ref<View>("overview");
const creditView = ref<CreditView>("government_reported");
const movementResource = ref<Resource>("debitos");
const resource = ref<Resource>("creditos");
const environment = ref<Environment>("fixture");
const loginBusy = ref(false);
const loading = ref(false);
const booting = ref(Boolean(token.value));
const creatingQuery = ref(false);
const creatingCompany = ref(false);
const loadingMore = ref(false);
const loadingMoreQueries = ref(false);
const downloadingRaw = ref(false);
const resolving = ref(false);
const resolveAction = ref<"" | "attach_ticket" | "mark_failed">("");
const resolveTicket = ref("");
const resolveNote = ref("");
const resolutionConfirmed = ref(false);
const showCompanyForm = ref(false);
const companyName = ref("");
const companyCnpj = ref("");
const banner = ref<{
  kind: "error" | "success" | "info";
  message: string;
} | null>(null);
const loginError = ref("");
const lastRefresh = ref<string | null>(null);
const dataLoaded = ref(false);
const online = ref(navigator.onLine);
const mobileMenu = ref(false);
const workspaceRefresh = ref(0);
let loadGeneration = 0;
let coverageGeneration = 0;
let pollTimer: number | undefined;
let pendingKey: string | null = null;
const updateOnline = () => {
  online.value = navigator.onLine;
};

const api = new ApiClient(
  () => token.value,
  () => logout("Sua sessão expirou. Entre novamente."),
);
const selectedCompany = computed(
  () => companies.value.find((item) => item.id === companyId.value) || null,
);
const activeQueries = computed(() => queries.value.filter(isActive));
const failedQueries = computed(() => queries.value.filter(isFailed));
const coverageGaps = computed(() =>
  RESOURCES.filter(
    (item) => coverage.value?.resources[item]?.gap_over_eight_days,
  ),
);
const visibleCredits = computed(() =>
  records.value.filter(
    (item) =>
      item.resource === "creditos" && classifyCredit(item) === creditView.value,
  ),
);
const visibleMovements = computed(() =>
  records.value.filter((item) => item.resource === movementResource.value),
);
const resourceAvailable = computed(() =>
  canQuery(capabilities.value, resource.value, environment.value),
);
const currentPageTitle = computed(
  () => navigation.find((item) => item.id === view.value)?.label || "",
);
const fixtureMode = computed(
  () => capabilities.value?.provider_mode === "fixture",
);
const queryReason = computed(() => {
  if (!capabilities.value) return "Aguardando capacidades da API.";
  if (!capabilities.value.environments.includes(environment.value))
    return "Este ambiente não está habilitado na API.";
  const selected = capabilities.value.resources[resource.value];
  if (!selected) return "Recurso não informado pela API.";
  return selected.reason || "Este recurso está indisponível neste ambiente.";
});

function notice(kind: "error" | "success" | "info", message: string) {
  banner.value = { kind, message };
}
function message(error: unknown): string {
  return error instanceof Error ? error.message : "Ocorreu um erro inesperado.";
}
function logout(reason?: string) {
  token.value = null;
  sessionStorage.removeItem("rt_access_token");
  sessionStorage.removeItem("rt_company_id");
  user.value = null;
  capabilities.value = null;
  coverage.value = null;
  coverageGeneration++;
  companies.value = [];
  companyId.value = "";
  queries.value = [];
  records.value = [];
  dataLoaded.value = false;
  selectedQuery.value = null;
  selectedRecord.value = null;
  pendingKey = null;
  loadGeneration++;
  if (reason) loginError.value = reason;
}

async function login() {
  loginBusy.value = true;
  loginError.value = "";
  try {
    const result = await api.login(username.value.trim(), password.value);
    if (!result.access_token || result.token_type.toLowerCase() !== "bearer")
      throw new Error("A API retornou uma sessão inválida.");
    token.value = result.access_token;
    sessionStorage.setItem("rt_access_token", result.access_token);
    password.value = "";
    await bootstrap();
  } catch (error) {
    loginError.value = message(error);
  } finally {
    loginBusy.value = false;
  }
}

async function bootstrap() {
  booting.value = true;
  try {
    const [me, cap, response] = await Promise.all([
      api.me(),
      api.capabilities(),
      api.companies(),
    ]);
    user.value = me;
    capabilities.value = cap;
    companies.value = response.items;
    if (!cap.environments.includes(environment.value))
      environment.value = cap.environments[0] || "fixture";
    const remembered = sessionStorage.getItem("rt_company_id");
    companyId.value =
      companies.value.find((item) => item.id === remembered)?.id ||
      companies.value[0]?.id ||
      "";
    if (companyId.value) await refreshData();
  } catch (error) {
    if (token.value) notice("error", message(error));
  } finally {
    booting.value = false;
  }
}

async function refreshData(silent = false) {
  if (!companyId.value) return;
  const generation = ++loadGeneration;
  const requestedCompany = companyId.value;
  if (!silent) loading.value = true;
  try {
    const [queryPage, recordPage] = await Promise.all([
      api.queries(requestedCompany),
      api.records(requestedCompany),
    ]);
    if (generation !== loadGeneration || requestedCompany !== companyId.value)
      return;
    queries.value = queryPage.items;
    nextQueryCursor.value = queryPage.next_cursor || null;
    records.value = recordPage.items;
    dataLoaded.value = true;
    nextCursor.value = recordPage.next_cursor || null;
    if (selectedQuery.value)
      selectedQuery.value =
        queryPage.items.find((item) => item.id === selectedQuery.value?.id) ||
        selectedQuery.value;
    if (selectedRecord.value)
      selectedRecord.value =
        records.value.find((item) => item.id === selectedRecord.value?.id) ||
        selectedRecord.value;
    lastRefresh.value = new Date().toISOString();
    void loadCoverage();
  } catch (error) {
    if (generation === loadGeneration) notice("error", message(error));
  } finally {
    if (generation === loadGeneration) loading.value = false;
  }
}

async function loadCoverage() {
  if (!companyId.value || !token.value) return;
  const generation = ++coverageGeneration;
  const requestedCompany = companyId.value;
  const requestedEnvironment = environment.value;
  loadingCoverage.value = true;
  coverageError.value = "";
  try {
    const result = await api.coverage(requestedCompany, requestedEnvironment);
    if (
      generation === coverageGeneration &&
      requestedCompany === companyId.value &&
      requestedEnvironment === environment.value
    )
      coverage.value = result;
  } catch (error) {
    if (generation === coverageGeneration) {
      coverage.value = null;
      coverageError.value = message(error);
    }
  } finally {
    if (generation === coverageGeneration) loadingCoverage.value = false;
  }
}

async function loadMoreRecords() {
  if (!nextCursor.value || loadingMore.value || !companyId.value) return;
  loadingMore.value = true;
  const requestedCompany = companyId.value;
  try {
    const page = await api.records(
      requestedCompany,
      undefined,
      nextCursor.value,
    );
    if (companyId.value !== requestedCompany) return;
    const seen = new Set(records.value.map((item) => item.id));
    records.value.push(...page.items.filter((item) => !seen.has(item.id)));
    nextCursor.value = page.next_cursor || null;
  } catch (error) {
    notice("error", message(error));
  } finally {
    loadingMore.value = false;
  }
}

async function loadMoreQueries() {
  if (!nextQueryCursor.value || loadingMoreQueries.value || !companyId.value)
    return;
  loadingMoreQueries.value = true;
  const requestedCompany = companyId.value;
  try {
    const page = await api.queries(requestedCompany, nextQueryCursor.value);
    if (companyId.value !== requestedCompany) return;
    const seen = new Set(queries.value.map((item) => item.id));
    queries.value.push(...page.items.filter((item) => !seen.has(item.id)));
    nextQueryCursor.value = page.next_cursor || null;
  } catch (error) {
    notice("error", message(error));
  } finally {
    loadingMoreQueries.value = false;
  }
}

async function changeCompany() {
  sessionStorage.setItem("rt_company_id", companyId.value);
  loadGeneration++;
  queries.value = [];
  records.value = [];
  coverage.value = null;
  coverageGeneration++;
  dataLoaded.value = false;
  nextCursor.value = null;
  nextQueryCursor.value = null;
  selectedQuery.value = null;
  selectedRecord.value = null;
  pendingKey = null;
  banner.value = null;
  await refreshData();
}

async function addCompany() {
  if (!companyName.value.trim() || !companyCnpj.value.trim()) return;
  creatingCompany.value = true;
  try {
    const newCompany = await api.createCompany(
      companyName.value.trim(),
      companyCnpj.value
        .trim()
        .replace(/[^A-Za-z0-9]/g, "")
        .toUpperCase(),
    );
    companies.value.push(newCompany);
    companyId.value = newCompany.id;
    companyName.value = "";
    companyCnpj.value = "";
    showCompanyForm.value = false;
    await changeCompany();
    notice(
      "success",
      "Empresa cadastrada. Agora você pode iniciar uma consulta.",
    );
  } catch (error) {
    notice("error", message(error));
  } finally {
    creatingCompany.value = false;
  }
}

async function startQuery() {
  if (!companyId.value || !resourceAvailable.value || creatingQuery.value)
    return;
  creatingQuery.value = true;
  pendingKey ||= crypto.randomUUID();
  try {
    const created = await api.createQuery(
      companyId.value,
      resource.value,
      environment.value,
      pendingKey,
    );
    pendingKey = null;
    selectedQuery.value = created;
    notice(
      "success",
      `Consulta ${RESOURCE_LABELS[resource.value].toLowerCase()} registrada. Acompanhe o tíquete e o estado abaixo.`,
    );
    await refreshData();
  } catch (error) {
    notice(
      "error",
      `${message(error)} ${error instanceof ApiError && error.status === 0 ? "A chave de idempotência foi preservada para uma nova tentativa segura." : ""}`.trim(),
    );
    if (error instanceof ApiError && error.status >= 400 && error.status < 500)
      pendingKey = null;
  } finally {
    creatingQuery.value = false;
  }
}

async function inspectQuery(item: Query) {
  view.value = "queries";
  selectedQuery.value = item;
  try {
    selectedQuery.value = await api.query(item.id);
  } catch (error) {
    notice("error", message(error));
  }
}

async function resolveUncertain() {
  if (
    !isAdmin() ||
    !selectedQuery.value ||
    selectedQuery.value.status !== "uncertain" ||
    !resolveAction.value ||
    !resolutionConfirmed.value ||
    resolveNote.value.trim().length < 10 ||
    (resolveAction.value === "attach_ticket" && !resolveTicket.value.trim())
  )
    return;
  resolving.value = true;
  const queryId = selectedQuery.value.id;
  try {
    const result = await api.resolveQuery(
      queryId,
      resolveAction.value,
      resolveNote.value.trim(),
      resolveTicket.value.trim(),
    );
    if (selectedQuery.value?.id === queryId) selectedQuery.value = result;
    resolveAction.value = "";
    resolveTicket.value = "";
    resolveNote.value = "";
    resolutionConfirmed.value = false;
    notice(
      "success",
      "Resolução manual registrada com auditoria. Confira o novo estado da consulta.",
    );
    await refreshData();
  } catch (error) {
    notice("error", message(error));
  } finally {
    resolving.value = false;
  }
}

async function downloadRaw() {
  if (!selectedQuery.value?.raw_sha256 || downloadingRaw.value) return;
  downloadingRaw.value = true;
  try {
    const blob = await api.raw(
      selectedQuery.value.id,
      selectedQuery.value.raw_sha256,
    );
    const objectUrl = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = `cbs-retorno-${selectedQuery.value.id.replace(/[^a-zA-Z0-9-]/g, "")}.json`;
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 10000);
    notice(
      "success",
      "Retorno bruto baixado com SHA-256 conferido no navegador.",
    );
  } catch (error) {
    notice("error", message(error));
  } finally {
    downloadingRaw.value = false;
  }
}

function openRecord(item: FiscalRecord) {
  selectedRecord.value = item;
  view.value = "audit";
}

async function inspectRecordQuery() {
  if (!selectedRecord.value) return;
  try {
    selectedQuery.value = await api.query(selectedRecord.value.query_id);
    view.value = "queries";
  } catch (error) {
    notice("error", message(error));
  }
}

function receiptHash(item: FiscalRecord): string {
  if (item.raw_sha256) return item.raw_sha256;
  if (
    item.provenance &&
    typeof item.provenance === "object" &&
    typeof item.provenance.receipt_sha256 === "string"
  )
    return item.provenance.receipt_sha256;
  return "—";
}

function navigate(target: View) {
  view.value = target;
  mobileMenu.value = false;
  banner.value = null;
}
function refreshCurrent() {
  if (view.value === "documents" || view.value === "agents" || view.value === "reconciliation" || view.value === "finance_controls") {
    workspaceRefresh.value++;
    return;
  }
  void refreshData();
}
function resetPendingKey() {
  pendingKey = null;
}
function formattedEnvironment(value: Environment) {
  return environmentLabels[value] || value;
}
function recordReference(item: FiscalRecord) {
  return item.source_key || item.id;
}
function isAdmin() {
  return user.value?.role === "admin";
}

watch(resource, resetPendingKey);
watch(
  () => selectedQuery.value?.id,
  () => {
    resolveAction.value = "";
    resolveTicket.value = "";
    resolveNote.value = "";
    resolutionConfirmed.value = false;
  },
);
watch(environment, () => {
  resetPendingKey();
  coverage.value = null;
  void loadCoverage();
});
onMounted(() => {
  if (token.value) void bootstrap();
  window.addEventListener("online", updateOnline);
  window.addEventListener("offline", updateOnline);
  pollTimer = window.setInterval(() => {
    if (
      token.value &&
      companyId.value &&
      activeQueries.value.length &&
      document.visibilityState === "visible" &&
      online.value
    )
      void refreshData(true);
  }, 12000);
});
onBeforeUnmount(() => {
  if (pollTimer) window.clearInterval(pollTimer);
  window.removeEventListener("online", updateOnline);
  window.removeEventListener("offline", updateOnline);
});
</script>

<template>
  <div v-if="!token" class="auth-screen">
    <div class="auth-brand">
      <div class="brand-mark">RT<span class="brand-dot"></span></div>
      <span>Reforma Tributária</span>
    </div>
    <div class="auth-grid">
      <section class="auth-intro">
        <div class="eyebrow light">
          <span class="eyebrow-line"></span> CONSOLE OPERACIONAL
        </div>
        <h1>Dados fiscais com<br /><em>rastreabilidade.</em></h1>
        <p>
          Consulte a CBS, acompanhe o processamento e confira os retornos
          governamentais por empresa e por origem.
        </p>
        <div class="auth-points">
          <span><AppIcon name="shield" /> Acesso autorizado</span
          ><span><AppIcon name="audit" /> Retornos preservados</span>
        </div>
      </section>
      <section class="auth-card" aria-label="Acesso ao console">
        <div class="auth-card-icon"><AppIcon name="lock" :size="22" /></div>
        <div class="eyebrow">ACESSO SEGURO</div>
        <h2>Entrar no console</h2>
        <p>Use as credenciais configuradas no serviço.</p>
        <form @submit.prevent="login">
          <label for="username">E-mail</label
          ><input
            id="username"
            v-model="username"
            type="email"
            autocomplete="username"
            required
            placeholder="seu@email.com"
          />
          <label for="password">Senha</label
          ><input
            id="password"
            v-model="password"
            type="password"
            autocomplete="current-password"
            required
            placeholder="Sua senha"
          />
          <div v-if="loginError" class="inline-error" role="alert">
            <AppIcon name="warning" :size="16" /> {{ loginError }}
          </div>
          <button
            class="btn primary login-button"
            type="submit"
            :disabled="loginBusy"
          >
            {{ loginBusy ? "Autenticando..." : "Acessar console" }}
            <AppIcon name="arrow" />
          </button>
        </form>
        <div class="auth-footnote">
          O acesso é restrito a usuários autorizados. Nenhum dado fiscal é
          armazenado para uso offline neste navegador.
        </div>
      </section>
    </div>
    <footer class="auth-footer">
      CONSULTAS CBS <span>•</span> APURAÇÃO ASSISTIDA <span>•</span> AUDITORIA
    </footer>
  </div>

  <div v-else class="app-shell">
    <div
      v-if="mobileMenu"
      class="mobile-backdrop"
      @click="mobileMenu = false"
    ></div>
    <aside class="sidebar" :class="{ 'sidebar-open': mobileMenu }">
      <div class="sidebar-brand">
        <div class="brand-mark">RT<span class="brand-dot"></span></div>
        <div class="brand-title">
          <strong>Reforma<br />Tributária</strong><small>CONSOLE FISCAL</small>
        </div>
      </div>
      <div class="sidebar-section-label">PLATAFORMA</div>
      <nav aria-label="Navegação principal">
        <button
          v-for="item in navigation"
          :key="item.id"
          class="nav-item"
          :class="{ active: view === item.id }"
          type="button"
          :aria-current="view === item.id ? 'page' : undefined"
          @click="navigate(item.id)"
        >
          <AppIcon :name="item.icon" /><span>{{ item.label }}</span
          ><span
            v-if="item.id === 'queries' && activeQueries.length"
            class="nav-count"
            >{{ activeQueries.length }}</span
          >
        </button>
      </nav>
      <div class="sidebar-bottom">
        <div class="sidebar-rule"></div>
        <div class="sidebar-service">
          <div class="service-pulse" :class="{ off: !online }"></div>
          <div>
            <strong>{{ online ? "Navegador conectado" : "Sem conexão" }}</strong
            ><span>{{
              online ? "Atualização sob demanda" : "Consultas indisponíveis"
            }}</span>
          </div>
        </div>
        <div class="sidebar-user">
          <div class="avatar">
            {{ user?.username?.slice(0, 1).toUpperCase() || "?" }}
          </div>
          <div class="user-caption">
            <strong>{{ user?.username || "Carregando..." }}</strong
            ><span>{{ user?.role || "Usuário" }}</span>
          </div>
          <button
            type="button"
            title="Sair"
            aria-label="Sair"
            class="icon-button"
            @click="logout()"
          >
            <AppIcon name="logout" :size="18" />
          </button>
        </div>
      </div>
    </aside>

    <main class="main-content">
      <div class="topbar">
        <div class="topbar-left">
          <button
            type="button"
            class="icon-button mobile-menu"
            aria-label="Abrir menu"
            @click="mobileMenu = true"
          >
            <AppIcon name="menu" /></button
          ><span class="crumb">PLATAFORMA FISCAL</span
          ><span class="crumb-separator">/</span
          ><strong>{{ currentPageTitle }}</strong>
        </div>
        <div class="topbar-right">
          <span class="today">{{
            new Intl.DateTimeFormat("pt-BR", { dateStyle: "medium" }).format(
              new Date(),
            )
          }}</span
          ><span class="topbar-divider"></span
          ><span class="topbar-user">{{ user?.username || "—" }}</span>
        </div>
      </div>

      <div class="content-container">
        <div v-if="booting" class="loading-state" role="status">
          <span class="spinner"></span> Carregando empresas e capacidades...
        </div>
        <template v-else>
          <div v-if="!online" class="notice warning">
            <AppIcon name="warning" /><span
              >Sem conexão. A interface pode abrir em modo instalado, mas
              consultas e dados fiscais exigem acesso à API.</span
            >
          </div>
          <div v-if="fixtureMode" class="notice fixture">
            <AppIcon name="info" /><span
              ><strong>Modo fixture:</strong> as consultas usam dados simulados.
              Não use estes resultados como apuração fiscal.</span
            >
          </div>
          <div v-if="banner" class="notice" :class="banner.kind" role="status">
            <AppIcon
              :name="
                banner.kind === 'success'
                  ? 'check'
                  : banner.kind === 'error'
                    ? 'warning'
                    : 'info'
              "
            /><span>{{ banner.message }}</span
            ><button
              type="button"
              class="notice-close"
              aria-label="Fechar mensagem"
              @click="banner = null"
            >
              <AppIcon name="close" :size="15" />
            </button>
          </div>

          <div class="page-heading">
            <div>
              <div class="eyebrow">
                <span class="eyebrow-line"></span> ÁREA DE TRABALHO
              </div>
              <h1>{{ currentPageTitle }}</h1>
              <p v-if="view === 'overview'">
                Acompanhe suas consultas e a origem dos dados em um só lugar.
              </p>
              <p v-else-if="view === 'queries'">
                Solicitações incrementais, estados e tíquetes da Apuração
                Assistida da CBS.
              </p>
              <p v-else-if="view === 'credits'">
                Crédito a apropriar, apropriado e utilizado em campos distintos.
              </p>
              <p v-else-if="view === 'movements'">
                Débitos, pagamentos e recolhimentos reportados pela origem.
              </p>
              <p v-else-if="view === 'documents'">
                XMLs fiscais coletados e preservados por empresa, com integridade verificada no download.
              </p>
              <p v-else-if="view === 'agents'">
                Instalações locais autorizadas a enviar os XMLs do cliente.
              </p>
              <p v-else-if="view === 'reconciliation'">
                Compare documentos recebidos com registros oficiais disponíveis.
              </p>
              <p v-else-if="view === 'finance_controls'">
                Registre evidências em rascunho e parcelas comerciais sem alterar a apuração oficial.
              </p>
              <p v-else>
                Documentos, proveniência e integridade dos retornos consultados.
              </p>
            </div>
            <div v-if="companyId" class="heading-action">
              <button
                class="btn secondary"
                :disabled="loading"
                type="button"
                @click="refreshCurrent"
              >
                <AppIcon name="refresh" :size="16" />
                {{ loading ? "Atualizando..." : "Atualizar dados" }}
              </button>
            </div>
          </div>

          <div class="company-toolbar">
            <div class="company-symbol">
              <AppIcon name="building" :size="21" />
            </div>
            <div class="company-info">
              <span>EMPRESA SELECIONADA</span
              ><strong>{{
                selectedCompany?.name || "Nenhuma empresa cadastrada"
              }}</strong>
            </div>
            <label class="sr-only" for="company-select"
              >Selecionar empresa</label
            >
            <div class="company-select-wrap">
              <select
                id="company-select"
                v-model="companyId"
                :disabled="!companies.length"
                @change="changeCompany"
              >
                <option v-if="!companies.length" value="">
                  Selecione uma empresa
                </option>
                <option
                  v-for="item in companies"
                  :key="item.id"
                  :value="item.id"
                >
                  {{ item.name }} · {{ item.cnpj }}
                </option></select
              ><AppIcon name="chevron" :size="17" />
            </div>
            <button
              v-if="isAdmin()"
              type="button"
              class="btn text-button"
              @click="showCompanyForm = !showCompanyForm"
            >
              <AppIcon name="plus" :size="17" /> Nova empresa
            </button>
          </div>
          <form
            v-if="showCompanyForm"
            class="company-form surface"
            @submit.prevent="addCompany"
          >
            <div>
              <label for="company-name">Razão social / nome</label
              ><input
                id="company-name"
                v-model="companyName"
                required
                maxlength="160"
                placeholder="Nome da empresa"
              />
            </div>
            <div>
              <label for="company-cnpj">CNPJ</label
              ><input
                id="company-cnpj"
                v-model="companyCnpj"
                required
                maxlength="18"
                placeholder="14 caracteres"
                autocapitalize="characters"
              />
            </div>
            <button
              class="btn primary"
              type="submit"
              :disabled="creatingCompany"
            >
              {{ creatingCompany ? "Salvando..." : "Cadastrar" }}</button
            ><button
              class="btn secondary"
              type="button"
              @click="showCompanyForm = false"
            >
              Cancelar
            </button>
          </form>

          <section v-if="!companyId" class="surface empty-state">
            <div class="empty-icon"><AppIcon name="building" :size="28" /></div>
            <h2>Comece pela empresa</h2>
            <p>
              Cadastre uma empresa autorizada para consultar seus recursos
              fiscais. O CNPJ é validado pela API.
            </p>
            <button
              v-if="isAdmin()"
              class="btn primary"
              type="button"
              @click="showCompanyForm = true"
            >
              <AppIcon name="plus" :size="17" /> Cadastrar empresa
            </button>
            <p v-else class="muted-small">
              Solicite o cadastro a um administrador.
            </p>
          </section>

          <template v-else-if="view === 'overview'">
            <div class="stats-grid">
              <div class="stat-card">
                <div class="stat-icon blue"><AppIcon name="queries" /></div>
                <span>CONSULTAS EXIBIDAS</span
                ><strong>{{ dataLoaded ? queries.length : "—" }}</strong
                ><small>Na página retornada pela API</small>
              </div>
              <div class="stat-card">
                <div class="stat-icon amber"><AppIcon name="clock" /></div>
                <span>EM ANDAMENTO</span
                ><strong>{{ dataLoaded ? activeQueries.length : "—" }}</strong
                ><small>Estados consultados a cada 12 s</small>
              </div>
              <div class="stat-card">
                <div class="stat-icon red"><AppIcon name="warning" /></div>
                <span>EXIGEM ATENÇÃO</span
                ><strong>{{ dataLoaded ? failedQueries.length : "—" }}</strong
                ><small>Falhas e envios incertos</small>
              </div>
              <div class="stat-card">
                <div class="stat-icon mint"><AppIcon name="audit" /></div>
                <span>REGISTROS EXIBIDOS</span
                ><strong>{{ dataLoaded ? records.length : "—" }}</strong
                ><small>{{
                  nextCursor
                    ? "Há mais registros para carregar"
                    : "Na página retornada pela API"
                }}</small>
              </div>
            </div>
            <div class="overview-grid">
              <section class="surface panel">
                <div class="panel-heading">
                  <div>
                    <div class="eyebrow">FLUXO DE TRABALHO</div>
                    <h2>Iniciar consulta CBS</h2>
                  </div>
                  <div class="panel-icon"><AppIcon name="arrow" /></div>
                </div>
                <p>
                  Selecione o recurso e o ambiente. A origem envia dados
                  incrementais; não há período arbitrário neste contrato.
                </p>
                <div class="quick-resource-grid">
                  <button
                    v-for="item in RESOURCES"
                    :key="item"
                    type="button"
                    class="quick-resource"
                    :disabled="!canQuery(capabilities, item, environment)"
                    @click="
                      resource = item;
                      navigate('queries');
                    "
                  >
                    <AppIcon
                      :name="
                        item === 'creditos'
                          ? 'credits'
                          : item === 'debitos'
                            ? 'queries'
                            : 'movements'
                      "
                      :size="18"
                    /><span>{{ RESOURCE_LABELS[item] }}</span
                    ><AppIcon name="arrow" :size="15" />
                  </button>
                </div>
                <button
                  type="button"
                  class="text-link"
                  @click="navigate('queries')"
                >
                  Ver todas as consultas <AppIcon name="arrow" :size="16" />
                </button>
              </section>
              <section class="surface panel operational">
                <div class="panel-heading">
                  <div>
                    <div class="eyebrow">ESTADO DA INTEGRAÇÃO</div>
                    <h2>Disponibilidade</h2>
                  </div>
                  <div class="panel-icon"><AppIcon name="shield" /></div>
                </div>
                <div class="integration-row">
                  <span>Provedor configurado</span
                  ><strong>{{
                    capabilities?.provider_mode === "receita"
                      ? "Receita Integra"
                      : "Fixture local"
                  }}</strong>
                </div>
                <div class="integration-row">
                  <span>Ambientes habilitados</span
                  ><strong>{{
                    capabilities?.environments
                      .map(formattedEnvironment)
                      .join(", ") || "—"
                  }}</strong>
                </div>
                <div class="integration-row">
                  <span>IBS</span
                  ><strong>{{
                    capabilities?.ibs?.enabled
                      ? "Habilitado"
                      : "Ainda indisponível"
                  }}</strong>
                </div>
                <div class="integration-row">
                  <span>Última atualização</span
                  ><strong>{{ formatDate(lastRefresh) }}</strong>
                </div>
                <p class="integration-note">
                  A disponibilidade efetiva é informada pela API. A tela não
                  presume homologação de serviços governamentais.
                </p>
              </section>
            </div>
            <section class="surface panel table-panel">
              <div class="panel-heading">
                <div>
                  <div class="eyebrow">ATIVIDADE</div>
                  <h2>Consultas recentes</h2>
                </div>
                <button
                  type="button"
                  class="text-link"
                  @click="navigate('queries')"
                >
                  Ver todas <AppIcon name="arrow" :size="16" />
                </button>
              </div>
              <div v-if="!queries.length" class="table-empty">
                {{
                  dataLoaded
                    ? "Nenhuma consulta registrada para esta empresa."
                    : "Dados ainda não carregados."
                }}
              </div>
              <div v-else class="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Recurso</th>
                      <th>Ambiente</th>
                      <th>Estado</th>
                      <th>Solicitada em</th>
                      <th><span class="sr-only">Abrir</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="item in queries.slice(0, 6)" :key="item.id">
                      <td>
                        <strong>{{ RESOURCE_LABELS[item.resource] }}</strong
                        ><span class="subcell">{{ item.id }}</span>
                      </td>
                      <td>{{ formattedEnvironment(item.environment) }}</td>
                      <td>
                        <span
                          class="status-pill"
                          :class="statusKind(item.status)"
                          ><span class="status-dot"></span
                          >{{ statusLabel(item.status) }}</span
                        >
                      </td>
                      <td>{{ formatDate(item.created_at) }}</td>
                      <td>
                        <button
                          type="button"
                          class="row-button"
                          :aria-label="`Abrir consulta ${item.id}`"
                          @click="inspectQuery(item)"
                        >
                          <AppIcon name="arrow" :size="17" />
                        </button>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </section>
          </template>

          <template v-else-if="view === 'queries'">
            <section class="surface panel coverage-panel">
              <div class="panel-heading">
                <div>
                  <div class="eyebrow">CONTINUIDADE DAS CONSULTAS</div>
                  <h2>
                    Cobertura incremental ·
                    {{ formattedEnvironment(environment) }}
                  </h2>
                </div>
                <AppIcon name="clock" :size="20" />
              </div>
              <div v-if="loadingCoverage" class="coverage-loading">
                Atualizando indicadores de cobertura...
              </div>
              <div
                v-else-if="coverageError"
                class="coverage-loading error-text"
              >
                Indicadores indisponíveis: {{ coverageError }}
              </div>
              <template v-else-if="coverage">
                <div v-if="coverageGaps.length" class="coverage-alert">
                  <AppIcon name="warning" :size="18" />
                  <span
                    >Lacuna superior a {{ coverage.incremental_max_days }} dias
                    em
                    {{
                      coverageGaps
                        .map((item) => RESOURCE_LABELS[item].toLowerCase())
                        .join(", ")
                    }}. A API não fornece recuperação histórica automática.
                    Revise o período manualmente.</span
                  >
                </div>
                <div class="coverage-grid">
                  <div
                    v-for="item in RESOURCES"
                    :key="item"
                    class="coverage-item"
                  >
                    <strong>{{ RESOURCE_LABELS[item] }}</strong>
                    <span
                      v-if="!canQuery(capabilities, item, environment)"
                      class="coverage-state neutral"
                      >Recurso não habilitado</span
                    >
                    <span
                      v-else-if="coverage.resources[item]?.first_query_limited"
                      class="coverage-state attention"
                      >Sem conclusão · primeira consulta limitada</span
                    >
                    <span
                      v-else-if="coverage.resources[item]?.gap_over_eight_days"
                      class="coverage-state danger"
                      >Lacuna &gt; 8 dias</span
                    >
                    <span
                      v-else-if="coverage.resources[item]?.needs_attention"
                      class="coverage-state attention"
                      >Atenção · janela próxima do limite</span
                    >
                    <span v-else class="coverage-state current"
                      >Última abertura dentro da janela</span
                    >
                    <small
                      >Última consulta concluída:
                      {{
                        formatDate(
                          coverage.resources[item]?.last_completed_opened_at,
                        )
                      }}</small
                    >
                    <small v-if="coverage.resources[item]?.age_days != null"
                      >Há {{ coverage.resources[item].age_days }} dia(s)</small
                    >
                  </div>
                </div>
                <p class="coverage-footnote">
                  Indicador calculado pela última abertura concluída por
                  recurso/CNPJ base. Ele não prova completude dos dados.
                  Backfill automático:
                  {{
                    coverage.has_automatic_backfill
                      ? "habilitado"
                      : "indisponível"
                  }}.
                </p>
              </template>
              <p v-else class="coverage-loading">
                Ainda não há indicador de cobertura disponível.
              </p>
            </section>
            <div class="work-grid">
              <section class="surface panel query-form-panel">
                <div class="eyebrow">NOVA SOLICITAÇÃO</div>
                <h2>Consultar recurso</h2>
                <p>
                  O processamento é assíncrono. A solicitação mantém a mesma
                  chave de idempotência se a conexão falhar antes da resposta.
                </p>
                <form @submit.prevent="startQuery">
                  <label for="resource-select">Recurso da CBS</label
                  ><select id="resource-select" v-model="resource">
                    <option v-for="item in RESOURCES" :key="item" :value="item">
                      {{ RESOURCE_LABELS[item] }}
                    </option></select
                  ><label for="environment-select">Ambiente</label
                  ><select id="environment-select" v-model="environment">
                    <option
                      v-for="item in capabilities?.environments || []"
                      :key="item"
                      :value="item"
                    >
                      {{ formattedEnvironment(item) }}
                    </option>
                  </select>
                  <div
                    v-if="environment === 'fixture'"
                    class="form-info fixture-info"
                  >
                    <AppIcon name="info" :size="17" /> Retorno simulado; sem
                    valor fiscal.
                  </div>
                  <div v-if="!resourceAvailable" class="form-info blocked-info">
                    <AppIcon name="warning" :size="17" /> {{ queryReason }}
                  </div>
                  <div class="form-info">
                    <AppIcon name="clock" :size="17" /> Consulta incremental.
                    Não é possível definir um intervalo arbitrário de datas.
                  </div>
                  <div class="form-info">
                    <AppIcon name="warning" :size="17" /> Para débitos e
                    créditos, a janela incremental oficial alcança até 8 dias; a
                    primeira consulta considera o mês corrente. Não conte com a
                    API para recuperar períodos históricos perdidos.
                  </div>
                  <div v-if="!isAdmin()" class="form-info blocked-info">
                    <AppIcon name="lock" :size="17" /> Seu perfil pode consultar
                    registros, mas somente administradores iniciam solicitações.
                  </div>
                  <button
                    type="submit"
                    class="btn primary wide"
                    :disabled="
                      creatingQuery ||
                      !resourceAvailable ||
                      !online ||
                      !isAdmin()
                    "
                  >
                    <AppIcon name="arrow" :size="17" />
                    {{
                      creatingQuery
                        ? "Registrando solicitação..."
                        : "Iniciar consulta"
                    }}
                  </button>
                </form>
              </section>
              <section class="surface panel query-detail">
                <div class="panel-heading">
                  <div>
                    <div class="eyebrow">ACOMPANHAMENTO</div>
                    <h2>
                      {{
                        selectedQuery
                          ? "Detalhes da consulta"
                          : "Selecione uma consulta"
                      }}
                    </h2>
                  </div>
                  <span
                    v-if="selectedQuery"
                    class="status-pill"
                    :class="statusKind(selectedQuery.status)"
                    ><span class="status-dot"></span
                    >{{ statusLabel(selectedQuery.status) }}</span
                  >
                </div>
                <template v-if="selectedQuery"
                  ><div class="detail-grid">
                    <div>
                      <span>ID</span><code>{{ selectedQuery.id }}</code>
                    </div>
                    <div>
                      <span>Recurso</span
                      ><strong>{{
                        RESOURCE_LABELS[selectedQuery.resource]
                      }}</strong>
                    </div>
                    <div>
                      <span>Ambiente</span
                      ><strong>{{
                        formattedEnvironment(selectedQuery.environment)
                      }}</strong>
                    </div>
                    <div>
                      <span>Tíquete governamental</span
                      ><code>{{
                        selectedQuery.ticket || "Ainda não atribuído"
                      }}</code>
                    </div>
                    <div>
                      <span>Criada em</span
                      ><strong>{{
                        formatDate(selectedQuery.created_at)
                      }}</strong>
                    </div>
                    <div>
                      <span>Atualizada em</span
                      ><strong>{{
                        formatDate(selectedQuery.updated_at)
                      }}</strong>
                    </div>
                    <div v-if="selectedQuery.checkpoint">
                      <span>Checkpoint</span
                      ><strong>{{ selectedQuery.checkpoint }}</strong>
                    </div>
                    <div v-if="selectedQuery.opening_started_at">
                      <span>Abertura iniciada em</span
                      ><strong>{{
                        formatDate(selectedQuery.opening_started_at)
                      }}</strong>
                    </div>
                    <div v-if="selectedQuery.next_poll_at">
                      <span>Próxima checagem pelo worker</span
                      ><strong>{{
                        formatDate(selectedQuery.next_poll_at)
                      }}</strong>
                    </div>
                    <div v-if="selectedQuery.attempts != null">
                      <span>Checagens registradas</span
                      ><strong>{{ selectedQuery.attempts }}</strong>
                    </div>
                    <div v-if="selectedQuery.raw_sha256">
                      <span>SHA-256 do retorno</span
                      ><code class="wrap-code">{{
                        selectedQuery.raw_sha256
                      }}</code>
                    </div>
                  </div>
                  <div
                    v-if="selectedQuery.error || selectedQuery.error_message"
                    class="detail-alert"
                  >
                    <AppIcon name="warning" :size="18" />
                    {{ selectedQuery.error_message || selectedQuery.error }}
                  </div>
                  <div
                    v-if="selectedQuery.status === 'uncertain'"
                    class="detail-alert"
                  >
                    <AppIcon name="warning" :size="18" /> A abertura pode ter
                    sido aceita sem tíquete confirmado. Investigue antes de
                    reenviar; não há repetição automática.
                  </div>
                  <form
                    v-if="selectedQuery.status === 'uncertain' && isAdmin()"
                    class="resolve-form"
                    @submit.prevent="resolveUncertain"
                  >
                    <h3>Resolução manual auditada</h3>
                    <label for="resolve-action">Ação</label>
                    <select
                      id="resolve-action"
                      v-model="resolveAction"
                      required
                    >
                      <option value="" disabled>Selecione uma ação</option>
                      <option value="attach_ticket">
                        Vincular tíquete confirmado oficialmente
                      </option>
                      <option value="mark_failed">
                        Encerrar como falha após investigação
                      </option>
                    </select>
                    <template v-if="resolveAction">
                      <label
                        v-if="resolveAction === 'attach_ticket'"
                        for="resolve-ticket"
                        >Tíquete confirmado</label
                      >
                      <input
                        v-if="resolveAction === 'attach_ticket'"
                        id="resolve-ticket"
                        v-model="resolveTicket"
                        required
                        placeholder="Identificador do tíquete oficial"
                      />
                      <label for="resolve-note"
                        >Justificativa para auditoria (mín. 10
                        caracteres)</label
                      >
                      <textarea
                        id="resolve-note"
                        v-model="resolveNote"
                        required
                        minlength="10"
                        rows="3"
                        placeholder="Registre como a situação foi confirmada"
                      ></textarea>
                      <label class="confirmation-label"
                        ><input
                          v-model="resolutionConfirmed"
                          type="checkbox"
                          required
                        /><span>{{
                          resolveAction === "attach_ticket"
                            ? "Confirmei o tíquete no canal oficial antes de vinculá-lo."
                            : "Investiguei a abertura e confirmo o encerramento como falha."
                        }}</span></label
                      >
                      <button
                        type="submit"
                        class="btn secondary"
                        :disabled="
                          resolving ||
                          !resolutionConfirmed ||
                          resolveNote.trim().length < 10 ||
                          (resolveAction === 'attach_ticket' &&
                            !resolveTicket.trim())
                        "
                      >
                        {{ resolving ? "Registrando..." : "Registrar decisão" }}
                      </button>
                    </template>
                  </form>
                  <div class="detail-actions">
                    <button
                      type="button"
                      class="btn secondary"
                      @click="inspectQuery(selectedQuery)"
                    >
                      <AppIcon name="refresh" :size="16" /> Atualizar estado</button
                    ><button
                      v-if="selectedQuery.raw_sha256"
                      type="button"
                      class="btn secondary"
                      :disabled="downloadingRaw"
                      @click="downloadRaw"
                    >
                      <AppIcon name="audit" :size="16" />
                      {{
                        downloadingRaw
                          ? "Verificando..."
                          : "Baixar retorno bruto (SHA-256)"
                      }}
                    </button>
                  </div></template
                >
                <div v-else class="selection-empty">
                  <AppIcon name="queries" :size="28" />
                  <p>
                    Os estados e tíquetes aparecem aqui ao selecionar uma linha.
                  </p>
                </div>
              </section>
            </div>
            <section class="surface panel table-panel">
              <div class="panel-heading">
                <div>
                  <div class="eyebrow">HISTÓRICO DA EMPRESA</div>
                  <h2>Solicitações</h2>
                </div>
                <span class="small-muted"
                  >{{ queries.length }} na página atual</span
                >
              </div>
              <div v-if="!queries.length" class="table-empty">
                {{
                  dataLoaded
                    ? "Nenhuma consulta iniciada para esta empresa."
                    : "Dados ainda não carregados."
                }}
              </div>
              <div v-else class="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Recurso / ID</th>
                      <th>Ambiente</th>
                      <th>Estado</th>
                      <th>Tíquete</th>
                      <th>Atualização</th>
                      <th><span class="sr-only">Ação</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="item in queries"
                      :key="item.id"
                      :class="{ 'selected-row': selectedQuery?.id === item.id }"
                    >
                      <td>
                        <strong>{{ RESOURCE_LABELS[item.resource] }}</strong
                        ><span class="subcell">{{ item.id }}</span>
                      </td>
                      <td>{{ formattedEnvironment(item.environment) }}</td>
                      <td>
                        <span
                          class="status-pill"
                          :class="statusKind(item.status)"
                          ><span class="status-dot"></span
                          >{{ statusLabel(item.status) }}</span
                        >
                      </td>
                      <td class="mono">{{ item.ticket || "—" }}</td>
                      <td>
                        {{ formatDate(item.updated_at || item.created_at) }}
                      </td>
                      <td>
                        <button
                          class="row-button"
                          type="button"
                          :aria-label="`Inspecionar consulta ${item.id}`"
                          @click="inspectQuery(item)"
                        >
                          <AppIcon name="arrow" :size="17" />
                        </button>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div v-if="nextQueryCursor" class="load-more">
                <button
                  type="button"
                  class="btn secondary"
                  :disabled="loadingMoreQueries"
                  @click="loadMoreQueries"
                >
                  {{
                    loadingMoreQueries
                      ? "Carregando..."
                      : "Carregar mais consultas"
                  }}
                </button>
              </div>
            </section></template
          >

          <template v-else-if="view === 'credits'"
            ><div class="context-note">
              <AppIcon name="info" :size="18" /><span
                >Crédito a apropriar, apropriado e utilizado são valores
                diferentes. Nenhuma baixa comercial libera crédito
                automaticamente nesta tela.</span
              >
            </div>
            <div
              class="segmented-control"
              role="group"
              aria-label="Categoria de crédito"
            >
              <button
                type="button"
                :class="{ selected: creditView === 'government_reported' }"
                @click="creditView = 'government_reported'"
              >
                Reportados pela origem</button
              ><button
                type="button"
                :class="{ selected: creditView === 'presumed_stock' }"
                @click="creditView = 'presumed_stock'"
              >
                Estoque presumido</button
              ><button
                type="button"
                :class="{ selected: creditView === 'unclassified' }"
                @click="creditView = 'unclassified'"
              >
                Sem classificação
              </button>
            </div>
            <section
              v-if="creditView === 'presumed_stock'"
              class="surface panel stock-unavailable"
            >
              <div class="empty-icon">
                <AppIcon name="credits" :size="26" />
              </div>
              <div class="eyebrow">CONTROLE PRÓPRIO</div>
              <h2>Estoque presumido ainda não implementado</h2>
              <p>
                Esta versão não possui cadastro, apuração nem controle de
                parcelas do crédito presumido sobre o estoque de abertura. A
                consulta CBS comum não retorna este controle e seus registros
                não são reclassificados automaticamente.
              </p>
            </section>
            <section v-else class="surface panel table-panel">
              <div class="panel-heading">
                <div>
                  <div class="eyebrow">REGISTROS RECEBIDOS</div>
                  <h2>
                    {{
                      creditView === "unclassified"
                        ? "Classificação pendente"
                        : "Créditos reportados"
                    }}
                  </h2>
                </div>
                <span class="small-muted"
                  >{{ visibleCredits.length }} na página carregada</span
                >
              </div>
              <div v-if="!visibleCredits.length" class="table-empty">
                {{
                  dataLoaded
                    ? "Nenhum registro desta categoria na página carregada."
                    : "Dados ainda não carregados."
                }}
              </div>
              <div v-else class="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Referência</th>
                      <th>Período</th>
                      <th>A apropriar</th>
                      <th>Apropriado</th>
                      <th>Utilizado</th>
                      <th><span class="sr-only">Origem</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="item in visibleCredits" :key="item.id">
                      <td>
                        <strong class="reference">{{
                          recordReference(item)
                        }}</strong
                        ><span class="subcell">{{ item.id }}</span>
                      </td>
                      <td>{{ item.period || "—" }}</td>
                      <td class="amount">
                        {{ formatBRL(item.credit_to_appropriate) }}
                      </td>
                      <td class="amount">
                        {{ formatBRL(item.credit_appropriated) }}
                      </td>
                      <td class="amount">{{ formatBRL(item.credit_used) }}</td>
                      <td>
                        <button
                          class="row-button"
                          type="button"
                          :aria-label="`Ver origem do registro ${item.id}`"
                          @click="openRecord(item)"
                        >
                          <AppIcon name="arrow" :size="17" />
                        </button>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div v-if="nextCursor" class="load-more">
                <button
                  type="button"
                  class="btn secondary"
                  :disabled="loadingMore"
                  @click="loadMoreRecords"
                >
                  {{
                    loadingMore ? "Carregando..." : "Carregar mais registros"
                  }}
                </button>
              </div>
            </section></template
          >

          <template v-else-if="view === 'movements'"
            ><div class="context-note">
              <AppIcon name="info" :size="18" /><span
                >Pagamentos comerciais e extinção do débito tributário não são
                equivalentes. Confira os dados recebidos da origem para cada
                operação.</span
              >
            </div>
            <div
              class="segmented-control"
              role="group"
              aria-label="Tipo de movimentação"
            >
              <button
                v-for="item in [
                  'debitos',
                  'pagamentos',
                  'recolhimentos',
                ] as Resource[]"
                :key="item"
                type="button"
                :class="{ selected: movementResource === item }"
                @click="movementResource = item"
              >
                {{ RESOURCE_LABELS[item] }}
              </button>
            </div>
            <section class="surface panel table-panel">
              <div class="panel-heading">
                <div>
                  <div class="eyebrow">MOVIMENTAÇÃO FISCAL</div>
                  <h2>{{ RESOURCE_LABELS[movementResource] }}</h2>
                </div>
                <span class="small-muted"
                  >{{ visibleMovements.length }} na página carregada</span
                >
              </div>
              <div v-if="!visibleMovements.length" class="table-empty">
                Nenhum registro de
                {{ RESOURCE_LABELS[movementResource].toLowerCase() }} na página
                carregada. Verifique a disponibilidade do recurso e as
                consultas.
              </div>
              <div v-else class="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Referência</th>
                      <th>Período</th>
                      <th>Valor informado</th>
                      <th>Consulta</th>
                      <th><span class="sr-only">Origem</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="item in visibleMovements" :key="item.id">
                      <td>
                        <strong class="reference">{{
                          recordReference(item)
                        }}</strong
                        ><span class="subcell">{{ item.id }}</span>
                      </td>
                      <td>{{ item.period || "—" }}</td>
                      <td class="amount">{{ formatBRL(item.amount) }}</td>
                      <td class="mono">{{ item.query_id }}</td>
                      <td>
                        <button
                          class="row-button"
                          type="button"
                          :aria-label="`Ver origem do registro ${item.id}`"
                          @click="openRecord(item)"
                        >
                          <AppIcon name="arrow" :size="17" />
                        </button>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div v-if="nextCursor" class="load-more">
                <button
                  type="button"
                  class="btn secondary"
                  :disabled="loadingMore"
                  @click="loadMoreRecords"
                >
                  {{
                    loadingMore ? "Carregando..." : "Carregar mais registros"
                  }}
                </button>
              </div>
            </section></template
          >

          <template v-else-if="view === 'audit'"
            ><div class="audit-grid">
              <section class="surface panel audit-list">
                <div class="panel-heading">
                  <div>
                    <div class="eyebrow">PROVENIÊNCIA</div>
                    <h2>Registros recebidos</h2>
                  </div>
                  <span class="small-muted">{{ records.length }} exibidos</span>
                </div>
                <div v-if="!records.length" class="table-empty">
                  {{
                    dataLoaded
                      ? "Nenhum registro disponível para inspeção."
                      : "Dados ainda não carregados."
                  }}
                </div>
                <div v-else class="audit-items">
                  <button
                    v-for="item in records"
                    :key="item.id"
                    type="button"
                    class="audit-item"
                    :class="{ selected: selectedRecord?.id === item.id }"
                    @click="selectedRecord = item"
                  >
                    <span class="audit-item-icon"
                      ><AppIcon name="audit" :size="17" /></span
                    ><span
                      ><strong>{{ recordReference(item) }}</strong
                      ><small
                        >{{ RESOURCE_LABELS[item.resource] }} ·
                        {{ item.period || "Período não informado" }}</small
                      ></span
                    ><AppIcon name="arrow" :size="16" />
                  </button>
                </div>
                <div v-if="nextCursor" class="load-more">
                  <button
                    type="button"
                    class="btn secondary"
                    :disabled="loadingMore"
                    @click="loadMoreRecords"
                  >
                    {{ loadingMore ? "Carregando..." : "Carregar mais" }}
                  </button>
                </div>
              </section>
              <section class="surface panel audit-detail">
                <div class="panel-heading">
                  <div>
                    <div class="eyebrow">EVIDÊNCIA DO RETORNO</div>
                    <h2>
                      {{
                        selectedRecord
                          ? "Detalhes do registro"
                          : "Selecione um registro"
                      }}
                    </h2>
                  </div>
                  <span v-if="selectedRecord" class="status-pill neutral">{{
                    RESOURCE_LABELS[selectedRecord.resource]
                  }}</span>
                </div>
                <template v-if="selectedRecord"
                  ><div class="detail-grid">
                    <div>
                      <span>ID do registro</span
                      ><code>{{ selectedRecord.id }}</code>
                    </div>
                    <div>
                      <span>ID da consulta</span
                      ><code>{{ selectedRecord.query_id }}</code>
                    </div>
                    <div>
                      <span>Chave de origem</span
                      ><code class="wrap-code">{{
                        selectedRecord.source_key || "—"
                      }}</code>
                    </div>
                    <div>
                      <span>Período</span
                      ><strong>{{ selectedRecord.period || "—" }}</strong>
                    </div>
                    <div v-if="selectedRecord.resource === 'creditos'">
                      <span>Categoria de crédito</span
                      ><strong>{{
                        selectedRecord.credit_category || "Não classificada"
                      }}</strong>
                    </div>
                    <div>
                      <span>SHA-256 do retorno</span
                      ><code class="wrap-code">{{
                        receiptHash(selectedRecord)
                      }}</code>
                    </div>
                  </div>
                  <div class="raw-title">METADADOS DE PROVENIÊNCIA</div>
                  <pre class="raw-box">{{
                    prettyJson(selectedRecord.provenance)
                  }}</pre>
                  <div class="raw-title">
                    DADOS BRUTOS DISPONIBILIZADOS PELA API
                  </div>
                  <pre class="raw-box">{{
                    prettyJson(selectedRecord.raw)
                  }}</pre>
                  <p class="audit-footnote">
                    O JSON exibido corresponde ao registro entregue pela API. O
                    SHA-256 acima refere-se ao arquivo bruto da consulta.
                  </p>
                  <button
                    type="button"
                    class="btn secondary"
                    @click="inspectRecordQuery"
                  >
                    <AppIcon name="queries" :size="16" /> Abrir consulta e
                    arquivo bruto
                  </button></template
                >
                <div v-else class="selection-empty">
                  <AppIcon name="search" :size="28" />
                  <p>
                    Selecione um registro para consultar seus dados e sua
                    proveniência.
                  </p>
                </div>
              </section>
            </div></template
          >
          <FiscalWorkspace
            v-else-if="view === 'documents' || view === 'agents' || view === 'reconciliation'"
            :key="`${view}:${companyId}`"
            :api="api"
            :company-id="companyId"
            :view="view"
            :admin="isAdmin()"
            :refresh-trigger="workspaceRefresh"
          />
          <FinanceControls
            v-else-if="view === 'finance_controls'"
            :key="companyId"
            :api="api"
            :company-id="companyId"
            :admin="isAdmin()"
            :refresh-trigger="workspaceRefresh"
          />
          <div v-if="companyId" class="page-footer">
            <span
              >Os resultados refletem apenas os dados recebidos pela API e a
              página carregada.</span
            ><span>Última atualização: {{ formatDate(lastRefresh) }}</span>
          </div>
        </template>
      </div>
    </main>
  </div>
</template>
