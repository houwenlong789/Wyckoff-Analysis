import type { SupabaseClient } from '@supabase/supabase-js'
import type { generateText as GenerateTextFn } from 'ai'

export interface KlineRow {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface ToolDeps {
  supabase: SupabaseClient
  fetch: typeof globalThis.fetch
  generateText: typeof GenerateTextFn
}

export interface LLMToolConfig {
  api_key: string
  model: string
  base_url: string
}

export function buildKlineDigest(data: KlineRow[]): string {
  if (data.length === 0) return '无可用K线数据'
  const last = data[data.length - 1]!
  const avg = (arr: number[]) => arr.length > 0 ? arr.reduce((a, b) => a + b, 0) / arr.length : 0
  const slice = (n: number) => data.slice(-n)
  const ma = (n: number) => avg(slice(n).map(d => d.close))
  const vol = (n: number) => avg(slice(n).map(d => d.volume))
  const p20 = slice(20)

  const lines = [
    `K线共${data.length}根，最新日期 ${last.date}`,
    `最新收盘 ${last.close.toFixed(2)}，开盘 ${last.open.toFixed(2)}，高 ${last.high.toFixed(2)}，低 ${last.low.toFixed(2)}`,
    `MA5=${ma(5).toFixed(2)} MA10=${ma(10).toFixed(2)} MA20=${ma(20).toFixed(2)}`,
  ]
  if (data.length >= 50) lines.push(`MA50=${ma(50).toFixed(2)}`)
  if (data.length >= 120) lines.push(`MA120=${ma(120).toFixed(2)}`)
  lines.push(
    `近20日最高 ${Math.max(...p20.map(d => d.high)).toFixed(2)}，最低 ${Math.min(...p20.map(d => d.low)).toFixed(2)}`,
    `近5日均量 ${vol(5).toFixed(0)}，近20日均量 ${vol(20).toFixed(0)}`,
    `量比(5/20) ${(vol(5) / (vol(20) || 1)).toFixed(2)}`,
  )

  const recent5 = slice(5)
  lines.push('近5日走势: ' + recent5.map(d => {
    const chg = ((d.close - d.open) / d.open * 100).toFixed(1)
    return `${d.date.slice(5)} ${Number(chg) >= 0 ? '+' : ''}${chg}%`
  }).join(' → '))

  return lines.join('\n')
}

export async function fetchTickFlowKey(deps: ToolDeps, userId: string): Promise<string | null> {
  const { data } = await deps.supabase
    .from('user_settings')
    .select('tickflow_api_key')
    .eq('user_id', userId)
    .single()
  return data?.tickflow_api_key || null
}

export async function fetchKlineForAgent(deps: ToolDeps, code: string, apiKey: string): Promise<KlineRow[]> {
  const end = new Date()
  end.setDate(end.getDate() - 1)
  const start = new Date()
  start.setDate(start.getDate() - 500)
  const fmt = (d: Date) => d.toISOString().slice(0, 10).replace(/-/g, '')

  const url = `https://api.tickflow.io/v1/stock/history?symbol=${code}&start_date=${fmt(start)}&end_date=${fmt(end)}&adjust=qfq&limit=250`
  try {
    const resp = await deps.fetch(url, { headers: { Authorization: `Bearer ${apiKey}` } })
    if (!resp.ok) return []
    const json = await resp.json()
    const rows = json.data || json.records || json || []
    if (!Array.isArray(rows)) return []
    return rows.map((r: Record<string, unknown>) => ({
      date: String(r.date || r.trade_date || '').replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3'),
      open: Number(r.open || 0),
      high: Number(r.high || 0),
      low: Number(r.low || 0),
      close: Number(r.close || 0),
      volume: Number(r.volume || r.vol || 0),
    })).filter((d: KlineRow) => d.date && d.close > 0)
  } catch {
    return []
  }
}

export async function fetchQuotes(
  deps: ToolDeps,
  tickflowKey: string | null,
  stocks: { code: number }[],
): Promise<Record<string, Record<string, number>>> {
  if (!tickflowKey || stocks.length === 0) return {}
  try {
    const symbols = stocks.map(r => {
      const c = String(r.code).padStart(6, '0')
      return c.startsWith('6') ? `${c}.SH` : `${c}.SZ`
    }).join(',')
    const resp = await deps.fetch(
      `https://api.tickflow.io/v1/quotes?symbols=${symbols}`,
      { headers: { Authorization: `Bearer ${tickflowKey}` } },
    )
    if (!resp.ok) return {}
    const json = await resp.json() as { data?: Record<string, number>[] }
    const result: Record<string, Record<string, number>> = {}
    for (const row of (json.data || [])) {
      const sym = String((row as Record<string, unknown>).symbol || '')
      const code6 = sym.split('.')[0] || ''
      if (code6) result[code6] = row
    }
    return result
  } catch { return {} }
}

export async function execSearchStock(deps: ToolDeps, userId: string, query: string): Promise<string> {
  const q = query.trim()
  const isCode = /^\d+$/.test(q)

  const tables = ['recommendation_tracking', 'portfolio_positions', 'tail_buy_history'] as const
  const allRows: { code: number; name: string }[] = []

  for (const table of tables) {
    const res = isCode
      ? await deps.supabase.from(table).select('code, name').eq('code', parseInt(q)).limit(5)
      : await deps.supabase.from(table).select('code, name').ilike('name', `%${q}%`).limit(10)
    if (res.data) allRows.push(...res.data)
  }

  if (allRows.length === 0) return `未找到匹配"${query}"的股票`

  const seen = new Set<number>()
  const unique = allRows.filter((r) => {
    if (seen.has(r.code)) return false
    seen.add(r.code)
    return true
  }).slice(0, 10)

  const tickflowKey = await fetchTickFlowKey(deps, userId)
  const quotes = await fetchQuotes(deps, tickflowKey, unique)

  const lines = unique.map(r => {
    const code6 = String(r.code).padStart(6, '0')
    const qt = quotes[code6]
    if (qt) {
      const price = qt.close || qt.last || qt.price || qt.current || 0
      const pct = qt.pct_chg ?? ((qt.close && qt.pre_close) ? ((qt.close - qt.pre_close) / qt.pre_close * 100) : null)
      const pctStr = pct != null ? `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%` : ''
      return `${code6} ${r.name} | ¥${price.toFixed(2)} ${pctStr}`
    }
    return `${code6} ${r.name}`
  })

  return lines.join('\n')
}

export async function execViewPortfolio(deps: ToolDeps, userId: string): Promise<string> {
  const portfolioId = `USER_LIVE:${userId}`

  const [pfResult, posResult] = await Promise.all([
    deps.supabase.from('portfolios').select('free_cash').eq('portfolio_id', portfolioId).single(),
    deps.supabase.from('portfolio_positions').select('code, name, shares, cost_price, buy_dt, stop_loss').eq('portfolio_id', portfolioId),
  ])

  const cash = pfResult.data?.free_cash || 0
  const positions = posResult.data || []

  if (positions.length === 0) {
    return `当前无持仓。可用资金：¥${cash.toLocaleString()}`
  }

  const lines = positions.map((p) => {
    const sl = p.stop_loss ? ` | 止损¥${p.stop_loss.toFixed(2)}` : ''
    return `${p.code} ${p.name} | ${p.shares}股 | 成本¥${p.cost_price.toFixed(2)} | 建仓${p.buy_dt || '未知'}${sl}`
  })
  const totalCost = positions.reduce((s, p) => s + p.shares * p.cost_price, 0)

  return [
    `持仓 ${positions.length} 只，可用资金 ¥${cash.toLocaleString()}，持仓成本合计 ¥${totalCost.toLocaleString()}`,
    '',
    ...lines,
  ].join('\n')
}

export async function execMarketOverview(deps: ToolDeps): Promise<string> {
  const { data } = await deps.supabase
    .from('market_signal_daily')
    .select('*')
    .order('trade_date', { ascending: false })
    .limit(3)

  if (!data || data.length === 0) return '暂无最新市场信号数据'

  const merged: Record<string, unknown> = { ...data[0] }
  for (const row of data) {
    for (const key of ['benchmark_regime', 'main_index_close', 'main_index_today_pct']) {
      if (!merged[key] && row[key]) merged[key] = row[key]
    }
    for (const key of ['a50_close', 'a50_pct_chg']) {
      if (!merged[key] && row[key]) merged[key] = row[key]
    }
    for (const key of ['vix_close', 'vix_pct_chg']) {
      if (!merged[key] && row[key]) merged[key] = row[key]
    }
  }

  const regimeMap: Record<string, string> = {
    RISK_ON: '偏强', NEUTRAL: '中性', RISK_OFF: '偏弱', CRASH: '极弱', BLACK_SWAN: '恶劣',
  }
  const regime = String(merged.benchmark_regime || 'NEUTRAL')
  const close = Number(merged.main_index_close || 0)
  const pct = Number(merged.main_index_today_pct || 0)
  const a50Close = Number(merged.a50_close || 0)
  const a50Pct = Number(merged.a50_pct_chg || 0)
  const vixClose = Number(merged.vix_close || 0)
  const title = String(merged.banner_title || '')
  const body = String(merged.banner_message || '')

  return [
    `大盘状态：${regimeMap[regime] || regime}`,
    close ? `上证指数：${close.toFixed(0)} (${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%)` : '',
    a50Close ? `A50：${a50Close.toFixed(0)} (${a50Pct >= 0 ? '+' : ''}${a50Pct.toFixed(2)}%)` : '',
    vixClose ? `VIX：${vixClose.toFixed(1)}` : '',
    title ? `\n${title}` : '',
    body ? body : '',
  ].filter(Boolean).join('\n')
}

export async function execQueryRecommendations(deps: ToolDeps, limit: number): Promise<string> {
  const { data } = await deps.supabase
    .from('recommendation_tracking')
    .select('code, name, recommend_date, initial_price, current_price, change_pct, is_ai_recommended, funnel_score')
    .order('recommend_date', { ascending: false })
    .limit(limit)

  if (!data || data.length === 0) return '暂无推荐记录'

  const lines = data.map((r) => {
    const code = String(r.code).padStart(6, '0')
    const chg = r.change_pct >= 0 ? `+${r.change_pct.toFixed(2)}%` : `${r.change_pct.toFixed(2)}%`
    const ai = r.is_ai_recommended ? ' [AI]' : ''
    return `${code} ${r.name} | 推荐日${r.recommend_date} | ${r.initial_price?.toFixed(2)}→${r.current_price?.toFixed(2)} ${chg}${ai}`
  })

  return `最近 ${data.length} 条推荐记录：\n\n${lines.join('\n')}`
}

export async function execQueryTailBuy(deps: ToolDeps, limit: number): Promise<string> {
  const { data } = await deps.supabase
    .from('tail_buy_history')
    .select('code, name, run_date, signal_type, rule_score, priority_score, llm_decision, llm_reason')
    .order('run_date', { ascending: false })
    .limit(limit)

  if (!data || data.length === 0) return '暂无尾盘买入记录'

  const lines = data.map((r) => {
    const code = String(r.code).padStart(6, '0')
    return `${code} ${r.name} | ${r.run_date} | ${r.signal_type} | 规则分${r.rule_score?.toFixed(1)} | ${r.llm_decision} | ${r.llm_reason || ''}`
  })

  return `最近 ${data.length} 条尾盘记录：\n\n${lines.join('\n')}`
}

export async function execExecutePortfolioUpdate(
  deps: ToolDeps,
  userId: string,
  action: 'add' | 'update' | 'delete',
  code: string,
  name: string | null,
  shares: number | null,
  cost_price: number | null,
  stop_loss: number | null,
): Promise<string> {
  const portfolioId = `USER_LIVE:${userId}`

  if (action === 'delete') {
    const { error } = await deps.supabase
      .from('portfolio_positions')
      .delete()
      .eq('portfolio_id', portfolioId)
      .eq('code', code)
    return error ? `删除失败: ${error.message}` : `✅ 已删除 ${code} ${name || ''}`
  }

  if (action === 'add' || action === 'update') {
    if (!name || !shares || !cost_price) {
      return '执行失败：缺少 name、shares、cost_price 参数'
    }
    const record: Record<string, unknown> = {
      portfolio_id: portfolioId, code, name, shares, cost_price,
      buy_dt: new Date().toISOString().slice(0, 10),
    }
    if (stop_loss !== undefined) record.stop_loss = stop_loss
    const { error } = await deps.supabase.from('portfolio_positions').upsert(record)
    return error
      ? `执行失败: ${error.message}`
      : `✅ 已${action === 'add' ? '新增' : '更新'} ${code} ${name} ${shares}股 @¥${cost_price}${stop_loss ? ` 止损¥${stop_loss}` : ''}`
  }

  return '未知操作'
}

export async function execScreenStocks(deps: ToolDeps): Promise<string> {
  const { data } = await deps.supabase
    .from('recommendation_tracking')
    .select('code, name, recommend_date, funnel_score, change_pct, is_ai_recommended')
    .eq('is_ai_recommended', true)
    .order('recommend_date', { ascending: false })
    .limit(30)

  if (!data || data.length === 0) return '暂无选股结果'

  const latestDate = data[0]!.recommend_date
  const latest = data.filter(r => r.recommend_date === latestDate)

  const lines = latest.map((r) => {
    const code = String(r.code).padStart(6, '0')
    const score = r.funnel_score?.toFixed(2) || '--'
    const chg = r.change_pct != null ? (r.change_pct >= 0 ? `+${r.change_pct.toFixed(2)}%` : `${r.change_pct.toFixed(2)}%`) : '--'
    return `${code} ${r.name} | 漏斗分 ${score} | 推荐后涨跌 ${chg}`
  })

  return `最新选股日期 ${latestDate}，共 ${latest.length} 只 AI 候选：\n\n${lines.join('\n')}`
}

export async function execAnalyzeStock(
  deps: ToolDeps, userId: string, _config: LLMToolConfig, model: unknown, code: string, name: string | null,
): Promise<string> {
  const tickflowKey = await fetchTickFlowKey(deps, userId)
  if (!tickflowKey) {
    return `未配置 TickFlow API Key，无法获取 ${code} 的K线数据。请在设置页配置。`
  }

  const kline = await fetchKlineForAgent(deps, code, tickflowKey)
  if (kline.length === 0) {
    return `无法获取 ${code} ${name || ''} 的K线数据，请检查代码是否正确。`
  }

  const digest = buildKlineDigest(kline)
  const result = await deps.generateText({
    model: model as Parameters<typeof GenerateTextFn>[0]['model'],
    system: `你是威科夫分析大师。基于以下K线数据，对 ${code} ${name || ''} 进行深度诊断：
1. 当前威科夫阶段（积累/上涨/派发/下跌），Phase A-E 定位
2. 量价关系分析（供需力量对比，近期量比变化）
3. 均线形态（多头/空头排列，金叉/死叉）
4. 关键支撑与阻力位
5. 主力行为判断（是否有吸筹/出货迹象）
6. 操作建议与风险提示（含建议止损位）

用 Markdown 格式输出，简洁专业。`,
    prompt: digest,
  })

  return result.text || '分析完成但无输出'
}

export async function execGenerateAiReport(
  deps: ToolDeps, userId: string, _config: LLMToolConfig, model: unknown, codes: string[],
): Promise<string> {
  const tickflowKey = await fetchTickFlowKey(deps, userId)
  if (!tickflowKey) return '未配置 TickFlow API Key，无法生成研报。'

  const results: string[] = []
  for (const code of codes.slice(0, 3)) {
    const kline = await fetchKlineForAgent(deps, code, tickflowKey)
    if (kline.length === 0) {
      results.push(`## ${code}\n无法获取K线数据\n`)
      continue
    }
    const digest = buildKlineDigest(kline)
    const result = await deps.generateText({
      model: model as Parameters<typeof GenerateTextFn>[0]['model'],
      system: `你是威科夫分析大师。为 ${code} 撰写一份简明研报，包含：阶段判断、量价特征、关键价位、操作建议。200字以内。`,
      prompt: digest,
    })
    results.push(`## ${code}\n${result.text || '无输出'}\n`)
  }

  return results.join('\n---\n\n')
}

export async function execStrategyDecision(deps: ToolDeps, userId: string, model: unknown): Promise<string> {
  const portfolioId = `USER_LIVE:${userId}`

  const [posResult, signalResult] = await Promise.all([
    deps.supabase.from('portfolio_positions').select('code, name, shares, cost_price, stop_loss').eq('portfolio_id', portfolioId),
    deps.supabase.from('market_signal_daily').select('*').order('trade_date', { ascending: false }).limit(1).single(),
  ])

  const positions = posResult.data || []
  const signal = signalResult.data

  if (positions.length === 0) return '当前无持仓，无法给出操作建议。建议先通过选股工具寻找标的。'

  const posInfo = positions.map(p =>
    `${p.code} ${p.name} | ${p.shares}股 成本¥${p.cost_price}${p.stop_loss ? ` 止损¥${p.stop_loss}` : ''}`
  ).join('\n')

  const marketInfo = signal
    ? `大盘状态: ${signal.benchmark_regime || '未知'}, 上证: ${signal.main_index_close || '--'}, A50涨幅: ${signal.a50_pct_chg || '--'}%, VIX: ${signal.vix_close || '--'}`
    : '暂无市场数据'

  const result = await deps.generateText({
    model: model as Parameters<typeof GenerateTextFn>[0]['model'],
    system: '你是威科夫大师。基于用户的持仓和当前市场环境，为每只持仓股给出操作建议（买入加仓/持有/减仓/卖出），并给出整体仓位管理建议。简洁明了，必须附带风险提示。',
    prompt: `当前持仓:\n${posInfo}\n\n市场环境:\n${marketInfo}`,
  })

  return result.text || '无法生成建议'
}
