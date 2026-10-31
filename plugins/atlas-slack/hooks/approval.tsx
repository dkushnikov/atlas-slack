/**
 * atlas-slack approval card — a Claude Code function-hooks module (mod).
 *
 * Why: the stock permission dialog for an MCP tool prints the whole tool input as
 * JSON (method, params, provenance with the decision token) and cuts the hook's
 * human text to three lines, so the person approves a blob (the owner, 2026-10-31).
 *
 * What: on `tool.check` for `slack_api`, when the engine's verdict is `ask` and the
 * call is a core send (chat.postMessage, chat.scheduleMessage), the mod asks the person itself through
 * `$.ui.ask` (the engine's AskUserQuestion dialog; a mod may not call that tool any
 * other way — "host check"). The *question text* is the whole card — the one part
 * of that dialog a mod controls (option hints and previews added by a `ui.render`
 * rewrite are never drawn; live checks 2026-10-31).
 *
 * The card, per the owner's sketch: where to (name · chat / thread / thread + channel ·
 * now), from whom (voice and mark), intention (from the ledger), the text (≤ 2000),
 * then one line of the door's own checks (icon · name · mark), one line of what the
 * context declared it checked (shown, not judged — A2) and one of what it did not.
 * Marks: ✅ verified · 🟡 declared / skipped / built-in policy · 🔴 missing, not
 * found, mismatch. No comm / op ids: they live in the journal and the result line.
 * Language follows the gate's prompt (profile `language`): ru or en.
 *
 * «Send» → allow: the call runs exactly as the PreToolUse hook rewrote it (the
 * decision token travels inside `e.input`, live-verified 2026-10-31). «No», a
 * dismissed dialog → deny; text typed under «Type something» is an edit request: a
 * deny whose reason carries the words, so the model reworks and asks again. A deny
 * before the call means nothing reaches Slack. Any failure of the mod falls back to
 * the stock dialog (`next(e)` replay), never to an allow. Reads and transitional
 * writes are untouched. A native notification goes out before the dialog.
 */
import type { Register } from 'claude-code'

const SLACK_API = 'mcp__plugin_atlas-slack_atlas-slack__slack_api'
export const HEADER = 'Slack'
export const TEXT_CAP = 2000       // 43 of 44 production sends fit (2026-10-31); longer → tail + Pane later

export const OK = '✅'
export const WARN = '🟡'
export const BAD = '🔴'

type Lang = 'ru' | 'en'

