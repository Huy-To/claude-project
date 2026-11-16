// Claude: the fourth view and the small panel. Both show the same conversation.
// Loaded after the page's own script, whose helpers it uses ($, esc, icon, store, TR, S, OP, DR,
// VIEW, setView, panelReveal, openPopover, ask, toast).
//
// The server does the thinking (see src/btcliq/desk/). This file sends a question with what is on
// screen, reads the answer as a stream of events, and draws them: text, a card for every lookup
// Claude runs, note proposals, and the usage chip.

const DK = {
  st: null,             // /api/desk/state: engines, models, briefs, vault, pinned notes, usage
  conv: null,           // the open conversation: { id, title, messages: [...] }
  list: [],             // every saved conversation, newest first
  hits: null,           // search results, while the search box has text
  busy: false, abort: null,
  model: store.get('deskModel', ''),
  web: false,           // web search: off for every new conversation
  keyConvs: new Set(),  // conversations you chose to continue on the API key
  usage: null,
  skipped: new Set(),   // note proposals you dismissed
  paintQ: false,
};
try { DK.skipped = new Set(JSON.parse(store.get('deskSkipped', '[]'))); } catch {}
const DK_ROWS = 8;      // rows a lookup card shows before "Show all"
const DK_ICON = { market: 'chart', options: 'options', account: 'bank', context: 'calendar', knowledge: 'layers',
                  memory: 'clock', calc: 'bars', web: 'globe' };

// ---------- talking to the server ----------
async function dkApi(path, opt = {}, retried = false) {
  if (!TR.token) await trStatus();
  const headers = { 'X-Btcliq-Trade': TR.token, ...(opt.body ? { 'Content-Type': 'application/json' } : {}) };
  const r = await fetch('/api/desk/' + path, { ...opt, headers });
  if (r.status === 403 && !retried) { await trStatus(); return dkApi(path, opt, true); }   // server restarted: new token
  return r;
}
async function dkJson(path, opt) {
  const r = await dkApi(path, opt);
  const text = await r.text();
  let d = null; try { d = JSON.parse(text); } catch {}
  if (!r.ok) throw new Error((d && d.error) || text || `HTTP ${r.status}`);
  return d ?? {};
}

// ---------- small formatters ----------
const dkTok = n => n == null ? '—' : n >= 1e6 ? (n / 1e6).toFixed(2) + 'M' : n >= 1e3 ? (n / 1e3).toFixed(1) + 'k' : String(n);
const dkClock = ms => new Date(ms).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
const dkDay = ms => new Date(ms).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
// "9:00 PM" today, "Sat 1:40 AM" otherwise.
const dkWhen = ms => new Date(ms).toDateString() === new Date().toDateString() ? dkClock(ms)
  : `${new Date(ms).toLocaleDateString([], { weekday: 'short' })} ${dkClock(ms)}`;
