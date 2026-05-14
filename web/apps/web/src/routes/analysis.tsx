import { useDeferredValue, useEffect, useMemo, useRef, useState, type Dispatch, type FocusEvent, type KeyboardEvent, type SetStateAction } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { Loader2, Play, Search } from 'lucide-react'
import { supabase } from '@/lib/supabase'
import { useAuthStore } from '@/stores/auth'
import { loadLLMConfig } from '@/lib/chat-agent'
import { streamLLMResponse } from '@/lib/llm-stream'
import { MarkdownContent } from '@/components/markdown'
import { KlineChart } from '@/components/kline-chart'
import { usePreferences } from '@/lib/preferences'
import { AIDisclaimer } from '@/components/ai-disclaimer'
import { detectWyckoffAnnotations } from '@/lib/wyckoff-detect'
import { TICKFLOW_PURCHASE, fetchKline, getUserDataKeys, checkWhitelist, isCnSymbol, isSupportedKlineCode, type KlineData } from '@/lib/kline'
import { avg } from '@/lib/math'
import { marketLabel, resolveStockQuery, searchStocks, type StockSearchResult } from '@/lib/market-search'

interface AnalysisResult {
  report: string
  symbol: string
  name: string
  klineData: KlineData[]
}

export function AnalysisPage() {
  const user = useAuthStore((s) => s.user)
  const { t } = usePreferences()
  const search = useStockSearch()
  const prerequisites = usePrerequisites(user?.id)
  const runner = useAnalysisRunner(search, prerequisites.setHasModelConfig)
  const disabled = runner.loading || !search.symbol.trim() || prerequisites.checkingConfig || !prerequisites.hasModelConfig || !prerequisites.hasDataSource

  return (
    <div className="flex h-full flex-col p-6">
      <h1 className="mb-6 text-xl font-semibold">{t('analysis.title')}</h1>
      <MissingConfigBanner prerequisites={prerequisites} />
      <SearchForm search={search} loading={runner.loading} disabled={disabled} onAnalyze={runner.handleAnalyze} onClearError={() => runner.setError('')} />
      {runner.error && <div className="mb-4 rounded-lg bg-red-50 px-4 py-2.5 text-sm text-red-700 dark:bg-red-500/10 dark:text-red-200">{runner.error}</div>}
      <AnalysisContent runner={runner} />
    </div>
  )
}

interface SearchController {
  symbol: string
  selectedStock: StockSearchResult | null
  suggestions: StockSearchResult[]
  searchOpen: boolean
  searching: boolean
  activeIndex: number
  setSymbol: Dispatch<SetStateAction<string>>
  setSelectedStock: Dispatch<SetStateAction<StockSearchResult | null>>
  setSearchOpen: Dispatch<SetStateAction<boolean>>
  setActiveIndex: Dispatch<SetStateAction<number>>
  updateSymbol: (value: string) => void
  selectSuggestion: (item: StockSearchResult) => void
}

function useStockSearch(): SearchController {
  const [symbol, setSymbol] = useState('')
  const deferredSymbol = useDeferredValue(symbol)
  const [selectedStock, setSelectedStock] = useState<StockSearchResult | null>(null)
  const [searchOpen, setSearchOpen] = useState(false)
  const suggestionState = useSuggestionSearch(deferredSymbol, selectedStock)
  const { suggestions, searching, activeIndex } = suggestionState
  useUrlSymbol(setSymbol)

  function updateSymbol(value: string) {
    setSymbol(value)
    setSelectedStock(null)
    setSearchOpen(true)
  }

  function selectSuggestion(item: StockSearchResult) {
    setSelectedStock(item)
    setSymbol(item.analysisCode)
    setSearchOpen(false)
  }

  return {
    symbol, selectedStock, suggestions, searchOpen, searching, activeIndex,
    setSymbol, setSelectedStock, setSearchOpen, setActiveIndex: suggestionState.setActiveIndex, updateSymbol, selectSuggestion,
  }
}

function useUrlSymbol(setSymbol: Dispatch<SetStateAction<string>>) {
  const [searchParams] = useSearchParams()
  useEffect(() => {
    const code = searchParams.get('code')?.trim().toUpperCase()
    if (code && isSupportedKlineCode(code)) setSymbol(code)
  }, [searchParams, setSymbol])
}

