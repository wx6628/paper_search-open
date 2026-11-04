// Minimal, safe Markdown renderer for AI analysis notes (everything is HTML-escaped first).
// Supports: headings, paragraphs, bold/italic/strike, inline code, fenced code, blockquotes,
// ordered/unordered (nested) lists, tables, hr, links, images, $inline$ / $$block$$ math.
// Math is rendered with KaTeX only if web/vendor/katex is present; otherwise shown as styled LaTeX.
(function () {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

  function mathHTML(tex, display) {
    if (window.katex) {
      try { return window.katex.renderToString(tex, { displayMode: display, throwOnError: false }); } catch (e) { /* fall through */ }
    }
    return display ? `<div class="math-block">${esc(tex)}</div>` : `<code class="math">${esc(tex)}</code>`;
  }

  function inline(text, ctx) {
    const stash = [];
    const keep = (html) => { stash.push(html); return `\u0000${stash.length - 1}\u0000`; };
    text = text.replace(/`([^`]+)`/g, (_, c) => keep(`<code>${esc(c)}</code>`));
    text = text.replace(/\$\$([^$]+)\$\$/g, (_, m) => keep(mathHTML(m, true)));
    text = text.replace(/(^|[^\\$])\$([^$\n]+?)\$/g, (_, pre, m) => pre + keep(mathHTML(m, false)));
    text = text.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g, (_, alt, src) => keep(
      `<img alt="${esc(alt)}" src="${esc(ctx.resolve(src))}" loading="lazy">`));
    text = text.replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_, t, label) => keep(`<span class="wikilink">${esc(label || t)}</span>`));
    text = text.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g, (_, label, href) => {
      const safe = /^(https?:|mailto:|#)/i.test(href) ? href : "#";
      return keep(`<a href="${esc(safe)}" target="_blank" rel="noopener">${esc(label)}</a>`);
    });
    text = text.replace(/(^|\s)(https?:\/\/[^\s<]+)/g, (_, pre, url) => pre + keep(`<a href="${esc(url)}" target="_blank" rel="noopener">${esc(url)}</a>`));
    text = esc(text);
    text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/__([^_]+)__/g, "<strong>$1</strong>");
    text = text.replace(/(^|[^*])\*([^*\s][^*]*?)\*/g, "$1<em>$2</em>");
    text = text.replace(/~~([^~]+)~~/g, "<del>$1</del>");
    return text.replace(/\u0000(\d+)\u0000/g, (_, i) => stash[+i]);
  }

  function render(src, ctx) {
    ctx = ctx || { resolve: (s) => s };
    src = src.replace(/\r\n?/g, "\n");
    // strip YAML front matter, but show it as a compact table
    let front = "";
    const fm = src.match(/^---\n([\s\S]*?)\n---\n/);
    if (fm) {
      const rows = fm[1].split("\n").filter((l) => /^[\w-]+\s*:/.test(l)).map((l) => {
        const i = l.indexOf(":");
        return `<tr><th>${esc(l.slice(0, i))}</th><td>${inline(l.slice(i + 1).trim(), ctx)}</td></tr>`;
      });
      front = rows.length ? `<table class="frontmatter">${rows.join("")}</table>` : "";
      src = src.slice(fm[0].length);
    }
    const lines = src.split("\n");
    const out = [];
    let i = 0;
    const isBlank = (l) => !l || !l.trim();
    while (i < lines.length) {
      const line = lines[i];
      if (isBlank(line)) { i++; continue; }
      let m;
      if ((m = line.match(/^(\s*)(```|~~~)\s*([\w+-]*)/))) {               // fenced code
        const fence = m[2], lang = m[3]; const buf = []; i++;
        while (i < lines.length && !lines[i].trim().startsWith(fence)) buf.push(lines[i++]);
        i++;
        out.push(lang === "math" ? mathHTML(buf.join("\n"), true) : `<pre><code class="lang-${esc(lang)}">${esc(buf.join("\n"))}</code></pre>`);
        continue;
      }
      if (line.trim().startsWith("$$")) {                                    // block math
        const buf = [line.trim().slice(2)];
        if (!(line.trim().length > 2 && line.trim().endsWith("$$"))) {
          i++;
          while (i < lines.length && !lines[i].includes("$$")) buf.push(lines[i++]);
          if (i < lines.length) buf.push(lines[i].replace("$$", ""));
        } else buf[0] = buf[0].slice(0, -2);
        i++;
        out.push(mathHTML(buf.join("\n").trim(), true));
        continue;
      }
      if ((m = line.match(/^(#{1,6})\s+(.*)$/))) {
        out.push(`<h${m[1].length}>${inline(m[2], ctx)}</h${m[1].length}>`); i++; continue;
      }
      if (/^\s*([-*_])\s*\1\s*\1[\s\-*_]*$/.test(line)) { out.push("<hr>"); i++; continue; }
      if (/^\s*>/.test(line)) {
        const buf = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ""));
        const callout = buf[0] && buf[0].match(/^\[!(\w+)\][+-]?\s*(.*)$/);
        if (callout) { buf[0] = callout[2] ? `**${callout[2]}**` : ""; }
        out.push(`<blockquote${callout ? ` class="callout callout-${esc(callout[1].toLowerCase())}"` : ""}>${render(buf.join("\n"), ctx)}</blockquote>`);
        continue;
      }
      if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
        const row = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        const head = row(line); i += 2;
        const body = [];
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) body.push(row(lines[i++]));
        out.push(`<table><thead><tr>${head.map((c) => `<th>${inline(c, ctx)}</th>`).join("")}</tr></thead><tbody>${
          body.map((r) => `<tr>${r.map((c) => `<td>${inline(c, ctx)}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
        continue;
      }
      if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) {                             // lists (nested by indent)
        const items = [];
        while (i < lines.length && (/^\s*([-*+]|\d+[.)])\s+/.test(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && items.length))) {
          const l = lines[i];
          const mm = l.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
          if (mm) items.push({ indent: mm[1].replace(/\t/g, "    ").length, ordered: /\d/.test(mm[2]), text: mm[3] });
          else items[items.length - 1].text += " " + l.trim();
          i++;
        }
        let k = 0;
        while (k < items.length) { const r = renderList(items, k, ctx); out.push(r.html); k = r.next; }
        continue;
      }
      const buf = [];                                                        // paragraph
      while (i < lines.length && !isBlank(lines[i]) && !/^(#{1,6}\s|\s*>|\s*([-*+]|\d+[.)])\s+|\s*```|\s*\$\$)/.test(lines[i])) buf.push(lines[i++]);
      if (!buf.length) { buf.push(lines[i++]); }
      out.push(`<p>${inline(buf.join("\n"), ctx).replace(/\n/g, "<br>")}</p>`);
    }
    return front + out.join("\n");
  }

  function renderList(items, start, ctx) {
    const base = items[start].indent, ordered = items[start].ordered;
    let html = ordered ? "<ol>" : "<ul>", i = start;
    while (i < items.length && items[i].indent >= base && !(items[i].indent === base && items[i].ordered !== ordered)) {
      if (items[i].indent > base) {
        const sub = renderList(items, i, ctx);
        html = html.replace(/<\/li>$/, sub.html + "</li>");
        i = sub.next; continue;
      }
      let t = items[i].text, cls = "";
      const task = t.match(/^\[( |x|X)\]\s+(.*)$/);
      if (task) { cls = ` class="task"`; t = (task[1] === " " ? "☐ " : "☑ ") + task[2]; }
      html += `<li${cls}>${inline(t, ctx)}</li>`;
      i++;
    }
    return { html: html + (ordered ? "</ol>" : "</ul>"), next: i };
  }

  window.MD = { render, esc };
})();