const L: Record<Lang, Record<string, string>> = {
  ru: {
    yes: 'Отправить', no: 'Нет', ask: 'Отправить?',
    to: 'Кому:    ', from: 'От кого: ', intention: 'Намерение: ', text: 'Текст:',
    chat: 'в чат', thread: 'в тред', threadChannel: 'в тред + в канал', now: 'сейчас',
    botVoice: '(голос бота)', personVoice: '(твоим голосом, пометка 💙 Atlas)',
    more: 'ещё {n} симв. — полный текст в транскрипте выше', noText: '(без текста: {n} blocks)', inThread: '(в тред {ts})',
    door: 'Дверь:  ', addressee: '👤 адресат', unresolved: 'id не распознан', voice: '🎙 голос', bot: 'бот', person: 'владелец + 💙',
    binding: '🔏 привязка', journal: '📓 журнал', policy: '📜 политика', builtin: 'встроенная строгая', cicero: '🎯 Cicero', resend: '🔁 повтор',
    declared: 'Контекст проверил:  ', nothing: 'ничего не заявлено', missing: 'Не проверено:       ',
    defaultSender: 'владелец', defaultBot: 'Atlas', defaultBotVoice: 'Голос бота.', defaultPersonVoice: 'Твоим голосом.',
    lblCicero: 'Cicero (голос и контекст)', lblRecipient: 'адресат и регистр', lblVoice: 'голос', lblDisclosure: 'раскрытие участия Atlas',
    lblMark: 'пометка', lblPolicyRow: 'строка политики', lblSources: 'источники',
    notify: 'atlas-slack: нужно решение',
    do: 'Выполнить', doAsk: 'Выполнить?', action: 'Действие: ', byBot: 'от Atlas', byYou: 'от тебя (твой аккаунт)', where: 'Где:      ', when: 'Когда:    ', message: 'сообщение', user: 'для',
    transitional: '🔏 привязка 🔴 нет · 📓 журнал 🔴 нет — переходный путь (не через ядро двери, срез 2)',
    mReactAdd: '👍 поставить реакцию :{name}:', mReactRemove: '👎 убрать реакцию :{name}:', mDelete: '🗑 удалить сообщение',
    mUpdate: '✏️ изменить сообщение', mSchedule: '⏰ запланировать сообщение', mEphemeral: '👁 показать сообщение только одному человеку',
    mPinAdd: '📌 закрепить сообщение', mPinRemove: '📌 открепить сообщение', mUpload: '📎 загрузить файл {name}', mOpen: '💬 открыть DM',
    mOther: 'Slack write: {method}',
    denyNo: 'atlas-slack: отклонено на карточке — ничего не отправлено',
    denyDismissed: 'atlas-slack: диалог одобрения закрыт (Esc) — ничего не отправлено; спроси человека, что делать дальше',
    denyEdit: 'atlas-slack: правка от владельца — «{t}». Ничего не отправлено: перепиши сообщение с учётом правки и вызови отправку снова (новая карточка).',
  },
  en: {
    yes: 'Send', no: 'No', ask: 'Send?',
    to: 'To:      ', from: 'From:    ', intention: 'Intention: ', text: 'Text:',
    chat: 'in chat', thread: 'in thread', threadChannel: 'in thread + channel', now: 'now',
    botVoice: '(bot voice)', personVoice: '(your voice, marked 💙 Atlas)',
    more: '{n} more chars — full text in the transcript above', noText: '(no text: {n} blocks)', inThread: '(thread {ts})',
    door: 'Door:   ', addressee: '👤 recipient', unresolved: 'id unresolved', voice: '🎙 voice', bot: 'bot', person: 'you + 💙',
    binding: '🔏 binding', journal: '📓 journal', policy: '📜 policy', builtin: 'built-in strict', cicero: '🎯 Cicero', resend: '🔁 resend',
    declared: 'Context checked:  ', nothing: 'nothing declared', missing: 'Not checked:      ',
    defaultSender: 'You', defaultBot: 'Atlas', defaultBotVoice: 'Bot voice.', defaultPersonVoice: 'In your voice.',
    lblCicero: 'Cicero (voice and context)', lblRecipient: 'recipient and register', lblVoice: 'voice', lblDisclosure: 'disclosure of Atlas',
    lblMark: 'mark', lblPolicyRow: 'policy row', lblSources: 'sources',
    notify: 'atlas-slack: decision needed',
    do: 'Proceed', doAsk: 'Proceed?', action: 'Action:  ', byBot: 'by Atlas', byYou: 'by you (your account)', where: 'Where:   ', when: 'When:    ', message: 'message', user: 'for',
    transitional: '🔏 binding 🔴 none · 📓 journal 🔴 none — transitional path (not through the door core; slice 2)',
    mReactAdd: '👍 add reaction :{name}:', mReactRemove: '👎 remove reaction :{name}:', mDelete: '🗑 delete a message',
    mUpdate: '✏️ edit a message', mSchedule: '⏰ schedule a message', mEphemeral: '👁 show a message to one person only',
    mPinAdd: '📌 pin a message', mPinRemove: '📌 unpin a message', mUpload: '📎 upload file {name}', mOpen: '💬 open a DM',
    mOther: 'Slack write: {method}',
    denyNo: 'atlas-slack: declined on the card — nothing was sent',
    denyDismissed: 'atlas-slack: the approval dialog was closed (Esc) — nothing was sent; ask the person what to do next',
    denyEdit: 'atlas-slack: edit from the person — «{t}». Nothing was sent: rework the message with this edit and call the send again (a new card).',
  },
}

type SlackInput = {
  method?: string
  as_bot?: boolean
  params?: { channel?: string; text?: string; thread_ts?: string; reply_broadcast?: unknown; blocks?: unknown[]; post_at?: unknown }
  provenance?: { comm_id?: string; checks?: Record<string, unknown>; sources?: unknown[]; origin?: string; resend?: boolean }
}

