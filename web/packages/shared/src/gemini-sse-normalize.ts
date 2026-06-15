function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

export function normalizeGeminiToolCalls(toolCalls: unknown[]): unknown[] {
  return toolCalls.map((toolCall, toolCallIndex) => {
    if (!isRecord(toolCall)) return toolCall
    const normalized = { ...toolCall }
    if (typeof normalized.index !== 'number') normalized.index = toolCallIndex
    // Preserve extra_content.google.thought_signature for Gemini 3 multi-turn tool calling.
    return normalized
  })
}

/** Normalize Gemini OpenAI-compat SSE chunks (add tool_call index, keep thought signatures). */
export function normalizeGeminiChunk(payload: unknown): unknown {
  if (!isRecord(payload) || !Array.isArray(payload.choices)) return payload

  payload.choices = payload.choices.map((choice, choiceIndex) => {
    if (!isRecord(choice)) return choice
    if (typeof choice.index !== 'number') choice.index = choiceIndex

    if (isRecord(choice.delta) && Array.isArray(choice.delta.tool_calls)) {
      choice.delta.tool_calls = normalizeGeminiToolCalls(choice.delta.tool_calls)
    }
    if (isRecord(choice.message) && Array.isArray(choice.message.tool_calls)) {
      choice.message.tool_calls = normalizeGeminiToolCalls(choice.message.tool_calls)
    }
    return choice
  })

  return payload
}

export function normalizeGeminiSseLine(line: string): string {
  if (!line.startsWith('data: ')) return line
  const data = line.slice(6).trim()
  if (!data || data === '[DONE]') return line

  try {
    return `data: ${JSON.stringify(normalizeGeminiChunk(JSON.parse(data)))}`
  } catch {
    return line
  }
}

export function normalizeGeminiStream(body: ReadableStream<Uint8Array>): ReadableStream<Uint8Array> {
  const decoder = new TextDecoder()
  const encoder = new TextEncoder()
  let buffer = ''

  return body.pipeThrough(new TransformStream<Uint8Array, Uint8Array>({
    transform(chunk, controller) {
      buffer += decoder.decode(chunk, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        controller.enqueue(encoder.encode(`${normalizeGeminiSseLine(line.replace(/\r$/, ''))}\n`))
      }
    },
    flush(controller) {
      buffer += decoder.decode()
      if (!buffer) return
      const lines = buffer.split('\n')
      for (const line of lines) {
        if (line) controller.enqueue(encoder.encode(`${normalizeGeminiSseLine(line.replace(/\r$/, ''))}\n`))
      }
    },
  }))
}
