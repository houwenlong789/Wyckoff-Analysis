import type { FundamentalMetric, ValueSnapshot } from './agent-market'

export type ValueTone = 'good' | 'bad' | 'neutral'

export interface ValueSignal {
  label: string
  tone: ValueTone
}

export interface ValueScore {
  label: string
  tone: ValueTone
  score: number
  strengths: ValueSignal[]
  risks: ValueSignal[]
}

export function buildValueScore(metrics: FundamentalMetric | null): ValueScore {
  if (!metrics) return { label: '暂无', tone: 'neutral', score: -99, strengths: [], risks: [] }

  let score = 0
  const strengths: ValueSignal[] = []
  const risks: ValueSignal[] = []
  const addStrength = (condition: boolean, label: string, points = 1) => {
    if (!condition) return
    strengths.push({ label, tone: 'good' })
    score += points
  }
  const addRisk = (condition: boolean, label: string, points = 1) => {
    if (!condition) return
    risks.push({ label, tone: 'bad' })
    score -= points
  }

  addStrength((metrics.roe ?? -Infinity) >= 10, 'ROE 较强', 2)
  addRisk((metrics.roe ?? Infinity) < 0, 'ROE 为负', 2)
  addStrength((metrics.net_income_yoy ?? -Infinity) > 0, '净利润正增长')
  addRisk((metrics.net_income_yoy ?? Infinity) < 0, '净利润下滑')
  addStrength((metrics.revenue_yoy ?? -Infinity) > 0, '营收正增长')
  addRisk((metrics.revenue_yoy ?? Infinity) < 0, '营收下滑')
  addStrength((metrics.gross_margin ?? -Infinity) >= 30, '毛利率较高')
  addRisk((metrics.gross_margin ?? Infinity) < 15, '毛利率偏低')
  addStrength((metrics.debt_to_asset_ratio ?? Infinity) <= 55, '杠杆较低')
  addRisk((metrics.debt_to_asset_ratio ?? -Infinity) >= 70, '资产负债率偏高', 2)
  addStrength((metrics.operating_cash_to_revenue ?? -Infinity) >= 5, '现金流匹配收入')
  addRisk((metrics.operating_cash_to_revenue ?? Infinity) < 0, '经营现金流偏弱')

  const tone: ValueTone = score >= 3 ? 'good' : score < 0 ? 'bad' : 'neutral'
  const label = tone === 'good' ? '稳健' : tone === 'bad' ? '承压' : '中性'
  return { label, tone, score, strengths, risks }
}

export function sourceLabel(snapshot: ValueSnapshot): string {
  if (snapshot.source === 'tickflow') return 'TickFlow'
  if (snapshot.source === 'tushare') return 'Tushare'
  return '--'
}

export function buildValuePrompt(snapshot: ValueSnapshot): string {
  const metrics = snapshot.metrics
  if (!metrics) return '价值面摘要：暂无可用基本面指标，本次只基于量价结构分析。'
  return [
    `价值面摘要（来源：${sourceLabel(snapshot)}${metrics.period_end ? `，报告期：${metrics.period_end}` : ''}）：`,
    `ROE=${formatPromptPercent(metrics.roe)}，净利润同比=${formatPromptPercent(metrics.net_income_yoy)}，营收同比=${formatPromptPercent(metrics.revenue_yoy)}`,
    `毛利率=${formatPromptPercent(metrics.gross_margin)}，净利率=${formatPromptPercent(metrics.net_margin)}，资产负债率=${formatPromptPercent(metrics.debt_to_asset_ratio)}`,
    `经营现金流/营收=${formatPromptPercent(metrics.operating_cash_to_revenue)}，EPS=${formatPromptNumber(metrics.eps_basic)}，每股净资产=${formatPromptNumber(metrics.bps)}`,
  ].join('\n')
}

export function formatPromptPercent(value: number | undefined): string {
  return Number.isFinite(value) ? `${(value as number).toFixed(2)}%` : '暂无'
}

export function formatPromptNumber(value: number | undefined): string {
  return Number.isFinite(value) ? (value as number).toFixed(2) : '暂无'
}
