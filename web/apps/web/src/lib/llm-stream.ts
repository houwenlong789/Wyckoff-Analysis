import type { LLMConfig } from './chat-agent'

interface ChatMessage {
  role: 'system' | 'user' | 'assistant'
  content: string
}

export async function streamLLMResponse(
  config: LLMConfig,
  messages: ChatMessage[],
  opts: { temperature?: number; maxTokens?: number; signal?: AbortSignal; onDelta?: (chunk: string) => void } = {},
): Promise<string> {
  const response = await fetch('/api/llm-proxy/chat/completions', {
    method: 'POST',
    signal: opts.signal,
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${config.api_key}`,
      'X-Target-URL': config.base_url,
    },
    body: JSON.stringify({
      model: config.model,
      messages,
      temperature: opts.temperature ?? 0.5,
      max_tokens: opts.maxTokens ?? 4096,
      stream: true,
    }),
  })

  if (!response.ok) {
    const err = await response.json().catch(() => ({}))
    throw new Error(err.error?.message || `模型请求失败 (${response.status})`)
  }

  const reader = response.body?.getReader()
  if (!reader) throw new Error('响应无可读流')

  const decoder = new TextDecoder()
  let result = ''
  let buffer = ''

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop()!
    for (const line of lines) {
      const trimmed = line.trim()
      if (!trimmed.startsWith('data: ')) continue
      const payload = trimmed.slice(6)
      if (payload === '[DONE]') break
      try {
        const json = JSON.parse(payload)
        const delta = json.choices?.[0]?.delta?.content
        if (delta) { opts.onDelta?.(delta); result += delta }
      } catch { /* skip malformed */ }
    }
  }
  return result
}
