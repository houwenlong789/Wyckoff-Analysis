import { describe, expect, it } from 'vitest'
import {
  captureThoughtSignaturesFromChunk,
  createThoughtSignatureCache,
  restoreThoughtSignaturesOnRequest,
} from '../gemini-thought-signatures'

describe('gemini thought signatures', () => {
  it('captures extra_content from streaming delta', () => {
    const cache = createThoughtSignatureCache()
    captureThoughtSignaturesFromChunk(
      {
        choices: [{
          delta: {
            tool_calls: [{
              id: 'call-1',
              index: 0,
              type: 'function',
              function: { name: 'view_portfolio', arguments: '{}' },
              extra_content: { google: { thought_signature: 'sig-abc' } },
            }],
          },
        }],
      },
      cache,
    )

    expect(cache.get('call-1')).toEqual({ google: { thought_signature: 'sig-abc' } })
    expect(cache.get('index:0')).toEqual({ google: { thought_signature: 'sig-abc' } })
  })

  it('restores extra_content on follow-up chat completion request', () => {
    const cache = createThoughtSignatureCache()
    cache.set('call-1', { google: { thought_signature: 'sig-abc' } })

    const init = restoreThoughtSignaturesOnRequest(
      {
        method: 'POST',
        body: JSON.stringify({
          model: 'gemini-3.1-flash-lite',
          messages: [
            { role: 'user', content: '我有什么持仓' },
            {
              role: 'assistant',
              content: null,
              tool_calls: [{
                id: 'call-1',
                type: 'function',
                function: { name: 'view_portfolio', arguments: '{}' },
              }],
            },
            { role: 'tool', tool_call_id: 'call-1', content: '持仓 2 只' },
          ],
        }),
      },
      cache,
    )

    const body = JSON.parse(String(init?.body)) as {
      messages: Array<{ tool_calls?: Array<Record<string, unknown>> }>
    }
    const toolCall = body.messages[1]?.tool_calls?.[0]
    expect(toolCall?.extra_content).toEqual({ google: { thought_signature: 'sig-abc' } })
  })
})
