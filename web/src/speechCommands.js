// 语音控制指令识别：只匹配很短的整句，避免把正文里的“继续”误当指令。

const NON_WORD = /[\s\p{P}\p{S}]+/gu
// ASR 常在指令前后加语气词：“呃，等一下先停一下”“嗯，继续吧”。
const LEADING_FILLERS = /^(?:嗯+|呃+|唉+|哎+|啊+|哦+|噢+|唔+|那个|就是)+/u
const TRAILING_PARTICLES = /(?:呀|啊|吧|呢|嘛|哦|噢|咯|啦|呗)+$/u

export function normalizeSpeech(text) {
  return String(text || '').toLowerCase().replace(NON_WORD, '')
}

export function normalizeCommand(text) {
  return normalizeSpeech(text)
    .replace(LEADING_FILLERS, '')
    .replace(TRAILING_PARTICLES, '')
}

const CONTINUE_PATTERNS = [
  /^(请)?(继续|接着)(说|讲|播放|念)?(下去|吧|呀|啊|吗)?$/,
  /^(接着|继续)(刚才|前面|上面|上一句|刚才的话)?(说|讲|播放)?(下去|吧)?$/,
  /^(然后呢|接下来呢?|往下说|说下去|不要停|别停|继续播放)$/,
  /^(goon|continue|keepgoing|resume)$/,
]

const PAUSE_PATTERNS = [
  /^(请)?((暂停|停一下|停一停|先停一下|先停一停|先停|停停|别说了|不要说了|等一下|等等|安静|stop|pause))+$/,
]

// 打断后用户可能只是“嗯/哦/好的”等附和，不应把它当成新问题。
const BACKCHANNEL_PATTERN = /^(嗯+|哦+|噢+|啊+|唔+|好+|好的|好呀|对+|是的|对的|行|可以|这样啊|原来如此|ok|okay)+$/

function matchesCommand(patterns, text) {
  const normalized = normalizeSpeech(text)
  if (!normalized) return false
  const stripped = normalizeCommand(text)
  const candidates = stripped && stripped !== normalized ? [normalized, stripped] : [normalized]
  return candidates.some((candidate) => (
    candidate.length > 0
    && candidate.length <= 12
    && patterns.some((pattern) => pattern.test(candidate))
  ))
}

export function isContinueCommand(text) {
  return matchesCommand(CONTINUE_PATTERNS, text)
}

export function isPauseCommand(text) {
  return matchesCommand(PAUSE_PATTERNS, text)
}

export function isBackchannel(text) {
  const normalized = normalizeSpeech(text)
  return Boolean(normalized) && normalized.length <= 10 && BACKCHANNEL_PATTERN.test(normalized)
}

function bigrams(text) {
  const result = new Map()
  for (let index = 0; index < text.length - 1; index += 1) {
    const gram = text.slice(index, index + 2)
    result.set(gram, (result.get(gram) || 0) + 1)
  }
  return result
}

function bigramCoverage(left, right) {
  const a = bigrams(left)
  const b = bigrams(right)
  if (!a.size || !b.size) return 0
  let intersection = 0
  for (const [gram, count] of a) {
    intersection += Math.min(count, b.get(gram) || 0)
  }
  return intersection / Math.min(a.size, b.size)
}

// 扬声器回声兜底：AEC 之后仍可能残留可被 ASR 识别的语音。
// 用户说的话如果高度重合于角色正在说的内容，视为回声而不是新一轮输入。
export function isLikelyEcho(userText, assistantText) {
  const user = normalizeSpeech(userText)
  const assistant = normalizeSpeech(assistantText)
  if (user.length < 4 || assistant.length < 4) return false
  if (assistant.includes(user) || user.includes(assistant)) return true
  // 只有几个字的模糊重合不算回声，避免把“今天天气”这类短问题吞掉。
  if (user.length < 6) return false
  // 用户话明显比角色台词长，说明更像新的问题，而不是回声片段。
  if (user.length > assistant.length * 1.6 + 2) return false
  return bigramCoverage(user, assistant) >= 0.8
}
