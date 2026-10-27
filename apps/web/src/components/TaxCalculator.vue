<script setup lang="ts">
import { onBeforeUnmount, reactive, ref, watch } from "vue";
import type { ApiClient } from "../api.ts";
import type { TaxCalculation, TaxCalculationStatus, TaxCalculationInput, TaxRegime } from "../types.ts";
import { formatDate } from "../logic.ts";

const props = defineProps<{ api: ApiClient; companyId: string; refreshTrigger: number }>();
type Entry = { ncm: string; nbs: string; cst: string; cClassTrib: string; baseCalculo: string; quantidade: string; unidade: string };
const emptyEntry = (): Entry => ({
  ncm: "", nbs: "", cst: "", cClassTrib: "", baseCalculo: "", quantidade: "", unidade: "",
});
const regime = ref<TaxRegime>("regular");
const advanced = ref(false);
const values = reactive({ issuedAt: "", uf: "", municipality: "", items: [emptyEntry()] });
const jsonText = ref("");
const status = ref<TaxCalculationStatus | null>(null);
const history = ref<TaxCalculation[]>([]);
const cursor = ref<string | null>(null);
const selected = ref<TaxCalculation | null>(null);
const loading = ref(false);
const saving = ref(false);
const loadingMore = ref(false);
const error = ref("");
const success = ref("");
let generation = 0;

function errorText(cause: unknown): string {
  return cause instanceof Error ? cause.message : "Falha ao consultar o motor tributário.";
}

function decimal(raw: string, field: string, decimals: number): number {
  const source = raw.trim().replace(",", ".");
  const pattern = decimals === 2
    ? /^(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,2})?$/
    : /^(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,6})?$/;
  if (!pattern.test(source)) throw new Error(field + ": valor decimal inválido.");
  const number = Number(source);
  if (!Number.isFinite(number)) throw new Error(field + ": valor não finito.");
  return number;
}

function buildRegular(): Record<string, unknown> {
  const when = new Date(values.issuedAt);
  if (!values.issuedAt || !Number.isFinite(when.valueOf())) throw new Error("Data de emissão inválida.");
  const uf = values.uf.trim().toUpperCase();
  if (!/^[A-Z]{2}$/.test(uf)) throw new Error("Informe a UF da operação.");
  if (!/^[0-9]{7}$/.test(values.municipality.trim())) throw new Error("Informe o código IBGE de sete dígitos.");
  const itens = values.items.map((item, index) => {
    if (!item.cst.trim() || !item.cClassTrib.trim() || !item.unidade.trim())
      throw new Error("Item " + (index + 1) + ": informe CST, cClassTrib e unidade.");
    if (!item.ncm.trim() && !item.nbs.trim())
      throw new Error("Item " + (index + 1) + ": informe NCM ou NBS.");
    return {
      numero: index + 1,
      ...(item.ncm.trim() ? { ncm: item.ncm.trim() } : {}),
      ...(item.nbs.trim() ? { nbs: item.nbs.trim() } : {}),
      cst: item.cst.trim(), cClassTrib: item.cClassTrib.trim(),
      baseCalculo: decimal(item.baseCalculo, "Base de cálculo", 2),
      quantidade: decimal(item.quantidade, "Quantidade", 6),
      unidade: item.unidade.trim().toUpperCase(),
    };
  });
  return { dataHoraEmissao: when.toISOString(), uf, municipio: Number(values.municipality), itens };
}

function buildPayload(): Record<string, unknown> {
  if (regime.value === "regular" && !advanced.value) return buildRegular();
  let parsed: unknown;
  try { parsed = JSON.parse(jsonText.value); }
  catch { throw new Error("Informe JSON válido conforme o contrato do regime instalado."); }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
    throw new Error("O JSON da operação deve ser um objeto.");
  return parsed as Record<string, unknown>;
}

async function refresh() {
  const current = ++generation;
  loading.value = true;
  error.value = ""; selected.value = null; history.value = []; cursor.value = null;
  try {
    const [s, page] = await Promise.all([props.api.calculatorStatus(), props.api.taxCalculations(props.companyId)]);
    if (current !== generation) return;
    status.value = s;
    history.value = page.items;
    cursor.value = page.next_cursor || null;
  } catch (cause) {
    if (current === generation) error.value = errorText(cause);
  } finally {
    if (current === generation) loading.value = false;
  }
}

async function calculate() {
  if (saving.value || !status.value?.[regime.value]?.configured) return;
  const current = generation;
  saving.value = true; error.value = ""; success.value = ""; selected.value = null;
  try {
    const payload: TaxCalculationInput = {
      company_id: props.companyId,
      regime: regime.value,
      payload: buildPayload(),
    };
    const item = await props.api.calculateTax(payload, crypto.randomUUID());
    if (current !== generation) return;
    selected.value = item;
    const page = await props.api.taxCalculations(props.companyId);
    if (current !== generation) return;
    history.value = page.items;
    cursor.value = page.next_cursor || null;
    success.value = "Cálculo concluído e memória armazenada com integridade e versões.";
  } catch (cause) {
    if (current === generation) error.value = errorText(cause);
  } finally { saving.value = false; }
}

