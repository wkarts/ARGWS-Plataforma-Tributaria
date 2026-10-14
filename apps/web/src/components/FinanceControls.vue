<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import type { ApiClient } from "../api.ts";
import { formatBRL, formatDate } from "../logic.ts";
import type { CommercialInstallment, StockCreditClaim } from "../types.ts";
import AppIcon from "./AppIcon.vue";

const props = defineProps<{
  api: ApiClient;
  companyId: string;
  admin: boolean;
  refreshTrigger: number;
}>();

const claims = ref<StockCreditClaim[]>([]);
const installments = ref<CommercialInstallment[]>([]);
const claimCursor = ref<string | null>(null);
const installmentCursor = ref<string | null>(null);
const loading = ref(false);
const moreBusy = ref<"claims" | "installments" | null>(null);
const saving = ref<"claim" | "installment" | null>(null);
const error = ref("");
const success = ref("");
const inventoryDate = ref("");
const expectedAmount = ref("");
const evidenceRef = ref("");
const documentRef = ref("");
const installmentNo = ref(1);
const installmentAmount = ref("");
const paidAt = ref("");
let generation = 0;

function message(value: unknown): string {
  return value instanceof Error ? value.message : "Ocorreu um erro inesperado.";
}

function exactAmount(raw: string): string {
  const amount = raw.trim().replace(",", ".");
  if (!/^\d{1,16}(?:\.\d{1,2})?$/.test(amount) || !/[1-9]/.test(amount))
    throw new Error("Informe um valor positivo, sem separador de milhar, com até duas casas decimais.");
  return amount;
}

function inventoryLabel(value: string): string {
  const parts = /^([0-9]{4})-([0-9]{2})-([0-9]{2})$/.exec(value);
  return parts ? `${parts[3]}/${parts[2]}/${parts[1]}` : value;
}

async function load() {
  const current = ++generation;
  const company = props.companyId;
  if (!company) return;
  loading.value = true;
  error.value = "";
  try {
    const [claimPage, installmentPage] = await Promise.all([
      props.api.stockCreditClaims(company),
      props.api.commercialInstallments(company),
    ]);
    if (current !== generation) return;
    claims.value = claimPage.items;
    installments.value = installmentPage.items;
    claimCursor.value = claimPage.next_cursor || null;
    installmentCursor.value = installmentPage.next_cursor || null;
  } catch (cause) {
    if (current === generation) error.value = message(cause);
  } finally {
    if (current === generation) loading.value = false;
  }
}

async function loadMore(which: "claims" | "installments") {
  const cursor = which === "claims" ? claimCursor.value : installmentCursor.value;
  if (!cursor || moreBusy.value || !props.companyId) return;
  moreBusy.value = which;
  const current = generation;
  const company = props.companyId;
  error.value = "";
  try {
    if (which === "claims") {
      const page = await props.api.stockCreditClaims(company, cursor);
      if (current !== generation) return;
      const seen = new Set(claims.value.map((item) => item.id));
      claims.value.push(...page.items.filter((item) => !seen.has(item.id)));
      claimCursor.value = page.next_cursor || null;
    } else {
      const page = await props.api.commercialInstallments(company, cursor);
      if (current !== generation) return;
      const seen = new Set(installments.value.map((item) => item.id));
      installments.value.push(...page.items.filter((item) => !seen.has(item.id)));
      installmentCursor.value = page.next_cursor || null;
    }
  } catch (cause) {
    if (current === generation) error.value = message(cause);
  } finally {
    moreBusy.value = null;
  }
}

async function saveClaim() {
  if (!props.admin || saving.value || !props.companyId) return;
  const current = generation;
  const company = props.companyId;
  error.value = "";
  success.value = "";
  try {
    const amount = exactAmount(expectedAmount.value);
    if (!inventoryDate.value || !evidenceRef.value.trim())
      throw new Error("Informe a data de inventário e a referência da evidência.");
    saving.value = "claim";
    const created = await props.api.createStockCreditClaim(company, inventoryDate.value, amount, evidenceRef.value.trim());
    if (current !== generation) return;
    claims.value.unshift(created);
    inventoryDate.value = "";
    expectedAmount.value = "";
    evidenceRef.value = "";
    success.value = "Candidato de crédito salvo como rascunho. Nenhum valor foi apropriado ou utilizado.";
  } catch (cause) {
    if (current === generation) error.value = message(cause);
  } finally {
    saving.value = null;
  }
}

async function saveInstallment() {
  if (!props.admin || saving.value || !props.companyId) return;
  const current = generation;
  const company = props.companyId;
  error.value = "";
  success.value = "";
  try {
    const amount = exactAmount(installmentAmount.value);
    if (!documentRef.value.trim() || !Number.isInteger(installmentNo.value) || installmentNo.value < 1 || installmentNo.value > 10000)
      throw new Error("Informe a referência e um número de parcela entre 1 e 10.000.");
    const time = paidAt.value ? new Date(paidAt.value) : null;
    if (time && Number.isNaN(time.getTime())) throw new Error("Data e hora do pagamento inválidas.");
    saving.value = "installment";
    const created = await props.api.createCommercialInstallment(company, documentRef.value.trim(), installmentNo.value, amount, time?.toISOString() || null);
    if (current !== generation) return;
    installments.value.unshift(created);
    documentRef.value = "";
    installmentNo.value = 1;
    installmentAmount.value = "";
    paidAt.value = "";
    success.value = "Parcela comercial registrada. Isso não comprova extinção do débito tributário nem libera crédito.";
  } catch (cause) {
    if (current === generation) error.value = message(cause);
  } finally {
    saving.value = null;
  }
}