function useSuggestionSearch(queryValue: string, selectedStock: StockSearchResult | null) {
  const [suggestions, setSuggestions] = useState<StockSearchResult[]>([])
  const [searching, setSearching] = useState(false)
  const [activeIndex, setActiveIndex] = useState(0)
  const selectedCode = selectedStock?.analysisCode

  useEffect(() => {
    const query = queryValue.trim()
    if (!query || selectedCode === query.toUpperCase()) {
      setSuggestions([])
      setSearching(false)
      return
    }
    let cancelled = false
    setSearching(true)
    searchStocks(query, 8)
      .then((rows) => {
        if (cancelled) return
        setSuggestions(rows)
        setActiveIndex(0)
      })
      .finally(() => { if (!cancelled) setSearching(false) })
    return () => { cancelled = true }
  }, [queryValue, selectedCode])

  return { suggestions, searching, activeIndex, setActiveIndex }
}

interface Prerequisites {
  checkingConfig: boolean
  hasModelConfig: boolean
  hasDataSource: boolean
  setHasModelConfig: Dispatch<SetStateAction<boolean>>
}

function usePrerequisites(userId: string | undefined): Prerequisites {
  const [checkingConfig, setCheckingConfig] = useState(true)
  const [hasModelConfig, setHasModelConfig] = useState(false)
  const [hasDataSource, setHasDataSource] = useState(false)

  useEffect(() => {
    if (!userId) return
    setCheckingConfig(true)
    void Promise.all([loadLLMConfig(userId), getUserDataKeys(userId), checkWhitelist(userId)])
      .then(([config, dataKeys, wl]) => {
        setHasModelConfig(Boolean(config?.api_key && config.model))
        setHasDataSource(Boolean(dataKeys.tickflow || dataKeys.tushare || wl))
      })
      .finally(() => setCheckingConfig(false))
  }, [userId])

  return { checkingConfig, hasModelConfig, hasDataSource, setHasModelConfig }
}

type AnalysisStep = 'resolve' | 'kline' | 'llm'

interface AnalysisRunnerState {
  loading: boolean
  result: AnalysisResult | null
  error: string
  step: AnalysisStep | null
  streamingReport: string
  earlyKline: { data: KlineData[]; symbol: string; name: string } | null
  setError: Dispatch<SetStateAction<string>>
  handleAnalyze: () => void
}

function useAnalysisRunner(search: SearchController, setHasModelConfig: Dispatch<SetStateAction<boolean>>): AnalysisRunnerState {
  const user = useAuthStore((s) => s.user)
  const { t } = usePreferences()
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<AnalysisResult | null>(null)
  const [error, setError] = useState('')
  const [step, setStep] = useState<AnalysisStep | null>(null)
  const [streamingReport, setStreamingReport] = useState('')
  const [earlyKline, setEarlyKline] = useState<{ data: KlineData[]; symbol: string; name: string } | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const streamBuf = useRef('')
  const rafRef = useRef(0)

  async function handleAnalyze() {
    setStep('resolve')
    const resolved = await resolveAnalysisCode(search.symbol, search.selectedStock)
    if (!resolved) { setError(t('analysis.invalidStockCode')); setStep(null); return }
    abortRef.current?.abort()
    const abort = (abortRef.current = new AbortController())
    setError(''); setLoading(true); setResult(null); setStreamingReport(''); setEarlyKline(null)
    search.setSymbol(resolved.code); search.setSelectedStock(resolved.stock); search.setSearchOpen(false)
    try {
      const [config, dataKeys] = await Promise.all([loadLLMConfig(user!.id), getUserDataKeys(user!.id)])
      setHasModelConfig(Boolean(config?.api_key && config?.model))
      if (!config?.api_key || !config.model) throw new Error(t('analysis.missingPrefix', { items: t('analysis.modelRequirement') }))
      setStep('kline')
      const [stockInfoResult, klineData] = await Promise.all([fetchStockName(resolved.code), fetchKline(resolved.code, dataKeys, user!.id)])
      if (klineData.length === 0) throw new Error(t('analysis.noKlineData'))
      const name = resolved.stock?.name || stockInfoResult.data?.name || resolved.code
      setEarlyKline({ data: klineData, symbol: resolved.code, name })
      setStep('llm'); streamBuf.current = ''
      const onDelta = (chunk: string) => { streamBuf.current += chunk; scheduleFlush(streamBuf, rafRef, setStreamingReport) }
      const report = await callLLM(config, resolved.code, name, buildKlinePayload(klineData), abort.signal, onDelta)
      cancelAnimationFrame(rafRef.current)
      if (abort.signal.aborted) return
      setStreamingReport(report)
      setResult({ report, symbol: resolved.code, name, klineData })
    } catch (err) {
      if (abort.signal.aborted) return
      setError(err instanceof Error ? err.message : t('analysis.failed'))
    } finally { cancelAnimationFrame(rafRef.current); setLoading(false); setStep(null) }
  }

  return { loading, result, error, step, streamingReport, earlyKline, setError, handleAnalyze }
}

