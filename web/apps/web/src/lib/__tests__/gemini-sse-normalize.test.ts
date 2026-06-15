import { describe, expect, it } from 'vitest'
import {
  normalizeGeminiChunk,
  normalizeGeminiSseLine,
  normalizeGeminiToolCalls,
} from '@wyckoff/shared'

describe('normalizeGeminiToolCalls', () => {
  it('adds missing index without stripping extra_content', () => {
    const input = [{
      id: 'call-1',
      type: 'function',
      function: { name: 'view_portfolio', arguments: '{}' },
      extra_content: {
        google: { thought_signature: 'CvcQAdHtim/pKv/c0ClPFkYA==' },
      },
    }]

    const out = normalizeGeminiToolCalls(input) as Array<Record<string, unknown>>
    expect(out[0]?.index).toBe(0)
    expect(out[0]?.extra_content).toEqual({
      google: { thought_signature: 'CvcQAdHtim/pKv/c0ClPFkYA==' },
    })
  })

  it('keeps existing index', () => {
    const input = [{ index: 2, id: 'call-2', type: 'function', function: { name: 'x', arguments: '{}' } }]
    const out = normalizeGeminiToolCalls(input) as typeof input
    expect(out[0]?.index).toBe(2)
  })
})

describe('normalizeGeminiChunk', () => {
  it('normalizes streaming delta tool_calls', () => {
    const chunk = {
      choices: [{
        delta: {
          role: 'assistant',
          tool_calls: [{
            id: 'call-1',
            type: 'function',
            function: { name: 'get_weather', arguments: '{}' },
            extra_content: { google: { thought_signature: 'sig-abc' } },
          }],
        },
        index: 0,
      }],
    }

    const out = normalizeGeminiChunk(chunk) as Record<string, unknown>
    const choices = out.choices as Array<Record<string, unknown>>
    const delta = choices[0]?.delta as Record<string, unknown>
    const toolCall = (delta.tool_calls as Array<Record<string, unknown>>)[0]
    expect(toolCall?.index).toBe(0)
    expect(toolCall?.extra_content).toEqual({ google: { thought_signature: 'sig-abc' } })
  })

  it('normalizes non-streaming message tool_calls', () => {
    const chunk = {
      choices: [{
        message: {
          role: 'assistant',
          tool_calls: [{
            id: 'call-1',
            type: 'function',
            function: { name: 'view_portfolio', arguments: '{}' },
            extra_content: { google: { thought_signature: 'sig-xyz' } },
          }],
        },
        finish_reason: 'tool_calls',
      }],
    }

    const out = normalizeGeminiChunk(chunk) as Record<string, unknown>
    const choices = out.choices as Array<Record<string, unknown>>
    const message = choices[0]?.message as Record<string, unknown>
    const toolCall = (message.tool_calls as Array<Record<string, unknown>>)[0]
    expect(toolCall?.index).toBe(0)
    expect(toolCall?.extra_content).toEqual({ google: { thought_signature: 'sig-xyz' } })
  })
})

describe('normalizeGeminiSseLine', () => {
  it('rewrites data lines while preserving thought signatures', () => {
    const payload = {
      choices: [{
        delta: {
          tool_calls: [{
            id: 'call-1',
            type: 'function',
            function: { name: 'view_portfolio', arguments: '{}' },
            extra_content: { google: { thought_signature: 'keep-me' } },
          }],
        },
        index: 0,
      }],
    }
    const line = `data: ${JSON.stringify(payload)}`
    const normalized = normalizeGeminiSseLine(line)
    const parsed = JSON.parse(normalized.slice(6)) as Record<string, unknown>
    const choices = parsed.choices as Array<Record<string, unknown>>
    const delta = choices[0]?.delta as Record<string, unknown>
    const toolCall = (delta.tool_calls as Array<Record<string, unknown>>)[0]
    expect(toolCall?.extra_content).toEqual({
      google: { thought_signature: 'keep-me' },
    })
  })

  it('passes through [DONE] unchanged', () => {
    expect(normalizeGeminiSseLine('data: [DONE]')).toBe('data: [DONE]')
  })
})