/** The gate's prompt without the engine's own prefix, split into its lines. */
function gateLines(reason: string | undefined): string[] {
  const raw = (reason ?? '').replace(/^Hook '[^']*' (?:blocked this action|requires approval[^:]*): /, '')
  return raw.split('\n').map(l => l.trimEnd()).filter(l => l.length > 0)
}

// "<sender> → <target>: “…” | сообщение ниже (N симв.) | message below (N chars) | blocks. <voice sentence> Отправить?|Send?"
const SENTENCE =
  /^(.+?) → (.+?): (?:“[\s\S]*?”|сообщение ниже \(\d+ [^)]*\)|message below \(\d+ [^)]*\)|blocks)\.\s*(.*?)\s*(Отправить\?|Send\?)\s*$/
// `ledger: …` / `intention: …` lines the gate adds when a ledger dir is configured (ledger.py).
const LEDGER_RE = /^ledger: (✅|☑|⚠) (.*)$/
const INTENTION_RE = /^intention: (.+)$/

// Content checks a send to others is expected to declare; absent ones are listed as not done.
const EXPECTED_CHECKS = ['cicero', 'recipient', 'disclosure']
const CHECK_KEY: Record<string, string> = {
  cicero: 'lblCicero', recipient: 'lblRecipient', voice: 'lblVoice', disclosure: 'lblDisclosure',
  mark: 'lblMark', policy_row: 'lblPolicyRow', sources: 'lblSources',
}
const MARK: Record<string, string> = { '✅': OK, '☑': WARN, '⚠': BAD }

function fmt(s: string, vars: Record<string, string | number>): string {
  return s.replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? ''))
}

