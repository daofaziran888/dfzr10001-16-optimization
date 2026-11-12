<template>
  <section class="page" data-module="website">
    <header class="page-head">
      <div>
        <h2>网页抓取规则管理</h2>
        <p class="page-desc">维护网页抓取的选择器规则、请求策略与编码配置，支持列表页与详情页两级抓取。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记抓取规则</button>
        <button class="btn" type="button" @click="exportRows">导出网页抓取规则清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th>规则编号</th>
          <th>目标站点</th>
          <th>列表选择器</th>
          <th>详情选择器</th>
          <th>请求间隔</th>
          <th>执行窗口</th>
          <th>关联任务</th>
          <th>字符编码</th>
          <th>重试上限</th>
          <th>校验结论</th>
          <th>阻断原因</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td>{{ row.规则编号 ?? '—' }}</td>
          <td v-for="field in editableFields" :key="field" :class="{ 'field-invalid': issueFields(row).includes(field) }">
            <input
              v-model="drafts[String(row.id)][field]"
              class="cell-input"
              :aria-label="`${row.规则编号} ${field}`"
            />
            <span v-if="field === '请求间隔' && issueFields(row).includes('请求间隔')" class="inline-window">
              当前执行窗口：{{ row.执行窗口 ?? '未配置' }}
            </span>
          </td>
          <td :class="{ 'field-invalid': issueFields(row).includes('执行窗口') }">
            {{ row.执行窗口 ?? '未配置' }}
            <span class="hint">以关联任务的执行窗口为准</span>
          </td>
          <td :class="{ 'field-invalid': issueFields(row).includes('关联任务') }">
            <input
              v-model="drafts[String(row.id)]['关联任务']"
              class="cell-input"
              :aria-label="`${row.规则编号} 关联任务`"
            />
          </td>
          <td>{{ row.字符编码 ?? '—' }}</td>
          <td>{{ row.重试上限 ?? '—' }}</td>
          <td>
            <span class="badge" :class="badgeClass(row)">{{ row.校验结论 || '未校验' }}</span>
          </td>
          <td :class="{ 'field-invalid': issueFields(row).includes('校验结论') }">
            <span v-if="row.阻断原因">{{ row.阻断原因 }}</span>
            <span v-else class="hint">—</span>
          </td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td colspan="12" class="empty-state">暂无网页抓取规则数据，可先登记抓取规则</td>
        </tr>
      </tbody>
    </table>

    <section v-if="feedback" class="feedback-panel">
      <div class="feedback-head">
        <strong>{{ feedback.ok ? '操作结果' : '校验未通过' }}</strong>
        <span class="feedback-message">{{ feedback.message }}</span>
      </div>
      <ul v-if="feedback.issues.length" class="issue-list">
        <li v-for="issue in feedback.issues" :key="issue.field + issue.message">
          <span class="field-chip">{{ issue.field }}</span>
          <span>{{ issue.message }}</span>
        </li>
      </ul>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条网页抓取规则记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null | undefined> & {
  id: number
  校验问题?: ValidationIssue[]
  校验字段?: string[]
  校验结论?: string
  阻断原因?: string
}

type ValidationIssue = { field: string; fields?: string[]; message: string }

type Feedback = { ok: boolean; message: string; issues: ValidationIssue[] }

const ENDPOINT = '/api/website'
const editableFields = ['目标站点', '列表选择器', '详情选择器', '请求间隔']
const actions = ['提交校验', '启用配置', '停用配置']
const filterFields = ['规则编号', '目标站点', '列表选择器']

const rows = ref<Row[]>([])
const drafts = reactive<Record<string, Record<string, string>>>({})
const total = ref(0)
const errorMessage = ref('')
const feedback = ref<Feedback | null>(null)
const filters = ref<Record<string, string>>({})

const stats = computed(() => [
  { label: '抓取规则总数', value: total.value },
  { label: '待校验抓取规则', value: rows.value.filter((row) => row.校验结论 === '阻断').length },
  { label: '已启用抓取规则', value: rows.value.filter((row) => row.status === '已启用').length },
])

function issueFields(row: Row): string[] {
  if (row.校验字段?.length) {
    return row.校验字段
  }
  return (row.校验问题 ?? []).map((issue) => issue.field)
}

function badgeClass(row: Row): string {
  if (row.校验结论 === '通过') return 'badge-ok'
  if (row.校验结论 === '阻断') return 'badge-block'
  return 'badge-idle'
}

function syncDrafts() {
  for (const row of rows.value) {
    const key = String(row.id)
    drafts[key] = {}
    for (const field of editableFields) {
      drafts[key][field] = String(row[field] ?? '')
    }
  }
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  window.open(`${ENDPOINT}/export?${query}`, '_blank')
}

function openCreate() {
  errorMessage.value = '抓取规则登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  feedback.value = null
  const values: Record<string, string> = { ...(drafts[String(row.id)] ?? {}), action }
  if (typeof row.关联任务 === 'string') {
    values.关联任务 = row.关联任务
  }
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const payload = await response.json()
    feedback.value = {
      ok: Boolean(payload.ok),
      message: payload.message ?? '',
      issues: payload.issues ?? [],
    }
    if (payload.ok && payload.entry) {
      applyEntry(payload.entry)
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '网页抓取规则操作失败'
  }
}

function applyEntry(entry: Row) {
  const index = rows.value.findIndex((row) => String(row.id) === String(entry.id))
  if (index >= 0) {
    rows.value[index] = { ...rows.value[index], ...entry }
    syncDrafts()
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('抓取规则列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    syncDrafts()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '网页抓取规则列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.cell-input { width: 100%; border: 1px solid transparent; border-radius: 4px; padding: 4px 6px; font: inherit; background: transparent; }
.cell-input:focus { border-color: var(--brand); background: #fff; outline: none; }
.field-invalid { background: #fff4f2; box-shadow: inset 2px 0 0 #b42318; }
.hint { color: var(--muted); font-size: 12px; }
.inline-window { display: block; color: #b42318; font-size: 12px; margin-top: 2px; }
.badge { display: inline-block; border-radius: 10px; padding: 1px 8px; font-size: 12px; background: #eef2f7; color: #334155; }
.badge-ok { background: #e7f6ec; color: #166534; }
.badge-block { background: #fdecea; color: #b42318; }
.feedback-panel { margin-top: 12px; border: 1px solid var(--border); border-radius: 8px; background: #fff; padding: 10px 12px; }
.feedback-head { display: flex; gap: 10px; align-items: baseline; }
.feedback-message { font-size: 13px; color: #334155; }
.issue-list { margin: 8px 0 0; padding-left: 0; list-style: none; display: flex; flex-direction: column; gap: 6px; }
.issue-list li { font-size: 13px; display: flex; gap: 8px; }
.field-chip { border: 1px solid #f2b8b5; background: #fdecea; color: #b42318; border-radius: 4px; padding: 0 6px; font-size: 12px; white-space: nowrap; }
</style>
