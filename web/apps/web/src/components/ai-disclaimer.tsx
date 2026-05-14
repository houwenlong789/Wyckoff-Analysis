import { TriangleAlert } from 'lucide-react'

export function AIDisclaimer() {
  return (
    <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
      <TriangleAlert size={14} className="mt-0.5 shrink-0" />
      <span>AI 分析基于威科夫方法论和历史量价数据，不构成投资建议。模型可能产生错误判断，市场存在不可预测风险。任何交易决策请结合个人风控纪律独立判断。</span>
    </div>
  )
}
