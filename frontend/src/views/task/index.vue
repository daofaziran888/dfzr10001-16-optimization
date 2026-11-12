<template>
  <section class="page" data-module="task">
    <header class="page-head">
      <div>
        <h2>采集任务管理</h2>
        <p class="page-desc">维护采集任务的调度周期、增量策略与执行参数，实现按需或定时采集调度。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记采集任务</button>
        <button class="btn" type="button" @click="exportRows">导出采集任务清单</button>
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
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>规则校验结论</th>
          <th>阻断条目</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td>
            <span v-if="row.可调度 === null" class="badge badge-idle">未关联网页抓取规则</span>
            <span v-else-if="row.可调度" class="badge badge-ok">可调度</span>
            <span v-else class="badge badge-block">不可调度</span>
            <div v-if="row.阻断原因" class="block-reason">{{ row.阻断原因 }}</div>
          </td>
          <td>
            <ul v-if="blockersOf(row).length" class="blocker-list">
              <li v-for="(blocker, index) in blockersOf(row)" :key="index">
                <span class="field-chip">{{ blocker.规则编号 }}</span>
                <span class="field-chip">{{ blocker.字段 }}</span>
                <span v-if="(blocker.涉及字段 ?? []).length > 1" class="field-chip">
                  涉及 {{ (blocker.涉及字段 ?? []).join('、') }}
                </span>
                <span>{{ blocker.阻断原因 }}</span>
              </li>
            </ul>
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
          <td :colspan="columns.length + 3" class="empty-state">暂无采集任务数据，可先登记采集任务</td>
        </tr>
      </tbody>
    </table>

    <section v-if="feedback" class="feedback-panel">
      <strong>{{ feedback.ok ? '操作结果' : '提交被阻断' }}</strong>
      <span class="feedback-message">{{ feedback.message }}</span>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条采集任务记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Blocker = {
  规则编号?: string
  关联任务?: string
  阻断状态?: string
  字段?: string
  涉及字段?: string[]
  阻断原因?: string
}

type Row = Record<string, string | number | boolean | null | undefined> & {
  id: number
  阻断条目?: Blocker[]
  阻断原因?: string
  可调度?: boolean | null
}

type Feedback = { ok: boolean; message: string }

const ENDPOINT = '/api/task'
const columns = ['任务编号', '任务名称', '关联源端', '采集频率', '增量字段', '起始水位', '批次大小', '执行窗口']
const actions = ['提交执行', '暂停任务', '恢复任务']
const filterFields = columns.slice(0, 3)

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const feedback = ref<Feedback | null>(null)
const filters = ref<Record<string, string>>({})

const stats = computed(() => [
  { label: '采集任务总数', value: total.value },
  { label: '待执行任务', value: rows.value.filter((row) => row.status === '待执行').length },
  { label: '执行中任务', value: rows.value.filter((row) => row.status === '执行中').length },
])

function blockersOf(row: Row): Blocker[] {
  return row.阻断条目 ?? []
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
  errorMessage.value = '采集任务登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  feedback.value = null
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json()
    feedback.value = { ok: Boolean(payload.ok), message: payload.message ?? '' }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集任务操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('采集任务列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集任务列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.badge { display: inline-block; border-radius: 10px; padding: 1px 8px; font-size: 12px; background: #eef2f7; color: #334155; }
.badge-ok { background: #e7f6ec; color: #166534; }
.badge-block { background: #fdecea; color: #b42318; }
.badge-idle { background: #eef2f7; color: #64748b; }
.block-reason { margin-top: 4px; font-size: 12px; color: #b42318; }
.blocker-list { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 4px; }
.blocker-list li { display: flex; gap: 6px; align-items: baseline; font-size: 12px; }
.field-chip { border: 1px solid #d8dee6; background: #f8fafc; border-radius: 4px; padding: 0 6px; white-space: nowrap; }
.hint { color: var(--muted); font-size: 12px; }
.feedback-panel { margin-top: 12px; border: 1px solid var(--border); border-radius: 8px; background: #fff; padding: 10px 12px; display: flex; gap: 10px; align-items: baseline; }
.feedback-message { font-size: 13px; color: #334155; }
</style>
