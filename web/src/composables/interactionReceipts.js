// Presentation is independent of generation. Keep unacknowledged receipts across
// turn changes; a retry reuses event IDs so the server can apply it exactly once.
export function createReceiptReporter({ send, onError = () => {}, delayMs = 80 }) {
  const pending = new Map()
  let timer = null
  let inFlight = null
  let disposed = false
  let sequence = 0
  const clientId = globalThis.crypto?.randomUUID?.() || `receipt-${Date.now()}-${Math.random()}`

  function report(roleName, turnId, receipt) {
    if (disposed || !roleName || !turnId || !receipt.segment_ids?.length) return
    const key = `${roleName}\n${turnId}`
    if (!pending.has(key)) pending.set(key, { role_name: roleName, turn_id: turnId, receipts: [] })
    pending.get(key).receipts.push({ ...receipt, event_id: receipt.event_id || `${clientId}:${++sequence}` })
    schedule()
  }

  function schedule() {
    if (timer !== null || disposed) return
    timer = setTimeout(() => {
      timer = null
      void flush().catch(() => {}) // onError reports the failure; data stays pending.
    }, delayMs)
  }

  async function flush() {
    if (timer !== null) clearTimeout(timer)
    timer = null
    if (inFlight) {
      await inFlight
      if (pending.size) return flush()
      return
    }
    if (!pending.size) return
    inFlight = (async () => {
      for (const [key, group] of [...pending]) {
        // A bounded batch also keeps page-unload keepalive bodies small.
        while (!group.discarded && group.receipts.length) {
          const batch = group.receipts.slice(0, 32)
          try {
            await send({ role_name: group.role_name, turn_id: group.turn_id, receipts: batch })
          } catch (error) {
            if (!group.discarded) throw error
          }
          group.receipts.splice(0, batch.length)
        }
        if (pending.get(key) === group && !group.receipts.length) pending.delete(key)
      }
    })()
    try {
      await inFlight
    } catch (error) {
      onError(error)
      throw error
    } finally {
      inFlight = null
    }
  }

  // Only use after an authoritative history clear. Network failures alone must
  // retain evidence; tombstoned turns must not block unrelated future receipts.
  function discard() {
    if (timer !== null) clearTimeout(timer)
    timer = null
    for (const group of pending.values()) group.discarded = true
    pending.clear()
  }

  function playback(event, fallbackRole) {
    const state = { started: 'started', completed: 'completed', interrupted: 'interrupted', failed: 'failed' }[event.type]
    if (!state) return
    report(event.roleName || fallbackRole, event.turnId, {
      segment_ids: event.segmentIds || [], channel: 'audio', state,
      played_ms: Math.max(0, Number(event.playedMs) || 0),
    })
  }

  async function dispose() {
    disposed = true
    await flush()
  }

  return { report, playback, flush, dispose, discard, get pendingCount() {
    return [...pending.values()].reduce((count, group) => count + group.receipts.length, 0)
  } }
}
