import { computed, ref, watch, type Ref } from 'vue'

/** Local form history. Server saves are explicit checkpoints, never undone silently. */
export function useLocalHistory<T>(source: Ref<T>, limit = 80) {
  const encode = () => JSON.stringify({ value: source.value })
  const past = ref<string[]>([]), future = ref<string[]>([])
  let current = encode(), paused = false, group = ''
  function record(key = '') {
    const next = encode()
    if (next === current) return
    if (!key || key !== group) {
      past.value.push(current)
      if (past.value.length > limit) past.value.shift()
    }
    current = next; group = key; future.value = []
  }
  watch(source, () => { if (!paused) record() }, { deep: true, flush: 'sync' })
  function transaction(change: () => void, key = '') {
    paused = true
    try { change() } finally { paused = false }
    record(key)
  }
  function restore(value: string) {
    paused = true
    try { source.value = JSON.parse(value).value; current = value; group = '' } finally { paused = false }
  }
  function undo() {
    const value = past.value.pop()
    if (value === undefined) return
    future.value.push(current); restore(value)
  }
  function redo() {
    const value = future.value.pop()
    if (value === undefined) return
    past.value.push(current); restore(value)
  }
  function reset(value: T) {
    restore(JSON.stringify({ value })); past.value = []; future.value = []
  }
  return { undo, redo, reset, transaction, endGroup: () => { group = '' },
    canUndo: computed(() => past.value.length > 0), canRedo: computed(() => future.value.length > 0) }
}
