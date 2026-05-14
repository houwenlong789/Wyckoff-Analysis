import { useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { CheckSquare, Loader2, Swords, XSquare } from 'lucide-react'
import { useAuthStore } from '@/stores/auth'
import { usePreferences } from '@/lib/preferences'
import { loadLLMConfig } from '@/lib/chat-agent'
import { streamLLMResponse } from '@/lib/llm-stream'
import { MarkdownContent } from '@/components/markdown'
import { KlineChart } from '@/components/kline-chart'
import { MultiStockChart, type ComparisonSeries } from '@/components/multi-stock-chart'
import { UpgradeNotice } from '@/components/upgrade-notice'
import { AIDisclaimer } from '@/components/ai-disclaimer'
import { TICKFLOW_PURCHASE, fetchKlineViaTickFlow, getUserDataKeys, isSupportedKlineCode, type KlineData } from '@/lib/kline'
import { avg } from '@/lib/math'
import { resolveStockQuery } from '@/lib/market-search'

interface BattleTarget {
  code: string
  name: string
}

interface BattleStock extends BattleTarget {
  data: KlineData[]
  stats: StrengthStats
}

interface StrengthStats {
  latestClose: number
  ret20: number
  ret60: number
  ret120: number
  drawdown60: number
  volumeRatio: number
  score: number
}

type ChartMode = 'overlay' | 'separate'

const DEFAULT_INPUT = '中国平安\n贵州茅台\nAAPL\nNVDA\n腾讯'

export function StockBattlePage() {
  const [input, setInput] = useState(DEFAULT_INPUT)
  const battle = useBattleRunner()
  const selectedSeries = useSelectedSeries(battle.stocks, battle.selectedCodes)

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-5 p-6">
      <BattleHeader />
      <BattleInput input={input} loading={battle.loading} onChange={setInput} onSubmit={() => battle.run(input)} />
      {battle.error && <UpgradeNotice message={battle.error} />}
      {battle.stocks.length > 0 && (
        <>
          <BattleControls battle={battle} />
          <BattleCharts mode={battle.mode} limit={battle.overlayLimit} stocks={selectedSeries} benchmark={battle.benchmark} />
          <StrengthTable stocks={battle.stocks} />
          <ReportPanel report={battle.report} loading={battle.loading} />
        </>
      )}
    </div>
  )
}

function useBattleRunner() {
  const user = useAuthStore((s) => s.user)
  const { t } = usePreferences()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [stocks, setStocks] = useState<BattleStock[]>([])
  const [selectedCodes, setSelectedCodes] = useState<string[]>([])
  const [mode, setMode] = useState<ChartMode>('overlay')
  const [overlayLimit, setOverlayLimit] = useState(6)
  const [report, setReport] = useState('')
  const abortRef = useRef<AbortController | null>(null)
  const [benchmark, setBenchmark] = useState<KlineData[]>([])
  async function run(input: string) {
    if (!user) return
    abortRef.current?.abort()
    const abort = new AbortController()
    abortRef.current = abort
    setLoading(true); setError(''); setStocks([]); setBenchmark([])
    try {
      const [config, keys, targets] = await Promise.all([loadLLMConfig(user.id), getUserDataKeys(user.id), resolveTargets(input)])
      if (!config) throw new Error(t('battle.missingModel'))
      if (!keys.tickflow) throw new Error(upgradeMessage())
      const [fetched, bench] = await Promise.all([
        fetchBattleStocks(targets, keys.tickflow),
        fetchKlineViaTickFlow('399300', keys.tickflow).catch(() => [] as KlineData[]),
      ])
      if (abort.signal.aborted) return
      setStocks(fetched)
      setBenchmark(bench)
      setSelectedCodes(fetched.slice(0, Math.min(6, fetched.length)).map((item) => item.code))
      setReport(await callBattleLLM(config, fetched, abort.signal))
    } catch (err) {
      if (abort.signal.aborted) return
      setError(normalizeBattleError(err))
    } finally {
      setLoading(false)
    }
  }
  return { loading, error, stocks, selectedCodes, mode, overlayLimit, report, benchmark, run, setSelectedCodes, setMode, setOverlayLimit }
}

