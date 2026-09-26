<template>
  <section class="page" data-module="execute">
    <header class="page-head">
      <div>
        <h2>检测执行管理</h2>
        <p class="page-desc">维护执行记录，围绕记录编号、关联任务、前处理方式、检测条件做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记执行记录</button>
        <button class="btn" type="button" @click="exportRows">导出检测执行清单</button>
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
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in availableActions(row)"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <span v-if="!availableActions(row).length" class="empty-state">已作废，不可再操作</span>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无检测执行数据，可先登记执行记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条检测执行记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { fetchJson, request } from '@/api/client'

type Row = Record<string, string | number | null>
type ExecuteStats = {
  待执行: number
  执行中: number
  今日提交: number
  按执行人员: { 执行人员: string; 完成数: number }[]
  已作废: number
}

const ENDPOINT = '/api/execute'
const columns = ["记录编号", "关联任务", "前处理方式", "检测条件", "原始记录号", "执行人员", "执行时间", "执行状态"]
const statuses = ["待执行", "执行中", "已提交", "已作废"]
// 与后端状态机一致：已作废是终态，不再提供任何动作
const actionsByStatus: Record<string, string[]> = {
  待执行: ["开始执行", "作废记录"],
  执行中: ["提交记录", "作废记录"],
  已提交: ["作废记录"],
  已作废: [],
}
const stats = ref([{"label": "待执行记录", "value": 0}, {"label": "执行中记录", "value": 0}, {"label": "今日提交数", "value": 0}])

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

function availableActions(row: Row): string[] {
  return actionsByStatus[String(row.status ?? '')] ?? []
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '执行记录登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const result = await response.json().catch(() => null)
    if (!response.ok || !result?.ok) {
      throw new Error(result?.message ?? result?.detail ?? '检测执行动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '检测执行操作失败'
  }
}

async function reloadStats() {
  try {
    const payload = await fetchJson<ExecuteStats>(`${ENDPOINT}/stats`)
    stats.value = [
      { label: '待执行记录', value: payload.待执行 ?? 0 },
      { label: '执行中记录', value: payload.执行中 ?? 0 },
      { label: '今日提交数', value: payload.今日提交 ?? 0 },
    ]
  } catch {
    // 统计读取失败时保留上一次的结果，列表仍然可用
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('执行记录列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '检测执行列表读取失败'
  }
  await reloadStats()
}

onMounted(reload)
</script>