function scheduleFlush(buf: React.MutableRefObject<string>, raf: React.MutableRefObject<number>, set: Dispatch<SetStateAction<string>>) {
  if (raf.current) return
  raf.current = requestAnimationFrame(() => { raf.current = 0; set(buf.current) })
}

function MissingConfigBanner({ prerequisites }: { prerequisites: Prerequisites }) {
  const navigate = useNavigate()
  const { t } = usePreferences()
  if (prerequisites.checkingConfig || (prerequisites.hasModelConfig && prerequisites.hasDataSource)) return null
  return (
    <div className="mb-6 rounded-xl border border-amber-200 bg-amber-50/80 p-4 dark:border-amber-500/30 dark:bg-amber-500/10">
      <h2 className="mb-2 text-sm font-semibold text-amber-900 dark:text-amber-100">{t('analysis.missingTitle')}</h2>
      <ul className="mb-3 list-disc space-y-1 pl-5 text-sm text-amber-800 dark:text-amber-200">
        {!prerequisites.hasModelConfig && <li>{t('analysis.missingModel')}</li>}
        {!prerequisites.hasDataSource && <li>{t('analysis.missingDataSource')}</li>}
      </ul>
      <button onClick={() => navigate('/settings')} className="rounded-lg bg-amber-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-amber-800">
        {t('analysis.goSettings')}
      </button>
    </div>
  )
}

function SearchForm({
  search,
  loading,
  disabled,
  onAnalyze,
  onClearError,
}: {
  search: SearchController
  loading: boolean
  disabled: boolean
  onAnalyze: () => void
  onClearError: () => void
}) {
  const { t } = usePreferences()
  return (
    <div className="mb-6">
      <div className="flex items-end gap-3">
        <StockSearchBox search={search} onAnalyze={onAnalyze} onClearError={onClearError} />
        <button onClick={onAnalyze} disabled={disabled} className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50">
          {loading ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}
          {loading ? t('analysis.analyzing') : t('analysis.start')}
        </button>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        {t('analysis.marketHint')}
        <a href={TICKFLOW_PURCHASE} target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">{t('common.tickflowLink')}</a>
      </p>
    </div>
  )
}

function StockSearchBox({ search, onAnalyze, onClearError }: { search: SearchController; onAnalyze: () => void; onClearError: () => void }) {
  const { t } = usePreferences()
  function handleChange(value: string) {
    search.updateSymbol(value)
    onClearError()
  }
  return (
    <div className="relative flex-1 max-w-md" onBlur={(e) => closeSearchOnOuterBlur(e, search.setSearchOpen)}>
      <label className="mb-1.5 block text-sm font-medium">{t('common.stockCode')}</label>
      <div className="relative">
        <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
        <input
          type="text"
          value={search.symbol}
          onChange={(e) => handleChange(e.target.value)}
          onFocus={() => search.setSearchOpen(true)}
          placeholder={t('analysis.searchPlaceholder')}
          maxLength={28}
          className="w-full rounded-lg border border-border bg-background py-2 pl-9 pr-3 text-sm outline-none focus:ring-2 focus:ring-ring/20"
          onKeyDown={(e) => handleSearchKeyDown(e, search, onAnalyze)}
          role="combobox"
          aria-expanded={search.searchOpen && search.suggestions.length > 0}
          aria-controls="analysis-stock-search"
        />
      </div>
      <SearchSuggestions search={search} />
    </div>
  )
}

function SearchSuggestions({ search }: { search: SearchController }) {
  const { t } = usePreferences()
  if (!search.searchOpen || !search.symbol.trim()) return null
  return (
    <div id="analysis-stock-search" className="absolute z-20 mt-1 max-h-72 w-full overflow-auto rounded-lg border border-border bg-popover py-1 shadow-lg" role="listbox">
      {search.searching && <LoadingSuggestion text={t('analysis.searching')} />}
      {!search.searching && search.suggestions.length === 0 && <div className="px-3 py-2 text-sm text-muted-foreground">{t('analysis.noSearchResults')}</div>}
      {!search.searching && search.suggestions.map((item, index) => (
        <SuggestionRow key={`${item.market}:${item.analysisCode}`} item={item} active={index === search.activeIndex} onClick={() => search.selectSuggestion(item)} />
      ))}
    </div>
  )
}