function BattleHeader() {
  const { t } = usePreferences()
  return (
    <header className="border-b border-border pb-5">
      <h1 className="flex items-center gap-2 text-xl font-semibold"><Swords size={21} />{t('battle.title')}</h1>
      <p className="mt-1 max-w-3xl text-sm text-muted-foreground">{t('battle.subtitle')}</p>
    </header>
  )
}

function BattleInput({ input, loading, onChange, onSubmit }: { input: string; loading: boolean; onChange: (value: string) => void; onSubmit: () => void }) {
  const { t } = usePreferences()
  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    onSubmit()
  }
  return (
    <form onSubmit={submit} className="rounded-lg border border-border p-4">
      <label className="text-sm font-medium">{t('battle.inputLabel')}</label>
      <textarea value={input} onChange={(e) => onChange(e.target.value)} className="mt-2 min-h-36 w-full rounded-lg border border-border bg-background p-3 text-sm outline-none focus:ring-2 focus:ring-ring/20" />
      <div className="mt-3 flex items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">{t('battle.inputHint')}</p>
        <button disabled={loading || !input.trim()} className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50">
          {loading ? <Loader2 size={16} className="animate-spin" /> : <Swords size={16} />}
          {loading ? t('battle.running') : t('battle.start')}
        </button>
      </div>
    </form>
  )
}

function BattleControls({ battle }: { battle: ReturnType<typeof useBattleRunner> }) {
  const { t } = usePreferences()
  return (
    <section className="rounded-lg border border-border p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <ModeSwitch mode={battle.mode} setMode={battle.setMode} />
        <OverlayLimit value={battle.overlayLimit} onChange={battle.setOverlayLimit} />
      </div>
      <SelectionGrid stocks={battle.stocks} selected={battle.selectedCodes} setSelected={battle.setSelectedCodes} />
      <div className="mt-3 flex gap-2">
        <SmallButton icon={<CheckSquare size={14} />} label={t('battle.selectAll')} onClick={() => battle.setSelectedCodes(battle.stocks.map((item) => item.code))} />
        <SmallButton icon={<XSquare size={14} />} label={t('battle.clearAll')} onClick={() => battle.setSelectedCodes([])} />
      </div>
    </section>
  )
}

function ModeSwitch({ mode, setMode }: { mode: ChartMode; setMode: (mode: ChartMode) => void }) {
  const { t } = usePreferences()
  return (
    <div className="inline-flex rounded-lg border border-border p-1 text-sm">
      <button type="button" onClick={() => setMode('overlay')} className={`rounded-md px-3 py-1.5 ${mode === 'overlay' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground'}`}>{t('battle.overlay')}</button>
      <button type="button" onClick={() => setMode('separate')} className={`rounded-md px-3 py-1.5 ${mode === 'separate' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground'}`}>{t('battle.separate')}</button>
    </div>
  )
}

function OverlayLimit({ value, onChange }: { value: number; onChange: (value: number) => void }) {
  const { t } = usePreferences()
  return (
    <label className="flex items-center gap-2 text-sm text-muted-foreground">
      {t('battle.overlayLimit')}
      <input type="number" min={1} value={value} onChange={(e) => onChange(Math.max(1, Number(e.target.value) || 1))} className="w-20 rounded-lg border border-border bg-background px-2 py-1.5 text-foreground outline-none" />
    </label>
  )
}

function SelectionGrid({ stocks, selected, setSelected }: { stocks: BattleStock[]; selected: string[]; setSelected: (codes: string[]) => void }) {
  return (
    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
      {stocks.map((stock) => (
        <label key={stock.code} className="flex cursor-pointer items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm hover:bg-muted/40">
          <input type="checkbox" checked={selected.includes(stock.code)} onChange={() => toggleSelected(stock.code, selected, setSelected)} />
          <span className="min-w-0 truncate">{stock.code} {stock.name}</span>
        </label>
      ))}
    </div>
  )
}

function BattleCharts({ mode, limit, stocks, benchmark }: { mode: ChartMode; limit: number; stocks: BattleStock[]; benchmark: KlineData[] }) {
  if (stocks.length === 0) return null
  const benchSeries: ComparisonSeries | null = benchmark.length > 0 ? { code: '399300', name: '沪深300', data: benchmark } : null
  if (mode === 'overlay') {
    const series = stocks.slice(0, limit).map(toComparisonSeries)
    if (benchSeries) series.push(benchSeries)
    return <MultiStockChart series={series} />
  }
  return (
    <section className="grid gap-4 xl:grid-cols-2">
      {stocks.map((stock) => <SingleStockPanel key={stock.code} stock={stock} />)}
    </section>
  )
}