export function buildCard(input: SlackInput, reason: string | undefined): string {
  const lines = gateLines(reason)
  const first = lines[0] ?? ''
  const m = SENTENCE.exec(first)
  const lang: Lang = m?.[4] === 'Send?' || (!m && /\bSend\?/.test(first)) ? 'en' : 'ru'
  const t = L[lang]
  const asBot = Boolean(input.as_bot)
  const sender = m?.[1] ?? (asBot ? t.defaultBot : t.defaultSender)
  const targetRaw = (m?.[2] ?? input.params?.channel ?? '?')
  const unresolved = /\(unresolved\)/.test(targetRaw) || !m
  const target = targetRaw.replace(/ \(в тред\)| \+ в канал| \(in thread\)| \+ also to the channel| · отложено на .*$| · scheduled for .*$/g, '')

  const thread = Boolean(input.params?.thread_ts)
  const broadcast = String(input.params?.reply_broadcast ?? '').toLowerCase() === 'true'
  const format = thread ? (broadcast ? t.threadChannel : t.thread) : t.chat
  const when = input.method === 'chat.scheduleMessage' ? (whenText(input.params?.post_at, lang) ?? String(input.params?.post_at)) : t.now

  const text = typeof input.params?.text === 'string' ? input.params.text : ''
  const shown = text.length > TEXT_CAP
    ? text.slice(0, TEXT_CAP).trimEnd() + '\n… ' + fmt(t.more!, { n: text.length - TEXT_CAP })
    : text
  const body = shown.length > 0 ? shown : fmt(t.noText!, { n: input.params?.blocks?.length ?? 0 })

  const checks = input.provenance?.checks ?? {}
  // Read the gate's trailer from the end: technical line, then intention?, ledger?, ⚠ policy?, checks?;
  // the first line that is none of these is the end of the trailer (message text sits above it).
  let ledgerLine: RegExpExecArray | null = null
  let intention: string | undefined
  let builtin = false
  let seenLedgerOrBelow = true
  for (let i = lines.length - 1; i >= 0; i--) {
    const l = lines[i]!
    if (i === lines.length - 1 && l.startsWith('class ')) continue
    const asLedger: RegExpExecArray | null = !ledgerLine && seenLedgerOrBelow ? LEDGER_RE.exec(l) : null
    if (asLedger) { ledgerLine = asLedger; seenLedgerOrBelow = false; continue }
    const asIntention: RegExpExecArray | null = !ledgerLine && seenLedgerOrBelow && intention === undefined ? INTENTION_RE.exec(l) : null
    if (asIntention) { intention = asIntention[1]; continue }
    if (l.startsWith('⚠') && /полити|policy/.test(l)) { builtin = true; seenLedgerOrBelow = false; continue }
    if (l.startsWith('checks:')) { seenLedgerOrBelow = false; continue }
    break
  }
  if (!ledgerLine) intention = undefined          // an intention line is only trusted under a ledger line
  // Cicero on the door line comes from the ledger the gate read. The context's own `checks.cicero`
  // claim always stays under «Контекст проверил» — shown, never a ✅.
  const cicero = ledgerLine ? `${t.cicero} ${MARK[ledgerLine[1]!] ?? BAD} ${ledgerLine[2]!.replace(/ · .*$/, '')}` : null

  const door = [
    unresolved ? `${t.addressee} ${BAD} ${t.unresolved}` : `${t.addressee} ${OK}`,
    asBot ? `${t.voice} ${OK} ${t.bot}` : `${t.voice} ${OK} ${t.person}`,
    `${t.binding} ${OK}`,
    `${t.journal} ${OK}`,
    builtin ? `${t.policy} ${WARN} ${t.builtin}` : `${t.policy} ${OK}`,
    ...(cicero ? [cicero] : []),
    ...(input.provenance?.resend ? [`${t.resend} ${WARN}`] : []),
  ].join(' · ')
  const label = (k: string) => (CHECK_KEY[k] ? t[CHECK_KEY[k]!]! : k)
  const declared = Object.entries(checks).filter(([, v]) => v !== undefined && v !== null && v !== '')
  const done = declared.map(([k, v]) => `${label(k)} — ${String(v)}`)
  const expected = cicero ? EXPECTED_CHECKS.filter(k => k !== 'cicero') : EXPECTED_CHECKS
  const missing = expected.filter(k => !declared.some(([d]) => d === k)).map(label)

  return [
    `${t.to}${target} · ${format} · ${when}`,
    `${t.from}${sender} ${asBot ? t.botVoice : t.personVoice}`,
    ...(intention ? [`${t.intention}${intention}`] : []),
    '',
    t.text!,
    body,
    ...(input.params?.thread_ts ? [fmt(t.inThread!, { ts: input.params.thread_ts })] : []),
    '',
    `${t.door}${door}`,
    `${t.declared}${done.length ? done.join(' · ') : t.nothing}`,
    ...(missing.length ? [`${t.missing}${BAD} ${missing.join(' · ')}`] : []),
    '',
    t.ask!,
  ].join('\n')
}

type AnyParams = Record<string, unknown>

const METHOD_KEY: Record<string, string> = {
  'reactions.add': 'mReactAdd', 'reactions.remove': 'mReactRemove', 'chat.delete': 'mDelete', 'chat.update': 'mUpdate',
  'chat.postEphemeral': 'mEphemeral', 'pins.add': 'mPinAdd', 'pins.remove': 'mPinRemove',
  'files.uploadV2': 'mUpload', 'conversations.open': 'mOpen',
}

function whenText(postAt: unknown, lang: Lang): string | null {
  const n = typeof postAt === 'number' ? postAt : typeof postAt === 'string' ? Number(postAt) : NaN
  if (!Number.isFinite(n) || n <= 0) return null
  try {
    return new Date(n * 1000).toLocaleString(lang === 'ru' ? 'ru-RU' : 'en-GB', { dateStyle: 'medium', timeStyle: 'short' })
  } catch {
    return new Date(n * 1000).toISOString()
  }
}

