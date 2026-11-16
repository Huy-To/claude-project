/* Notes: an Obsidian-style editor for the vault's own Markdown files (the fifth view) and the
   Scratchpad panel. Loaded after /desk.js; uses $, esc, icon, ask, toast, store, clamp and
   dkApi from the page.

   Two modes, like Obsidian's Source mode and Reading view: Edit (Markdown with syntax colors,
   toolbar, list continuation, [[ suggestions, pasted images) and Read (the formatted page).
   Ctrl+E switches. Every edit saves itself a moment later. A save names the version it was
   based on; when Obsidian changed the file in between, nothing is overwritten and a bar asks
   which version to keep.

   Everything a note renders goes through DOMPurify: this page holds the trading token, and a
   note can contain raw HTML (yours, a paste, or a note another session wrote). */

const NT_CDN = {
  md: 'https://unpkg.com/markdown-it@14.1.0/dist/markdown-it.min.js',
  footnote: 'https://unpkg.com/markdown-it-footnote@4.0.0/dist/markdown-it-footnote.min.js',
  purify: 'https://unpkg.com/dompurify@3.2.3/dist/purify.min.js',
  katex: 'https://unpkg.com/katex@0.16.11/dist/katex.min.js',
  katexCss: 'https://unpkg.com/katex@0.16.11/dist/katex.min.css',
  hljs: 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.10.0/highlight.min.js',
  mermaid: 'https://unpkg.com/mermaid@11.4.1/dist/mermaid.min.js',
};
const NT_IMG = /\.(png|jpe?g|gif|webp|svg|bmp|avif)$/i;
const NT = {
  open: false, tree: null, idx: null, doc: null, mode: store.get('ntMode', 'edit') === 'read' ? 'read' : 'edit',
  libs: null, mermaid: null, blobs: new Map(), folded: new Set(), q: '', hits: null, searchT: null,
  rail: store.get('ntRail', '1') === '1', pollT: null, sug: null, renderN: 0,
};
try { NT.folded = new Set(JSON.parse(store.get('ntFolded', '[]'))); } catch {}

// ---------- server ----------
async function ntApi(path, opt = {}) {
  const o = { ...opt };
  if (o.json !== undefined) { o.body = JSON.stringify(o.json); delete o.json; }
  const r = await dkApi('vault/' + path, o);
  const text = await r.text();
  let d = null; try { d = JSON.parse(text); } catch {}
  if (!r.ok) { const e = new Error((d && d.error) || text || `HTTP ${r.status}`); e.status = r.status; e.data = d; throw e; }
  return d ?? {};
}
const ntQ = p => 'path=' + encodeURIComponent(p);

// ---------- libraries (loaded the first time a note is shown formatted) ----------
function ntScript(src) {
  return new Promise((res, rej) => {
    const s = document.createElement('script'); s.src = src; s.crossOrigin = 'anonymous';
    s.onload = res; s.onerror = () => rej(new Error('Could not load ' + src.split('/').slice(2, 4).join('/') + ' (offline?)'));
    document.head.append(s);
  });
}
function ntLibs() {
  if (!NT.libs) NT.libs = (async () => {
    const l = document.createElement('link'); l.rel = 'stylesheet'; l.href = NT_CDN.katexCss; l.crossOrigin = 'anonymous'; document.head.append(l);
    await Promise.all([NT_CDN.md, NT_CDN.purify, NT_CDN.katex, NT_CDN.hljs].map(ntScript));
    await ntScript(NT_CDN.footnote);
    ntPurifyHooks();
    return ntBuildMd();
  })().catch(e => { NT.libs = null; throw e; });
  return NT.libs;
}
function ntMermaid() {
  if (!NT.mermaid) NT.mermaid = ntScript(NT_CDN.mermaid).then(() => {
    window.mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'dark', fontFamily: 'JetBrains Mono, monospace',
      themeVariables: { background: '#282622', primaryColor: '#33302b', primaryTextColor: '#f4f0e9', lineColor: '#a39b8f', primaryBorderColor: '#9cc0d9' } });
    return window.mermaid;
  }).catch(e => { NT.mermaid = null; throw e; });
  return NT.mermaid;
}
function ntPurifyHooks() {
  // A note can't pin anything over the app: no fixed or sticky positioning in inline styles.
  DOMPurify.addHook('uponSanitizeAttribute', (node, data) => {
    if (data.attrName === 'style' && /position\s*:\s*(fixed|sticky)/i.test(data.attrValue)) data.keepAttr = false;
  });
}
// SANITIZE_NAMED_PROPS renames every id/name a note supplies to user-content-…, so a note can never
// shadow one of the app's own elements (the ticket's fields, the confirmation window) in a lookup by id.
const ntPurify = html => DOMPurify.sanitize(html, { FORBID_TAGS: ['style', 'form', 'base', 'meta', 'link'], FORBID_ATTR: ['autofocus', 'formaction'],
  SANITIZE_NAMED_PROPS: true });