async function openCalculation(id: string) {
  const current = generation; error.value = "";
  try {
    const calc = await props.api.taxCalculation(id);
    if (current === generation) selected.value = calc;
  } catch (cause) {
    if (current === generation) error.value = errorText(cause);
  }
}

async function loadMore() {
  if (!cursor.value || loadingMore.value) return;
  const current = generation; loadingMore.value = true;
  try {
    const page = await props.api.taxCalculations(props.companyId, cursor.value);
    if (current !== generation) return;
    const seen = new Set(history.value.map((value) => value.id));
    history.value.push(...page.items.filter((item) => !seen.has(item.id)));
    cursor.value = page.next_cursor || null;
  } catch (cause) {
    if (current === generation) error.value = errorText(cause);
  } finally { loadingMore.value = false; }
}

watch(() => [props.companyId, props.refreshTrigger] as const, () => { void refresh(); }, { immediate: true });
watch(regime, () => { selected.value = null; error.value = ""; success.value = ""; });
onBeforeUnmount(() => { ++generation; });
</script>

<template>
  <div class="tax-workspace">
    <div class="context-note">Cálculos obtidos exclusivamente da Calculadora local configurada. Não há estimativa substitutiva quando o serviço está ausente. O cálculo de operação não é apuração fiscal nem apropriação de créditos.</div>
    <div v-if="error" class="notice error" role="alert">{{ error }}</div>
    <div v-if="success" class="notice success" role="status">{{ success }}</div>
    <section class="surface panel">
      <div class="panel-heading"><div><div class="eyebrow">TRIBUTAÇÃO POR OPERAÇÃO</div><h2>Calculadora CBS / IBS</h2></div></div>
      <div class="tax-modes">
        <button class="btn" type="button" :class="regime === 'regular' ? 'primary' : 'secondary'" @click="regime = 'regular'">Regime regular</button>
        <button class="btn" type="button" :class="regime === 'simples' ? 'primary' : 'secondary'" @click="regime = 'simples'">Simples Nacional</button>
      </div>
      <p class="tax-availability">
        <span v-if="loading">Verificando configuração...</span>
        <span v-else-if="status?.[regime]?.configured">Integração configurada. A versão e a disponibilidade serão verificadas no cálculo.</span>
        <span v-else>Configure a Calculadora local e o endpoint de versão deste regime na instalação.</span>
      </p>
      <form class="tax-form" @submit.prevent="calculate">
        <template v-if="regime === 'regular' && !advanced">
          <div class="tax-grid">
            <label>Data e hora da emissão<input v-model="values.issuedAt" type="datetime-local" required /></label>
            <label>UF<input v-model="values.uf" maxlength="2" type="text" required /></label>
            <label>Município (IBGE)<input v-model="values.municipality" maxlength="7" type="text" inputmode="numeric" required /></label>
          </div>
          <div v-for="(entry, idx) in values.items" :key="idx" class="tax-item">
            <div class="tax-item-title"><strong>Item {{ idx + 1 }}</strong><button v-if="values.items.length > 1" type="button" class="btn secondary" @click="values.items.splice(idx, 1)">Remover</button></div>
            <div class="tax-grid">
              <label>NCM<input v-model="entry.ncm" type="text" maxlength="12" /></label>
              <label>NBS<input v-model="entry.nbs" type="text" maxlength="12" /></label>
              <label>CST<input v-model="entry.cst" type="text" maxlength="3" required /></label>
              <label>cClassTrib<input v-model="entry.cClassTrib" type="text" maxlength="6" required /></label>
              <label>Base de cálculo (R$)<input v-model="entry.baseCalculo" type="text" inputmode="decimal" required /></label>
              <label>Quantidade<input v-model="entry.quantidade" type="text" inputmode="decimal" required /></label>
              <label>Unidade<input v-model="entry.unidade" type="text" maxlength="12" required /></label>
            </div>
          </div>
          <button class="btn secondary" type="button" :disabled="values.items.length >= 500" @click="values.items.push(emptyEntry())">Adicionar item</button>
        </template>
        <label v-if="regime === 'regular'" class="tax-check">
          <input v-model="advanced" type="checkbox" /> Contrato JSON completo para operações especiais
        </label>
        <label v-if="regime === 'simples' || advanced" class="tax-json">
          Dados completos da operação (JSON do motor instalado)
          <textarea v-model="jsonText" rows="12" spellcheck="false" required></textarea>
          <small>Para o Simples Nacional e as operações especiais, o próprio serviço instalado valida os campos da versão utilizada.</small>
        </label>
        <button type="submit" class="btn primary" :disabled="saving || loading || !status?.[regime]?.configured">
          {{ saving ? "Calculando..." : "Calcular e registrar" }}
        </button>
      </form>
    </section>
    <section v-if="selected" class="surface panel">
      <div class="panel-heading"><div><div class="eyebrow">CÁLCULO REAL ARQUIVADO</div><h2>Memória e rastreabilidade</h2></div></div>
      <div class="tax-result">
        <div><small>Regime</small><strong>{{ selected.regime }}</strong></div>
        <div><small>Versão da aplicação</small><strong>{{ selected.engine_version.versaoApp || "—" }}</strong></div>
        <div><small>Versão normativa</small><strong>{{ selected.engine_version.versaoDb || "—" }}</strong></div>
        <div v-if="selected.totals.cbs !== undefined"><small>CBS calculada</small><strong>R$ {{ selected.totals.cbs }}</strong></div>
        <div v-if="selected.totals.ibs !== undefined"><small>IBS calculado</small><strong>R$ {{ selected.totals.ibs }}</strong></div>
      </div>
      <p class="muted-small">Os valores são transcritos do motor local. A verificação de direito a crédito e a Apuração Assistida são processos separados.</p>
      <div class="tax-hashes"><strong>SHA-256 da requisição</strong><code>{{ selected.request_sha256 }}</code><strong>SHA-256 da resposta</strong><code>{{ selected.response_sha256 }}</code></div>
      <details><summary>Memória de cálculo e retorno original</summary><pre>{{ JSON.stringify(selected.engine_response, null, 2) }}</pre></details>
      <details><summary>Dados usados no cálculo</summary><pre>{{ JSON.stringify(selected.request, null, 2) }}</pre></details>
    </section>
    <section class="surface panel">
      <div class="panel-heading"><div><div class="eyebrow">HISTÓRICO AUDITÁVEL</div><h2>Cálculos da empresa</h2></div></div>
      <div v-if="!history.length" class="table-empty">Nenhum cálculo foi realizado para esta empresa.</div>
      <div v-else class="table-scroll">
        <table>
          <thead><tr><th>Data</th><th>Regime</th><th>Versão normativa</th><th>Integridade</th><th>Ação</th></tr></thead>
          <tbody><tr v-for="calc in history" :key="calc.id">
            <td>{{ formatDate(calc.created_at) }}</td><td>{{ calc.regime }}</td>
            <td>{{ calc.engine_version.versaoDb || "—" }}</td>
            <td class="tax-break"><code :title="calc.response_sha256">{{ calc.response_sha256 }}</code></td>
            <td><button type="button" class="btn secondary" @click="openCalculation(calc.id)">Ver memória</button></td>
          </tr></tbody>
        </table>
      </div>
      <div v-if="cursor" class="load-more"><button type="button" class="btn secondary" :disabled="loadingMore" @click="loadMore">{{ loadingMore ? "Carregando..." : "Carregar mais" }}</button></div>
    </section>
  </div>