function SuggestionRow({ item, active, onClick }: { item: StockSearchResult; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      role="option"
      aria-selected={active}
      onMouseDown={(e) => e.preventDefault()}
      onClick={onClick}
      className={`flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm hover:bg-muted ${active ? 'bg-muted' : ''}`}
    >
      <span className="min-w-0">
        <span className="block truncate font-medium">{item.name || item.analysisCode}</span>
        <span className="block truncate text-xs text-muted-foreground">
          {item.analysisCode} · {marketLabel(item.market)}{item.assetType === 'etf' ? ' · ETF' : ''}
        </span>
      </span>
      <span className="shrink-0 rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground">{item.market.toUpperCase()}</span>
    </button>
  )
}

function LoadingSuggestion({ text }: { text: string }) {
  return <div className="flex items-center gap-2 px-3 py-2 text-sm text-muted-foreground"><Loader2 size={14} className="animate-spin" />{text}</div>
}

function AnalysisContent({ runner }: { runner: AnalysisRunnerState }) {
  const { result, loading, step, streamingReport, earlyKline } = runner
  if (!result && !loading) return <EmptyAnalysisState />

  const kline = result?.klineData ?? earlyKline?.data
  const symbol = result?.symbol ?? earlyKline?.symbol
  const name = result?.name ?? earlyKline?.name
  const report = result?.report ?? streamingReport

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      {step && <AnalysisProgressBar step={step} />}
      {symbol && name && <div className="mb-4 flex items-center gap-2"><span className="rounded-full bg-primary/10 px-3 py-1 text-sm font-medium text-primary">{symbol} {name}</span></div>}
      {kline && <KlineSection klineData={kline} />}
      {report && <ReportSection report={report} />}
    </div>
  )
}

function KlineSection({ klineData }: { klineData: KlineData[] }) {
  const { t } = usePreferences()
  const wyckoff = useMemo(() => detectWyckoffAnnotations(klineData), [klineData])
  return (
    <section className="mb-6">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
        <div><h2 className="text-base font-semibold">{t('analysis.chartTitle')}</h2><p className="mt-1 text-xs text-muted-foreground">{t('analysis.chartSubtitle')}</p></div>
        <span className="rounded-full border border-border px-2.5 py-1 text-xs text-muted-foreground">{klineData.length} {t('common.rows')}</span>
      </div>
      <KlineChart data={klineData} height={350} wyckoffMarkers={wyckoff?.markers} tradingRange={wyckoff?.tradingRange ?? undefined} stage={wyckoff?.stage} showIndicators />
    </section>
  )
}

function ReportSection({ report }: { report: string }) {
  const { t } = usePreferences()
  return (
    <div className="rounded-lg border border-border p-6">
      <h2 className="mb-4 text-base font-semibold">{t('analysis.reportTitle')}</h2>
      <AIDisclaimer />
      <article className="mt-4 prose prose-sm max-w-none text-foreground"><MarkdownContent content={report} /></article>
    </div>
  )
}

function AnalysisProgressBar({ step }: { step: AnalysisStep }) {
  const { t } = usePreferences()
  const stages: { key: AnalysisStep; label: string; pct: number }[] = [
    { key: 'resolve', label: t('analysis.progressResolve'), pct: 5 },
    { key: 'kline', label: t('analysis.progressKline'), pct: 30 },
    { key: 'llm', label: t('analysis.progressLLM'), pct: 60 },
  ]
  const current = stages.find((s) => s.key === step) ?? stages[0]!
  return (
    <div className="mb-4 rounded-lg border border-border bg-muted/10 px-4 py-2.5">
      <div className="mb-1.5 flex items-center justify-between text-xs">
        <span className="text-muted-foreground">{current.label}</span>
        <span className="font-mono text-muted-foreground">{current.pct}%</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-border">
        <div className="h-full rounded-full bg-indigo-500 transition-all duration-300" style={{ width: `${current.pct}%` }} />
      </div>
    </div>
  )
}

function EmptyAnalysisState() {
  const { t } = usePreferences()
  return (
    <div className="flex flex-1 items-center justify-center text-muted-foreground">
      <div className="text-center"><div className="mb-3 text-4xl">📊</div><p className="text-sm">{t('analysis.emptyTitle')}</p><p className="mt-1 text-xs">{t('analysis.emptySubtitle')}</p></div>
    </div>
  )
}