watch(() => props.companyId, () => {
  claims.value = [];
  installments.value = [];
  claimCursor.value = null;
  installmentCursor.value = null;
  success.value = "";
  void load();
}, { immediate: true });
watch(() => props.refreshTrigger, () => { void load(); });
onBeforeUnmount(() => { generation++; });
</script>

<template>
  <div class="workspace finance-workspace">
    <div class="context-note">
      <AppIcon name="info" :size="18" />
      <span>Estes registros manuais organizam evidências para revisão. Não alteram o retorno oficial da CBS, não calculam crédito presumido e não fecham a apuração.</span>
    </div>
    <div v-if="error" class="notice error" role="alert"><AppIcon name="warning" :size="17" /><span>{{ error }}</span></div>
    <div v-if="success" class="notice success" role="status"><AppIcon name="check" :size="17" /><span>{{ success }}</span></div>

    <section class="surface panel finance-panel">
      <div class="panel-heading"><div><div class="eyebrow">CANDIDATOS A CRÉDITO</div><h2>Estoque em rascunho</h2></div></div>
      <p>Informe o valor esperado e a referência do inventário para análise posterior. O cadastro não verifica a evidência, não apropria nem utiliza crédito.</p>
      <form v-if="admin" class="finance-form" @submit.prevent="saveClaim">
        <div><label for="stock-date">Data do inventário</label><input id="stock-date" v-model="inventoryDate" type="date" required /></div>
        <div><label for="stock-amount">Valor esperado (R$)</label><input id="stock-amount" v-model="expectedAmount" type="text" inputmode="decimal" maxlength="19" placeholder="1234,56" required /></div>
        <div class="finance-wide"><label for="stock-evidence">Referência da evidência</label><input id="stock-evidence" v-model="evidenceRef" type="text" maxlength="255" placeholder="Inventário 2026 / arquivo e localização" required /></div>
        <button class="btn primary" type="submit" :disabled="!!saving || loading">{{ saving === "claim" ? "Salvando..." : "Salvar rascunho" }}</button>
      </form>
      <p v-else class="small-muted">Somente administradores podem criar registros; a consulta permanece disponível.</p>
      <div v-if="loading" class="table-empty">Carregando candidatos...</div>
      <div v-else-if="!claims.length" class="table-empty">Nenhum candidato de crédito registrado nesta empresa.</div>
      <div v-else class="table-scroll"><table><thead><tr><th>Inventário</th><th>Valor esperado</th><th>Apropriado</th><th>Utilizado</th><th>Evidência informada</th><th>Situação</th></tr></thead><tbody>
        <tr v-for="item in claims" :key="item.id"><td>{{ inventoryLabel(item.inventory_date) }}</td><td class="amount">{{ formatBRL(item.expected_amount) }}</td><td class="amount">{{ formatBRL(item.appropriated_amount) }}</td><td class="amount">{{ formatBRL(item.used_amount) }}</td><td class="finance-reference">{{ item.evidence_ref }}</td><td><span class="status-pill neutral"><span class="status-dot"></span>{{ item.status === "draft" ? "Rascunho não verificado" : item.status }}</span></td></tr>
      </tbody></table></div>
      <div v-if="claimCursor" class="load-more"><button class="btn secondary" type="button" :disabled="!!moreBusy" @click="loadMore('claims')">{{ moreBusy === "claims" ? "Carregando..." : "Carregar mais candidatos" }}</button></div>
    </section>

    <section class="surface panel finance-panel">
      <div class="panel-heading"><div><div class="eyebrow">CONTROLE COMERCIAL</div><h2>Parcelas de documentos</h2></div></div>
      <p>O pagamento contratual é acompanhado separadamente da quitação tributária. Referência e data informadas pelo operador não constituem prova fiscal.</p>
      <form v-if="admin" class="finance-form" @submit.prevent="saveInstallment">
        <div class="finance-wide"><label for="installment-document">Referência do documento</label><input id="installment-document" v-model="documentRef" type="text" maxlength="255" placeholder="Chave DF-e ou referência comercial" required /></div>
        <div><label for="installment-number">Número da parcela</label><input id="installment-number" v-model.number="installmentNo" type="number" min="1" max="10000" step="1" required /></div>
        <div><label for="installment-amount">Valor (R$)</label><input id="installment-amount" v-model="installmentAmount" type="text" inputmode="decimal" maxlength="19" placeholder="100,00" required /></div>
        <div><label for="installment-paid">Pagamento informado (opcional)</label><input id="installment-paid" v-model="paidAt" type="datetime-local" /></div>
        <button class="btn primary" type="submit" :disabled="!!saving || loading">{{ saving === "installment" ? "Salvando..." : "Registrar parcela" }}</button>
      </form>
      <p v-else class="small-muted">Somente administradores podem criar registros; a consulta permanece disponível.</p>
      <div v-if="loading" class="table-empty">Carregando parcelas...</div>
      <div v-else-if="!installments.length" class="table-empty">Nenhuma parcela comercial registrada nesta empresa.</div>
      <div v-else class="table-scroll"><table><thead><tr><th>Documento</th><th>Parcela</th><th>Valor</th><th>Pagamento informado</th><th>Natureza</th></tr></thead><tbody>
        <tr v-for="item in installments" :key="item.id"><td class="finance-reference">{{ item.document_ref }}</td><td>{{ item.installment_no }}</td><td class="amount">{{ formatBRL(item.amount) }}</td><td>{{ formatDate(item.paid_at) }}</td><td>Comercial · sem prova de extinção tributária</td></tr>
      </tbody></table></div>
      <div v-if="installmentCursor" class="load-more"><button class="btn secondary" type="button" :disabled="!!moreBusy" @click="loadMore('installments')">{{ moreBusy === "installments" ? "Carregando..." : "Carregar mais parcelas" }}</button></div>
    </section>
  </div>
</template>
