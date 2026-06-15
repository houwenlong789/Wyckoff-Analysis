export type ThoughtSignatureCache = Map<string, Record<string, unknown>>

export function createThoughtSignatureCache(): ThoughtSignatureCache {
  return new Map()
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function cacheKeyForToolCall(toolCall: Record<string, unknown>, fallbackIndex: number): string | null {
  const id = typeof toolCall.id === 'string' ? toolCall.id.trim() : ''
  if (id) return id
  const index = typeof toolCall.index === 'number' ? toolCall.index : fallbackIndex
  return `index:${index}`
}

/** 从 Gemini OpenAI 兼容 SSE chunk 提取 tool_calls.extra_content 写入缓存。 */
export function captureThoughtSignaturesFromChunk(evt: unknown, cache: ThoughtSignatureCache): void {
  if (!isRecord(evt) || !Array.isArray(evt.choices)) return

  for (const choice of evt.choices) {
    if (!isRecord(choice)) continue
    const delta = isRecord(choice.delta) ? choice.delta : null
    const message = isRecord(choice.message) ? choice.message : null
    const toolCalls = (delta?.tool_calls ?? message?.tool_calls) as unknown
    if (!Array.isArray(toolCalls)) continue

    toolCalls.forEach((toolCall, index) => {
      if (!isRecord(toolCall)) return
      const extraContent = toolCall.extra_content
      if (!isRecord(extraContent)) return
      const key = cacheKeyForToolCall(toolCall, index)
      if (!key) return
      cache.set(key, extraContent)
      if (typeof toolCall.index === 'number') {
        cache.set(`index:${toolCall.index}`, extraContent)
      }
    })
  }
}

/** 在发往 Gemini 的请求体中，为 assistant.tool_calls 补回 extra_content。 */
export function restoreThoughtSignaturesOnRequest(
  init: RequestInit | undefined,
  cache: ThoughtSignatureCache,
): RequestInit | undefined {
  if (!init?.body || typeof init.body !== 'string' || cache.size === 0) return init

  try {
    const body = JSON.parse(init.body) as Record<string, unknown>
    if (!Array.isArray(body.messages)) return init

    body.messages = body.messages.map((message) => {
      if (!isRecord(message) || message.role !== 'assistant' || !Array.isArray(message.tool_calls)) {
        return message
      }

      const toolCalls = message.tool_calls.map((toolCall, index) => {
        if (!isRecord(toolCall)) return toolCall
        const existing = toolCall.extra_content
        if (isRecord(existing) && isRecord(existing.google) && existing.google.thought_signature) {
          return toolCall
        }

        const key = cacheKeyForToolCall(toolCall, index)
        const cached = key ? cache.get(key) : undefined
        if (!cached) return toolCall
        return { ...toolCall, extra_content: cached }
      })

      return { ...message, tool_calls: toolCalls }
    })

    return { ...init, body: JSON.stringify(body) }
  } catch {
    return init
  }
}