// ---------- vault index: resolve [[links]] like Obsidian (path, then file name) ----------
function ntIndex(tree) {
  const byPath = new Map(), byName = new Map();
  const add = (row, isNote) => {
    const noExt = isNote ? row.path.replace(/\.md$/i, '') : row.path;
    byPath.set(noExt.toLowerCase(), row.path);
    const nm = (isNote ? row.name : row.path.split('/').pop()).toLowerCase();
    const prev = byName.get(nm);
    if (!prev || row.path.length < prev.length) byName.set(nm, row.path);   // shortest path wins, like Obsidian
  };
  for (const n of tree.notes) add(n, true);
  for (const f of tree.files) add(f, false);
  return { byPath, byName };
}
function ntResolve(target) {
  if (!NT.idx || !target) return null;
  let t = target.trim().replace(/\\/g, '/').replace(/^\.?\//, '');
  try { t = decodeURIComponent(t); } catch {}
  const low = t.toLowerCase().replace(/\.md$/, '');
  return NT.idx.byPath.get(low) || NT.idx.byName.get(low) || NT.idx.byName.get(low.split('/').pop()) || null;
}
function ntParseLink(inner) {
  const bar = inner.indexOf('|');
  const link = bar < 0 ? inner : inner.slice(0, bar), alias = bar < 0 ? '' : inner.slice(bar + 1).trim();
  const hash = link.indexOf('#');
  return { target: (hash < 0 ? link : link.slice(0, hash)).trim(), heading: hash < 0 ? '' : link.slice(hash + 1).trim(), alias };
}

// ---------- Markdown: markdown-it plus Obsidian's extras ----------
function ntBuildMd() {
  const md = window.markdownit({
    html: true, linkify: true, breaks: true, typographer: false,
    highlight: (src, lang) => {
      lang = (lang || '').trim().toLowerCase();
      if (lang === 'mermaid') return `<pre class="nt-mermaid">${esc(src)}</pre>`;
      if (lang && window.hljs?.getLanguage(lang)) {
        try { return `<pre class="nt-code"><code class="hljs">${hljs.highlight(src, { language: lang, ignoreIllegals: true }).value}</code></pre>`; } catch {}
      }
      return '';
    },
  });
  md.use(window.markdownitFootnote);
  md.inline.ruler.before('link', 'nt_wikilink', ntWikiRule);
  md.inline.ruler.after('backticks', 'nt_math', ntMathInline);
  md.inline.ruler.before('emphasis', 'nt_mark', ntMarkRule);
  md.inline.ruler.push('nt_tag', ntTagRule);
  md.block.ruler.after('blockquote', 'nt_math_block', ntMathBlock, { alt: ['paragraph', 'reference', 'blockquote', 'list'] });
  md.core.ruler.after('inline', 'nt_tasks', ntTasks);
  const R = md.renderer.rules;
  R.nt_wikilink = (t, i) => {
    const { target, heading, alias } = ntParseLink(t[i].meta.inner);
    const hit = !target || ntResolve(target);
    const label = alias || (target ? target.split('/').pop() + (heading ? ' › ' + heading.replace(/^\^/, '') : '') : heading.replace(/^\^/, ''));
    return `<a href="#" class="nt-wl${hit ? '' : ' missing'}" data-wl="${esc(target)}" data-h="${esc(heading)}">${esc(label)}</a>`;
  };
  R.nt_embed = (t, i) => {
    const { target, heading, alias } = ntParseLink(t[i].meta.inner);
    return `<span class="nt-embed" data-embed="${esc(target)}" data-h="${esc(heading)}" data-alt="${esc(alias)}"></span>`;
  };
  R.nt_math = (t, i) => ntKatex(t[i].content, t[i].meta.display);
  R.nt_math_block = (t, i) => `<div class="nt-math">${ntKatex(t[i].content, true)}</div>\n`;
  R.nt_tag = (t, i) => `<a href="#" class="nt-tag" data-tag="${esc(t[i].content)}">#${esc(t[i].content)}</a>`;
  R.mark_open = () => '<mark>'; R.mark_close = () => '</mark>';
  return md;
}
function ntKatex(src, display) {
  try { return katex.renderToString(src, { displayMode: !!display, throwOnError: false, trust: false, output: 'htmlAndMathml' }); }
  catch (e) { return `<code class="nt-err">${esc(src)}</code>`; }
}
function ntWikiRule(state, silent) {
  const s = state.src, p = state.pos;
  let embed = false, start = p;
  if (s.charCodeAt(p) === 0x21) { if (s.slice(p + 1, p + 3) !== '[[') return false; embed = true; start = p + 1; }
  else if (s.slice(p, p + 2) !== '[[') return false;
  const end = s.indexOf(']]', start + 2);
  if (end < 0) return false;
  const inner = s.slice(start + 2, end);
  if (!inner.trim() || /[\n[]/.test(inner)) return false;
  if (!silent) state.push(embed ? 'nt_embed' : 'nt_wikilink', '', 0).meta = { inner };
  state.pos = end + 2;
  return true;
}
function ntMarkRule(state, silent) {
  const s = state.src, p = state.pos;
  if (s.charCodeAt(p) !== 0x3D || s.charCodeAt(p + 1) !== 0x3D || s.charCodeAt(p + 2) === 0x3D) return false;
  const end = s.indexOf('==', p + 2);
  if (end < 0 || end === p + 2 || /\s/.test(s[p + 2]) || /\s/.test(s[end - 1])) return false;
  if (!silent) {
    const max = state.posMax;
    state.push('mark_open', 'mark', 1);
    state.pos = p + 2; state.posMax = end;
    state.md.inline.tokenize(state);
    state.posMax = max;
    state.push('mark_close', 'mark', -1);
  }
  state.pos = end + 2;
  return true;
}
function ntMathInline(state, silent) {
  const s = state.src, p = state.pos;
  if (s.charCodeAt(p) !== 0x24) return false;
  if (s.charCodeAt(p + 1) === 0x24) {
    const end = s.indexOf('$$', p + 2);
    if (end < 0 || end === p + 2) return false;
    if (!silent) { const t = state.push('nt_math', '', 0); t.content = s.slice(p + 2, end); t.meta = { display: true }; }
    state.pos = end + 2;
    return true;
  }
  const c = s[p + 1];
  if (!c || /\s/.test(c)) return false;
  let end = p + 1;
  while ((end = s.indexOf('$', end)) >= 0 && s[end - 1] === '\\') end++;
  if (end < 0 || /\s/.test(s[end - 1]) || /\d/.test(s[end + 1] || '')) return false;   // "$95k to $100k" stays text
  if (!silent) { const t = state.push('nt_math', '', 0); t.content = s.slice(p + 1, end); t.meta = { display: false }; }
  state.pos = end + 1;
  return true;
}
function ntMathBlock(state, startLine, endLine, silent) {
  const lineAt = l => state.src.slice(state.bMarks[l] + state.tShift[l], state.eMarks[l]);
  const first = lineAt(startLine);
  if (!first.startsWith('$$')) return false;
  const rest = first.slice(2).trim();
  let content, last = startLine;
  if (rest.endsWith('$$') && rest.length >= 2) content = rest.slice(0, -2);
  else {
    const body = [rest];
    let found = false;
    for (let l = startLine + 1; l < endLine; l++) {
      const s = lineAt(l).trim();
      if (s.endsWith('$$')) { body.push(s.slice(0, -2)); last = l; found = true; break; }
      body.push(lineAt(l));
    }
    if (!found) return false;
    content = body.join('\n');
  }
  if (silent) return true;
  const t = state.push('nt_math_block', '', 0);
  t.content = content.trim(); t.map = [startLine, last + 1]; t.block = true;
  state.line = last + 1;
  return true;
}
function ntTagRule(state, silent) {
  const s = state.src, p = state.pos;
  if (s.charCodeAt(p) !== 0x23 || (p > 0 && !/\s|[(]/.test(s[p - 1]))) return false;
  const m = /^#([\p{L}\p{N}_\-/]+)/u.exec(s.slice(p));
  if (!m || /^\d+$/.test(m[1])) return false;
  if (!silent) state.push('nt_tag', '', 0).content = m[1];
  state.pos = p + m[0].length;
  return true;
}
function ntTasks(state) {
  const T = state.tokens;
  for (let i = 2; i < T.length; i++) {
    const t = T[i];
    if (t.type !== 'inline' || T[i - 1].type !== 'paragraph_open' || T[i - 2].type !== 'list_item_open') continue;
    const m = /^\[([ xX\-/>!?*])\](\s|$)/.exec(t.content);
    const first = t.children?.[0];
    if (!m || !first || first.type !== 'text' || !first.content.startsWith(`[${m[1]}]`)) continue;
    first.content = first.content.slice(3).replace(/^\s/, '');
    const line = T[i - 2].map ? T[i - 2].map[0] : -1;
    const cb = new state.Token('html_inline', '', 0);
    cb.content = `<input type="checkbox" class="nt-task" data-line="${line}"${m[1] !== ' ' ? ' checked' : ''}>`;
    t.children.unshift(cb);
    T[i - 2].attrJoin('class', 'nt-task-item' + (m[1] !== ' ' ? ' done' : '') + (m[1] === '-' ? ' cancelled' : ''));
  }
}

// Front matter and %%comments%%, removed before rendering. Comments become blank lines, so line
// numbers (used by the task checkboxes) still match the file.
function ntSplit(text) {
  let fm = null, body = text, offset = 0;
  const m = /^---\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/.exec(text);
  if (m) { fm = m[1]; body = text.slice(m[0].length); offset = m[0].split('\n').length - 1; }
  body = body.replace(/(^|\n)(```|~~~)[^\n]*\n[\s\S]*?\n\2[^\n]*|%%[\s\S]*?%%/g, (all, a, fence) => fence ? all : all.replace(/[^\n]/g, ''));
  return { fm, body, offset };
}
function ntProps(fm) {
  // Obsidian's properties: scalars, lists (- item) and inline lists ([a, b]).
  const out = [];
  let cur = null;
  for (const line of fm.split('\n')) {
    let m;
    if ((m = /^([^\s:#][^:]*):\s*(.*)$/.exec(line))) {
      cur = { k: m[1].trim(), v: [] }; out.push(cur);
      const v = m[2].trim();
      if (/^\[.*\]$/.test(v)) cur.v = v.slice(1, -1).split(',').map(x => x.trim()).filter(Boolean);
      else if (v) cur.v = [v];
    } else if ((m = /^\s+-\s*(.*)$/.exec(line)) && cur) cur.v.push(m[1].trim());
  }
  for (const p of out) p.v = p.v.map(x => x.replace(/^(["'])(.*)\1$/, '$2'));
  return out;
}

// Render Markdown into an element. depth > 0 is an embedded note (![[note]]).
async function ntRender(el, text, { depth = 0, path = '', heading = '' } = {}) {
  const md = await ntLibs();
  let { fm, body, offset } = ntSplit(text);
  if (heading) {                      // ![[note#Heading]]: just that section
    const lines = body.split('\n');
    const h = heading.replace(/^\^/, '').toLowerCase();
    const at = lines.findIndex(l => /^#{1,6}\s/.test(l) && l.replace(/^#+\s+/, '').trim().toLowerCase() === h);
    if (at >= 0) {
      const lvl = lines[at].match(/^#+/)[0].length;
      let end = lines.findIndex((l, i) => i > at && (m => m && m[0].length <= lvl)(l.match(/^#{1,6}(?=\s)/)));
      body = lines.slice(at, end < 0 ? lines.length : end).join('\n');
    }
    fm = null;
  }
  let html = md.render(body, {});
  if (fm && depth === 0) html = ntPropsHtml(ntProps(fm)) + html;
  el.innerHTML = ntPurify(html);
  el.dataset.offset = heading ? '' : offset;
  ntCallouts(el);
  ntHeadingIds(el);
  el.querySelectorAll('a[href]').forEach(a => {
    const h = a.getAttribute('href');
    if (/^(https?:|mailto:)/i.test(h)) { a.target = '_blank'; a.rel = 'noopener noreferrer'; }
  });
  el.querySelectorAll('table').forEach(t => { if (!t.parentElement.classList.contains('nt-tw')) { const w = document.createElement('div'); w.className = 'nt-tw'; t.replaceWith(w); w.append(t); } });
  await Promise.all([ntEmbeds(el, depth, path), ntLocalImages(el), ntMermaidRender(el)]);
}
function ntPropsHtml(props) {
  if (!props.length) return '';
  const val = v => /^\[\[.*\]\]$/.test(v) ? v : esc(v);
  return `<div class="nt-props" data-props><div class="nt-props-h">Properties</div>${props.map(p => `<div class="nt-prop"><span class="k">${esc(p.k)}</span><span class="v">${
    p.v.length > 1 || ['tags', 'aliases', 'categories', 'cssclasses'].includes(p.k.toLowerCase())
      ? p.v.map(x => `<span class="pill">${p.k.toLowerCase() === 'tags' ? '#' + esc(x.replace(/^#/, '')) : val(x)}</span>`).join('')
      : val(p.v[0] ?? '') || '<span class="muted">empty</span>'}</span></div>`).join('')}</div>\n\n`;
}
// Wiki links inside property values get rendered by markdown-it when the block is parsed as HTML: they
// don't, so turn [[x]] in the properties block into links here.
function ntPropLinks(el) {
  el.querySelectorAll('.nt-props .v, .nt-props .pill').forEach(v => {
    if (!v.children.length && /\[\[.+\]\]/.test(v.textContent)) {
      const { target, heading, alias } = ntParseLink(v.textContent.trim().slice(2, -2));
      v.innerHTML = `<a href="#" class="nt-wl${ntResolve(target) ? '' : ' missing'}" data-wl="${esc(target)}" data-h="${esc(heading)}">${esc(alias || target)}</a>`;
    }
  });
}
const NT_CALLOUT = {
  note: ['accent', 'edit'], info: ['accent', 'alert'], todo: ['accent', 'check-c'], abstract: ['teal', 'list'], summary: ['teal', 'list'], tldr: ['teal', 'list'],
  tip: ['teal', 'flame'], hint: ['teal', 'flame'], important: ['teal', 'flame'], success: ['up', 'check-c'], check: ['up', 'check-c'], done: ['up', 'check-c'],
  question: ['warn', 'alert'], help: ['warn', 'alert'], faq: ['warn', 'alert'], warning: ['warn', 'alert'], caution: ['warn', 'alert'], attention: ['warn', 'alert'],
  failure: ['down', 'x-c'], fail: ['down', 'x-c'], missing: ['down', 'x-c'], danger: ['down', 'flame'], error: ['down', 'x-c'], bug: ['down', 'alert'],
  example: ['pl', 'list'], quote: ['gray', 'text'], cite: ['gray', 'text'],
};
function ntCallouts(root) {
  ntPropLinks(root);
  for (const bq of [...root.querySelectorAll('blockquote')]) {
    const p = bq.firstElementChild, tn = p?.firstChild;
    if (!p || p.tagName !== 'P' || !tn || tn.nodeType !== 3) continue;
    const m = /^\s*\[!([\w-]+)\]([+-]?)[ \t]*/.exec(tn.textContent);
    if (!m) continue;
    const type = m[1].toLowerCase(), [tone, ic] = NT_CALLOUT[type] || ['accent', 'edit'];
    tn.textContent = tn.textContent.slice(m[0].length);
    const title = document.createElement('div'); title.className = 'nt-co-t';
    title.innerHTML = icon(ic);
    const tt = document.createElement('span');
    while (p.firstChild && p.firstChild.nodeName !== 'BR') tt.append(p.firstChild);
    if (p.firstChild?.nodeName === 'BR') p.firstChild.remove();
    if (!tt.textContent.trim()) tt.textContent = type[0].toUpperCase() + type.slice(1);
    title.append(tt);
    const box = document.createElement('div');
    box.className = `nt-co ${tone}`; box.dataset.callout = type;
    const content = document.createElement('div'); content.className = 'nt-co-b';
    if (!p.textContent.trim() && !p.querySelector('img, input, .nt-embed')) p.remove();
    while (bq.firstChild) content.append(bq.firstChild);
    box.append(title);
    if (content.childNodes.length && content.textContent.trim() + content.querySelectorAll('img, .nt-embed, input').length) box.append(content);
    if (m[2]) {
      box.classList.add('fold'); if (m[2] === '-') box.classList.add('shut');
      title.insertAdjacentHTML('beforeend', icon('chev-down', 'nt-co-chev'));
      title.tabIndex = 0; title.setAttribute('role', 'button');
    }
    bq.replaceWith(box);
  }
}
const ntSlug = s => s.toLowerCase().trim().replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-|-$/g, '');
function ntHeadingIds(root) {
  root.querySelectorAll('h1, h2, h3, h4, h5, h6').forEach(h => { if (!h.id) h.id = 'nt-h-' + ntSlug(h.textContent); });
}
async function ntBlob(path) {
  if (NT.blobs.has(path)) return NT.blobs.get(path);
  const p = (async () => {
    const r = await dkApi('vault/file?' + ntQ(path));
    if (!r.ok) throw new Error('missing');
    return URL.createObjectURL(await r.blob());
  })();
  NT.blobs.set(path, p);
  p.catch(() => NT.blobs.delete(path));
  return p;
}
function ntSize(img, alt) {       // ![[pic.png|300]] or |300x200
  const m = /^(\d+)(?:x(\d+))?$/.exec(alt || '');
  if (m) { img.style.width = m[1] + 'px'; if (m[2]) img.style.height = m[2] + 'px'; }
  else if (alt) img.alt = alt;
}
async function ntEmbeds(root, depth, from) {
  const jobs = [...root.querySelectorAll('.nt-embed')].map(async sp => {
    const target = sp.dataset.embed, heading = sp.dataset.h, alt = sp.dataset.alt;
    const path = ntResolve(target);
    if (!path) { sp.className = 'nt-embed missing'; sp.textContent = `"${target}" is not in the vault yet`; return; }
    if (NT_IMG.test(path)) {
      const img = document.createElement('img'); img.className = 'nt-img'; img.alt = path.split('/').pop(); ntSize(img, alt);
      sp.replaceWith(img);
      try { img.src = await ntBlob(path); } catch { img.replaceWith(Object.assign(document.createElement('span'), { className: 'nt-embed missing', textContent: `Could not load ${path}` })); }
      return;
    }
    if (/\.pdf$/i.test(path)) {
      try {
        const f = document.createElement('iframe'); f.className = 'nt-pdf'; f.title = path; f.src = await ntBlob(path); sp.replaceWith(f);
      } catch { sp.textContent = `Could not load ${path}`; }
      return;
    }
    if (depth >= 2 || path === from) { sp.innerHTML = `<a href="#" class="nt-wl" data-wl="${esc(target)}">${esc(target)}</a>`; return; }
    try {
      const n = await ntApi('note?' + ntQ(path));
      const box = document.createElement('div'); box.className = 'nt-transclude';
      box.innerHTML = `<div class="nt-transclude-h"><a href="#" class="nt-wl" data-wl="${esc(target)}" data-h="${esc(heading)}">${esc(n.name)}${heading ? ' › ' + esc(heading) : ''}</a></div><div class="nt-transclude-b"></div>`;
      sp.replaceWith(box);
      await ntRender(box.lastElementChild, n.text, { depth: depth + 1, path, heading });
    } catch (e) { sp.textContent = `Could not embed ${target}: ${e.message}`; }
  });
  await Promise.all(jobs);
}
async function ntLocalImages(root) {     // ![](attachments/x.png): a path inside the vault
  await Promise.all([...root.querySelectorAll('img[src]')].map(async img => {
    const src = img.getAttribute('src');
    if (/^(https?:|data:|blob:)/i.test(src)) return;
    const path = ntResolve(src) || ntResolve(NT.doc?.path?.replace(/[^/]*$/, '') + src);
    if (!path) return;
    try { img.src = await ntBlob(path); } catch {}
  }));
}
async function ntMermaidRender(root) {
  const blocks = [...root.querySelectorAll('pre.nt-mermaid')];
  if (!blocks.length) return;
  let mm;
  try { mm = await ntMermaid(); } catch (e) { blocks.forEach(b => b.title = e.message); return; }
  for (const b of blocks) {
    try {
      const { svg } = await mm.render('ntmm' + (++NT.renderN), b.textContent);
      const d = document.createElement('div'); d.className = 'nt-diagram'; d.innerHTML = svg; b.replaceWith(d);
    } catch (e) {
      b.classList.add('nt-err'); b.title = 'Mermaid: ' + (e.message || e);
      document.querySelectorAll('[id^="dntmm"]').forEach(x => x.remove());     // mermaid leaves its error box behind
    }
  }
}

// ---------- one open file (the view's note, or the Scratchpad panel's) ----------
// Saves itself a moment after each edit, never over a newer version on disk.
class NtDoc {
  constructor(path, { onState, onReload, onConflict }) {
    Object.assign(this, { path, text: '', hash: null, exists: false, dirty: false, saving: null, timer: null, conflict: null,
      onState, onReload, onConflict, state: 'idle', err: '' });
  }
  async load({ create = '' } = {}) {
    try {
      const n = await ntApi('note?' + ntQ(this.path));
      Object.assign(this, { text: n.text, hash: n.hash, exists: true, dirty: false, conflict: null });
    } catch (e) {
      if (e.status !== 404) throw e;
      Object.assign(this, { text: create, hash: null, exists: false, dirty: false, conflict: null });
    }
    this.set('saved');
    return this;
  }
  set(state, err = '') { this.state = state; this.err = err; this.onState?.(this); }
  edit(text) {
    if (text === this.text) return;
    this.text = text; this.dirty = true;
    if (this.conflict) return this.set('conflict');
    this.set('edited');
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.save(), 900);
  }
  async save({ force = false } = {}) {
    clearTimeout(this.timer);
    if (this.saving) { await this.saving.catch(() => {}); if (!this.dirty) return true; }
    if (!this.dirty || (this.conflict && !force)) return !this.dirty;
    const text = this.text;
    this.set('saving');
    this.saving = (async () => {
      const base = force ? this.conflict?.hash ?? null : this.hash;
      const r = await ntApi('note', { method: 'PUT', json: { path: this.path, text, base, create: !force && !this.exists } });
      this.hash = r.hash; this.exists = true; this.conflict = null;
      if (this.text === text) this.dirty = false;
    })();
    try {
      await this.saving;
      this.set(this.dirty ? 'edited' : 'saved');
      if (this.dirty) this.timer = setTimeout(() => this.save(), 400);
      return true;
    } catch (e) {
      if (e.status === 409 || (e.status === 400 && /already exists/.test(e.message))) {
        this.conflict = e.data?.current || { hash: null, text: '' };
        if (/already exists/.test(e.message)) { try { this.conflict = await ntApi('note?' + ntQ(this.path)); } catch {} }
        this.set('conflict'); this.onConflict?.(this);
      } else this.set('error', e.message);
      return false;
    } finally { this.saving = null; }
  }
  // Picks up a change made elsewhere (Obsidian, the other editor on this page).
  async poll() {
    if (this.saving || document.hidden) return;
    let n = null;
    try { n = await ntApi('note?' + ntQ(this.path)); } catch (e) { if (e.status !== 404) return; }
    if (this.saving) return;
    const hash = n?.hash ?? null;
    if (hash === this.hash || (this.conflict && hash === (this.conflict.hash ?? null))) return;
    if (!n && !this.exists) return;
    if (!this.dirty) {
      Object.assign(this, { text: n ? n.text : '', hash, exists: !!n, conflict: null });
      this.set('saved'); this.onReload?.(this);
    } else { this.conflict = n || { hash: null, text: '' }; this.set('conflict'); this.onConflict?.(this); }
  }
  takeTheirs() {
    const c = this.conflict;
    Object.assign(this, { text: c?.text ?? '', hash: c?.hash ?? null, exists: c?.hash != null, dirty: false, conflict: null });
    this.set('saved'); this.onReload?.(this);
  }
}
const NT_STATE = { idle: '', saved: 'Saved', edited: 'Edited', saving: 'Saving…', conflict: 'Not saved: changed on disk', error: 'Not saved' };

// ---------- the editor (a textarea over a syntax-colored copy of itself) ----------
function ntHlInline(s) {
  const keep = [];
  const stash = h => `\u0000${keep.push(h) - 1}\u0000`;
  s = s.replace(/(`+)(.+?)\1/g, m => stash(`<span class="h-code">${m}</span>`));
  s = s.replace(/%%.*?%%/g, m => stash(`<span class="h-cmt">${m}</span>`));
  s = s.replace(/!?\[\[[^\]\n]+\]\]/g, m => stash(`<span class="h-wl">${m}</span>`));
  s = s.replace(/!?\[[^\]\n]*\]\([^)\n]*\)/g, m => stash(`<span class="h-link">${m}</span>`));
  s = s.replace(/(^|[\s(])(#[\p{L}\p{N}_\-/]*[\p{L}_\-/][\p{L}\p{N}_\-/]*)/gu, (m, a, t) => a + stash(`<span class="h-tag">${t}</span>`));
  s = s.replace(/\$\$.+?\$\$|\$[^\s$](?:[^$]*[^\s$])?\$(?!\d)/g, m => stash(`<span class="h-math">${m}</span>`));
  s = s.replace(/(\*\*|__)(?=\S)(.+?)(\S)\1/g, '<span class="h-b"><span class="h-mk">$1</span>$2$3<span class="h-mk">$1</span></span>');
  s = s.replace(/(^|[^*\w])(\*|_)(?=\S)([^*_\n]+?)(\S?)\2(?![*\w])/g, '$1<span class="h-i"><span class="h-mk">$2</span>$3$4<span class="h-mk">$2</span></span>');
  s = s.replace(/==(?=\S)(.+?)==/g, '<span class="h-mark"><span class="h-mk">==</span>$1<span class="h-mk">==</span></span>');
  s = s.replace(/~~(?=\S)(.+?)~~/g, '<span class="h-del"><span class="h-mk">~~</span>$1<span class="h-mk">~~</span></span>');
  return s.replace(/\u0000(\d+)\u0000/g, (m, i) => keep[+i]);
}
function ntHl(text) {
  const out = [];
  let fence = null, fm = false, math = false;
  text.split('\n').forEach((raw, i) => {
    const ln = esc(raw);
    if (i === 0 && raw === '---') { fm = true; out.push(`<span class="h-fm">${ln}</span>`); return; }
    if (fm) { if (raw.trim() === '---') fm = false; out.push(`<span class="h-fm">${ln.replace(/^([^\s:][^:]*:)/, '<span class="h-key">$1</span>')}</span>`); return; }
    const f = /^\s*(`{3,}|~{3,})/.exec(raw);
    if (f && (!fence || f[1][0] === fence[0])) { fence = fence ? null : f[1]; out.push(`<span class="h-fence">${ln}</span>`); return; }
    if (fence) { out.push(`<span class="h-code">${ln}</span>`); return; }
    const tr = raw.trim();
    if (!math && tr.startsWith('$$') && (tr === '$$' || !tr.slice(2).endsWith('$$'))) { math = true; out.push(`<span class="h-math">${ln}</span>`); return; }
    if (math) { out.push(`<span class="h-math">${ln}</span>`); if (tr.endsWith('$$')) math = false; return; }
    let m;
    if ((m = /^(#{1,6})(\s.*)?$/.exec(raw))) { out.push(`<span class="h-h h${m[1].length}"><span class="h-mk">${m[1]}</span>${ntHlInline(esc(m[2] || ''))}</span>`); return; }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(raw)) { out.push(`<span class="h-mk">${ln}</span>`); return; }
    if ((m = /^(\s*(?:&gt;\s?)+)(\[![\w-]+\][+-]?)?(.*)$/.exec(ln))) {
      out.push(`<span class="h-quote"><span class="h-mk">${m[1]}</span>${m[2] ? `<span class="h-co">${m[2]}</span>` : ''}${ntHlInline(m[3])}</span>`); return;
    }
    if ((m = /^(\s*)([-*+]|\d+[.)])(\s+)(\[[ xX\-/>!?*]\]\s)?(.*)$/.exec(ln))) {
      out.push(`${m[1]}<span class="h-li">${m[2]}</span>${m[3]}${m[4] ? `<span class="h-task${m[4][1] !== ' ' ? ' done' : ''}">${m[4]}</span>` : ''}${ntHlInline(m[5])}`); return;
    }
    if (/^\s*\|/.test(raw)) { out.push(ntHlInline(ln).replace(/\|/g, '<span class="h-mk">|</span>')); return; }
    out.push(ntHlInline(ln));
  });
  return out.join('\n') + '\n​';
}
// One editor: a textarea plus its colored copy, stacked in one grid cell, inside a scroller.
function ntEditor(ta, hl, { onInput, onSave, onToggle, getPath }) {
  const paint = () => { hl.innerHTML = ntHl(ta.value); };
  const insert = (start, end, text, selA = null, selB = null) => {
    ta.focus();
    ta.setSelectionRange(start, end);
    // execCommand keeps Ctrl+Z working. An empty insert is a delete (insertText '' leaves the caret one short).
    const ok = text ? document.execCommand('insertText', false, text) : start === end || document.execCommand('delete');
    if (!ok) ta.setRangeText(text, start, end, 'end');
    if (selA != null) ta.setSelectionRange(selA, selB ?? selA);
    else ta.setSelectionRange(start + text.length, start + text.length);
    paint(); onInput();
  };
  const lineStart = i => ta.value.lastIndexOf('\n', i - 1) + 1;
  const lineEnd = i => { const e = ta.value.indexOf('\n', i); return e < 0 ? ta.value.length : e; };
  const wrap = (a, b = a, ph = '') => {
    const s = ta.selectionStart, e = ta.selectionEnd, sel = ta.value.slice(s, e);
    if (sel && sel.startsWith(a) && sel.endsWith(b) && sel.length >= a.length + b.length) return insert(s, e, sel.slice(a.length, sel.length - b.length), s, e - a.length - b.length);
    if (!sel && ta.value.slice(s - a.length, s) === a && ta.value.slice(e, e + b.length) === b) return insert(s - a.length, e + b.length, '', s - a.length);
    const inner = sel || ph;
    insert(s, e, a + inner + b, s + a.length, s + a.length + inner.length);
  };
  const lines = fn => {       // apply fn(line, i) to every selected line
    const s = lineStart(ta.selectionStart), e = lineEnd(ta.selectionEnd);
    const block = ta.value.slice(s, e).split('\n').map(fn).join('\n');
    insert(s, e, block, s, s + block.length);
  };
  const prefix = (re, add) => lines(l => re.test(l) ? l.replace(re, '') : add + l.replace(/^(\s*)([-*+]\s(\[.\]\s)?|\d+[.)]\s|>\s?|#{1,6}\s)/, '$1'));
  const ops = {
    bold: () => wrap('**', '**', 'bold'), italic: () => wrap('*', '*', 'italic'), strike: () => wrap('~~', '~~', 'text'),
    mark: () => wrap('==', '==', 'highlight'), code: () => wrap('`', '`', 'code'),
    heading: () => lines(l => { const m = /^(#{1,6})\s/.exec(l); return m ? (m[1].length >= 3 ? l.replace(/^#+\s/, '') : '#' + l) : '## ' + l; }),
    ul: () => prefix(/^\s*[-*+]\s(?!\[.\])/, '- '), ol: () => { let n = 0; lines(l => /^\s*\d+[.)]\s/.test(l) ? l.replace(/^(\s*)\d+[.)]\s/, '$1') : `${++n}. ` + l); },
    task: () => prefix(/^\s*[-*+]\s\[.\]\s/, '- [ ] '), quote: () => prefix(/^\s*>\s?/, '> '),
    wikilink: () => { const s = ta.selectionStart, e = ta.selectionEnd, sel = ta.value.slice(s, e); insert(s, e, `[[${sel}]]`, s + 2, s + 2 + sel.length); suggest(); },
    link: () => { const s = ta.selectionStart, e = ta.selectionEnd, sel = ta.value.slice(s, e) || 'text'; insert(s, e, `[${sel}](https://)`, s + sel.length + 3, s + sel.length + 11); },
    callout: () => { const s = lineStart(ta.selectionStart), e = lineEnd(ta.selectionEnd), sel = ta.value.slice(s, e);
      const body = sel ? sel.split('\n').map(l => '> ' + l).join('\n') : '> ';
      insert(s, e, `> [!note] Title\n${body}`, s + 10, s + 15); },
    codeblock: () => { const s = ta.selectionStart, e = ta.selectionEnd, sel = ta.value.slice(s, e); const pre = s && ta.value[s - 1] !== '\n' ? '\n' : '';
      insert(s, e, `${pre}\`\`\`\n${sel}\n\`\`\`\n`, s + pre.length + 3); },
    math: () => wrap('$', '$', 'x^2'),
    table: () => { const s = ta.selectionStart; const pre = s && ta.value[s - 1] !== '\n' ? '\n\n' : '';
      insert(s, ta.selectionEnd, `${pre}| Column | Column |\n| ------ | ------ |\n|        |        |\n`, s + pre.length + 2, s + pre.length + 8); },
    hr: () => { const s = ta.selectionStart; insert(s, ta.selectionEnd, (s && ta.value[s - 1] !== '\n' ? '\n' : '') + '\n---\n'); },
    image: () => { const inp = document.createElement('input'); inp.type = 'file'; inp.accept = 'image/*'; inp.onchange = () => inp.files[0] && attach(inp.files[0]); inp.click(); },
  };
  async function attach(file) {
    const ext = (file.type.split('/')[1] || file.name.split('.').pop() || '').replace('jpeg', 'jpg').replace('svg+xml', 'svg');
    const at = ta.selectionStart, mark = `![[Uploading ${file.name || 'image'}…]]`;
    insert(at, ta.selectionEnd, mark);
    try {
      const r = await dkApi(`vault/attach?ext=${encodeURIComponent(ext)}${file.name && !/^image\.\w+$/.test(file.name) ? '&name=' + encodeURIComponent(file.name) : ''}`, { method: 'POST', body: file });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
      await ntRefreshTree();
      const i = ta.value.indexOf(mark);
      if (i >= 0) insert(i, i + mark.length, `![[${d.name}]]`);
    } catch (e) {
      const i = ta.value.indexOf(mark);
      if (i >= 0) insert(i, i + mark.length, '');
      toast('Image not saved: ' + e.message, 'bad');
    }
  }
  // [[ suggestions: notes (and files after ![[), shown at the caret.
  const pop = document.createElement('div'); pop.className = 'nt-sug'; pop.hidden = true; pop.setAttribute('role', 'listbox');
  document.body.append(pop);
  let sug = null;
  function caretXY() {
    const probe = `<span class="nt-probe">​</span>`;
    hl.innerHTML = esc(ta.value.slice(0, ta.selectionStart)) + probe;
    const r = hl.querySelector('.nt-probe').getBoundingClientRect();
    paint();
    return r;
  }
  function suggest() {
    const before = ta.value.slice(lineStart(ta.selectionStart), ta.selectionStart);
    const m = /(!?)\[\[([^\]|#\n]*)$/.exec(before);
    if (!m || !NT.tree || ta.selectionStart !== ta.selectionEnd) return close();
    const q = m[2].toLowerCase(), embed = !!m[1];
    const rows = [...NT.tree.notes.map(n => ({ path: n.path, name: n.name, note: true })),
                  ...(embed ? NT.tree.files.map(f => ({ path: f.path, name: f.path.split('/').pop(), note: false })) : [])];
    const scored = rows.map(r => { const nm = r.name.toLowerCase(), p = r.path.toLowerCase();
      return [nm.startsWith(q) ? 0 : nm.includes(q) ? 1 : p.includes(q) ? 2 : 9, r]; })
      .filter(x => x[0] < 9).sort((a, b) => a[0] - b[0] || a[1].name.localeCompare(b[1].name)).slice(0, 8).map(x => x[1]);
    if (!scored.length && !q) return close();
    sug = { q: m[2], rows: scored, i: 0, create: q && !scored.some(r => r.name.toLowerCase() === q) ? m[2] : null };
    const r = caretXY();
    pop.innerHTML = scored.map((x, i) => `<div class="o${i === 0 ? ' on' : ''}" role="option" data-i="${i}"><b>${esc(x.name)}</b><small>${esc(x.path.includes('/') ? x.path.replace(/\/[^/]*$/, '') : '')}</small></div>`).join('')
      + (sug.create ? `<div class="o new${scored.length ? '' : ' on'}" data-i="${scored.length}">${icon('plus')}<span>Link to a new note "<b>${esc(sug.create)}</b>"</span></div>` : '')
      + `<div class="ft"><kbd>↑↓</kbd> pick <kbd>Enter</kbd> link <kbd>Esc</kbd> close</div>`;
    if (!scored.length) sug.i = 0;
    pop.hidden = false;
    const w = pop.offsetWidth, h = pop.offsetHeight;
    pop.style.left = clamp(r.left, 8, innerWidth - w - 8) + 'px';
    pop.style.top = (r.bottom + h + 8 > innerHeight ? r.top - h - 4 : r.bottom + 4) + 'px';
  }
  function close() { sug = null; pop.hidden = true; }
  function pick(i) {
    if (!sug) return;
    const row = sug.rows[i];
    const name = row ? (NT.idx.byName.get(row.name.toLowerCase()) === row.path ? row.name : row.path.replace(/\.md$/i, '')) : sug.create;
    const s = ta.selectionStart, start = s - sug.q.length;
    const closeBr = ta.value.slice(s, s + 2) === ']]' ? '' : ']]';
    close();
    insert(start, s, name + closeBr, start + name.length + 2);
  }
  pop.addEventListener('pointerdown', e => { const o = e.target.closest('.o'); if (o) { e.preventDefault(); pick(+o.dataset.i); } });
  ta.addEventListener('blur', () => setTimeout(close, 120));

  ta.addEventListener('input', () => { paint(); onInput(); suggest(); });
  ta.addEventListener('keydown', e => {
    const mod = e.ctrlKey || e.metaKey, k = e.key.toLowerCase();
    if (sug && !pop.hidden) {
      const n = sug.rows.length + (sug.create ? 1 : 0);
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault(); sug.i = (sug.i + (e.key === 'ArrowDown' ? 1 : n - 1)) % n;
        pop.querySelectorAll('.o').forEach((o, i) => o.classList.toggle('on', i === sug.i)); return;
      }
      if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); pick(sug.i); return; }
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); return; }
    }
    if (mod && !e.altKey) {
      const map = { b: 'bold', i: 'italic', k: 'link', l: 'task' };
      if (k === 's') { e.preventDefault(); onSave(); return; }
      if (k === 'e' && onToggle) { e.preventDefault(); onToggle(); return; }
      if (e.shiftKey && k === 'x') { e.preventDefault(); ops.strike(); return; }
      if (e.shiftKey && k === 'h') { e.preventDefault(); ops.mark(); return; }
      if (map[k] && !e.shiftKey) { e.preventDefault(); ops[map[k]](); return; }
    }
    if (e.key === 'Tab' && !mod) {
      e.preventDefault();
      const s = ta.selectionStart, ee = ta.selectionEnd;
      const multi = ta.value.slice(s, ee).includes('\n'), inList = /^\s*([-*+]|\d+[.)])\s/.test(ta.value.slice(lineStart(s), lineEnd(s)));
      if (e.shiftKey) lines(l => l.replace(/^(\t| {1,4})/, ''));
      else if (multi || inList) lines(l => '\t' + l);
      else insert(s, ee, '\t');
      return;
    }
    if (e.key === 'Enter' && !e.shiftKey && !mod && ta.selectionStart === ta.selectionEnd) {
      const s = ta.selectionStart, ls = lineStart(s), line = ta.value.slice(ls, s);
      const m = /^(\s*)(>\s?)?(?:([-*+])\s(\[.\]\s)?|(\d+)([.)])\s)?/.exec(line);
      if (m && (m[2] || m[3] || m[5])) {
        const marker = m[3] ? `${m[3]} ${m[4] ? '[ ] ' : ''}` : m[5] ? `${+m[5] + 1}${m[6]} ` : '';
        if (line.trim() === m[0].trim()) { e.preventDefault(); insert(ls, s, ''); return; }   // an empty item ends the list
        e.preventDefault();
        insert(s, s, '\n' + m[1] + (m[2] || '') + marker);
      }
    }
  });
  ta.addEventListener('paste', e => {
    const f = [...(e.clipboardData?.files || [])].find(f => f.type.startsWith('image/'));
    if (f) { e.preventDefault(); attach(f); }
  });
  ta.addEventListener('dragover', e => { if ([...e.dataTransfer.items].some(i => i.kind === 'file')) e.preventDefault(); });
  ta.addEventListener('drop', e => {
    const f = [...(e.dataTransfer?.files || [])].find(f => f.type.startsWith('image/'));
    if (f) { e.preventDefault(); attach(f); }
  });
  return { paint, ops, set(text) { ta.value = text; paint(); close(); }, close, attach };
}

// ---------- the view ----------
function ntMount() {
  if (NT.mounted) return;
  NT.mounted = true;
  const tb = [
    ['heading', 'H', 'Heading (cycles ## ###)'], ['bold', '<b>B</b>', 'Bold (Ctrl+B)'], ['italic', '<i>I</i>', 'Italic (Ctrl+I)'],
    ['strike', '<s>S</s>', 'Strikethrough (Ctrl+Shift+X)'], ['mark', '<span class="hlt">H</span>', 'Highlight ==text== (Ctrl+Shift+H)'],
    ['code', '&lt;/&gt;', 'Inline code'], '|', ['ul', icon('list'), 'Bullet list'], ['ol', '1.', 'Numbered list'], ['task', icon('check-c'), 'Task - [ ] (Ctrl+L)'],
    ['quote', '❝', 'Quote'], ['callout', icon('alert'), 'Callout > [!note]'], '|', ['wikilink', '[[ ]]', 'Link to a note'], ['link', icon('globe'), 'Web link (Ctrl+K)'],
    ['image', icon('download'), 'Add an image (or paste / drop one)'], ['table', icon('options'), 'Table'], ['codeblock', '```', 'Code block'], ['math', '∑', 'Math $…$ (LaTeX)'], ['hr', '—', 'Divider'],
  ];
  $('nt-tb').innerHTML = tb.map(b => b === '|' ? '<span class="sep"></span>' : `<button type="button" class="ibtn sm" data-op="${b[0]}" data-tip="${esc(b[2])}" aria-label="${esc(b[2])}">${b[1]}</button>`).join('');
  NT.ed = ntEditor($('nt-src'), $('nt-hl'), {
    onInput: () => NT.doc?.edit($('nt-src').value), onSave: () => ntSaveNow(), onToggle: () => ntSetMode(NT.mode === 'edit' ? 'read' : 'edit'),
  });
  $('nt-tb').addEventListener('mousedown', e => { if (e.target.closest('[data-op]')) e.preventDefault(); });   // keep the selection
  $('nt-tb').addEventListener('click', e => { const b = e.target.closest('[data-op]'); if (b && NT.doc) NT.ed.ops[b.dataset.op](); });
  $('nt-mode').addEventListener('click', e => { const b = e.target.closest('[data-m]'); if (b) ntSetMode(b.dataset.m); });
  $('nt-new').onclick = () => ntNew();
  $('nt-today').onclick = () => ntDaily();
  $('nt-rail-btn').onclick = () => { NT.rail = !NT.rail; store.set('ntRail', NT.rail ? '1' : '0'); ntRailShow(); };
  $('nt-more').onclick = e => ntMenu(e.currentTarget);
  $('nt-ask').onclick = () => ntAskClaude();
  $('nt-q').addEventListener('input', e => ntSearch(e.target.value));
  $('nt-q').addEventListener('keydown', e => {
    if (e.key === 'Escape') { e.stopPropagation(); e.target.value = ''; ntSearch(''); e.target.blur(); }
    if (e.key === 'Enter') { const first = $('nt-list').querySelector('[data-path]'); if (first) ntOpen(first.dataset.path); }
  });
  $('nt-list').addEventListener('click', ntListClick);
  $('nt-title').addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); e.target.blur(); }
    if (e.key === 'Escape') { e.stopPropagation(); e.target.value = NT.doc?.path.split('/').pop().replace(/\.md$/i, '') || ''; e.target.blur(); }
  });
  $('nt-title').addEventListener('blur', ntRenameFromTitle);
  $('nt-read').addEventListener('click', ntReadClick);
  $('nt-read').addEventListener('keydown', e => { if ((e.key === 'Enter' || e.key === ' ') && e.target.classList.contains('nt-co-t')) { e.preventDefault(); e.target.click(); } });
  $('nt-rail').addEventListener('click', ntRailClick);
  $('nt-cf').addEventListener('click', e => { const b = e.target.closest('[data-cf]'); if (b) ntResolveConflict(b.dataset.cf); });
  $('nts').addEventListener('keydown', e => {
    if (e.defaultPrevented) return;            // the editor already handled it
    const mod = e.ctrlKey || e.metaKey, k = e.key.toLowerCase();
    if (mod && k === 'e' && document.activeElement !== $('nt-src')) { e.preventDefault(); ntSetMode(NT.mode === 'edit' ? 'read' : 'edit'); }
    if (mod && k === 's') { e.preventDefault(); ntSaveNow(); }
    if (mod && (k === 'o' || (e.shiftKey && k === 'f'))) { e.preventDefault(); $('nt-q').focus(); $('nt-q').select(); }
  });
  document.addEventListener('visibilitychange', () => { if (document.hidden) { NT.doc?.save(); NT.scratch?.doc?.save(); } });
  ntRailShow();
}
function ntOnView(open) {
  NT.open = open;
  clearInterval(NT.pollT);
  if (!open) { NT.ed?.close(); NT.doc?.save(); return; }
  ntMount();
  ntRefreshTree().then(() => {
    if (!NT.doc) { const last = store.get('ntLast', ''); if (last && NT.tree.notes.some(n => n.path === last)) ntOpen(last); else ntEmpty(); }
  });
  ntLibs().catch(e => toast(e.message, 'bad'));
  NT.pollT = setInterval(() => { if (NT.open) { NT.doc?.poll(); ntRefreshTree(true); } }, 4000);
}
async function ntRefreshTree(quiet = false) {
  try {
    const t = await ntApi('tree');
    const sig = JSON.stringify([t.notes.map(n => n.path), t.files.length, t.folders]);
    const changed = sig !== NT.treeSig;
    NT.tree = t; NT.idx = ntIndex(t); NT.treeSig = sig;
    if (!t.ok) { $('nt-list').innerHTML = `<p class="note nt-pad">No Obsidian vault is connected. Start btcliq with <code>--vault ~/path/to/vault</code>, or set BTCLIQ_VAULT in .env.</p>`; return; }
    $('nt-vault').textContent = t.name;
    if (changed || !quiet) ntList();
  } catch (e) { if (!quiet) $('nt-list').innerHTML = `<p class="note nt-pad err">${esc(e.message)}</p>`; }
}
function ntList() {
  if (NT.q) return ntHits();
  const t = NT.tree; if (!t?.ok) return;
  const kids = new Map();
  const node = p => { if (!kids.has(p)) kids.set(p, { folders: [], notes: [] }); return kids.get(p); };
  node('');
  for (const f of t.folders) { const parent = f.includes('/') ? f.replace(/\/[^/]*$/, '') : ''; node(parent).folders.push(f); node(f); }
  for (const n of t.notes) node(n.path.includes('/') ? n.path.replace(/\/[^/]*$/, '') : '').notes.push(n);
  const cur = NT.doc?.path;
  const html = (dir, depth) => {
    const k = kids.get(dir); if (!k) return '';
    const pad = `style="--d:${depth}"`;
    return k.folders.map(f => {
      const shut = NT.folded.has(f), n = t.notes.filter(x => x.path.startsWith(f + '/')).length;
      return `<button type="button" class="nt-dir${shut ? ' shut' : ''}" data-dir="${esc(f)}" ${pad} aria-expanded="${!shut}">${icon('chev-down')}<span>${esc(f.split('/').pop())}</span><small>${n || ''}</small></button>`
        + (shut ? '' : html(f, depth + 1));
    }).join('') + k.notes.map(n => `<div class="nt-f${n.path === cur ? ' on' : ''}" data-path="${esc(n.path)}" ${pad} tabindex="0" role="button"><span class="nm">${esc(n.name)}</span>`
      + `<span class="acts"><button type="button" class="ibtn" data-act="rename" aria-label="Rename" data-tip="Rename">${icon('edit')}</button>`
      + `<button type="button" class="ibtn" data-act="trash" aria-label="Move to trash" data-tip="Move to the vault's .trash">${icon('trash')}</button></span></div>`).join('');
  };
  $('nt-list').innerHTML = html('', 0) || '<p class="note nt-pad">The vault is empty. Press New note.</p>';
  $('nt-count').textContent = `${t.notes.length} note${t.notes.length === 1 ? '' : 's'}`;
}
function ntSearch(q) {
  NT.q = q.trim();
  clearTimeout(NT.searchT);
  if (!NT.q) { NT.hits = null; return ntList(); }
  ntHits();
  NT.searchT = setTimeout(async () => {
    try { const d = await ntApi('search?q=' + encodeURIComponent(NT.q)); if (d.q.trim() === NT.q) { NT.hits = d.hits; ntHits(); } } catch {}
  }, 220);
}
function ntHits() {
  const q = NT.q.toLowerCase();
  const byName = (NT.tree?.notes || []).filter(n => n.path.toLowerCase().includes(q)).slice(0, 30);
  const names = new Set(byName.map(n => n.path));
  const text = (NT.hits || []).filter(h => !names.has(h.path));
  const row = (path, name, sub) => `<div class="nt-f hit${path === NT.doc?.path ? ' on' : ''}" data-path="${esc(path)}" tabindex="0" role="button"><span class="nm">${esc(name)}</span>`
    + `<small>${esc(path.includes('/') ? path.replace(/\/[^/]*$/, '') : '')}</small>${sub ? `<span class="hit-t">${sub}</span>` : ''}</div>`;
  const mark = s => esc(s).replace(new RegExp('(' + NT.q.split(/\s+/).filter(Boolean).map(w => esc(w).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')', 'gi'), '<mark>$1</mark>');
  $('nt-list').innerHTML = (byName.length ? `<div class="nt-sec">Names</div>` + byName.map(n => row(n.path, n.name, '')).join('') : '')
    + (text.length ? `<div class="nt-sec">In the text</div>` + text.map(h => row(h.path, h.name, mark(h.text))).join('') : '')
    + (!byName.length && !text.length ? `<p class="note nt-pad">${NT.hits ? 'Nothing found.' : 'Searching…'}</p>` : '');
}
async function ntListClick(e) {
  const dir = e.target.closest('[data-dir]');
  if (dir) { const f = dir.dataset.dir; NT.folded.has(f) ? NT.folded.delete(f) : NT.folded.add(f); store.set('ntFolded', JSON.stringify([...NT.folded])); return ntList(); }
  const row = e.target.closest('[data-path]'); if (!row) return;
  const act = e.target.closest('[data-act]')?.dataset.act;
  if (act === 'trash') return ntTrash(row.dataset.path);
  await ntOpen(row.dataset.path);
  if (act === 'rename') { $('nt-title').focus(); $('nt-title').select(); }
}
function ntEmpty() {
  NT.doc = null;
  $('nt-main').classList.add('empty');
  $('nt-title').value = ''; $('nt-path').textContent = ''; $('nt-state').textContent = '';
  $('nt-read').innerHTML = ''; NT.ed?.set('');
  $('nt-cf').hidden = true;
  ntList();
}
const ntLeave = async () => !NT.doc?.dirty || await NT.doc.save() || await ask({
  tone: 'danger', icon: 'alert', title: 'Leave without saving?', lede: `Your latest edits to <b>${esc(NT.doc.path)}</b> could not be saved${NT.doc.err ? ': ' + esc(NT.doc.err) : ''}.`,
  ok: 'Discard my edits', keep: 'Stay on this note' });
async function ntOpen(path, { heading = '', mode = null, create = '' } = {}) {
  if (NT.doc?.path === path) { if (mode) ntSetMode(mode); if (heading) ntJump(heading); return; }
  if (!await ntLeave()) return;
  const doc = new NtDoc(path, { onState: ntState, onReload: d => { if (d === NT.doc) ntShowDoc(true); }, onConflict: d => { if (d === NT.doc) ntConflictBar(); } });
  try { await doc.load({ create }); } catch (e) { toast(`Could not open ${path}: ${e.message}`, 'bad'); return; }
  NT.doc = doc;
  store.set('ntLast', path);
  $('nt-main').classList.remove('empty');
  if (mode) NT.mode = mode;
  ntShowDoc();
  ntList();
  ntBacklinks();
  if (heading) setTimeout(() => ntJump(heading), 60);
}
async function ntShowDoc(keepScroll = false) {
  const d = NT.doc; if (!d) return;
  const name = d.path.split('/').pop().replace(/\.md$/i, '');
  if (document.activeElement !== $('nt-title')) $('nt-title').value = name;
  $('nt-path').textContent = d.path.includes('/') ? d.path.replace(/\/[^/]*$/, '') + ' /' : '';
  const sc = $('nt-scroll').scrollTop, ta = $('nt-src'), sel = [ta.selectionStart, ta.selectionEnd];
  NT.ed.set(d.text);
  if (keepScroll) { ta.setSelectionRange(...sel); }
  $('nt-cf').hidden = !d.conflict;
  ntState(d);
  await ntSetMode(NT.mode, { keep: true });
  if (keepScroll) $('nt-scroll').scrollTop = sc; else $('nt-scroll').scrollTop = 0;
  ntOutline();
}
function ntState(d) {
  if (d === NT.scratch?.doc) return ntScratchState();
  if (d !== NT.doc) return;
  const el = $('nt-state');
  el.textContent = d.state === 'error' ? `Not saved: ${d.err}` : NT_STATE[d.state] || '';
  el.dataset.s = d.state;
  if (d.state === 'conflict') ntConflictBar();
  if (d.state === 'saved' || d.state === 'edited') { clearTimeout(NT.outT); NT.outT = setTimeout(ntOutline, 400); }
}
async function ntSetMode(m, { keep = false } = {}) {
  const was = NT.mode;
  NT.mode = m === 'read' ? 'read' : 'edit';
  store.set('ntMode', NT.mode);
  $('nt-mode').querySelectorAll('[data-m]').forEach(b => { b.setAttribute('aria-pressed', String(b.dataset.m === NT.mode)); b.classList.toggle('on', b.dataset.m === NT.mode); });
  $('nt-main').dataset.mode = NT.mode;
  $('nt-tb').hidden = NT.mode !== 'edit';
  if (!NT.doc) return;
  if (NT.mode === 'read') {
    try { await ntRender($('nt-read'), NT.doc.text, { path: NT.doc.path }); }
    catch (e) { $('nt-read').innerHTML = `<p class="err">Could not format this note: ${esc(e.message)}. Edit mode still works.</p>`; }
    if (!keep && was === 'edit') $('nt-read').focus({ preventScroll: true });
  } else if (!keep || was !== 'edit') { NT.ed.paint(); if (!keep) $('nt-src').focus({ preventScroll: true }); }
}
function ntSaveNow() {
  if (!NT.doc) return;
  if (!NT.doc.dirty && NT.doc.exists) { $('nt-state').textContent = 'Saved'; return; }
  NT.doc.dirty = true; NT.doc.save();
}

// ---------- new, daily, rename, trash ----------
function ntFree(dir, stem) {
  const taken = new Set((NT.tree?.notes || []).map(n => n.path.toLowerCase()));
  let p = `${dir}/${stem}.md`, i = 1;
  while (taken.has(p.toLowerCase())) p = `${dir}/${stem} ${i++}.md`;
  return p;
}
async function ntNew(name = '') {
  const dir = NT.tree?.notes_dir || 'Notes';
  const path = name ? (name.includes('/') ? name : `${dir}/${name}`).replace(/(\.md)?$/i, '.md') : ntFree(dir, 'Untitled');
  try {
    await ntApi('note', { method: 'PUT', json: { path, text: '', create: true } });
  } catch (e) { if (!/already exists/.test(e.message)) return toast(`Could not create the note: ${e.message}`, 'bad'); }
  await ntRefreshTree();
  await ntOpen(path, { mode: 'edit' });
  if (!name) { $('nt-title').focus(); $('nt-title').select(); }
}
async function ntDaily() {
  const d = new Date(), day = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const path = `${NT.tree?.notes_dir || 'Notes'}/Daily/${day}.md`;
  if (!NT.tree?.notes.some(n => n.path === path)) {
    const text = `---\ntags: [daily]\n---\n# ${d.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' })}\n\n## Plan\n- [ ] \n\n## Trades\n\n## Notes\n`;
    try { await ntApi('note', { method: 'PUT', json: { path, text, create: true } }); } catch (e) { if (!/already exists/.test(e.message)) return toast(e.message, 'bad'); }
    await ntRefreshTree();
  }
  ntOpen(path, { mode: 'edit' });
}
async function ntRenameFromTitle() {
  const d = NT.doc; if (!d) return;
  const name = $('nt-title').value.trim().replace(/\.md$/i, '');
  const cur = d.path.split('/').pop().replace(/\.md$/i, '');
  if (!name || name === cur) { $('nt-title').value = cur; return; }
  const to = d.path.replace(/[^/]*$/, '') + name + '.md';
  try {
    await d.save();
    if (!d.exists) { d.path = to; d.dirty = true; await d.save(); }
    else { const r = await ntApi('rename', { method: 'POST', json: { from: d.path, to } }); d.path = r.path; }
    store.set('ntLast', d.path);
    await ntRefreshTree(); ntShowDoc(true); ntBacklinks();
    toast(`Renamed to ${name}. Links that point to "${cur}" keep the old name; update them if you need to.`, 'ok');
  } catch (e) { $('nt-title').value = cur; toast(`Not renamed: ${e.message}`, 'bad'); }
}
async function ntTrash(path) {
  const name = path.split('/').pop().replace(/\.md$/i, '');
  const ok = await ask({
    tone: 'danger', icon: 'trash', title: `Move "${name}" to the trash?`,
    lede: `It goes to the <b>.trash</b> folder in your vault, the same place Obsidian puts deleted notes. You can move it back from there.`,
    items: [{ t: path, v: '' }], ok: 'Move to trash', keep: 'Keep the note',
    run: async () => { await ntApi('note?' + ntQ(path), { method: 'DELETE' }); },
  });
  if (!ok) return;
  if (NT.doc?.path === path) { NT.doc.dirty = false; ntEmpty(); }
  await ntRefreshTree();
}
function ntMenu(btn) {
  const d = NT.doc; if (!d) return;
  const m = document.createElement('div'); m.className = 'pop nt-menu'; m.setAttribute('role', 'menu');
  const name = d.path.split('/').pop().replace(/\.md$/i, '');
  m.innerHTML = [['rename', 'edit', 'Rename'], ['link', 'copy', 'Copy [[link]]'], ['path', 'copy', 'Copy file path'], ['obsidian', 'external', 'Open in Obsidian'], ['trash', 'trash', 'Move to trash']]
    .map(([k, ic, t]) => `<button type="button" class="mi${k === 'trash' ? ' bad' : ''}" data-k="${k}" role="menuitem">${icon(ic)}${esc(t)}</button>`).join('');
  document.body.append(m);
  const r = btn.getBoundingClientRect();
  m.style.left = Math.max(8, r.right - m.offsetWidth) + 'px'; m.style.top = r.bottom + 4 + 'px';
  const off = e => { if (!m.contains(e.target)) done(); };
  const done = () => { m.remove(); removeEventListener('pointerdown', off, true); removeEventListener('keydown', esc_, true); };
  const esc_ = e => { if (e.key === 'Escape') { e.stopPropagation(); done(); btn.focus(); } };
  setTimeout(() => { addEventListener('pointerdown', off, true); addEventListener('keydown', esc_, true); });
  m.querySelector('button').focus();
  m.onclick = e => {
    const k = e.target.closest('[data-k]')?.dataset.k; if (!k) return;
    done();
    if (k === 'rename') { $('nt-title').focus(); $('nt-title').select(); }
    if (k === 'link') navigator.clipboard?.writeText(`[[${name}]]`).then(() => toast('Link copied', 'ok'));
    if (k === 'path') navigator.clipboard?.writeText(`${NT.tree?.name || ''}/${d.path}`).then(() => toast('Path copied', 'ok'));
    if (k === 'obsidian') location.href = `obsidian://open?vault=${encodeURIComponent(NT.tree?.name || '')}&file=${encodeURIComponent(d.path.replace(/\.md$/i, ''))}`;
    if (k === 'trash') ntTrash(d.path);
  };
}
function ntAskClaude() {
  const d = NT.doc; if (!d) return;
  const name = d.path.split('/').pop().replace(/\.md$/i, '');
  const inNotes = d.path.startsWith((NT.tree?.notes_dir || 'Notes') + '/');
  setView('claude');
  setTimeout(() => {
    const inp = $('cld-input'); if (!inp) return;
    inp.value = inNotes ? `Read my note "${name}" and ` : `About my vault note "${name}" (it's outside the Notes folder, so here is the text):\n\n${d.text.slice(0, 6000)}\n\n`;
    inp.dispatchEvent(new Event('input')); inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length);
  }, 80);
}

// ---------- conflicts ----------
function ntConflictBar() {
  const d = NT.doc; if (!d?.conflict) { $('nt-cf').hidden = true; return; }
  $('nt-cf').hidden = false;
  $('nt-cf-t').textContent = d.conflict.hash == null
    ? 'This note was deleted or renamed outside btcliq after you opened it. Your edits are not saved yet.'
    : 'This note changed outside btcliq (Obsidian?) after you opened it. Your edits are not saved yet.';
}
async function ntResolveConflict(k) {
  const d = NT.doc; if (!d?.conflict) return;
  const theirs = d.conflict.text || '', mine = d.text;
  // Where the two versions first differ, so the choice is made on content, not on line counts.
  const A = theirs.split('\n'), B = mine.split('\n');
  let at = 0; while (at < A.length && at < B.length && A[at] === B[at]) at++;
  const cut = s => s == null ? '(nothing)' : (s.length > 70 ? s.slice(0, 70) + '…' : s || '(blank line)');
  const items = d.conflict.hash == null ? [{ t: 'On disk', v: 'deleted or renamed' }, { t: 'Yours', v: `${B.length} lines` }]
    : [{ t: `On disk, line ${at + 1}`, v: cut(A[at]) }, { t: `Yours, line ${at + 1}`, v: cut(B[at]) }];
  if (k === 'mine') {
    const ok = await ask({ tone: 'danger', icon: 'alert', title: 'Overwrite the file with your version?',
      lede: `The version saved on disk will be replaced by what you see in btcliq. The first difference:`, items,
      ok: 'Overwrite with mine', keep: 'Cancel' });
    if (ok) { d.dirty = true; await d.save({ force: true }); ntConflictBar(); }
  } else if (k === 'theirs') {
    const ok = await ask({ tone: 'danger', icon: 'alert', title: 'Load the version on disk?',
      lede: 'Your unsaved edits in btcliq will be thrown away. Copy mine first keeps them on your clipboard. The first difference:', items,
      ok: 'Discard my edits', keep: 'Cancel' });
    if (ok) d.takeTheirs();
  } else if (k === 'copy') {
    navigator.clipboard?.writeText(mine).then(() => toast('Your version is on the clipboard', 'ok'));
  }
}

// ---------- reading view clicks: links, tags, tasks, callouts ----------
async function ntOpenLink(target, heading) {
  if (!target) return ntJump(heading);
  const path = ntResolve(target);
  if (path && !path.endsWith('.md')) { try { window.open(await ntBlob(path), '_blank', 'noopener'); } catch {} return; }
  if (path) return ntOpen(path, { heading });
  const ok = await ask({ tone: 'accent', icon: 'plus', title: `Create "${target.split('/').pop()}"?`,
    lede: `No note with that name exists yet. It will be created in <b>${esc(target.includes('/') ? target.replace(/\/[^/]*$/, '') : NT.tree?.notes_dir || 'Notes')}</b>.`,
    ok: 'Create note', keep: 'Cancel' });
  if (ok) ntNew(target);
}
function ntReadClick(e) {
  const a = e.target.closest('a');
  const box = e.currentTarget;
  if (a?.classList.contains('nt-wl')) { e.preventDefault(); return ntOpenLink(a.dataset.wl, a.dataset.h); }
  if (a?.classList.contains('nt-tag')) { e.preventDefault(); $('nt-q').value = '#' + a.dataset.tag; return ntSearch('#' + a.dataset.tag); }
  if (a && a.getAttribute('href')?.startsWith('#')) {
    e.preventDefault();
    const id = decodeURIComponent(a.getAttribute('href').slice(1));
    const t = box.querySelector(`[id="${CSS.escape(id)}"]`) || box.querySelector(`[id="user-content-${CSS.escape(id)}"]`)   // footnotes
      || box.querySelector(`[id="nt-h-${CSS.escape(ntSlug(id))}"]`);
    t?.scrollIntoView({ block: 'start', behavior: 'smooth' });
    return;
  }
  if (a && !/^[a-z][a-z0-9+.-]*:/i.test(a.getAttribute('href') || '')) {     // [text](Other note.md)
    const p = ntResolve(a.getAttribute('href'));
    if (p) { e.preventDefault(); return ntOpen(p); }
  }
  const t = e.target.closest('.nt-co.fold > .nt-co-t');
  if (t) { t.parentElement.classList.toggle('shut'); return; }
  const cb = e.target.closest('input.nt-task');
  if (cb && NT.doc) {
    const off = +(box.dataset.offset || 0), line = +cb.dataset.line + off;
    if (cb.closest('.nt-transclude') || cb.dataset.line === '-1') { e.preventDefault(); return; }
    const L = NT.doc.text.split('\n');
    if (!L[line]) return;
    L[line] = L[line].replace(/^(\s*(?:>\s*)*(?:[-*+]|\d+[.)])\s+\[)(.)(\])/, (m, a, c, b) => a + (c === ' ' ? 'x' : ' ') + b);
    NT.doc.edit(L.join('\n')); NT.ed.set(NT.doc.text);
    cb.closest('li')?.classList.toggle('done', cb.checked);
  }
}

// ---------- right rail: outline and backlinks ----------
function ntRailShow() {
  $('nt-rail').hidden = !NT.rail;
  $('nt-rail-btn').setAttribute('aria-pressed', String(NT.rail));
}
function ntOutline() {
  const d = NT.doc;
  if (!d) { $('nt-outline').innerHTML = ''; return; }
  const { body, offset } = ntSplit(d.text);
  let fence = false;
  const heads = [];
  body.split('\n').forEach((l, i) => {
    if (/^\s*(```|~~~)/.test(l)) fence = !fence;
    const m = !fence && /^(#{1,6})\s+(.+)$/.exec(l);
    if (m) heads.push({ lvl: m[1].length, t: m[2].replace(/[*_=`~]|\[\[|\]\]/g, '').trim(), line: i + offset });
  });
  const top = Math.min(...heads.map(h => h.lvl), 6);
  $('nt-outline').innerHTML = heads.length ? heads.map(h => `<button type="button" class="nt-o" style="--d:${h.lvl - top}" data-line="${h.line}" data-t="${esc(h.t)}">${esc(h.t)}</button>`).join('')
    : '<p class="note">No headings. Start a line with # to add one.</p>';
}
async function ntBacklinks() {
  const d = NT.doc, el = $('nt-back');
  if (!d) { el.innerHTML = ''; return; }
  el.innerHTML = '<p class="note">Looking…</p>';
  try {
    const r = await ntApi('backlinks?' + ntQ(d.path));
    if (NT.doc !== d) return;
    $('nt-back-n').textContent = r.links.length || '';
    el.innerHTML = r.links.length ? r.links.map(l => `<button type="button" class="nt-bl" data-path="${esc(l.path)}"><b>${esc(l.name)}</b><span>${esc(l.text)}</span></button>`).join('')
      : '<p class="note">No other note links here yet.</p>';
  } catch (e) { el.innerHTML = `<p class="note err">${esc(e.message)}</p>`; }
}
function ntRailClick(e) {
  const o = e.target.closest('.nt-o');
  if (o) return ntJump(o.dataset.t, +o.dataset.line);
  const b = e.target.closest('.nt-bl');
  if (b) ntOpen(b.dataset.path);
}
function ntJump(heading, line = null) {
  if (!NT.doc || !heading) return;
  if (NT.mode === 'read') {
    const h = [...$('nt-read').querySelectorAll('h1, h2, h3, h4, h5, h6')].find(x => x.textContent.trim().toLowerCase() === heading.replace(/^\^/, '').toLowerCase())
      || $('nt-read').querySelector(`#nt-h-${CSS.escape(ntSlug(heading))}`);
    h?.scrollIntoView({ block: 'start', behavior: 'smooth' });
    return;
  }
  const L = NT.doc.text.split('\n');
  if (line == null) line = L.findIndex(l => /^#{1,6}\s/.test(l) && l.replace(/^#+\s+/, '').trim().toLowerCase() === heading.toLowerCase());
  if (line < 0) return;
  const ta = $('nt-src'), pos = L.slice(0, line).join('\n').length + (line ? 1 : 0);
  ta.focus({ preventScroll: true }); ta.setSelectionRange(pos, pos);
  $('nt-hl').innerHTML = esc(ta.value.slice(0, pos)) + '<span class="nt-probe">​</span>';
  const pr = $('nt-hl').querySelector('.nt-probe').offsetTop;
  NT.ed.paint();
  $('nt-scroll').scrollTo({ top: Math.max(0, pr - 40), behavior: 'smooth' });
}

// ---------- the Scratchpad panel ----------
function ntScratchInit() {
  const ta = $('scr-src'); if (!ta) return;
  NT.scratch = { mode: 'edit', doc: null, ready: false };
  NT.scratch.ed = ntEditor(ta, $('scr-hl'), { onInput: () => NT.scratch.doc?.edit(ta.value), onSave: () => { if (NT.scratch.doc) { NT.scratch.doc.dirty = true; NT.scratch.doc.save(); } },
    onToggle: () => ntScratchMode(NT.scratch.mode === 'edit' ? 'read' : 'edit') });
  $('scr-mode').addEventListener('click', e => { const b = e.target.closest('[data-m]'); if (b) ntScratchMode(b.dataset.m); });
  $('scr-open').onclick = () => { const p = NT.scratch.doc?.path || 'Notes/Scratch.md'; setView('notes'); setTimeout(() => ntOpen(p), 60); };
  $('scr-read').addEventListener('click', e => {
    const a = e.target.closest('a.nt-wl'); if (a) { e.preventDefault(); setView('notes'); setTimeout(() => ntOpenLink(a.dataset.wl, a.dataset.h), 60); return; }
    const cb = e.target.closest('input.nt-task'); const d = NT.scratch.doc;
    if (cb && d) {
      const line = +cb.dataset.line + +($('scr-read').dataset.offset || 0), L = d.text.split('\n');
      if (!L[line]) return;
      L[line] = L[line].replace(/^(\s*(?:[-*+]|\d+[.)])\s+\[)(.)(\])/, (m, a, c, b) => a + (c === ' ' ? 'x' : ' ') + b);
      d.edit(L.join('\n')); NT.scratch.ed.set(d.text);
    }
  });
  $('scr-cf').addEventListener('click', async e => {
    const k = e.target.closest('[data-cf]')?.dataset.cf, d = NT.scratch.doc; if (!k || !d) return;
    if (k === 'theirs') { if (await ask({ tone: 'danger', icon: 'alert', title: 'Load the Scratchpad saved on disk?', lede: 'Your unsaved Scratchpad edits will be thrown away.', ok: 'Discard my edits', keep: 'Cancel' })) d.takeTheirs(); }
    else if (k === 'mine') { if (await ask({ tone: 'danger', icon: 'alert', title: 'Overwrite the Scratchpad on disk?', lede: 'The version saved on disk will be replaced by what you see here.', ok: 'Overwrite with mine', keep: 'Cancel' })) { d.dirty = true; await d.save({ force: true }); ntScratchState(); } }
  });
  setInterval(() => { if (NT.scratch.ready && panelShown('scratch')) NT.scratch.doc.poll(); }, 4000);
  ntScratchLoad();
}
async function ntScratchLoad() {
  const s = NT.scratch;
  try {
    const t = NT.tree || await ntApi('tree');
    if (!NT.tree && t.ok) { NT.tree = t; NT.idx = ntIndex(t); }
    if (!t.ok) { $('scr-state').textContent = 'No vault connected'; $('scr-src').disabled = true; return; }
    s.doc = new NtDoc(t.scratch, { onState: ntScratchState, onReload: () => { s.ed.set(s.doc.text); if (s.mode === 'read') ntScratchMode('read'); }, onConflict: ntScratchState });
    await s.doc.load({ create: '' });
    s.ed.set(s.doc.text); s.ready = true; ntScratchState();
  } catch (e) { $('scr-state').textContent = e.message; }
}
function ntScratchState() {
  const d = NT.scratch?.doc; if (!d) return;
  $('scr-state').textContent = d.state === 'error' ? 'Not saved' : d.state === 'saved' ? '' : NT_STATE[d.state] || '';
  $('scr-state').dataset.s = d.state;
  $('scr-cf').hidden = !d.conflict;
}
async function ntScratchMode(m) {
  const s = NT.scratch; s.mode = m === 'read' ? 'read' : 'edit';
  $('scr-mode').querySelectorAll('[data-m]').forEach(b => { b.setAttribute('aria-pressed', String(b.dataset.m === s.mode)); b.classList.toggle('on', b.dataset.m === s.mode); });
  $('scr-body').dataset.mode = s.mode;
  if (s.mode === 'read') { try { await ntRender($('scr-read'), s.doc?.text || '', { path: s.doc?.path }); } catch (e) { $('scr-read').textContent = e.message; } }
  else $('scr-src').focus({ preventScroll: true });
}

// Closing or reloading the tab with edits not yet saved: send them with keepalive, so the request
// outlives the page. Only a note too big for keepalive (64 KB) falls back to the browser's own prompt.
addEventListener('beforeunload', e => {
  let ask_ = false;
  for (const d of [NT.doc, NT.scratch?.doc]) {
    if (!d?.dirty || d.conflict || !TR.token) continue;
    const body = JSON.stringify({ path: d.path, text: d.text, base: d.hash, create: !d.exists });
    if (body.length > 60000) { ask_ = true; continue; }
    fetch('/api/desk/vault/note', { method: 'PUT', keepalive: true, body, headers: { 'X-Btcliq-Trade': TR.token, 'Content-Type': 'application/json' } });
  }
  if (ask_) e.preventDefault();
});

ntScratchInit();
if (typeof VIEW !== 'undefined' && VIEW === 'notes') ntOnView(true);