function closeSearchOnOuterBlur(e: FocusEvent<HTMLDivElement>, setSearchOpen: Dispatch<SetStateAction<boolean>>) {
  const next = e.relatedTarget as Node | null
  if (!next || !e.currentTarget.contains(next)) setSearchOpen(false)
}

function handleSearchKeyDown(e: KeyboardEvent<HTMLInputElement>, search: SearchController, onAnalyze: () => void) {
  if (!search.searchOpen || search.suggestions.length === 0) {
    if (e.key === 'Enter') onAnalyze()
    return
  }
  if (e.key === 'ArrowDown') { e.preventDefault(); search.setActiveIndex((idx) => Math.min(idx + 1, search.suggestions.length - 1)); return }
  if (e.key === 'ArrowUp') { e.preventDefault(); search.setActiveIndex((idx) => Math.max(idx - 1, 0)); return }
  if (e.key !== 'Enter') return
  e.preventDefault()
  const item = search.suggestions[search.activeIndex]
  if (item) search.selectSuggestion(item)
  else onAnalyze()
}

async function resolveAnalysisCode(rawInput: string, selected: StockSearchResult | null): Promise<{ code: string; stock: StockSearchResult | null } | null> {
  const raw = rawInput.trim()
  const stock = selected?.analysisCode === raw.toUpperCase() ? selected : await resolveStockQuery(raw)
  const code = stock?.analysisCode || (/^\d+$/.test(raw) ? raw : raw.toUpperCase())
  return isSupportedKlineCode(code) ? { code, stock } : null
}

async function fetchStockName(code: string): Promise<{ data: { name?: string } | null }> {
  if (!isCnSymbol(code)) return { data: null }
  const { data } = await supabase.from('recommendation_tracking').select('name').eq('code', parseInt(code, 10)).limit(1).single()
  return { data }
}

function buildKlinePayload(data: KlineData[]): string {
  const last = data[data.length - 1]!
  const prev20 = data.slice(-20)
  const ma5 = avg(data.slice(-5).map((d) => d.close))
  const ma20 = avg(prev20.map((d) => d.close))
  const ma50 = data.length >= 50 ? avg(data.slice(-50).map((d) => d.close)) : 0

  const summary = [
    `日线数据摘要（前复权，共${data.length}根，按日期升序）：`,
    `最新收盘：${last.close.toFixed(2)}`,
    `MA5=${ma5.toFixed(2)} MA20=${ma20.toFixed(2)}${ma50 ? ` MA50=${ma50.toFixed(2)}` : ''}`,
    `近20日最高：${Math.max(...prev20.map((d) => d.high)).toFixed(2)}`,
    `近20日最低：${Math.min(...prev20.map((d) => d.low)).toFixed(2)}`,
    `近5日平均量：${avg(data.slice(-5).map((d) => d.volume)).toFixed(0)}`,
    `近20日平均量：${avg(prev20.map((d) => d.volume)).toFixed(0)}`,
  ].join('\n')
  const csvRows = data.map((d) => [d.date, d.open.toFixed(2), d.high.toFixed(2), d.low.toFixed(2), d.close.toFixed(2), Math.round(d.volume)].join(','))

  return [
    summary, '',
    '以下是近320个交易日以内的完整日线OHLCV CSV数据。你必须读取这些数据进行判断，不要声称无法读取日线数据。',
    '```csv', 'date,open,high,low,close,volume', ...csvRows, '```',
  ].join('\n')
}

async function callLLM(config: Parameters<typeof streamLLMResponse>[0], code: string, name: string, klinePayload: string, signal?: AbortSignal, onDelta?: (chunk: string) => void): Promise<string> {
  const result = await streamLLMResponse(config, [
    { role: 'system', content: '你是威科夫分析大师，精通量价分析和威科夫方法。请对给定股票进行深度分析，包括：\n1. 当前所处威科夫阶段（积累/上涨/派发/下跌），Phase A-E 定位\n2. 量价关系分析（供需力量对比）\n3. 关键支撑与阻力位\n4. 主力意图判断\n5. 操作建议与风险提示（含止损位）\n\n请用简洁、专业的中文回答。使用 markdown 格式，结构清晰。' },
    { role: 'user', content: `请分析股票 ${code} ${name}。基于威科夫理论给出当前阶段判断和操作建议。\n\n${klinePayload}` },
  ], { temperature: 0.7, signal, onDelta })
  if (!result) throw new Error('模型未返回结果，请重试')
  return result
}
