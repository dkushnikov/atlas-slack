/**
 * atlas-slack approval card — a Claude Code function-hooks module (mod).
 *
 * Why: the stock permission dialog for an MCP tool prints the whole tool input as
 * JSON (method, params, provenance with the decision token) and cuts the hook's
 * human text to three lines, so the person approves a blob (the owner, 2026-10-31).
 *
 * What: on `tool.check` for `slack_api`, when the engine's verdict is `ask` and the
 * call is a core send (chat.postMessage), the mod asks the person itself through
 * `$.ui.ask` (the engine's AskUserQuestion dialog; a mod may not call that tool any
 * other way — "host check"). The *question text* is the whole card — it is the one
 * part of that dialog a mod controls: option hints and previews added by a
 * `ui.render` rewrite were never drawn (live checks 2026-10-31 10:12 and 10:18).
 *
 * The card, per the owner's sketch (2026-10-31): where to (name · chat / thread / thread
 * + channel · now), from whom (voice and mark), the text (≤ 1000 chars, as sent),
 * then one line of the door's own checks (icon · name · status), one line of what the
 * context declared it checked (shown, not judged — A2) and one of what it did not. No
 * comm / op ids: they live in the journal and the result line.
 *
 * «Отправить» → allow: the call runs exactly as the PreToolUse hook rewrote it (the
 * decision token travels inside `e.input`, live-verified 2026-10-31). Anything else,
 * or a dismissed dialog → deny, and a deny before the call means nothing reaches
 * Slack. Text typed under «Type something» is an edit request: a deny whose reason
 * carries the words, so the model reworks the message and asks again. Any failure of the mod falls back to the stock dialog (`next(e)` replay),
 * never to an allow. Reads and transitional writes are untouched. A native
 * notification goes out before the dialog.
 */
import type { Register } from 'claude-code'

const SLACK_API = 'mcp__plugin_atlas-slack_atlas-slack__slack_api'
export const YES = 'Отправить'
export const NO = 'Нет'
export const HEADER = 'Slack'
export const TEXT_CAP = 2000       // 43 of 44 production sends fit (2026-10-31); longer → tail + Pane later

type SlackInput = {
  method?: string
  as_bot?: boolean
  params?: { channel?: string; text?: string; thread_ts?: string; reply_broadcast?: unknown; blocks?: unknown[] }
  provenance?: { comm_id?: string; checks?: Record<string, unknown>; sources?: unknown[]; origin?: string; resend?: boolean }
}

/** The gate's prompt without the engine's own prefix, split into its lines. */
function gateLines(reason: string | undefined): string[] {
  const raw = (reason ?? '').replace(/^Hook '[^']*' (?:blocked this action|requires approval[^:]*): /, '')
  return raw.split('\n').map(l => l.trimEnd()).filter(l => l.length > 0)
}

// "<sender> → <target>: “…” | сообщение ниже (N симв.) | message below (N chars) | blocks. <voice sentence> Отправить?"
const SENTENCE =
  /^(.+?) → (.+?): (?:“[\s\S]*?”|сообщение ниже \(\d+ [^)]*\)|message below \(\d+ [^)]*\)|blocks)\.\s*(.*?)\s*(?:Отправить\?|Send\?)\s*$/

// `ledger: …` / `intention: …` lines the gate adds when a ledger dir is configured (ledger.py).
const LEDGER_RE = /^ledger: (✅|☑|⚠) (.*)$/
const INTENTION_RE = /^intention: (.+)$/

const CHECK_LABEL: Record<string, string> = {
  cicero: 'Cicero (голос и контекст)',
  recipient: 'адресат и регистр',
  voice: 'голос',
  disclosure: 'раскрытие участия Atlas',
  mark: 'пометка',
  policy_row: 'строка политики',
  sources: 'источники',
}

// Content checks a send to others is expected to declare; absent ones are listed as not done.
const EXPECTED_CHECKS = ['cicero', 'recipient', 'disclosure']

