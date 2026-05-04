import { memo, useMemo } from 'react'

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function renderMarkdown(content: string): string {
  const escaped = escapeHtml(content)
  return escaped
    .replace(/^### (.+)$/gm, '<h3 class="mt-3 mb-1.5 text-sm font-semibold">$1</h3>')
    .replace(/^## (.+)$/gm, '<h2 class="mt-4 mb-2 text-base font-semibold">$1</h2>')
    .replace(/^# (.+)$/gm, '<h1 class="mt-5 mb-2 text-lg font-bold">$1</h1>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/`([^`]+)`/g, '<code class="rounded bg-black/5 px-1 py-0.5 text-xs font-mono">$1</code>')
    .replace(/\|(.+)\|/gm, (match) => {
      const cells = match.split('|').filter(Boolean).map(c => c.trim())
      if (cells.every(c => /^-+$/.test(c))) return ''
      const tds = cells.map(c => `<td class="border border-border/50 px-2 py-1">${c}</td>`).join('')
      return `<tr>${tds}</tr>`
    })
    .replace(/^- (.+)$/gm, '<li class="ml-4 mb-0.5 list-disc">$1</li>')
    .replace(/^(\d+)\. (.+)$/gm, '<li class="ml-4 mb-0.5 list-decimal">$1. $2</li>')
    .replace(/\n\n/g, '</p><p class="mb-2">')
    .replace(/\n/g, '<br/>')
}

export const MarkdownContent = memo(function MarkdownContent({
  content,
  className = '',
}: {
  content: string
  className?: string
}) {
  const html = useMemo(() => renderMarkdown(content), [content])

  return (
    <div
      className={className}
      dangerouslySetInnerHTML={{ __html: `<p class="mb-2">${html}</p>` }}
    />
  )
})