</template>
<style scoped>
.tax-modes{display:flex;gap:10px;flex-wrap:wrap;margin:16px 0}
.tax-availability{margin-bottom:18px;color:#61768b;font-size:.84rem}
.tax-form{display:grid;gap:16px}
.tax-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
.tax-grid label,.tax-json{display:grid;gap:7px;font-size:.78rem;font-weight:700;color:#344961;min-width:0}
.tax-grid input,.tax-json textarea{width:100%;min-width:0;padding:11px;background:white;color:#29435c;border:1px solid #dbe4ee;border-radius:8px;font:inherit}
.tax-json textarea{resize:vertical;font-family:monospace;line-height:1.5}
.tax-json small{color:#61768b;font-size:.76rem;font-weight:400}
.tax-item{background:#f6f9fc;border:1px solid #e3eaf1;border-radius:10px;padding:16px}
.tax-item-title{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}
.tax-check{display:flex;gap:9px;align-items:center;color:#344961;font-size:.8rem}
.tax-form>.btn{justify-self:start}
.tax-result{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:16px 0}
.tax-result>div{background:#f6f9fc;border:1px solid #e3eaf1;border-radius:8px;display:grid;gap:9px;padding:14px;min-width:0}
.tax-result small{color:#61768b}.tax-result strong{color:#183f5b;overflow-wrap:anywhere}
.tax-hashes{display:grid;gap:6px;margin:14px 0}.tax-hashes code{font-size:.75rem;overflow-wrap:anywhere}
.tax-break{max-width:185px;overflow-wrap:anywhere}.tax-break code{font-size:.72rem}
details{border:1px solid #e3eaf1;border-radius:8px;padding:12px;margin-top:10px}
summary{font-weight:700;cursor:pointer}pre{overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;max-height:500px}
@media(max-width:820px){.tax-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:520px){.tax-grid{grid-template-columns:1fr}.tax-form>.btn{width:100%}}
</style>
