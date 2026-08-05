const EXPRESSION_KEYWORDS = [
  { regex: /哈哈|嘻嘻|嘿嘿|开心|高兴|太棒|真好|喜欢|不错|很棒|厉害|好玩|有趣|可爱|笑|乐/, expression: 'happy', action: 'clapping' },
  { regex: /难过|伤心|哭|呜呜|疼|痛|可怜|遗憾|可惜|失望|不好/, expression: 'sad', action: 'sad' },
  { regex: /生气|哼|讨厌|烦|滚|坏|可恶|过分|不行|不准/, expression: 'angry', action: 'angry' },
  { regex: /啊$|什么!|天哪|居然|哇|哦\?|吓|惊|不得了|怎么会/, expression: 'surprised', action: 'surprised' },
]

export function guessExpression(text) {
  const normalized = text.toLowerCase()
  for (const rule of EXPRESSION_KEYWORDS) {
    if (rule.regex.test(normalized)) {
      return { expression: rule.expression, intensity: 0.8, action: rule.action }
    }
  }
  return { expression: 'neutral', intensity: 0.5, action: 'none' }
}