function dkMoney(v) {
  const a = Math.abs(v), s = v < 0 ? '-' : '';
  if (a >= 1e9) return `${s}$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${s}$${(a / 1e6).toFixed(1)}M`;
  if (a >= 1e4) return `${s}$${Math.round(a).toLocaleString()}`;
  return `${s}$${a.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
function dkNum(v) {
  const a = Math.abs(v);
  if (a >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 1 });
  if (a >= 1 || a === 0) return String(+v.toFixed(4));
  return String(+v.toPrecision(4));
}
// One value on a lookup card, formatted by what its name says it is.
function dkVal(key, v) {
  if (v == null || v === '') return '—';
  if (typeof v === 'boolean') return v ? 'yes' : 'no';
  if (typeof v === 'number') {
    const k = String(key || '').toLowerCase();
    if (/probab/.test(k)) return (v * 100).toFixed(1) + '%';
    if (/(^|_)(pct|percent|percentile)($|_)/.test(k)) return (+v.toFixed(Math.abs(v) < 1 ? 3 : 2)).toLocaleString() + '%';
    if (/usd|pnl|premium|equity|notional|fee|cost|value/.test(k) && !/btc|per_unit/.test(k)) return dkMoney(v);
    return dkNum(v);
  }
  if (typeof v === 'string') return esc(v);
  if (Array.isArray(v) && v.every(x => x == null || typeof x !== 'object')) return esc(v.join(', '));
  return esc(JSON.stringify(v));
}
const dkLabel = k => esc(String(k).replace(/_/g, ' '));

// ---------- Markdown: paragraphs, lists, headings, bold, italic, code, tables, links ----------
const dkInline = s => esc(s)
  .replace(/`([^`]+)`/g, '<code>$1</code>')
  .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
  .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<i>$2</i>')
  .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
function dkMd(src) {
  return String(src || '').split(/```/).map((part, i) => {
    if (i % 2) return `<pre><code>${esc(part.replace(/^\w*\n/, ''))}</code></pre>`;
    const out = []; let list = null, para = [];
    const flushPara = () => { if (para.length) { out.push(`<p>${para.map(dkInline).join('<br>')}</p>`); para = []; } };
    const flushList = () => { if (list) { out.push(`<${list.tag}>${list.items.map(x => `<li>${dkInline(x)}</li>`).join('')}</${list.tag}>`); list = null; } };
    const lines = part.split('\n');
    const cells = l => l.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
    for (let n = 0; n < lines.length; n++) {
      const line = lines[n];
      if (/^\s*\|.*\|\s*$/.test(line) && /^\s*\|?\s*:?-{2,}/.test(lines[n + 1] || '')) {      // a table
        flushPara(); flushList();
        const head = cells(line), rows = [];
        n += 2;
        while (n < lines.length && /^\s*\|.*\|\s*$/.test(lines[n])) rows.push(cells(lines[n++]));
        n--;
        out.push(`<div class="tw"><table><thead><tr>${head.map(h => `<th>${dkInline(h)}</th>`).join('')}</tr></thead><tbody>`
          + rows.map(r => `<tr>${r.map(c => `<td>${dkInline(c)}</td>`).join('')}</tr>`).join('') + '</tbody></table></div>');
        continue;
      }
      const ul = line.match(/^\s*[-*•]\s+(.*)/), ol = line.match(/^\s*\d+[.)]\s+(.*)/), h = line.match(/^#{1,6}\s+(.*)/);
      if (ul || ol) {
        flushPara();
        const tag = ul ? 'ul' : 'ol';
        if (!list || list.tag !== tag) { flushList(); list = { tag, items: [] }; }
        list.items.push((ul || ol)[1]);
      } else if (h) { flushPara(); flushList(); out.push(`<h4>${dkInline(h[1])}</h4>`); }
      else if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) { flushPara(); flushList(); out.push('<hr>'); }
      else if (!line.trim()) { flushPara(); flushList(); }
      else { flushList(); para.push(line.replace(/^\s*>\s?/, '')); }
    }
    flushPara(); flushList();
    return out.join('');
  }).join('');
}

// ---------- lookup cards ----------
function dkTable(cols, rows, b, path) {
  const all = b.all && b.all[path], shown = all ? rows : rows.slice(0, DK_ROWS);
  const wide = c => /text|detail|title|summary|why|about|note/.test(String(c).toLowerCase());
  return `<table><thead><tr>${cols.map(c => `<th>${dkLabel(c)}</th>`).join('')}</tr></thead><tbody>`
    + shown.map(r => `<tr>${cols.map((c, i) => `<td${wide(c) ? ' class="txt"' : ''}>${dkVal(c, Array.isArray(r) ? r[i] : r[c])}</td>`).join('')}</tr>`).join('')
    + '</tbody></table>'
    + (rows.length > DK_ROWS ? `<button type="button" class="lk-more" data-path="${esc(path)}">${all ? 'Show fewer' : `Show all ${rows.length} rows`}</button>` : '');
}
function dkData(d, b, path = '') {
  if (d == null) return '';
  if (typeof d === 'string') {
    const all = b.all && b.all[path], lines = d.split('\n');
    return `<pre class="lk-text">${esc((all ? lines : lines.slice(0, 14)).join('\n'))}</pre>`
      + (lines.length > 14 ? `<button type="button" class="lk-more" data-path="${esc(path)}">${all ? 'Show less' : `Show all ${lines.length} lines`}</button>` : '');
  }
  if (Array.isArray(d)) {
    if (!d.length) return '<span class="muted">nothing</span>';
    if (d.every(x => x && typeof x === 'object' && !Array.isArray(x))) {
      const cols = [...new Set(d.slice(0, 30).flatMap(x => Object.keys(x)))].filter(c => d.some(x => x[c] != null && typeof x[c] !== 'object'));
      return dkTable(cols, d, b, path);
    }
    return `<div class="kv"><div class="wide"><b>${dkVal('', d)}</b></div></div>`;
  }
  if (Array.isArray(d.columns) && Array.isArray(d.rows)) {
    const rest = Object.fromEntries(Object.entries(d).filter(([k]) => k !== 'columns' && k !== 'rows'));
    return (Object.keys(rest).length ? dkData(rest, b, path + '.') : '') + `<div class="lk-sub">${dkTable(d.columns, d.rows, b, path + '#')}</div>`;
  }
  const flat = [], subs = [];
  for (const [k, v] of Object.entries(d)) {
    if (v && typeof v === 'object' && !(Array.isArray(v) && v.every(x => x == null || typeof x !== 'object'))) subs.push([k, v]);
    else flat.push([k, v]);
  }
  return (flat.length ? `<div class="kv">${flat.map(([k, v]) => {
    const html = dkVal(k, v);
    return `<div${html.length > 44 ? ' class="wide"' : ''}><span>${dkLabel(k)}</span><b>${html}</b></div>`;
  }).join('')}</div>` : '')
    + subs.map(([k, v]) => `<div class="lk-sub"><h5>${dkLabel(k)}</h5>${dkData(v, b, path + '/' + k)}</div>`).join('');
}
function dkArgs(a) {
  const parts = Object.entries(a || {}).map(([k, v]) => `${k.replace(/_/g, ' ')}: ${typeof v === 'object' ? JSON.stringify(v) : v}`);
  return esc(parts.join(' · ').slice(0, 160));
}
function dkCard(b, mi, bi) {
  const run = b.state === 'run', bad = !run && b.ok === false;
  const body = run ? '' : bad ? `<div class="err">${esc(b.error || 'This lookup failed.')}</div>`
    : b.dropped ? `<span class="muted">${esc(b.dropped)}</span>` : dkData(b.data, b);
  return `<div class="lk${run ? ' run' : ''}${bad ? ' bad' : ''}${b.shut || run ? ' shut' : ''}" data-m="${mi}" data-b="${bi}">
    <button type="button" class="lk-h" aria-expanded="${!(b.shut || run)}">${icon(run ? 'refresh' : bad ? 'x-c' : DK_ICON[b.group] || 'search')}
      <span class="t">${esc(b.title || b.name)}</span><span class="a">${dkArgs(b.args)}</span>
      ${b.untrusted ? '<span class="tag" title="This text was written by other people. Claude treats it as a claim, not as fact or instructions.">unverified</span>' : ''}
      <span class="ms">${run ? 'looking…' : b.ms != null ? (b.ms >= 1000 ? (b.ms / 1000).toFixed(1) + 's' : b.ms < 1 ? '&lt;1ms' : b.ms + 'ms') : ''}</span>${run ? '' : icon('chev-down', 'chev')}</button>
    ${body ? `<div class="lk-b">${body}</div>` : ''}</div>`;
}

// ---------- one message ----------
function dkNoteHtml(b, mi, bi) {
  const pinned = (DK.st?.pinned || []).some(p => p.text.toLowerCase() === b.text.toLowerCase());
  if (pinned) return `<div class="np done">${icon('check-c')} Pinned: ${esc(b.text)}</div>`;
  if (DK.skipped.has(b.text)) return '';
  return `<div class="np" data-m="${mi}" data-b="${bi}"><h5>Note proposed</h5><div class="np-text">${esc(b.text)}</div>
    ${b.why ? `<div class="why">${esc(b.why)}</div>` : ''}
    <div class="row"><button type="button" class="btn sm primary" data-np="save">Save</button>
      <button type="button" class="btn sm" data-np="edit">Edit</button><button type="button" class="btn sm" data-np="skip">Skip</button>
      <span class="muted">Pinned notes are sent with every question.</span></div><div class="err"></div></div>`;
}
function dkMeta(m) {
  if (m.live) return '';
  const u = m.usage, bits = [];
  const label = (DK.st?.models || []).find(x => x.id === m.model)?.label || (m.model || '').replace(/^claude-/, '');
  if (label) bits.push(esc(label));
  if (u) bits.push(`<span title="Tokens sent and received for this answer. The cached part was read from Claude's cache, which costs far less.">${dkTok(u.in)} in${u.cache_read ? ` (${dkTok(u.cache_read)} cached)` : ''} · ${dkTok(u.out)} out</span>`);
  if (m.engine === 'api') bits.push(`<span class="warn">API key${u?.usd != null ? ' $' + u.usd.toFixed(4) : ''}</span>`);
  if (m.stop === 'stopped') bits.push('stopped');
  return bits.length ? `<div class="meta">${bits.map(b => `<span>${b}</span>`).join('')}
    <button type="button" class="ibtn" data-copy title="Copy this answer" aria-label="Copy this answer">${icon('copy')}</button></div>` : '';
}
// ---------- prepared actions: Claude lines something up, you send it ----------
// A card per action, with one button that hands over to the app's own controls: the order
// ticket, the Close panel, a cancel, or a change you confirm. Nothing here sends an order.
const DK_STALE_MS = 10 * 60000;
function dkLeg(l, i) {
  const buy = l.side === 'buy', cost = l.premium_usd != null ? `Premium ${dkMoney(l.premium_usd)}` : l.value_usd != null ? `Value ${dkMoney(l.value_usd)}` : '';
  const bits = [cost, l.fee_usd != null ? `fee ${dkMoney(l.fee_usd)}` : '', l.margin_usd != null ? `margin ${dkMoney(l.margin_usd)}` : '',
    l.worst_case_usd != null ? `worst case ${dkMoney(l.worst_case_usd)}` : '', l.breakeven ? `breakeven $${Math.round(l.breakeven).toLocaleString()}` : ''].filter(Boolean);
  return `<div class="pp-leg">
    <div class="pp-line"><b class="${buy ? 'up' : 'dn'}">${buy ? 'BUY' : 'SELL'}</b><b>${esc(trSize(+l.sz, l.unit))}</b><span>${esc(l.instId)}</span>
      <span class="muted">${l.px != null ? 'at ' + esc(trPxU(+l.px, l.price_ccy)) : 'at market'} · ${esc(ordName(l.type))}${l.reduce ? ' · reduce only' : ''}</span></div>
    ${bits.length ? `<div class="pp-sub">${esc(bits.join(' · '))}</div>` : ''}
    ${(l.asks || []).length ? `<div class="pp-rail ask">${icon('alert')}<span>Needs your OK: ${esc(l.asks.join('; '))}</span></div>`
      : `<div class="pp-rail ok">${icon('check-c')}<span>Passed every safety rail when prepared</span></div>`}
    ${(l.warnings || []).map(w => `<div class="pp-sub">${esc(w)}</div>`).join('')}
    <div class="row"><button type="button" class="btn sm primary" data-pp="ticket" data-leg="${i}">${icon('ticket')}Review in ticket</button></div></div>`;
}
function dkProposal(b, mi, bi) {
  const old = b.at && Date.now() - b.at > DK_STALE_MS;
  let title, body, foot;
  if (b.kind === 'order') {
    const n = (b.legs || []).length;
    title = n > 1 ? `${n}-leg order` : 'Order';
    body = (b.legs || []).map(dkLeg).join('');
    foot = n > 1 ? 'Each leg goes into the ticket on its own, and you send each one yourself.' : 'Nothing is sent until you confirm it in the ticket.';
  } else if (b.kind === 'close') {
    const buy = b.side === 'buy';
    title = 'Close position';
    body = `<div class="pp-leg"><div class="pp-line"><b>Close ${b.percent}%</b><span>${esc(b.instId)}</span>${b.parts > 1 ? `<span class="muted">${esc(b.mgnMode)} part</span>` : ''}</div>
      <div class="pp-sub"><span class="${buy ? 'up' : 'dn'}">${buy ? 'Buy' : 'Sell'}</span> ${esc(trSize(+b.sz, b.unit))} of ${esc(num(b.total, 4))}, ${b.px != null ? 'limit at the mid, about ' + esc(trPxU(+b.px, b.price_ccy)) : 'at market'}${b.est_pnl_usd != null ? ` · realizes about ${esc(trSignedUsd(b.est_pnl_usd))}` : ''}</div>
      ${(b.asks || []).length ? `<div class="pp-rail ask">${icon('alert')}<span>Needs your OK: ${esc(b.asks.join('; '))}</span></div>` : ''}
      <div class="row"><button type="button" class="btn sm primary" data-pp="close">${icon('x-c')}Open Close panel</button></div></div>`;
    foot = 'The Close panel shows the live price and rail checks. Nothing is sent until you confirm there.';
  } else {
    const what = `${b.side === 'buy' ? 'Buy' : 'Sell'} ${trSize(+b.sz, b.unit)} ${b.instId}${b.px != null ? ' at ' + trPxU(+b.px, b.price_ccy) : ''} · ${ordName(b.ordType)}`;
    if (b.kind === 'cancel') {
      title = 'Cancel order';
      body = `<div class="pp-leg"><div class="pp-line"><span>${esc(what)}</span></div>
        <div class="row"><button type="button" class="btn sm danger" data-pp="cancel">${icon('x')}Cancel order</button></div></div>`;
      foot = 'The order stays open until you confirm in the window that follows.';
    } else {
      const f = b.field === 'px', fmt = v => f ? trPxU(+v, b.price_ccy) : trSize(+v, b.unit);
      title = f ? 'Change order price' : 'Change order size';
      body = `<div class="pp-leg"><div class="pp-line"><span>${esc(what)}</span></div>
        <div class="pp-sub">${f ? 'Price' : 'Size'} <b>${esc(fmt(f ? b.px : b.sz))}</b> ${icon('arrow-r')} <b>${esc(fmt(b.to))}</b></div>
        <div class="row"><button type="button" class="btn sm primary" data-pp="amend">${icon('edit')}Review change</button></div></div>`;
      foot = 'The order is unchanged until you confirm. Your safety rails are checked then.';
    }
  }
  return `<div class="pp" data-m="${mi}" data-b="${bi}"><h5>${icon('sparkle')}Prepared by Claude · ${esc(title)}</h5>
    ${b.why ? `<div class="why">${esc(b.why)}</div>` : ''}${body}
    <div class="pp-foot">${b.done ? `<b>${esc(b.done)}</b> ` : ''}${esc(foot)}${old ? ` Prepared ${esc(dkWhen(b.at))}: prices have moved since, and the app re-checks everything.` : ''}</div>
    <div class="err"></div></div>`;
}
async function dkAct(btn) {
  const box = btn.closest('.pp'), b = DK.conv?.messages?.[+box.dataset.m]?.blocks?.[+box.dataset.b]; if (!b) return;
  const fail = msg => { box.querySelector('.err').textContent = msg; };
  const done = msg => { b.done = msg; dkRender(); };
  const act = btn.dataset.pp;
  if (act === 'ticket') {
    const l = b.legs[+btn.dataset.leg]; if (!l) return;
    trFill({ instId: l.instId, side: l.side, sz: l.sz, px: l.px != null ? +l.px : null, reduce: !!l.reduce,
             type: ['limit', 'market', 'post_only', 'ioc'].includes(l.type) ? l.type : 'limit' });
    toast(`In the order ticket: ${l.side} ${l.sz} ${l.instId}. Review it there, then send it.`, 'ok');
    return;
  }
  if (!trAcctOn()) { fail('Your OKX account has not loaded yet. Try again in a moment.'); return; }
  if (act === 'close') {
    const i = TR.acct.positions.findIndex(p => p.instId === b.instId && (!b.mgnMode || p.mgnMode === b.mgnMode));
    if (i < 0) { fail('That position is no longer open.'); return; }
    openClose(i, btn, b.order === 'market' ? 'market' : 'limit');
    if (TR.closing) { TR.closing.step = b.percent; TR.closing.check = null; renderClose(); closeCheck(); }
    return;
  }
  const i = TR.acct.orders.findIndex(o => String(o.ordId) === String(b.ordId)), o = TR.acct.orders[i];
  if (!o) { fail('That order is no longer open.'); return; }
  if (act === 'cancel') {      // red window first: a prepared cancel never acts on one click
    const buyO = o.side === 'buy';
    const ok = await ask({ title: 'Cancel this order?', icon: 'x-c', tone: 'danger', env: true,
      lede: `<span class="${buyO ? 'up' : 'dn'}">${buyO ? 'Buy' : 'Sell'}</span> ${esc(trSize(+o.sz, o.unit))} ${esc(o.instId)}${o.px != null ? ' at ' + esc(trPxU(+o.px, o.price_ccy)) : ''}
        <span class="about">${esc(ordName(o.ordType))} order. Prepared by Claude${b.why ? ': ' + esc(b.why) : '.'}</span>`,
      change: { label: 'Now', now: 'Open order', afterLabel: 'After', after: 'Canceled' },
      note: 'The order is removed from OKX. A notice then offers Place again, which refills the ticket with the same order.',
      keep: 'Keep order', ok: 'Cancel order' });
    if (!ok) return;
    const j = TR.acct.orders.findIndex(x => String(x.ordId) === String(b.ordId));      // the table may have refreshed meanwhile
    if (j < 0) { fail('That order is no longer open.'); return; }
    await cancelOrder(j, btn); done('Cancel sent.');
    return;
  }
  const f = b.field === 'px', fmt = v => f ? trPxU(+v, o.price_ccy) : trSize(+v, o.unit), buy = o.side === 'buy';
  const yes = await ask({ title: f ? 'Change this order\'s price?' : 'Change this order\'s size?', icon: 'edit', env: true,
    lede: `<span class="${buy ? 'up' : 'dn'}">${buy ? 'Buy' : 'Sell'}</span> ${esc(trSize(+o.sz, o.unit))} ${esc(o.instId)}
      <span class="about">${esc(ordName(o.ordType))} order. Prepared by Claude${b.why ? ': ' + esc(b.why) : '.'}</span>`,
    change: { label: f ? 'Price now' : 'Size now', now: fmt(f ? o.px : o.sz), afterLabel: f ? 'New price' : 'New size', after: fmt(b.to) },
    note: 'Your safety rails are checked when the change is sent.', keep: 'Keep it as it is', ok: 'Change order' });
  if (!yes) return;
  try { if (await amendOrder(o, { [b.field]: b.to })) { trRefresh(); done('Change sent.'); } }
  catch (e) { fail(`Couldn't change the order: ${e.message}`); }
}
// The one always-visible line about what Claude can do and what could mislead it.
function dkRisk() {
  const read = new Set((DK.conv?.messages || []).flatMap(m => m.sources || []));
  const web = DK.web || read.has('web');
  const seen = [...read].map(s => ({ telegram: 'Telegram posts', web: 'web results' }[s] || s));
  const html = `${icon('alert')}<span>Claude can prepare orders but never sends them. It reads Telegram posts${web ? ' and web results' : ''}, which other people write and can word to mislead it. Check every prepared order before you confirm.${seen.length ? ` This conversation has read: ${esc(seen.join(', '))}.` : ''}</span>`;
  for (const id of ['cld-risk', 'chat-risk']) { const el = $(id); if (el) { el.innerHTML = html; el.hidden = !DK.st?.enabled; } }
}
function dkLimitHtml(m) {
  const l = m.limit; if (!l) return '';
  const when = l.resets ? `It resets ${dkWhen(l.resets)}.` : '';
  const k = l.key || {};
  return `<div class="lim"><h5>Plan limit reached</h5><div>${esc(l.message || '')} ${when}</div>
    ${l.can_use_key ? `<div class="row"><button type="button" class="btn sm" data-key>Continue on API key</button>
      <span class="muted">Billed per question. Today: $${(k.usd || 0).toFixed(2)} of your $${(k.cap || 0).toFixed(2)} cap.</span></div>`
      : l.has_key ? `<div class="muted">The API key's daily cap of $${(k.cap || 0).toFixed(2)} is used up. Raise it from the usage chip to continue.</div>`
      : '<div class="muted">Add ANTHROPIC_API_KEY to .env to have a fallback for when this happens.</div>'}</div>`;
}
function dkTurn(m, mi, compact) {
  if (m.role === 'user') return `<div class="msg user">${esc(m.text)}</div>`;
  const blocks = m.blocks || (m.text ? [{ t: 'text', text: m.text }] : []);
  let html = '';
  if (compact) {      // the panel: lookups on one line, then the text
    const cards = blocks.filter(b => b.t === 'card');
    if (cards.length) {
      const run = cards.some(c => c.state === 'run');
      html += `<button type="button" class="lkline${run ? ' run' : ''}" data-open="${mi}" title="Open the Claude view to see the data behind each lookup">
        ${icon(run ? 'refresh' : 'search')}<span>${run ? 'Looking up' : 'Looked up'}: ${esc([...new Set(cards.map(c => (c.title || c.name).toLowerCase()))].join(' · '))}</span></button>`;
    }
    const text = blocks.filter(b => b.t === 'text').map(b => b.text).join('\n\n');
    if (text.trim() || m.live) html += `<div class="md${m.live ? ' typing' : ''}">${dkMd(text)}</div>`;
    blocks.forEach((b, bi) => { if (b.t === 'note') html += dkNoteHtml(b, mi, bi); else if (b.t === 'proposal') html += dkProposal(b, mi, bi); });
  } else {
    blocks.forEach((b, bi) => {
      if (b.t === 'card') html += dkCard(b, mi, bi);
      else if (b.t === 'text') { if (b.text.trim()) html += `<div class="md${m.live && bi === blocks.length - 1 ? ' typing' : ''}">${dkMd(b.text)}</div>`; }
      else if (b.t === 'note') html += dkNoteHtml(b, mi, bi);
      else if (b.t === 'proposal') html += dkProposal(b, mi, bi);
      else if (b.t === 'notice') html += `<div class="ntc">${esc(b.text)}</div>`;
    });
    if (m.live && (!blocks.length || !['text', 'proposal'].includes(blocks[blocks.length - 1].t))) html += '<div class="md typing"></div>';
  }
  if (m.limit) html += dkLimitHtml(m);
  else if (m.error) html += `<div class="cerr">${esc(m.error)}</div>`;
  return `<div class="turn" data-m="${mi}">${html}${dkMeta(m)}</div>`;
}

// ---------- drawing the conversation, in the view and in the panel ----------
const dkBoxes = () => [[$('cld-thread'), false], [$('msgs'), true]];
const dkNear = el => el.scrollHeight - el.scrollTop - el.clientHeight < 80;
function dkEmpty(compact) {
  if (!DK.st) return '<p class="cld-empty">Loading…</p>';
  if (!DK.st.enabled) {
    return DK.st.disabled_by_flag
      ? `<div class="setup"><b>Chat is off</b> because btcliq was started with <code>--chat off</code>. Restart without it to talk to Claude.</div>`
      : `<div class="setup"><b>Chat is off.</b> Install Claude Code and log in (the chat then uses your Claude subscription), or add
         <code>ANTHROPIC_API_KEY=sk-ant-…</code> to <code>btcliq/.env</code>. Then restart <code>btcliq</code>.</div>`;
  }
  const v = DK.st.vault || {};
  const know = v.ok ? `${v.knowledge_notes} knowledge note${v.knowledge_notes === 1 ? '' : 's'} from ${esc(v.name)}` : 'no knowledge vault connected';
  if (compact) {
    return `<div class="cld-empty"><p>Claude gets a fresh market snapshot with every question and can look things up in the app:
      your account, any option, the calendar, calculators. It can prepare an order for you to review; it never sends one.</p>
      <div class="suggest">${(DK.st.briefs || []).map(b => `<button type="button" class="chip" data-brief="${esc(b.id)}">${esc(b.label)}</button>`).join('')}</div></div>`;
  }
  return `<div class="cld-empty"><h3>Ask about the market, your positions or an idea</h3>
    <p>Every question carries a fresh snapshot of what the app shows. Claude can also run ${DK.st.lookups} lookups in the running app:
    your OKX account, any option contract, Polymarket odds, the calendar, past conversations and the calculators (volatility, GARCH, payoff, position risk).</p>
    <p>It can prepare an order, a close, a cancel or a change as a card for you to review. It never sends anything itself. It has ${know}.</p></div>`;
}
function dkRender(stick = false) {
  const msgs = DK.conv?.messages || [];
  for (const [box, compact] of dkBoxes()) {
    if (!box) continue;
    const near = stick || dkNear(box);
    box.innerHTML = msgs.length ? msgs.map((m, i) => dkTurn(m, i, compact)).join('') : dkEmpty(compact);
    if (near) box.scrollTop = box.scrollHeight;
  }
  dkControls();
  dkRisk();
}
// While an answer streams, only the last message changes: repaint just that, once per frame.
function dkPaint() {
  if (DK.paintQ) return;
  DK.paintQ = true;
  requestAnimationFrame(() => {
    DK.paintQ = false;
    const msgs = DK.conv?.messages || [], mi = msgs.length - 1;
    if (mi < 0) return;
    for (const [box, compact] of dkBoxes()) {
      if (!box) continue;
      const near = dkNear(box), el = box.querySelector(`.turn[data-m="${mi}"]`);
      if (!el) { dkRender(); return; }
      el.outerHTML = dkTurn(msgs[mi], mi, compact);
      if (near) box.scrollTop = box.scrollHeight;
    }
  });
}
function dkScrollPanel() { const m = $('msgs'); if (m) m.scrollTop = m.scrollHeight; }

function dkControls() {
  const on = !!DK.st?.enabled;
  for (const id of ['cld-input', 'chat-input']) { const el = $(id); if (el) { el.disabled = !on; el.placeholder = on ? el.dataset.ph : 'Chat is off'; } }
  for (const id of ['cld-send', 'chat-send']) {
    const el = $(id); if (!el) continue;
    el.disabled = !on;
    el.innerHTML = DK.busy ? `${icon('stop')}Stop` : `${icon('send')}Send`;
  }
  document.querySelectorAll('#cld-briefs .chip').forEach(c => c.disabled = !on || DK.busy);
  for (const id of ['cld-web', 'chat-web']) { const el = $(id); if (el) { el.checked = DK.web; el.disabled = !on; } }
  const t = $('cld-title'); if (t) t.textContent = DK.conv?.title || 'New chat';
}

// ---------- what is on screen, sent with every question ----------
function dkScreen() {
  const val = id => ($(id)?.value || '').trim();
  return {
    view: VIEW, tf: S.tf, tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
    option: OP.sel ? OP.sel.name : null,
    ticket: { inst: val('tr-inst').toUpperCase() || TR.inst, side: TR.side, type: TR.type, sz: val('tr-sz'), px: val('tr-px') },
    drawings: DR.list.slice(0, 150).map(d => ({ type: d.type, text: d.text || undefined,
      points: (d.pts || []).map(p => ({ t: new Date(p.t).toISOString().slice(0, 16) + 'Z', p: +(+p.p).toFixed(1) })) })),
  };
}

// ---------- asking ----------
async function dkSend(text, { retry = false, engine = null } = {}) {
  if (DK.busy || !DK.st?.enabled) return;
  if (!retry) { text = String(text || '').trim(); if (!text) return; }
  if (!DK.conv) DK.conv = { id: null, title: '', messages: [] };
  const msgs = DK.conv.messages;
  if (retry) { while (msgs.length && msgs[msgs.length - 1].role === 'assistant' && msgs[msgs.length - 1].stop !== 'end') msgs.pop(); }
  else msgs.push({ role: 'user', text, ts: Date.now() });
  const live = { role: 'assistant', blocks: [], live: true, model: DK.model, ts: Date.now() };
  msgs.push(live);
  DK.busy = true; DK.abort = new AbortController();
  dkRender(true);
  const useKey = engine === 'api' || (DK.conv.id && DK.keyConvs.has(DK.conv.id));
  const card = id => live.blocks.find(b => b.t === 'card' && b.id === id);
  try {
    const r = await dkApi('ask', { method: 'POST', signal: DK.abort.signal, body: JSON.stringify({
      text, retry, conversation: DK.conv.id, model: DK.model, web: DK.web, screen: dkScreen(), engine: useKey ? 'api' : 'auto' }) });
    if (!r.ok) throw new Error((await r.text()) || `HTTP ${r.status}`);
    const reader = r.body.getReader(), dec = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, nl); buf = buf.slice(nl + 1);
        if (!line.trim()) continue;
        let ev; try { ev = JSON.parse(line); } catch { continue; }
        if (ev.type === 'start') {
          DK.conv.id = ev.conversation.id; DK.conv.title = ev.conversation.title; live.engine = ev.engine; live.model = ev.model;
          store.set('deskConv', DK.conv.id);
          if (useKey) DK.keyConvs.add(DK.conv.id);
        } else if (ev.type === 'text') {
          const last = live.blocks[live.blocks.length - 1];
          if (last && last.t === 'text') last.text += ev.text; else live.blocks.push({ t: 'text', text: ev.text });
        } else if (ev.type === 'tool') {
          if (ev.state === 'start') live.blocks.push({ t: 'card', id: ev.id, name: ev.name, title: ev.title, group: ev.group, args: ev.args || {}, state: 'run' });
          else { const b = card(ev.id); if (b) Object.assign(b, { state: 'done', ok: ev.ok, ms: ev.ms, data: ev.data, error: ev.error, untrusted: ev.untrusted, args: ev.args || b.args }); }
        } else if (ev.type === 'note') live.blocks.push({ t: 'note', text: ev.text, why: ev.why });
        else if (ev.type === 'proposal') { const { type, ...p } = ev; live.blocks.push({ t: 'proposal', ...p }); }
        else if (ev.type === 'notice') live.blocks.push({ t: 'notice', text: ev.text });
        else if (ev.type === 'limit') live.limit = ev;
        else if (ev.type === 'error') live.error = ev.message;
        else if (ev.type === 'done') {
          live.stop = ev.stop; live.sources = ev.sources || [];
          if (ev.usage) { DK.usage = ev.usage; live.usage = ev.usage.last && ev.stop !== 'limit' ? ev.usage.last : null; }
        }
        dkPaint();
      }
    }
  } catch (e) {
    if (e.name === 'AbortError') live.stop = 'stopped';
    else live.error = e.message || String(e);
  }
  live.live = false; live.text = live.blocks.filter(b => b.t === 'text').map(b => b.text).join('');
  for (const b of live.blocks) if (b.t === 'card' && b.state === 'run') Object.assign(b, { state: 'done', ok: false, error: 'stopped before it finished' });
  if (!live.blocks.length && !live.error && !live.limit) msgs.pop();       // nothing came back: don't leave an empty bubble
  DK.busy = false; DK.abort = null;
  dkRender();
  dkChip();
  dkLoadList();
}
function dkStop() { DK.abort?.abort(); }
// Called by "Ask Claude" buttons elsewhere on the page (news posts, an option contract).
function send(text) {
  if (VIEW !== 'claude') panelReveal('chat');
  dkSend(text);
}

// ---------- conversations: list, open, new, rename, delete, search ----------
async function dkLoadList() {
  try { DK.list = (await dkJson('conversations')).conversations || []; } catch { return; }
  dkList();
}
function dkList() {
  const box = $('cld-list'); if (!box) return;
  const rows = DK.hits || DK.list;
  if (!rows.length) {
    box.innerHTML = `<p class="empty" style="padding:8px">${DK.hits ? 'No conversation contains that.' : 'No conversations yet.'}</p>`;
    return;
  }
  let day = '', html = '';
  const today = new Date().toDateString(), yest = new Date(Date.now() - 864e5).toDateString();
  for (const c of rows) {
    const d = new Date(c.updated || 0).toDateString(), name = d === today ? 'Today' : d === yest ? 'Yesterday' : dkDay(c.updated || 0);
    if (!DK.hits && name !== day) { day = name; html += `<div class="cv-day">${esc(name)}</div>`; }
    html += `<div class="cv${DK.conv?.id === c.id ? ' on' : ''}" role="button" tabindex="0" data-id="${esc(c.id)}">
      <b>${esc(c.title)}</b>
      ${DK.hits ? `<small>${esc(c.date || '')}</small>${(c.hits || []).slice(0, 1).map(h => `<span class="hit">${esc(h.text)}</span>`).join('')}`
        : `<small>${dkClock(c.updated || 0)} · ${Math.ceil((c.messages || 0) / 2)} question${c.messages > 2 ? 's' : ''}</small>`}
      <span class="acts"><button type="button" class="ibtn" data-act="rename" title="Rename" aria-label="Rename">${icon('edit')}</button>
        <button type="button" class="ibtn" data-act="delete" title="Delete" aria-label="Delete">${icon('trash')}</button></span></div>`;
  }
  box.innerHTML = html;
}
async function dkOpen(id) {
  if (DK.busy) return;
  try {
    const c = await dkJson('conversations/' + encodeURIComponent(id));
    DK.conv = { id: c.id, title: c.title, messages: c.messages || [] };
    DK.usage = c.usage || DK.usage;
    DK.web = false;
    store.set('deskConv', c.id);
  } catch (e) { toast(`Couldn't open that conversation: ${e.message}`, 'bad'); return; }
  dkRender(true); dkList(); dkChip();
}
function dkNew() {
  if (DK.busy) return;
  DK.conv = null; DK.web = false; store.set('deskConv', '');
  if (DK.usage) DK.usage = { ...DK.usage, conversation: { tokens: 0, in: 0, out: 0 } };
  dkRender(); dkList(); dkChip();
  (VIEW === 'claude' ? $('cld-input') : $('chat-input'))?.focus();
}
async function dkDelete(id) {
  const c = DK.list.find(x => x.id === id) || (DK.hits || []).find(x => x.id === id); if (!c) return;
  const ok = await ask({ title: 'Delete this conversation?', icon: 'trash', tone: 'danger',
    lede: `${esc(c.title)}<span class="about">It is removed from this PC. Pinned notes you saved from it stay.</span>`,
    keep: 'Keep it', ok: 'Delete conversation' });
  if (!ok) return;
  try { await dkJson('conversations/' + encodeURIComponent(id), { method: 'DELETE' }); } catch (e) { toast(e.message, 'bad'); return; }
  if (DK.conv?.id === id) dkNew();
  if (DK.hits) DK.hits = DK.hits.filter(x => x.id !== id);
  dkLoadList();
}
function dkRename(el, id) {
  const cur = el.querySelector('b').textContent;
  el.innerHTML = `<input class="input cv-edit" value="${esc(cur)}" aria-label="Conversation title" maxlength="120">`;
  const inp = el.querySelector('input'); inp.focus(); inp.select();
  let done = false;
  const finish = async save => {
    if (done) return; done = true;
    const title = inp.value.trim();
    if (save && title && title !== cur) {
      try { await dkJson('conversations/' + encodeURIComponent(id), { method: 'POST', body: JSON.stringify({ title }) }); if (DK.conv?.id === id) DK.conv.title = title; }
      catch (e) { toast(e.message, 'bad'); }
    }
    await dkLoadList(); dkControls();
  };
  inp.onkeydown = e => { if (e.key === 'Enter') finish(true); else if (e.key === 'Escape') { e.stopPropagation(); finish(false); } };
  inp.onblur = () => finish(true);
  inp.onclick = e => e.stopPropagation();
}
let dkSearchT = null;
function dkSearch(q) {
  clearTimeout(dkSearchT);
  if (!q.trim()) { DK.hits = null; dkList(); return; }
  dkSearchT = setTimeout(async () => {
    try { DK.hits = (await dkJson('conversations?q=' + encodeURIComponent(q.trim()))).matches || []; } catch { DK.hits = []; }
    if ($('cld-q').value.trim() === q.trim()) dkList();
  }, 220);
}

// ---------- usage chip ----------
function dkChip() {
  const u = DK.usage || DK.st?.usage; if (!u) return;
  const onKey = DK.st?.primary === 'api' || (DK.conv?.id && DK.keyConvs.has(DK.conv.id));
  const tok = dkTok(u.conversation?.tokens || 0) + ' tok';
  let html, cls = 'uchip', title;
  if (onKey) {
    const k = u.key || {}, pct = k.cap ? Math.round(k.usd / k.cap * 100) : 0;
    html = `<span>KEY</span><b>$${(k.usd || 0).toFixed(2)} / $${(k.cap || 0).toFixed(2)}</b><span>(${pct}%)</span><span>· ${tok}</span>`;
    cls += ' key'; title = 'This conversation is on the API key. Click for details.';
  } else {
    const w = u.plan?.five_hour, pct = w && !w.stale ? w.pct : null;
    html = `<span>5H</span><b>${pct == null ? '—' : Math.round(pct) + '%'}</b><span class="bar"><i style="width:${clamp(pct || 0, 0, 100)}%"></i></span><span>· ${tok}</span>`;
    if (pct != null && pct >= 80) cls += ' warn';
    title = pct == null ? "Your Claude plan's 5-hour usage shows here after the first answer. Click for details."
      : `Your Claude plan's 5-hour window is ${Math.round(pct)}% used; this conversation has used ${tok}. Click for details.`;
  }
  for (const id of ['cld-usage', 'chat-usage']) {
    const el = $(id); if (!el) continue;
    el.className = cls + (id === 'chat-usage' ? ' sm' : ''); el.innerHTML = html; el.title = title; el.hidden = !DK.st?.enabled;
  }
}
function dkUsagePop(anchor) {
  const u = DK.usage || DK.st?.usage || {}, p = u.plan || {}, last = u.last || {}, k = u.key || {};
  const win = (w, label) => {
    if (!w) return `<div class="urow"><span>${label}</span><b>not reported yet</b></div>`;
    const when = w.resets ? `resets ${dkWhen(w.resets)}` : '';
    return `<div class="urow"><span>${label}</span><b>${w.stale ? 'reset since last answer' : Math.round(w.pct) + '%'} <small>${when}</small></b></div>`;
  };
  const el = $('pop-usage');
  el.innerHTML = `<div class="pop-head"><span>Usage</span><span class="muted">${esc(DK.st?.label || '')}</span></div>
    ${DK.st?.engines?.claude_code ? win(p.five_hour, 'Plan, 5-hour window') + win(p.seven_day, 'Plan, this week') : ''}
    <div class="urow"><span>This conversation</span><b>${(u.conversation?.tokens || 0).toLocaleString()} tokens</b></div>
    <div class="urow sub"><span>last answer</span><b>${last.in != null ? `${dkTok(last.in)} in${last.cache_read ? ` (${dkTok(last.cache_read)} cached)` : ''} · ${dkTok(last.out)} out` : '—'}</b></div>
    <div class="urow"><span>Today</span><b>${(u.today?.tokens || 0).toLocaleString()} tokens <small>${u.today?.answers || 0} answers</small></b></div>
    ${DK.st?.engines?.api ? `<hr><div class="urow"><span>API key today</span><b>$${(k.usd || 0).toFixed(2)} of $${(k.cap || 0).toFixed(2)}</b></div>
      <form class="ucap" id="ucap"><span>Daily cap $</span><input class="input" id="ucap-v" inputmode="decimal" value="${(k.cap ?? 2).toFixed(2)}" aria-label="Daily cap in dollars">
        <button class="btn sm" type="submit">Save</button><span class="err" id="ucap-err"></span></form>
      <p class="note" style="padding:0 8px 6px">The key is used only when you choose it after the plan limit is hit, and never past this cap.</p>`
      : '<p class="note" style="padding:4px 8px 6px">No API key is set, so there is no paid fallback.</p>'}
    <p class="note" style="padding:0 8px 6px">Plan percentages come from Claude Code and include everything else you do on your plan. Tokens count what was sent and received, including the part read from cache.</p>`;
  const f = $('ucap');
  if (f) f.onsubmit = async e => {
    e.preventDefault();
    const v = parseFloat($('ucap-v').value);
    if (!(v >= 0)) { $('ucap-err').textContent = 'Enter a number'; return; }
    try { DK.usage = { ...(DK.usage || u), ...(await dkJson('settings', { method: 'POST', body: JSON.stringify({ key_cap_usd: v }) })).usage, conversation: u.conversation }; dkChip(); closePopover(); }
    catch (err) { $('ucap-err').textContent = err.message; }
  };
  togglePopover(el, anchor, 'below-end');
}

// ---------- pinned notes ----------
function dkNotesBody() {
  const el = $('pop-notes'), v = DK.st?.vault || {}, notes = DK.st?.pinned || [];
  el.innerHTML = `<div class="pop-head"><span>Pinned notes</span><span class="muted">sent with every question</span></div>
    <div id="pn-list" class="scroll">${notes.length ? notes.map(n => `<div class="pn" data-id="${esc(n.id)}"><p>${esc(n.text)}${n.date ? ` <small>${esc(n.date)}</small>` : ''}</p>
      <button type="button" class="ibtn" data-pn="edit" title="Edit" aria-label="Edit note">${icon('edit')}</button>
      <button type="button" class="ibtn" data-pn="del" title="Remove" aria-label="Remove note">${icon('trash')}</button></div>`).join('')
      : '<p class="empty" style="padding:4px 8px">Nothing pinned yet. Tell Claude “remember …”, or add a note here.</p>'}</div>
    <form class="pn-add" id="pn-add"><input class="input" id="pn-new" maxlength="400" placeholder="A rule or preference to carry into every chat" aria-label="New pinned note">
      <button class="btn sm" type="submit">Pin</button></form><div class="err" id="pn-err" style="padding:0 8px"></div>
    <hr><p class="note" style="padding:2px 8px 6px">${v.ok ? `Saved as bullets in <b>Pinned notes</b> in your ${esc(v.name)} vault, so you can edit them in Obsidian too. Claude can also read ${v.knowledge_notes} knowledge note${v.knowledge_notes === 1 ? '' : 's'} there.`
      : 'No Obsidian vault is connected, so notes are kept in btcliq\'s data folder and Claude has no knowledge notes. Start btcliq with <code>--vault</code> to connect one.'}</p>`;
  $('pn-add').onsubmit = async e => { e.preventDefault(); await dkNote('POST', { text: $('pn-new').value }); };
}
async function dkNote(method, body, query = '') {
  try {
    const d = await dkJson('notes' + query, { method, ...(body ? { body: JSON.stringify(body) } : {}) });
    DK.st.pinned = d.pinned; DK.st.vault = d.vault || DK.st.vault;
    if (!$('pop-notes').hidden) dkNotesBody();
    $('cld-notes-n').textContent = d.pinned.length || '';
    dkRender();
    return true;
  } catch (e) {
    const err = $('pn-err'); if (err && !$('pop-notes').hidden) err.textContent = e.message; else toast(e.message, 'bad');
    return false;
  }
}

// ---------- wiring ----------
function dkOnView() {            // the Claude view was opened
  dkRender(true); dkChip();
  if (!DK.list.length) dkLoadList();
  setTimeout(() => $('cld-input')?.focus(), 30);
}
function dkThreadClick(e) {
  const t = e.target;
  const more = t.closest('.lk-more'), head = t.closest('.lk-h'), np = t.closest('[data-np]');
  const turn = t.closest('[data-m]'), msgs = DK.conv?.messages || [];
  if (t.closest('[data-brief]')) { const b = DK.st.briefs.find(x => x.id === t.closest('[data-brief]').dataset.brief); if (b) dkSend(b.prompt); return; }
  if (t.closest('[data-open]')) { setView('claude'); return; }
  if (t.closest('[data-key]')) { dkSend(null, { retry: true, engine: 'api' }); return; }
  if (t.closest('[data-copy]') && turn) {
    const m = msgs[+turn.dataset.m];
    navigator.clipboard?.writeText(m?.text || (m?.blocks || []).filter(b => b.t === 'text').map(b => b.text).join('\n\n'));
    toast('Answer copied');
    return;
  }
  const lk = t.closest('.lk');
  if (lk && (more || head)) {
    const b = msgs[+lk.dataset.m]?.blocks?.[+lk.dataset.b]; if (!b || b.state === 'run') return;
    if (more) { b.all = { ...(b.all || {}), [more.dataset.path]: !(b.all && b.all[more.dataset.path]) }; }
    else b.shut = !b.shut;
    lk.outerHTML = dkCard(b, +lk.dataset.m, +lk.dataset.b);
    return;
  }
  if (t.closest('[data-pp]')) { dkAct(t.closest('[data-pp]')); return; }
  if (np) {
    const box = np.closest('.np'), b = msgs[+box.dataset.m]?.blocks?.[+box.dataset.b]; if (!b) return;
    if (np.dataset.np === 'skip') { DK.skipped.add(b.text); store.set('deskSkipped', JSON.stringify([...DK.skipped].slice(-200))); dkRender(); }
    else if (np.dataset.np === 'edit') {
      box.querySelector('.np-text').outerHTML = `<textarea class="input np-text" maxlength="400">${esc(b.text)}</textarea>`;
      np.remove(); box.querySelector('textarea').focus();
    } else {
      const ta = box.querySelector('textarea'), text = (ta ? ta.value : b.text).trim();
      dkJson('notes', { method: 'POST', body: JSON.stringify({ text }) }).then(d => {
        DK.st.pinned = d.pinned; b.text = text; $('cld-notes-n').textContent = d.pinned.length || ''; dkRender();
      }).catch(err => { box.querySelector('.err').textContent = err.message; });
    }
  }
}
function dkComposer(form, input) {
  form.addEventListener('submit', e => {
    e.preventDefault();
    if (DK.busy) { dkStop(); return; }
    const text = input.value; input.value = ''; input.style.height = '';
    dkSend(text);
  });
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); }
    else if (e.key === 'Escape') { e.stopPropagation(); input.blur(); }
  });
  input.addEventListener('input', () => { input.style.height = ''; input.style.height = Math.min(180, input.scrollHeight) + 'px'; });
}
function dkModels() {
  const opts = (DK.st?.models || []).map(m => `<option value="${esc(m.id)}" title="${esc(m.hint)}">${esc(m.label)}</option>`).join('');
  for (const id of ['cld-model', 'chat-model-sel']) {
    const el = $(id); if (!el) continue;
    el.innerHTML = opts; el.value = DK.model; el.hidden = !DK.st?.enabled;
    el.onchange = () => { DK.model = el.value; store.set('deskModel', DK.model); for (const o of ['cld-model', 'chat-model-sel']) if ($(o)) $(o).value = DK.model; };
  }
}
async function dkInit() {
  for (const [box] of dkBoxes()) box?.addEventListener('click', dkThreadClick);
  dkComposer($('cld-form'), $('cld-input'));
  dkComposer($('chat-form'), $('chat-input'));
  $('cld-briefs').addEventListener('click', dkThreadClick);
  $('cld-new').onclick = dkNew; $('chat-new').onclick = dkNew;
  $('chat-open').onclick = () => setView('claude');
  $('cld-q').addEventListener('input', e => dkSearch(e.target.value));
  $('cld-q').addEventListener('keydown', e => { if (e.key === 'Escape') { e.stopPropagation(); e.target.value = ''; dkSearch(''); e.target.blur(); } });
  const pick = e => {
    const row = e.target.closest('.cv'); if (!row) return;
    const act = e.target.closest('[data-act]');
    if (act) { e.stopPropagation(); act.dataset.act === 'delete' ? dkDelete(row.dataset.id) : dkRename(row, row.dataset.id); }
    else if (!e.target.closest('input')) dkOpen(row.dataset.id);
  };
  $('cld-list').addEventListener('click', pick);
  $('cld-list').addEventListener('keydown', e => { if (e.key === 'Enter' && e.target.classList.contains('cv')) pick(e); });
  for (const id of ['cld-web', 'chat-web']) $(id).addEventListener('change', e => { DK.web = e.target.checked; dkControls(); dkRisk(); });
  for (const id of ['cld-usage', 'chat-usage']) $(id).onclick = e => dkUsagePop(e.currentTarget);
  $('cld-notes').onclick = e => { dkNotesBody(); togglePopover($('pop-notes'), e.currentTarget, 'below-end'); };
  $('pop-notes').addEventListener('click', e => {
    const b = e.target.closest('[data-pn]'); if (!b) return;
    const row = b.closest('.pn'), id = row.dataset.id, note = (DK.st.pinned || []).find(n => n.id === id);
    if (b.dataset.pn === 'del') dkNote('DELETE', null, '?id=' + encodeURIComponent(id));
    else if (note) {
      row.innerHTML = `<input class="input" style="flex:1" maxlength="400" value="${esc(note.text)}" aria-label="Edit note">`;
      const inp = row.querySelector('input'); inp.focus();
      inp.onkeydown = ev => { if (ev.key === 'Enter') { ev.preventDefault(); dkNote('POST', { id, text: inp.value }); } else if (ev.key === 'Escape') { ev.stopPropagation(); dkNotesBody(); } };
    }
  });
  dkRender();
  try { DK.st = await dkJson('state'); } catch (e) { DK.st = { enabled: false }; }
  if (!DK.st.models?.some(m => m.id === DK.model)) DK.model = DK.st.model || '';
  DK.usage = DK.st.usage;
  $('cld-briefs').innerHTML = (DK.st.briefs || []).map(b => `<button type="button" class="chip" data-brief="${esc(b.id)}" title="${esc(b.prompt)}">${esc(b.label)}</button>`).join('');
  $('cld-notes-n').textContent = (DK.st.pinned || []).length || '';
  $('chat-via').textContent = DK.st.enabled ? `via ${DK.st.label}` : '';
  dkModels(); dkRender(); dkChip();
  if (DK.st.enabled) {
    await dkLoadList();
    const last = store.get('deskConv', '');
    if (last && DK.list.some(c => c.id === last)) await dkOpen(last);
  }
  if (VIEW === 'claude') dkOnView();
}
dkInit();