function SingleStockPanel({ stock }: { stock: BattleStock }) {
  return (
    <div className="rounded-lg border border-border p-4">
      <h2 className="mb-3 text-sm font-semibold">{stock.code} {stock.name}</h2>
      <KlineChart data={stock.data} height={300} />
    </div>
  )
}

function StrengthTable({ stocks }: { stocks: BattleStock[] }) {
  const { t } = usePreferences()
  const rows = [...stocks].sort((a, b) => b.stats.score - a.stats.score)
  return (
    <section className="overflow-hidden rounded-lg border border-border">
      <table className="w-full text-sm">
        <thead className="bg-muted/40"><tr>{['#', t('common.code'), t('battle.score'), '20D', '60D', '120D', t('battle.drawdown')].map((h) => <th key={h} scope="col" className="px-3 py-2 text-left font-medium">{h}</th>)}</tr></thead>
        <tbody>{rows.map((stock, index) => <StrengthRow key={stock.code} stock={stock} rank={index + 1} />)}</tbody>
      </table>
    </section>
  )
}

function StrengthRow({ stock, rank }: { stock: BattleStock; rank: number }) {
  return (
    <tr className="border-t border-border">
      <td className="px-3 py-2">{rank}</td>
      <td className="px-3 py-2 font-mono">{stock.code} <span className="font-sans text-muted-foreground">{stock.name}</span></td>
      <td className="px-3 py-2 font-medium">{stock.stats.score.toFixed(1)}</td>
      <td className="px-3 py-2">{fmtPct(stock.stats.ret20)}</td>
      <td className="px-3 py-2">{fmtPct(stock.stats.ret60)}</td>
      <td className="px-3 py-2">{fmtPct(stock.stats.ret120)}</td>
      <td className="px-3 py-2">{fmtPct(stock.stats.drawdown60)}</td>
    </tr>
  )
}

function ReportPanel({ report, loading }: { report: string; loading: boolean }) {
  const { t } = usePreferences()
  if (!report && !loading) return null
  return (
    <section className="rounded-lg border border-border p-5">
      <h2 className="mb-4 text-base font-semibold">{t('battle.report')}</h2>
      {report ? (
        <>
          <AIDisclaimer />
          <article className="mt-4 prose prose-sm max-w-none text-foreground"><MarkdownContent content={report} /></article>
        </>
      ) : (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 size={16} className="animate-spin" />
          <span>正在生成对抗结论...</span>
        </div>
      )}
    </section>
  )
}

function SmallButton({ icon, label, onClick }: { icon: ReactNode; label: string; onClick: () => void }) {
  return <button type="button" onClick={onClick} className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs text-muted-foreground hover:bg-muted">{icon}{label}</button>
}

function useSelectedSeries(stocks: BattleStock[], selectedCodes: string[]) {
  return useMemo(() => stocks.filter((item) => selectedCodes.includes(item.code)), [stocks, selectedCodes])
}

function toggleSelected(code: string, selected: string[], setSelected: (codes: string[]) => void) {
  setSelected(selected.includes(code) ? selected.filter((item) => item !== code) : [...selected, code])
}

function parseInput(input: string): string[] {
  return Array.from(new Set(input.split(/[\s,，;；、]+/).map((item) => item.trim()).filter(Boolean)))
}

async function resolveTargets(input: string): Promise<BattleTarget[]> {
  const tokens = parseInput(input)
  const rows = await Promise.all(tokens.map(resolveToken))
  const byCode = new Map<string, BattleTarget>()
  for (const row of rows) if (row) byCode.set(row.code, row)
  if (byCode.size === 0) throw new Error('请输入至少一只有效股票代码或名称')
  return [...byCode.values()]
}

async function resolveToken(token: string): Promise<BattleTarget | null> {
  const resolved = await resolveStockQuery(token)
  const code = resolved?.analysisCode || token.toUpperCase()
  if (!isSupportedKlineCode(code)) return null
  return { code, name: resolved?.name || code }
}

