export function avg(values: number[]): number {
  return values.length > 0 ? values.reduce((sum, v) => sum + v, 0) / values.length : 0
}

export function sma(closes: number[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(closes.length).fill(null)
  if (closes.length < period) return out
  let sum = 0
  for (let i = 0; i < period; i++) sum += closes[i]!
  out[period - 1] = sum / period
  for (let i = period; i < closes.length; i++) {
    sum += closes[i]! - closes[i - period]!
    out[i] = sum / period
  }
  return out
}

export function ema(closes: number[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(closes.length).fill(null)
  if (closes.length < period) return out
  let sum = 0
  for (let i = 0; i < period; i++) sum += closes[i]!
  let prev = sum / period
  out[period - 1] = prev
  const k = 2 / (period + 1)
  for (let i = period; i < closes.length; i++) {
    prev = closes[i]! * k + prev * (1 - k)
    out[i] = prev
  }
  return out
}

export interface RSIResult { values: (number | null)[] }

export function rsi(closes: number[], period = 14): RSIResult {
  const out: (number | null)[] = new Array(closes.length).fill(null)
  if (closes.length < period + 1) return { values: out }

  let gainSum = 0, lossSum = 0
  for (let i = 1; i <= period; i++) {
    const diff = closes[i]! - closes[i - 1]!
    if (diff > 0) gainSum += diff
    else lossSum -= diff
  }
  let avgGain = gainSum / period
  let avgLoss = lossSum / period
  out[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss)

  for (let i = period + 1; i < closes.length; i++) {
    const diff = closes[i]! - closes[i - 1]!
    avgGain = (avgGain * (period - 1) + Math.max(diff, 0)) / period
    avgLoss = (avgLoss * (period - 1) + Math.max(-diff, 0)) / period
    out[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss)
  }
  return { values: out }
}

export interface MACDResult {
  macd: (number | null)[]
  signal: (number | null)[]
  histogram: (number | null)[]
}

export function macd(closes: number[], fast = 12, slow = 26, sig = 9): MACDResult {
  const fastEma = ema(closes, fast)
  const slowEma = ema(closes, slow)
  const macdLine: (number | null)[] = new Array(closes.length).fill(null)

  for (let i = 0; i < closes.length; i++) {
    if (fastEma[i] != null && slowEma[i] != null) macdLine[i] = fastEma[i]! - slowEma[i]!
  }

  const validMacd = macdLine.filter((v): v is number => v != null)
  const signalEma = ema(validMacd, sig)

  const signal: (number | null)[] = new Array(closes.length).fill(null)
  const histogram: (number | null)[] = new Array(closes.length).fill(null)

  let validIdx = 0
  for (let i = 0; i < closes.length; i++) {
    if (macdLine[i] == null) continue
    if (signalEma[validIdx] != null) {
      signal[i] = signalEma[validIdx]!
      histogram[i] = macdLine[i]! - signalEma[validIdx]!
    }
    validIdx++
  }
  return { macd: macdLine, signal, histogram }
}

export interface BollingerResult {
  upper: (number | null)[]
  middle: (number | null)[]
  lower: (number | null)[]
}

export function bollinger(closes: number[], period = 20, mult = 2): BollingerResult {
  const upper: (number | null)[] = new Array(closes.length).fill(null)
  const middle: (number | null)[] = new Array(closes.length).fill(null)
  const lower: (number | null)[] = new Array(closes.length).fill(null)
  if (closes.length < period) return { upper, middle, lower }

  let sum = 0
  for (let i = 0; i < period; i++) sum += closes[i]!

  for (let i = period - 1; i < closes.length; i++) {
    if (i > period - 1) sum += closes[i]! - closes[i - period]!
    const ma = sum / period
    let sqSum = 0
    for (let j = i - period + 1; j <= i; j++) sqSum += (closes[j]! - ma) ** 2
    const std = Math.sqrt(sqSum / period)
    middle[i] = ma
    upper[i] = ma + mult * std
    lower[i] = ma - mult * std
  }
  return { upper, middle, lower }
}