/** A compact card for a write outside the door core (today's gate semantics): what · where · when · text. */
export function buildTransitionalCard(method: string, params: AnyParams, asBot: boolean, reason: string | undefined, langHint?: Lang): string {
  const lines = gateLines(reason)
  const lang: Lang = langHint ?? 'ru'
  const t = L[lang]
  const key = METHOD_KEY[method]
  const what = key
    ? fmt(t[key]!, { name: String(params.name ?? params.filename ?? params.title ?? ''), method })
    : fmt(t.mOther!, { method })
  const place: string[] = []
  const channel = params.channel ?? params.channel_id
  if (typeof channel === 'string') place.push(channel)
  if (typeof params.users === 'string') place.push(`${t.user} ${params.users}`)
  if (typeof params.user === 'string') place.push(`${t.user} ${params.user}`)
  const ts = params.timestamp ?? params.ts
  if (typeof ts === 'string' || typeof ts === 'number') place.push(`${t.message} ${ts}`)
  if (typeof params.thread_ts === 'string') place.push(fmt(t.inThread!, { ts: params.thread_ts }))
  const when = method === 'chat.scheduleMessage' ? whenText(params.post_at, lang) : null
  const text = typeof params.text === 'string' ? params.text : typeof params.initial_comment === 'string' ? params.initial_comment : ''
  const shown = text.length > TEXT_CAP ? text.slice(0, TEXT_CAP).trimEnd() + '\n… ' + fmt(t.more!, { n: text.length - TEXT_CAP }) : text
  // the gate's own explanation (GATE-001 for conversations.open, the transitional label) stays, minus the label
  const gate = (lines[0] ?? '').replace(/\s*\((transitional|переходн)[^)]*\)\s*$/, '')
  return [
    `${t.action}${what} · ${asBot ? t.byBot : t.byYou}`,
    ...(place.length ? [`${t.where}${place.join(' · ')}`] : []),
    ...(when ? [`${t.when}${when}`] : []),
    ...(shown ? ['', t.text!, shown] : []),
    ...(gate && !key ? ['', gate] : []),
    '',
    `${t.door}${t.transitional}`,
    '',
    t.doAsk!,
  ].join('\n')
}

/** The language of a built card (its last line is the question). */
export function cardLang(card: string): Lang {
  return card.endsWith(L.en.ask!) || card.endsWith(L.en.doAsk!) ? 'en' : 'ru'
}

export const register: Register = on => {
  let lastLang: Lang = 'ru'
  on('tool.check', { tool: SLACK_API }, async ($, e, next) => {
    const verdict = await next(e)
    if (verdict.decision !== 'ask') return verdict
    const input = (e.input ?? {}) as SlackInput
    const method = input.method ?? ''
    if (!method || method.startsWith('_')) return verdict
    const core = method === 'chat.postMessage' || method === 'chat.scheduleMessage'
    // A transitional write has no names resolved and no language in its gate text: take the
    // profile's language from the core prompt we saw last, else ru (the only profile today).
    const card = core
      ? buildCard(input, verdict.reason)
      : buildTransitionalCard(method, (input.params ?? {}) as AnyParams, Boolean(input.as_bot), verdict.reason, lastLang)
    const t = L[cardLang(card)]
    if (core) lastLang = cardLang(card)
    try {
      await $.ui.notify(card.split('\n')[0] ?? 'Slack send', { title: t.notify! })
    } catch {
      // a notification is a courtesy, never a gate
    }
    let answer: string
    try {
      answer = await $.ui.ask(card, { options: [core ? t.yes! : t.do!, t.no!], header: HEADER })
    } catch (err) {
      // Esc / a closed dialog: the engine's own error text is long and machine-shaped (MC session
      // finding 2026-10-31); the model only needs to know that nothing was sent.
      const why = err instanceof Error ? err.message : String(err)
      try { $.ui.log(`atlas-slack: approval dialog dismissed — ${why.slice(0, 200)}`, { to: 'debug' }) } catch { /* a log is a courtesy */ }
      return { decision: 'deny', reason: t.denyDismissed! }
    }
    if (answer === (core ? t.yes : t.do)) {
      return { decision: 'allow', reason: 'atlas-slack: approved on the card' }
    }
    if (answer === t.no || answer.trim() === '') {
      return { decision: 'deny', reason: t.denyNo! }
    }
    // Anything typed under «Type something» is an edit request: nothing is sent; the model reads it
    // as the tool error, reworks the text and comes back with a fresh card.
    return { decision: 'deny', reason: fmt(t.denyEdit!, { t: answer.trim() }) }
  }).catch(($, e, next) => (next.called ? next(e) : { decision: 'ask', reason: 'atlas-slack card failed — stock dialog' }))
}