export function buildCard(input: SlackInput, reason: string | undefined): string {
  const lines = gateLines(reason)
  const first = lines[0] ?? ''
  const m = SENTENCE.exec(first)
  const asBot = Boolean(input.as_bot)
  const sender = m?.[1] ?? (asBot ? 'Atlas' : 'владелец')
  const targetRaw = (m?.[2] ?? input.params?.channel ?? '?')
  const unresolved = /\(unresolved\)/.test(targetRaw) || !m
  const target = targetRaw.replace(/ \(в тред\)| \+ в канал| \(in thread\)| \+ also to the channel/g, '')

  const thread = Boolean(input.params?.thread_ts)
  const broadcast = String(input.params?.reply_broadcast ?? '').toLowerCase() === 'true'
  const format = thread ? (broadcast ? 'в тред + в канал' : 'в тред') : 'в чат'
  const when = 'сейчас'                            // scheduled sends reach the core in slice 2

  const text = typeof input.params?.text === 'string' ? input.params.text : ''
  const shown = text.length > TEXT_CAP
    ? text.slice(0, TEXT_CAP).trimEnd() + `\n… ещё ${text.length - TEXT_CAP} симв. — полный текст в транскрипте выше`
    : text
  const body = shown.length > 0 ? shown : `(без текста: ${input.params?.blocks?.length ?? 0} blocks)`

  const checks = input.provenance?.checks ?? {}
  const builtin = lines.some(l => l.startsWith('⚠') && /полити|policy/.test(l))
  // The gate appends `ledger:` / `intention:` just above its technical line; the message text sits
  // higher up, so read from the end and stop at the first line that is neither (a line of the
  // message that says «ledger: ✅ …» never reaches the card — verifier, 2026-10-31).
  // The gate emits `ledger:` then (optionally) `intention:`; the nearest `ledger:` to the end is the
  // real one and nothing above it is read, so quoted lines inside the message cannot win.
  let ledgerLine: RegExpExecArray | null = null
  let intention: string | undefined
  for (let i = lines.length - 1; i >= 0; i--) {
    const l = lines[i]!
    if (i === lines.length - 1 && l.startsWith('class ')) continue
    const asLedger = LEDGER_RE.exec(l)
    if (asLedger) { ledgerLine = asLedger; break }
    const asIntention = INTENTION_RE.exec(l)
    if (asIntention && intention === undefined) { intention = asIntention[1]; continue }
    break
  }
  if (!ledgerLine) intention = undefined          // an intention line is only trusted under a ledger line
  // Cicero on the door line comes from the ledger the gate read (✅ / ☑ / ⚠️). The context's own
  // `checks.cicero` claim always stays under «Контекст проверил» — shown, never a ✅.
  const cicero = ledgerLine
    ? `🎯 Cicero ${ledgerLine[1] === '⚠' ? '⚠️' : ledgerLine[1]} ${ledgerLine[2]!.replace(/ · .*$/, '')}`
    : null
  // The door's own checks, one line: icon · name · status. Details only where something is off.
  const door = [
    unresolved ? `👤 адресат ⚠️ id не распознан` : `👤 адресат ✅`,
    asBot ? `🎙 голос ✅ бот` : `🎙 голос ✅ владелец + 💙`,
    `🔏 привязка ✅`,
    `📓 журнал ✅`,
    builtin ? `📜 политика ⚠️ встроенная строгая` : `📜 политика ✅`,
    ...(cicero ? [cicero] : []),
    ...(input.provenance?.resend ? ['🔁 повтор ☑'] : []),
  ].join(' · ')
  // Content checks: what the context declares it did (shown, not judged — A2) and what it did not.
  const declared = Object.entries(checks).filter(([, v]) => v !== undefined && v !== null && v !== '')
  const done = declared.map(([k, v]) => `${CHECK_LABEL[k] ?? k} — ${String(v)}`)
  const expected = cicero ? EXPECTED_CHECKS.filter(k => k !== 'cicero') : EXPECTED_CHECKS
  const missing = expected.filter(k => !declared.some(([d]) => d === k)).map(k => CHECK_LABEL[k] ?? k)

  return [
    `Кому:    ${target} · ${format} · ${when}`,
    `От кого: ${sender}${asBot ? ' (голос бота)' : ' (твоим голосом, пометка 💙 Atlas)'}`,
    ...(intention ? [`Намерение: ${intention}`] : []),
    '',
    'Текст:',
    body,
    '',
    `Дверь:  ${door}`,
    `Контекст проверил:  ${done.length ? done.join(' · ') : 'ничего не заявлено'}`,
    ...(missing.length ? [`Не проверено:       ${missing.join(' · ')}`] : []),
    '',
    `${YES}?`,
  ].join('\n')
}

export const register: Register = on => {
  on('tool.check', { tool: SLACK_API }, async ($, e, next) => {
    const verdict = await next(e)
    if (verdict.decision !== 'ask') return verdict
    const input = (e.input ?? {}) as SlackInput
    if (input.method !== 'chat.postMessage') return verdict      // transitional writes keep the stock dialog
    const card = buildCard(input, verdict.reason)
    try {
      await $.ui.notify(card.split('\n')[0] ?? 'Slack send', { title: 'atlas-slack: нужно решение' })
    } catch {
      // a notification is a courtesy, never a gate
    }
    let answer: string
    try {
      answer = await $.ui.ask(card, { options: [YES, NO], header: HEADER })
    } catch (err) {
      const why = err instanceof Error ? err.message : String(err)
      return { decision: 'deny', reason: `atlas-slack: the approval dialog was dismissed (${why}) — nothing was sent` }
    }
    if (answer === YES) {
      return { decision: 'allow', reason: 'atlas-slack: approved on the card' }
    }
    if (answer === NO || answer.trim() === '') {
      return { decision: 'deny', reason: 'atlas-slack: declined on the card — nothing was sent' }
    }
    // Anything typed under «Type something» is an edit request: nothing is sent; the model reads it
    // as the tool error, reworks the text and comes back with a fresh card.
    return { decision: 'deny',
             reason: `atlas-slack: правка от владельца — «${answer.trim()}». Ничего не отправлено: перепиши сообщение с учётом правки и вызови отправку снова (новая карточка).` }
  }).catch(($, e, next) => (next.called ? next(e) : { decision: 'ask', reason: 'atlas-slack card failed — stock dialog' }))
}