async function fetchBattleStocks(targets: BattleTarget[], apiKey: string): Promise<BattleStock[]> {
  const errors: string[] = []
  const stocks: BattleStock[] = []
  await Promise.all(
    targets.map(async (target) => {
      try {
        const result = await fetchOneBattleStock(target, apiKey)
        if (result) stocks.push(result)
        else errors.push(`${target.code}: 无数据`)
      } catch (err) {
        errors.push(`${target.code}: ${err instanceof Error ? err.message : '失败'}`)
      }
    }),
  )
  if (errors.length > 0) throw new Error(`K 线获取失败: ${errors.join(', ')}`)
  if (stocks.length === 0) throw new Error('没有获取到有效 K 线数据')
  return stocks
}

async function fetchOneBattleStock(target: BattleTarget, apiKey: string): Promise<BattleStock | null> {
  const data = await fetchKlineViaTickFlow(target.code, apiKey)
  if (data.length === 0) return null
  return { ...target, data, stats: computeStrengthStats(data) }
}

function computeStrengthStats(data: KlineData[]): StrengthStats {
  const latest = data[data.length - 1]!
  const ret20 = periodReturn(data, 20), ret60 = periodReturn(data, 60), ret120 = periodReturn(data, 120)
  const recent60 = data.slice(-60), high60 = Math.max(...recent60.map((row) => row.high))
  const volumeBase = data.length > 21 ? avg(data.slice(-21, -1).map((row) => row.volume)) : avg(data.map((row) => row.volume))
  const volumeRatio = volumeBase > 0 ? latest.volume / volumeBase : 0
  const drawdown60 = high60 > 0 ? (latest.close / high60 - 1) * 100 : 0
  const score = ret20 * 0.35 + ret60 * 0.35 + ret120 * 0.2 + drawdown60 * 0.1 + Math.min(volumeRatio, 3)
  return { latestClose: latest.close, ret20, ret60, ret120, drawdown60, volumeRatio, score }
}

function periodReturn(data: KlineData[], days: number): number {
  const latest = data[data.length - 1]?.close || 0
  const base = data[Math.max(0, data.length - days - 1)]?.close || latest
  return base > 0 ? (latest / base - 1) * 100 : 0
}

async function callBattleLLM(config: Parameters<typeof streamLLMResponse>[0], stocks: BattleStock[], signal?: AbortSignal): Promise<string> {
  const result = await streamLLMResponse(config, buildBattleMessages(stocks), { temperature: 0.45, maxTokens: 3500, signal })
  if (!result) throw new Error('模型未返回结果，请重试')
  return result
}

function buildBattleMessages(stocks: BattleStock[]) {
  return [
    { role: 'system' as const, content: '你是威科夫强弱对抗分析师。只基于给定行情数据比较股票相对强弱，输出强弱排序、胜出原因、落后风险、适合观察的触发价位。' },
    { role: 'user' as const, content: `请比较这些股票的强弱，并给出结论。\n\n${stocks.map(buildStockDigest).join('\n\n---\n\n')}` },
  ]
}

function buildStockDigest(stock: BattleStock): string {
  const rows = stock.data.slice(-60).map((row) => [row.date, row.open, row.high, row.low, row.close, Math.round(row.volume)].join(','))
  return [`## ${stock.code} ${stock.name}`, `score=${stock.stats.score.toFixed(2)} ret20=${stock.stats.ret20.toFixed(2)} ret60=${stock.stats.ret60.toFixed(2)} ret120=${stock.stats.ret120.toFixed(2)} drawdown60=${stock.stats.drawdown60.toFixed(2)} volumeRatio=${stock.stats.volumeRatio.toFixed(2)}`, '```csv', 'date,open,high,low,close,volume', ...rows, '```'].join('\n')
}

function normalizeBattleError(err: unknown): string {
  const message = err instanceof Error ? err.message : String(err)
  return message.includes(TICKFLOW_PURCHASE) ? upgradeMessage() : message
}

function upgradeMessage(): string {
  return `触发数据源并发请求限制，请升级数据源：${TICKFLOW_PURCHASE}`
}

function toComparisonSeries(stock: BattleStock): ComparisonSeries {
  return { code: stock.code, name: stock.name, data: stock.data }
}

function fmtPct(value: number): string {
  if (!Number.isFinite(value)) return '--'
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
}
