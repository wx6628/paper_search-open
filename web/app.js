/* Paper Library UI — vanilla JS, talks to ps/server.py */
"use strict";
const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => MD.esc(String(s ?? ""));

async function api(method, url, body, raw) {
  const opt = { method, headers: { "X-PS": "1" } };
  if (body !== undefined) {
    if (raw) { opt.body = body; opt.headers["Content-Type"] = "application/pdf"; }
    else { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
  }
  const r = await fetch(url, opt);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { toast("出错：" + (data.error || r.status)); throw new Error(data.error || r.status); }
  return data;
}
function toast(msg, ms = 2600) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), ms);
}
const qs = (o) => Object.entries(o).filter(([, v]) => v !== "" && v != null && v !== false).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");

const STATUS = { unread: "待读", reading: "在读", read: "已读", excluded: "已排除" };
const LEVEL = { 0: "L0 目录", 1: "L1 摘要", 2: "L2 全文", 3: "L3 研读" };
const PUBST = { accepted: "已录用", under_review: "审稿中", claimed: "arXiv 声明录用", preprint: "预印本" };
const ABS_SRC = { pdf: "本地 PDF 全文提取", manual: "手动编辑" };
const JOB_KEYS = { papers: "处理", abstract_added: "新增摘要", failed: "失败", skipped: "跳过", downloaded: "已下载",
  no_oa_source: "无开放获取来源", abstract_from_pdf: "从 PDF 提取摘要", pdf: "其中来自 PDF" };
const REL_TYPES = ["改进自", "对比了", "基于/使用", "同一数据集", "观点相反", "综述涵盖", "相关"];
const COLORS = ["#2f6feb", "#b42318", "#027a48", "#b54708", "#7a3fd1", "#0e7490", "#be185d", "#4b5563"];

const S = {
  view: "library", filter: { group: "", status: "", starred: "", tier: "", level: "", q: "", sort: "priority", search: "" },
  papers: [], groups: [], selected: new Set(), current: null, stats: {}, allTitles: [], collapsed: new Set(),
  dblp: { q: "", tier: "", year: "", results: [], total: 0, sel: new Set() },
  venueOff: new Set(),   // venues hidden by the Excel-style filter on the 出处 column (client-side)
};

/* Replace #main, keeping the list's scroll position while the same view (key) is re-rendered —
   e.g. after opening a paper, starring, selecting. A different key (new filter/sort/view) starts at the top. */
function setMain(html, key) {
  const old = $("#main .list"), keep = old && key != null && setMain.key === key ? old.scrollTop : 0;
  setMain.key = key;
  $("#main").innerHTML = html;
  const list = $("#main .list"); if (list && keep) list.scrollTop = keep;
}

/* ------------------------------------------------------------------ two-level groups (S.groups = tree from /api/groups) */
const G = {
  flat() {
    const out = [];
    for (const t of S.groups) {
      out.push({ ...t, isChild: false, path: t.name });
      for (const c of t.children) out.push({ ...c, isChild: true, parentName: t.name, path: `${t.name}/${c.name}` });
    }
    return out;
  },
  find(id) { return G.flat().find((g) => g.id === +id); },
  top(id) { const g = G.find(id); return g ? S.groups.find((t) => t.id === (g.isChild ? g.parent_id : g.id)) : null; },
  path(id) { const g = G.find(id); return g ? g.path : ""; },
  // <option> list: big groups, each followed by its sub-groups (indented)
  options(disabledIds = []) {
    return S.groups.map((t) => `<option value="${t.id}" ${disabledIds.includes(t.id) ? "disabled" : ""}>${esc(t.name)}</option>` +
      t.children.map((c) => `<option value="${c.id}" ${disabledIds.includes(c.id) ? "disabled" : ""}>　└ ${esc(c.name)}</option>`).join("")).join("");
  },
};

/* ------------------------------------------------------------------ sidebar */
async function loadSide() {
  [S.groups, S.stats] = await Promise.all([api("GET", "/api/groups"), api("GET", "/api/stats")]);
  renderSide();
}
function renderSide() {
  const f = S.filter, isLib = S.view === "library";
  const item = (label, active, onclick, count, extra = "") =>
    `<div class="side-item ${active ? "active" : ""}" onclick="${onclick}">${extra}<span>${label}</span>${count != null ? `<span class="count">${count}</span>` : ""}</div>`;
  const noFilter = !f.group && !f.status && !f.starred && !f.search;
  let h = `<h1>📚 论文库</h1><div class="side-sec"><div class="sec-title">文献库</div>`;
  h += item("全部论文", isLib && noFilter, "UI.setFilter({group:'',status:'',starred:'',search:''})", S.stats.papers);
  h += item("★ 星标", isLib && f.starred, "UI.setFilter({group:'',status:'',starred:'1',search:''})", S.stats.starred);
  for (const [k, v] of Object.entries(STATUS))
    h += item(v, isLib && f.status === k && !f.group, `UI.setFilter({group:'',status:'${k}',starred:'',search:''})`, S.stats.by_status?.[k]);
  h += `</div><div class="side-sec"><div class="sec-title"><span>分组</span><button class="btn small ghost" onclick="UI.newGroup()">＋ 新建大组</button></div>`;
  const sel = (id) => isLib && +f.group === id;
  for (const t of S.groups) {
    const open = !S.collapsed.has(t.id), hasKids = t.children.length > 0;
    h += `<div class="side-item ${sel(t.id) && !f.unassigned ? "active" : ""}" onclick="UI.setFilter({group:${t.id},unassigned:'',status:'',starred:'',search:''})">
      <span class="caret" onclick="event.stopPropagation();UI.toggleCollapse(${t.id})">${hasKids ? (open ? "▾" : "▸") : "·"}</span>
      <span class="dot" style="background:${esc(t.color || "#9aa4b2")}"></span><span class="gname" title="${esc(t.description || "")}">${esc(t.name)}</span>
      <span class="count">${t.n}</span><span class="more" title="分组操作" onclick="event.stopPropagation();UI.groupMenu(${t.id})">⋯</span></div>`;
    if (open) {
      for (const c of t.children)
        h += `<div class="side-item sub ${sel(c.id) ? "active" : ""}" onclick="UI.setFilter({group:${c.id},unassigned:'',status:'',starred:'',search:''})">
          <span class="gname" title="${esc(c.description || "")}">└ ${esc(c.name)}</span><span class="count">${c.n}</span>
          <span class="more" title="小组操作" onclick="event.stopPropagation();UI.groupMenu(${c.id})">⋯</span></div>`;
      if (hasKids && t.unassigned)
        h += `<div class="side-item sub muted ${sel(t.id) && f.unassigned ? "active" : ""}" onclick="UI.setFilter({group:${t.id},unassigned:'1',status:'',starred:'',search:''})">
          <span class="gname">└ 未分小组</span><span class="count">${t.unassigned}</span></div>`;
      const inThis = isLib && f.group && G.top(f.group)?.id === t.id;   // only under the big group being viewed
      if (inThis) h += `<div class="side-item sub add" onclick="UI.newGroup(${t.id})"><span class="gname">＋ 新建小组</span></div>`;
    }
  }
  if (!S.groups.length) h += `<div class="small" style="padding:4px 8px">还没有分组。点“＋ 新建大组”，或选中论文后“加入分组”。</div>`;
  h += `</div><div class="side-sec"><div class="sec-title">工具</div>`;
  h += item("🔎 dblp 目录检索", S.view === "dblp", "UI.go('dblp')");
  h += item("🕸 关系图", S.view === "graph", "UI.go('graph')");
  h += item("🗂 检索记录", S.view === "searches", "UI.go('searches')");
  h += item("🤖 AI 任务队列", S.view === "queue", "UI.go('queue')", S.stats.pending_queue || null);
  h += item("📖 使用指南", false, "UI.viewGuide()");
  h += `</div><div class="side-stats">补全等级：${[0, 1, 2, 3].map((i) => `<span class="lv lv${i}">L${i}</span> ${S.stats.by_level?.["L" + i] ?? 0}`).join("　")}</div>`;
  $("#sidebar").innerHTML = h;
}

/* ------------------------------------------------------------------ venue filter (Excel-style, client-side on the loaded list) */
const NOVENUE = "（无出处）";
const venueOf = (p) => p.venue_short || p.venue || "";
const visiblePapers = () => (S.venueOff.size ? S.papers.filter((p) => !S.venueOff.has(venueOf(p))) : S.papers);
const VF = {
  el: null, draft: null, items: [], cur: [],
  open(ev) {
    ev.stopPropagation();
    if (VF.el) return VF.close();
    const cnt = new Map();
    for (const p of S.papers) cnt.set(venueOf(p), (cnt.get(venueOf(p)) || 0) + 1);
    for (const v of S.venueOff) if (!cnt.has(v)) cnt.set(v, 0);
    VF.items = [...cnt].sort(([a], [b]) => (!a) - (!b) || a.localeCompare(b, "zh-CN", { sensitivity: "base" }));
    VF.draft = new Set(S.venueOff);
    const el = document.createElement("div"); el.className = "vfilter";
    el.innerHTML = `<div class="row" style="margin:0"><button class="btn small" onclick="VF.sort()">按出处 A→Z 排序</button><span class="small">共 ${VF.items.length} 个出处</span></div>
      <input type="search" placeholder="搜索出处…（回车 = 只显示匹配项）">
      <div class="vlist"></div>
      <div class="acts"><button class="btn small" onclick="VF.close()">取消</button><button class="btn small primary" onclick="VF.apply()">确定</button></div>`;
    document.body.appendChild(el); VF.el = el;
    const inp = $("input", el);
    inp.oninput = VF.renderList;
    inp.onkeydown = (e) => { if (e.key === "Enter") VF.onlyMatches(); };
    $(".vlist", el).onchange = (e) => {
      const t = e.target;
      if (t.dataset.all != null) VF.cur.forEach(([v]) => (t.checked ? VF.draft.delete(v) : VF.draft.add(v)));
      else { const v = VF.cur[+t.dataset.i][0]; t.checked ? VF.draft.delete(v) : VF.draft.add(v); }
      VF.renderList();
    };
    const r = ev.currentTarget.getBoundingClientRect();
    el.style.left = Math.max(8, Math.min(r.left, innerWidth - 310)) + "px";
    el.style.top = Math.max(8, Math.min(r.bottom + 4, innerHeight - 440)) + "px";
    VF.renderList(); inp.focus();
  },
  query() { return $("input", VF.el).value.trim().toLowerCase(); },
  renderList() {
    const q = VF.query(), list = $(".vlist", VF.el), top = list.scrollTop;
    VF.cur = VF.items.filter(([v]) => !q || (v || NOVENUE).toLowerCase().includes(q));
    const on = VF.cur.filter(([v]) => !VF.draft.has(v)).length;
    list.innerHTML = (VF.cur.length ? `<label class="all"><input type="checkbox" data-all ${on === VF.cur.length ? "checked" : ""}> ${q ? "（全选搜索结果）" : "（全选）"}</label>` : `<div class="small" style="padding:4px 8px">没有匹配的出处</div>`) +
      VF.cur.map(([v, n], i) => `<label><input type="checkbox" data-i="${i}" ${VF.draft.has(v) ? "" : "checked"}><span>${esc(v || NOVENUE)}</span><span class="n">${n}</span></label>`).join("");
    const all = $("input[data-all]", list); if (all) all.indeterminate = on > 0 && on < VF.cur.length;
    list.scrollTop = top;
  },
  onlyMatches() {   // Excel-like: type in the search box + Enter -> show only the matching venues
    if (!VF.query() || !VF.cur.length) return;
    const keep = new Set(VF.cur.map(([v]) => v));
    VF.draft = new Set(VF.items.map(([v]) => v).filter((v) => !keep.has(v)));
    VF.apply();
  },
  apply() {
    S.venueOff = VF.draft; VF.close();
    const ids = new Set(visiblePapers().map((p) => p.id));
    for (const id of [...S.selected]) if (!ids.has(id)) S.selected.delete(id);
    renderMain();
  },
  sort() { VF.close(); UI.setFilter({ sort: "venue" }); },
  clear() { S.venueOff = new Set(); VF.close(); renderMain(); },
  close() { if (VF.el) { VF.el.remove(); VF.el = null; } },
};

/* ------------------------------------------------------------------ library list */
async function loadPapers() {
  S.papers = await api("GET", "/api/papers?" + qs(S.filter));
  const ids = new Set(visiblePapers().map((p) => p.id));
  for (const id of [...S.selected]) if (!ids.has(id)) S.selected.delete(id);
  renderMain();
}
function tierBadge(p) {
  return p.tier ? `<span class="badge t${p.tier}">T${p.tier}</span>` : `<span class="badge tn">—</span>`;
}
function yBadge(p) { return p.year_band ? `<span class="badge y">Y${p.year_band}</span>` : ""; }
function viewTitle() {
  const f = S.filter;
  if (f.search) return `检索记录 #${f.search} 的论文`;
  if (f.group) return `分组：${G.path(f.group).replace("/", " › ")}${f.unassigned ? " › 未分小组" : ""}`;
  if (f.starred) return "★ 星标论文";
  if (f.status) return STATUS[f.status];
  return "全部论文";
}
function renderMain() {
  if (S.view !== "library") return;
  const f = S.filter, n = S.selected.size, vis = visiblePapers(), vOn = S.venueOff.size > 0;
  let h = `<div class="toolbar"><span class="title">${esc(viewTitle())}</span>
    <input type="search" placeholder="搜索库内标题 / 作者 / 摘要…" value="${esc(f.q)}" onkeydown="if(event.key==='Enter')UI.setFilter({q:this.value})">
    <select onchange="UI.setFilter({tier:this.value})"><option value="">全部层级</option>${[1, 2, 3].map((t) => `<option value="${t}" ${f.tier == t ? "selected" : ""}>Tier ${t}</option>`).join("")}</select>
    <select onchange="UI.setFilter({level:this.value})"><option value="">全部补全等级</option>${[1, 2, 3].map((l) => `<option value="${l}" ${f.level == l ? "selected" : ""}>≥ ${LEVEL[l]}</option>`).join("")}</select>
    <select onchange="UI.setFilter({sort:this.value})">${[["priority", "按优先级"], ["added", "按加入时间"], ["year", "按年份"], ["title", "按标题"], ["venue", "按出处"]].map(([k, v]) => `<option value="${k}" ${f.sort === k ? "selected" : ""}>${v}</option>`).join("")}</select>
    <span class="small">${vOn ? `${vis.length} / ${S.papers.length}` : S.papers.length} 篇</span></div>`;
  // sub-group switcher when viewing a big group (or one of its sub-groups)
  const cur = f.group ? G.find(f.group) : null, top = f.group ? G.top(f.group) : null;
  if (top) {
    const chip = (label, active, onclick, count) => `<span class="gchip ${active ? "on" : ""}" onclick="${onclick}">${label}${count != null ? ` <b>${count}</b>` : ""}</span>`;
    h += `<div class="subbar"><span class="small">${esc(top.name)} ›</span>
      ${chip("全部", cur && !cur.isChild && !f.unassigned, `UI.setFilter({group:${top.id},unassigned:''})`, top.n)}
      ${top.children.map((c) => chip(esc(c.name), cur && cur.id === c.id, `UI.setFilter({group:${c.id},unassigned:''})`, c.n)).join("")}
      ${top.children.length ? chip("未分小组", !!f.unassigned, `UI.setFilter({group:${top.id},unassigned:'1'})`, top.unassigned) : ""}
      <span class="gchip add" onclick="UI.newGroup(${top.id})">＋ 新建小组</span></div>`;
  }
  if (n) {
    const subOpts = top ? top.children.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("") : "";
    h += `<div class="bulkbar"><b>已选 ${n} 篇</b>
      <select onchange="UI.bulk('status',this.value);this.value=''"><option value="">设置状态…</option>${Object.entries(STATUS).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select>
      <button class="btn small" onclick="UI.bulk('star')">★ 加星</button><button class="btn small" onclick="UI.bulk('unstar')">☆ 去星</button>
      <select onchange="UI.bulkGroup(this.value);this.value=''"><option value="">加入分组…</option>${G.options()}<option value="__new">＋ 新建分组…</option></select>
      ${top ? `<select onchange="UI.bulkMove(this.value);this.value=''" title="放入该小组，并离开本大组下的其他小组"><option value="">移入小组…</option>${subOpts}<option value="__newsub">＋ 新建小组…</option></select>` : ""}
      ${cur ? `<button class="btn small" onclick="UI.bulk('group_remove',${cur.id})" title="${cur.isChild ? "只离开这个小组，仍留在大组中" : "离开大组及其所有小组"}">${cur.isChild ? "移出本小组" : "移出本大组"}</button>` : ""}
      <button class="btn small" onclick="UI.bulk('enrich_meta')">补全摘要</button>
      <button class="btn small" onclick="UI.bulk('abstract_pdf')" title="从已关联的本地 PDF 中提取摘要（离线，不消耗 API 额度）">从 PDF 提取摘要</button>
      <button class="btn small" onclick="UI.bulk('fetch_pdf')">下载开放获取 PDF</button>
      <button class="btn small" onclick="UI.bulk('queue_read')">加入 AI 精读队列</button>
      <button class="btn small" onclick="UI.bulkBib()">导出 BibTeX</button>
      <button class="btn small danger" onclick="UI.bulkDelete()">从库中删除</button>
      <button class="btn small ghost" onclick="S.selected.clear();renderMain()">取消选择</button></div>`;
  }
  if (vOn) {
    const hidden = [...S.venueOff].map((v) => v || NOVENUE);
    h += `<div class="filterbar">出处筛选：已隐藏 ${hidden.length} 个出处（${esc(hidden.slice(0, 4).join("、"))}${hidden.length > 4 ? " 等" : ""}），显示 ${vis.length} / ${S.papers.length} 篇
      <button class="btn small" onclick="VF.open(event)">修改</button><button class="btn small" onclick="VF.clear()">清除筛选</button></div>`;
  }
  if (S.papers.length && !vis.length) {
    h += `<div class="empty">出处筛选后没有论文。<br><button class="btn" onclick="VF.clear()">清除出处筛选</button></div>`;
  } else if (!S.papers.length) {
    h += `<div class="empty">这里还没有论文。<br>在 Claude Code 中用检索命令找论文，或在左侧“dblp 目录检索”中直接添加。</div>`;
  } else {
    const all = vis.every((p) => S.selected.has(p.id));
    h += `<div class="list"><table class="papers"><thead><tr>
      <th style="width:28px"><input type="checkbox" ${all ? "checked" : ""} onchange="UI.selectAll(this.checked)"></th><th style="width:26px"></th>
      <th>标题</th><th style="width:120px"><span class="thf ${vOn ? "on" : ""}" onclick="VF.open(event)" title="按出处筛选（类似 Excel 筛选）">出处 ${vOn ? "▼ 已筛选" : "▾"}</span></th><th style="width:78px">层级</th><th style="width:62px">补全</th><th style="width:64px">状态</th></tr></thead><tbody>`;
    for (const p of vis) {
      h += `<tr data-id="${p.id}" class="${p.id === S.current ? "current" : ""} ${p.status === "excluded" ? "excluded" : ""}">
        <td><input type="checkbox" ${S.selected.has(p.id) ? "checked" : ""} onchange="UI.toggleSel(${p.id},this.checked)"></td>
        <td><span class="star ${p.starred ? "on" : ""}" onclick="UI.star(${p.id},${p.starred ? 0 : 1})">★</span></td>
        <td><div class="ptitle" onclick="UI.open(${p.id})">${esc(p.title)}</div>
          <div class="psub">${esc((p.authors || "").split(";").slice(0, 3).join(", "))}${(p.authors || "").split(";").length > 3 ? " 等" : ""}
          ${p.pub_status && PUBST[p.pub_status] ? `<span class="pubst">${PUBST[p.pub_status]}</span>` : ""}</div>
          <div>${(p.group_refs || []).map((g) => `<span class="chip"><i class="cdot" style="background:${esc(g.color || "#9aa4b2")}"></i>${esc(g.path.replace("/", " › "))}</span>`).join("")}</div></td>
        <td>${esc(p.venue_short || p.venue || "")}<div class="psub">${p.year || ""}</div></td>
        <td>${tierBadge(p)} ${yBadge(p)}</td>
        <td><span class="lv lv${p.level}" title="${LEVEL[p.level]}">L${p.level}</span>${p.pdf_path ? (p.pdf_exists ? " 📄" : ' <span title="PDF 文件找不到了">⚠</span>') : ""}</td>
        <td><span class="status ${p.status}">${STATUS[p.status]}</span></td></tr>`;
    }
    h += `</tbody></table></div>`;
  }
  setMain(h, "lib:" + JSON.stringify(f) + "|" + [...S.venueOff].join("\u0001"));
}

/* ------------------------------------------------------------------ detail panel */
async function openPaper(id) {
  S.current = id;
  const p = await api("GET", `/api/papers/${id}`);
  // remember where the clicked row sits, so it stays put even though the list narrows when the panel opens
  const rowTop = () => { const r = $(`#main .list tr[data-id="${id}"]`); return r ? r.getBoundingClientRect().top : null; };
  const before = S.view === "library" ? rowTop() : null;
  renderDetail(p);
  if (S.view !== "library") return;
  renderMain();
  const after = rowTop(), list = $("#main .list");
  if (before != null && after != null) list.scrollTop += after - before;
  else $("#main tr.current")?.scrollIntoView({ block: "nearest" });
}
function renderDetail(p) {
  const d = $("#detail"), keep = d.dataset.pid === String(p.id) && !d.classList.contains("hidden") ? d.scrollTop : 0;
  d.classList.remove("hidden"); $("#resizer").classList.remove("hidden");
  const links = [];
  if (p.doi) links.push(`<a href="https://doi.org/${esc(p.doi)}" target="_blank">DOI</a>`);
  if (p.arxiv) links.push(`<a href="https://arxiv.org/abs/${esc(p.arxiv)}" target="_blank">arXiv</a>`);
  if (p.openreview_id) links.push(`<a href="https://openreview.net/forum?id=${esc(p.openreview_id)}" target="_blank">OpenReview</a>`);
  if (p.dblp_key) links.push(`<a href="https://dblp.org/rec/${esc(p.dblp_key)}" target="_blank">dblp</a>`);
  if (p.oa_url) links.push(`<a href="${esc(p.oa_url)}" target="_blank">开放获取</a>`);
  const gopts = G.options(p.group_ids || []);
  let h = `<div class="row" style="justify-content:space-between"><span>${tierBadge(p)} ${yBadge(p)} <span class="lv lv${p.level}">${LEVEL[p.level]}</span></span>
    <button class="btn small ghost" onclick="UI.closeDetail()">✕</button></div>
    <h2>${esc(p.title)}</h2>
    <div class="meta-line">${esc(p.authors || "")}</div>
    <div class="meta-line"><b>${esc(p.venue_short || "")}</b> ${esc(p.venue && p.venue !== p.venue_short ? "· " + p.venue : "")} · ${p.year || ""}
      ${p.pub_status && PUBST[p.pub_status] ? `<span class="pubst">${PUBST[p.pub_status]}</span>` : ""}
      ${p.citations != null ? ` · 被引 ${p.citations}` : ""}</div>
    <div class="links">${links.join("")}</div>
    <div class="row" style="margin-top:8px"><span class="seg">${Object.entries(STATUS).map(([k, v]) => `<button class="${p.status === k ? "on" : ""}" onclick="UI.patch(${p.id},{status:'${k}'})">${v}</button>`).join("")}</span>
      <span class="star ${p.starred ? "on" : ""}" style="font-size:20px" onclick="UI.patch(${p.id},{starred:${p.starred ? 0 : 1}})">★</span></div>
    <div class="row">${(p.group_refs || []).map((g) => `<span class="chip"><i class="cdot" style="background:${esc(g.color || "#9aa4b2")}"></i>${esc(g.path.replace("/", " › "))}<span class="x" title="${g.parent_id ? "移出该小组（仍留在大组）" : "移出该大组"}" onclick="UI.groupRemove(${p.id},${g.id})">✕</span></span>`).join("")}
      <select onchange="UI.groupAddOne(${p.id},this.value)"><option value="">＋ 加入分组 / 小组</option>${gopts}<option value="__new">新建分组…</option></select></div>`;

  const absBtns = `${p.pdf_path ? `<button class="btn small" onclick="UI.bulk('abstract_pdf',null,[${p.id}])" title="离线，不消耗 API 额度">从 PDF 提取</button> ` : ""}<button class="btn small" onclick="UI.bulk('enrich_meta',null,[${p.id}])">尝试补全</button>`;
  const absHint = p.pdf_path ? "暂无摘要。点“从 PDF 提取”直接从已关联的全文中读取摘要（离线）；或点“尝试补全”从 OpenAlex / Springer / arXiv 获取。"
    : "暂无摘要（L0）。点“尝试补全”从 OpenAlex / Springer / arXiv 获取。Elsevier 期刊摘要不对外开放，可能补不到；关联 PDF 后会自动从全文提取。";
  h += `<div class="dsec"><div class="dhead"><span>摘要</span>${!p.abstract ? `<span>${absBtns}</span>` : `<span class="small">来源：${esc(ABS_SRC[p.abstract_source] || p.abstract_source || "")}</span>`}</div>
    <div class="abstract">${p.abstract ? esc(p.abstract) : `<span class="small">${absHint}</span>`}</div></div>`;

  h += `<div class="dsec"><div class="dhead"><span>PDF 全文</span></div>`;
  if (p.pdf_path) {
    h += `<div class="row"><a class="btn primary small" href="/pdf/${p.id}" target="_blank">在新标签页打开</a>
      <button class="btn small" onclick="UI.togglePdf(${p.id})">内嵌预览</button>
      <button class="btn small danger" onclick="UI.pdfUnlink(${p.id})">取消关联</button></div>
      <div class="small">${p.pdf_kind === "linked" ? "🔗 链接到本地文件" : "📁 存放在库内"}：${esc(p.pdf_path)} ${p.pdf_exists ? "" : '<b style="color:var(--danger)">（文件不存在，可能被移动了）</b>'}</div>
      <div id="pdfbox"></div>`;
  } else {
    h += `<div class="row"><button class="btn small" onclick="UI.bulk('fetch_pdf',null,[${p.id}])">下载开放获取版本</button></div>
      <div class="row"><input type="text" id="pdfpath" placeholder="本地 PDF 路径，如 D:\\papers\\xxx.pdf" style="flex:1">
      <label class="small"><input type="checkbox" id="pdfcopy">复制到库内</label><button class="btn small" onclick="UI.pdfLink(${p.id})">关联</button></div>
      <div class="dropzone" id="drop" ondragover="event.preventDefault();this.classList.add('over')" ondragleave="this.classList.remove('over')"
        ondrop="UI.pdfDrop(event,${p.id})">把 PDF 拖到这里（会复制到库内 library/pdfs）<br>或 <input type="file" accept="application/pdf" onchange="UI.pdfUpload(this.files[0],${p.id})"></div>`;
  }
  h += `</div>`;

  h += `<div class="dsec"><div class="dhead"><span>AI 分析笔记（Markdown）</span>
      <span>${p.queue.length ? `<span class="small">已在精读队列</span>` : `<button class="btn small" onclick="UI.bulk('queue_read',null,[${p.id}])">加入 AI 精读队列</button>`}</span></div>`;
  h += p.ai_notes.length ? p.ai_notes.map((n) => `<div class="row"><a class="btn small primary" onclick="UI.viewNote(${n.id})">📝 ${esc(n.title)}</a>
      <span class="small">${esc(n.created_at || "")}</span><button class="btn small ghost" title="取消关联（不删除文件）" onclick="UI.noteUnlink(${n.id},${p.id})">✕</button></div>`).join("")
    : `<div class="small">还没有 AI 分析。加入队列后，在 Claude Code 中运行 <code>/queue</code> 即可生成；生成的笔记会自动出现在这里。</div>`;
  h += `<div class="row"><input type="text" id="mdpath" placeholder="或关联已有的 .md 文件路径" style="flex:1"><button class="btn small" onclick="UI.noteLink(${p.id})">关联</button></div></div>`;

  h += `<div class="dsec"><div class="dhead"><span>我的笔记（纯文本）</span><span id="notesaved" class="saved"></span></div>
    <textarea id="mynote" rows="6" placeholder="随手记…（自动保存为 library/notes/${p.id}.txt）" oninput="UI.noteChanged(${p.id})">${esc(p.note || "")}</textarea></div>`;

  h += `<div class="dsec"><div class="dhead"><span>关联论文</span></div>`;
  h += p.relations.length ? p.relations.map((r) => `<div class="rel">${r.dir === "out" ? "本文" : ""}<span class="rt">${esc(r.rel_type)}</span>${r.dir === "in" ? "←" : "→"}
      <a onclick="UI.open(${r.other_id})">${esc(r.other_title)}</a>${r.note ? `<div class="small">${esc(r.note)}</div>` : ""}
      <button class="btn small ghost" onclick="UI.relDelete(${r.id},${p.id})">✕</button></div>`).join("") : `<div class="small">暂无</div>`;
  h += `<div class="row"><input list="titles" id="reltarget" placeholder="输入库内论文标题…" style="flex:1">
      <datalist id="titles">${S.allTitles.filter((t) => t.id !== p.id).map((t) => `<option value="${esc(t.title)} [#${t.id}]">`).join("")}</datalist></div>
    <div class="row"><select id="reltype">${REL_TYPES.map((t) => `<option>${t}</option>`).join("")}</select>
      <input type="text" id="relnote" placeholder="一句说明（可选）" style="flex:1"><button class="btn small" onclick="UI.relAdd(${p.id})">添加</button></div></div>`;

  if (p.found_in.length)
    h += `<div class="dsec"><div class="dhead"><span>来自哪些检索</span></div>${p.found_in.map((s) => `<div class="small">#${s.id} ${esc(s.query)}（${esc(s.run_at)}）${s.reason ? `<br>　→ ${esc(s.reason)}` : ""}</div>`).join("")}</div>`;
  if (p.enrich_log) h += `<div class="dsec"><div class="dhead"><span class="small">补全日志</span></div><pre class="log">${esc(p.enrich_log)}</pre></div>`;
  h += `<div class="dsec small">库内编号 #${p.id} · 加入于 ${esc(p.added_at)} · 来源 ${esc(p.found_by || "")}
    <button class="btn small danger" style="float:right" onclick="UI.deleteOne(${p.id})">从库中删除</button></div>`;
  d.innerHTML = h; d.dataset.pid = p.id; S.detail = p;
  d.scrollTop = keep;   // same paper refreshed (status/star/group…) keeps its place; a new paper starts at the top
}

/* ------------------------------------------------------------------ dblp catalog view */
function renderDblp() {
  const D = S.dblp;
  let h = `<div class="toolbar"><span class="title">dblp 目录检索（本地 884 万篇）</span>
    <input type="search" id="dq" placeholder="标题关键词，如 vision transformer object detection" value="${esc(D.q)}" onkeydown="if(event.key==='Enter')UI.dblpSearch()" style="min-width:380px">
    <select id="dtier"><option value="">全部出处</option>${[1, 2, 3].map((t) => `<option value="${t}" ${D.tier == t ? "selected" : ""}>仅 Tier ${t}</option>`).join("")}</select>
    <input type="text" id="dyear" placeholder="年份，如 2021-" value="${esc(D.year)}" style="width:110px">
    <button class="btn primary" onclick="UI.dblpSearch()">检索</button>
    ${D.results.length ? `<span class="small">共 ${D.total} 条匹配，显示前 ${D.results.length} 条</span>` : ""}</div>`;
  if (D.sel.size) {
    h += `<div class="bulkbar"><b>已选 ${D.sel.size} 篇</b><select id="dgroup"><option value="">（不加入分组）</option>${G.options()}</select>
      <button class="btn small primary" onclick="UI.dblpAdd()">加入文献库</button></div>`;
  }
  if (D.results.length) {
    h += `<div class="list"><table class="papers"><thead><tr><th style="width:28px"></th><th>标题</th><th style="width:140px">出处</th><th style="width:80px">层级</th><th style="width:70px"></th></tr></thead><tbody>`;
    for (const r of D.results) {
      h += `<tr><td>${r.in_library ? "" : `<input type="checkbox" ${D.sel.has(r.key) ? "checked" : ""} onchange="UI.dblpSel('${esc(r.key)}',this.checked)">`}</td>
        <td><div class="ptitle">${esc(r.title)}</div><div class="psub">${esc((r.authors || "").split(";").slice(0, 3).join(", "))}${r.also?.length ? ` · 另有 ${esc(r.also.join(", "))}` : ""}</div></td>
        <td>${esc(r.short || r.venue)}<div class="psub">${r.year || ""}</div></td><td>${tierBadge(r)} ${yBadge(r)}</td>
        <td>${r.in_library ? `<a class="btn small" onclick="UI.go('library');UI.open(${r.in_library})">库内 #${r.in_library}</a>` : ""}</td></tr>`;
    }
    h += `</tbody></table></div>`;
  } else h += `<div class="empty">在本地 dblp 目录中检索（离线、不消耗任何 API 额度）。<br>这里只有基本信息；加入文献库后可补全摘要和 PDF。<br>dblp 尚未收录的最新论文请在 Claude Code 中用检索命令获取。</div>`;
  setMain(h, "dblp:" + JSON.stringify([D.q, D.tier, D.year, D.total]));
}

/* ------------------------------------------------------------------ graph view */
async function renderGraph() {
  const g = S.graphGroup || "";
  $("#main").innerHTML = `<div class="toolbar"><span class="title">论文关系图</span>
    <select onchange="S.graphGroup=this.value;renderGraph()"><option value="">所有有关联的论文</option>${G.options().replace(`value="${g}"`, `value="${g}" selected`)}</select>
    <span class="small">节点颜色 = 层级；点击节点查看详情；在详情面板“关联论文”里添加关系</span></div><div id="graph"></div>`;
  const data = await api("GET", "/api/graph?" + qs({ group: g }));
  const box = $("#graph"), W = box.clientWidth || 900, H = box.clientHeight || 600;
  if (!data.nodes.length) { box.innerHTML = `<div class="empty">还没有论文关联。打开一篇论文，在“关联论文”中添加关系后，这里会显示关系网络。</div>`; return; }
  const N = data.nodes.map((n, i) => ({ ...n, x: W / 2 + Math.cos(i) * 200, y: H / 2 + Math.sin(i) * 200, vx: 0, vy: 0 }));
  const idx = Object.fromEntries(N.map((n, i) => [n.id, i]));
  const E = data.edges.filter((e) => e.src_id in idx && e.dst_id in idx);
  for (let it = 0; it < 400; it++) {                    // simple force layout
    for (let i = 0; i < N.length; i++) for (let j = i + 1; j < N.length; j++) {
      const a = N[i], b = N[j]; let dx = a.x - b.x, dy = a.y - b.y; const d2 = dx * dx + dy * dy + 0.01, f = 9000 / d2;
      const d = Math.sqrt(d2); dx /= d; dy /= d; a.vx += dx * f; a.vy += dy * f; b.vx -= dx * f; b.vy -= dy * f;
    }
    for (const e of E) {
      const a = N[idx[e.src_id]], b = N[idx[e.dst_id]]; const dx = b.x - a.x, dy = b.y - a.y; const d = Math.sqrt(dx * dx + dy * dy) || 1, f = (d - 150) * 0.02;
      a.vx += dx / d * f * 10; a.vy += dy / d * f * 10; b.vx -= dx / d * f * 10; b.vy -= dy / d * f * 10;
    }
    for (const n of N) { n.vx += (W / 2 - n.x) * 0.002; n.vy += (H / 2 - n.y) * 0.002; n.x += Math.max(-20, Math.min(20, n.vx * 0.1)); n.y += Math.max(-20, Math.min(20, n.vy * 0.1)); n.vx *= 0.6; n.vy *= 0.6; }
  }
  const col = (t) => ({ 1: "#b42318", 2: "#b54708", 3: "#027a48" })[t] || "#6a737d";
  // fit the drawing to its content (labels extend ~260px to the right of a node)
  const xs = N.map((n) => n.x), ys = N.map((n) => n.y);
  const x0 = Math.min(...xs) - 40, x1 = Math.max(...xs) + 280, y0 = Math.min(...ys) - 40, y1 = Math.max(...ys) + 40;
  const vw = Math.max(x1 - x0, W), vh = Math.max(y1 - y0, H);   // never zoom in beyond 1:1
  const cx = (x0 + x1) / 2 - vw / 2, cy = (y0 + y1) / 2 - vh / 2;
  let svg = `<svg viewBox="${cx} ${cy} ${vw} ${vh}" preserveAspectRatio="xMidYMid meet"><defs><marker id="arr" viewBox="0 0 10 10" refX="22" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0L10,5L0,10z" fill="#b6bfcc"/></marker></defs>`;
  for (const e of E) {
    const a = N[idx[e.src_id]], b = N[idx[e.dst_id]];
    svg += `<line class="edge" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" marker-end="url(#arr)"><title>${esc(e.rel_type)}${e.note ? "：" + esc(e.note) : ""}</title></line>
      <text class="edge-label" x="${(a.x + b.x) / 2}" y="${(a.y + b.y) / 2}">${esc(e.rel_type)}</text>`;
  }
  for (const n of N) {
    const label = (n.title || "").length > 28 ? n.title.slice(0, 28) + "…" : n.title;
    svg += `<g class="node" onclick="UI.open(${n.id})"><circle cx="${n.x}" cy="${n.y}" r="${n.starred ? 11 : 8}" fill="${col(n.tier)}"><title>${esc(n.title)}</title></circle>
      <text x="${n.x + 12}" y="${n.y + 4}">${esc(label)} · ${esc(n.venue_short || "")} ${n.year || ""}</text></g>`;
  }
  box.innerHTML = svg + "</svg>";
}

/* ------------------------------------------------------------------ searches + queue views */
async function renderSearches() {
  const rows = await api("GET", "/api/searches");
  let h = `<div class="toolbar"><span class="title">检索记录</span><span class="small">每次检索命令的结果都会记录在这里</span></div>`;
  h += rows.length ? `<div class="list"><table class="papers"><thead><tr><th>检索问题</th><th style="width:150px">时间</th><th style="width:110px">候选 / 保留</th><th style="width:170px"></th></tr></thead><tbody>${rows.map((r) => `<tr>
      <td>#${r.id} ${esc(r.query || "")}<div class="psub">${esc(r.kind || "")}</div></td><td>${esc(r.run_at)}</td><td>${r.n_candidates ?? "-"} / ${r.n_kept ?? "-"}</td>
      <td><button class="btn small" onclick="UI.setFilter({search:${r.id},group:'',status:'',starred:''})">查看论文</button>
      ${r.report_path ? `<button class="btn small" onclick="UI.viewReport(${r.id})">报告</button>` : ""}</td></tr>`).join("")}</tbody></table></div>`
    : `<div class="empty">还没有检索记录。</div>`;
  setMain(h, "searches");
}
async function renderQueue() {
  const rows = await api("GET", "/api/queue");
  const TASK = { read: "AI 精读", enrich: "补全", fetch_pdf: "下载 PDF" };
  let h = `<div class="toolbar"><span class="title">AI 任务队列</span>
    <span class="small">需要 AI 的任务（如精读生成 Markdown 分析）在这里排队；在 Claude Code 中输入 <code>/queue</code> 即可批量处理。</span></div>`;
  h += rows.length ? `<div class="list"><table class="papers"><thead><tr><th>论文</th><th style="width:90px">任务</th><th style="width:80px">状态</th><th style="width:150px">时间</th><th style="width:60px"></th></tr></thead><tbody>${rows.map((r) => `<tr>
      <td><a class="ptitle" onclick="UI.open(${r.paper_id})">${esc(r.title)}</a>${r.message ? `<div class="psub">${esc(r.message)}</div>` : ""}</td>
      <td>${TASK[r.task] || esc(r.task)}</td><td>${{ pending: "待处理", done: "完成", failed: "失败" }[r.status] || esc(r.status)}</td><td>${esc(r.created_at)}</td>
      <td>${r.status === "pending" ? `<button class="btn small ghost" onclick="UI.queueDelete(${r.id})">✕</button>` : ""}</td></tr>`).join("")}</tbody></table></div>`
    : `<div class="empty">队列为空。</div>`;
  setMain(h, "queue");
}

/* ------------------------------------------------------------------ actions */
let noteTimer = null;
const UI = {
  go(view) { S.view = view; setMain.key = null; VF.close(); renderSide(); ({ library: loadPapers, dblp: renderDblp, graph: renderGraph, searches: renderSearches, queue: renderQueue })[view](); },
  setFilter(o) {
    if ("group" in o || "search" in o) S.venueOff.clear();   // navigating to another list resets the venue filter
    Object.assign(S.filter, o); S.view = "library"; S.selected.clear(); VF.close(); renderSide(); loadPapers();
  },
  open: openPaper,
  closeDetail() { S.current = null; $("#detail").classList.add("hidden"); $("#resizer").classList.add("hidden"); if (S.view === "library") renderMain(); },
  toggleSel(id, on) { on ? S.selected.add(id) : S.selected.delete(id); renderMain(); },
  selectAll(on) { visiblePapers().forEach((p) => (on ? S.selected.add(p.id) : S.selected.delete(p.id))); renderMain(); },
  async refresh() { await loadSide(); if (S.view === "library") await loadPapers(); if (S.current) await openPaper(S.current); await loadTitles(); },
  async star(id, v) { await api("PATCH", `/api/papers/${id}`, { starred: v }); UI.refresh(); },
  async patch(id, o) { await api("PATCH", `/api/papers/${id}`, o); UI.refresh(); },
  async bulk(action, value, ids) {
    if (action === "status" && !value) return;
    ids = ids || [...S.selected];
    const r = await api("POST", "/api/papers/bulk", { ids, action, value });
    if (r.job) { toast(`已开始后台任务（${ids.length} 篇），完成后自动刷新`); watchJob(r.job); }
    else toast({ queue_read: "已加入 AI 精读队列", group_add: `已加入：${r.group || ""}`, group_move: `已移入：${r.group || ""}`, group_remove: `已移出：${r.group || ""}` }[action] || "已更新");
    UI.refresh();
  },
  async bulkGroup(val) {
    if (!val) return;
    if (val === "__new") {
      val = prompt("新分组名称。\n· 建大组：直接输入名称，如「目标检测」\n· 建小组：用 大组/小组，如「目标检测/半监督」（大组不存在会一并创建）");
      if (!val) return;
    }
    await UI.bulk("group_add", val.trim());
  },
  async bulkMove(val) {   // into one sub-group of the current big group, leaving its sibling sub-groups
    if (!val) return;
    const top = G.top(S.filter.group);
    if (val === "__newsub") { const n = prompt(`在「${top.name}」下新建小组，名称：`); if (!n) return; val = `${top.name}/${n.trim()}`; }
    await UI.bulk("group_move", val);
  },
  async bulkBib() {
    const r = await api("POST", "/api/papers/bulk", { ids: [...S.selected], action: "bib" });
    UI.showModal(`BibTeX（${S.selected.size} 篇）`, `<div class="row"><button class="btn primary small" onclick="navigator.clipboard.writeText($('#bibtext').value);toast('已复制')">复制全部</button>
      <button class="btn small" onclick="UI.download('papers.bib',$('#bibtext').value)">下载 .bib</button></div><textarea id="bibtext" rows="30" style="font-family:monospace;font-size:12px">${esc(r.bib)}</textarea>`);
  },
  download(name, text) { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([text], { type: "text/plain" })); a.download = name; a.click(); },
  async bulkDelete() {
    if (!confirm(`确定从库中删除选中的 ${S.selected.size} 篇论文？\n（只删除库记录；PDF 与笔记文件不会被删除）`)) return;
    await UI.bulk("delete"); S.selected.clear();
  },
  async deleteOne(id) {
    if (!confirm("确定从库中删除这篇论文？（PDF 与笔记文件不会被删除）")) return;
    await api("DELETE", `/api/papers/${id}`); UI.closeDetail(); UI.refresh();
  },
  async newGroup(parentId) {
    const parent = parentId ? G.find(parentId) : null;
    const name = prompt(parent ? `在大组「${parent.name}」下新建小组，名称：` : "新建大组，名称：");
    if (!name) return;
    const desc = prompt("说明（可选）：") || "";
    const r = await api("POST", "/api/groups", { name: name.trim(), description: desc, parent_id: parentId || null });
    if (parentId) S.collapsed.delete(parentId);
    await loadSide(); toast(`已创建：${r.path}`); return r.id;
  },
  toggleCollapse(id) { S.collapsed.has(id) ? S.collapsed.delete(id) : S.collapsed.add(id); renderSide(); },
  groupMenu(gid) {
    const g = G.find(gid);
    const b = (label, js, cls = "") => `<button class="btn ${cls}" onclick="UI.closeModal();${js}">${label}</button>`;
    const others = S.groups.filter((t) => t.id !== gid && t.id !== g.parent_id);
    let h = `<div class="gmenu"><div class="small">${g.isChild ? `小组 · 属于大组「${esc(g.parentName)}」` : `大组 · ${g.children.length} 个小组`} · ${g.n} 篇论文
      ${g.description ? `<br>说明：${esc(g.description)}` : ""}</div>
      <h4>编辑</h4><div class="row">${b("重命名", `UI.groupAct(${gid},'rename')`)}${b("修改说明", `UI.groupAct(${gid},'desc')`)}
        ${g.isChild ? "" : b("换颜色", `UI.groupAct(${gid},'color')`)}${g.isChild ? "" : b("＋ 新建小组", `UI.newGroup(${gid})`)}</div>
      <h4>结构</h4><div class="row">`;
    if (g.isChild) {
      h += b("升级为大组", `UI.groupAct(${gid},'promote')`);
      if (others.length) h += `<select onchange="UI.closeModal();UI.groupAct(${gid},'reparent',this.value)"><option value="">移到另一个大组下…</option>${others.map((t) => `<option value="${t.id}">${esc(t.name)}</option>`).join("")}</select>`;
    } else if (!g.children.length && others.length) {
      h += `<select onchange="UI.closeModal();UI.groupAct(${gid},'reparent',this.value)"><option value="">变为某个大组的小组…</option>${others.map((t) => `<option value="${t.id}">${esc(t.name)}</option>`).join("")}</select>`;
    } else h += `<span class="small">${g.children.length ? "含有小组的大组不能再变成小组（最多两层）" : "暂无其他大组"}</span>`;
    h += `</div><h4>对组内全部论文</h4><div class="row">${b("导出 BibTeX", `UI.groupAct(${gid},'bib')`)}${b("补全摘要", `UI.groupAct(${gid},'enrich_meta')`)}${b("从 PDF 提取摘要", `UI.groupAct(${gid},'abstract_pdf')`)}
        ${b("下载开放获取 PDF", `UI.groupAct(${gid},'fetch_pdf')`)}${b("加入 AI 精读队列", `UI.groupAct(${gid},'queue_read')`)}
        ${b("全部标为已读", `UI.groupAct(${gid},'read')`)}</div>
      <h4>危险操作</h4><div class="row">${b(g.isChild ? "删除小组" : "删除大组", `UI.groupAct(${gid},'delete')`, "danger")}
        <span class="small">${g.isChild ? "论文仍保留在大组中" : "其下小组一并删除；论文本身不会被删除"}</span></div></div>`;
    UI.showModal(`${g.isChild ? "小组" : "大组"}：${g.path.replace("/", " › ")}`, h, true);
  },
  async groupAct(gid, act, arg) {
    const g = G.find(gid);
    const ids = async () => (await api("GET", "/api/papers?" + qs({ group: gid, exclude_hidden: 0 }))).map((p) => p.id);
    if (act === "rename") { const n = prompt("新名称：", g.name); if (!n) return; await api("PATCH", `/api/groups/${gid}`, { name: n.trim() }); }
    else if (act === "desc") { const d = prompt("说明：", g.description || ""); if (d == null) return; await api("PATCH", `/api/groups/${gid}`, { description: d }); }
    else if (act === "color") await api("PATCH", `/api/groups/${gid}`, { color: COLORS[(COLORS.indexOf(g.color) + 1) % COLORS.length] });
    else if (act === "promote") await api("PATCH", `/api/groups/${gid}`, { parent_id: null });
    else if (act === "reparent") { if (!arg) return; await api("PATCH", `/api/groups/${gid}`, { parent_id: +arg }); }
    else if (act === "delete") {
      const msg = g.isChild ? `删除小组「${g.path}」？
组内论文仍保留在大组「${g.parentName}」中。`
        : `删除大组「${g.name}」${g.children.length ? `及其 ${g.children.length} 个小组` : ""}？
论文本身不会被删除。`;
      if (!confirm(msg)) return;
      await api("DELETE", `/api/groups/${gid}`);
      if (+S.filter.group === gid || (!g.isChild && g.children.some((c) => c.id === +S.filter.group))) S.filter.group = "";
    }
    else if (act === "bib") { S.selected = new Set(await ids()); await UI.bulkBib(); }
    else if (act === "read") await UI.bulk("status", "read", await ids());
    else await UI.bulk(act, null, await ids());
    UI.refresh();
  },
  async groupAddOne(id, val) {
    if (!val) return;
    if (val === "__new") {
      val = prompt("新分组名称（建小组用 大组/小组 格式）："); if (!val) return; val = val.trim();
    }
    await api("POST", "/api/papers/bulk", { ids: [id], action: "group_add", value: val }); UI.refresh();
  },
  async groupRemove(id, gid) { await api("POST", "/api/papers/bulk", { ids: [id], action: "group_remove", value: gid }); UI.refresh(); },
  togglePdf(id) { const b = $("#pdfbox"); b.innerHTML = b.innerHTML ? "" : `<iframe class="pdfframe" src="/pdf/${id}"></iframe>`; },
  async pdfLink(id) {
    const path = $("#pdfpath").value.trim(); if (!path) return toast("请输入 PDF 路径");
    const r = await api("POST", `/api/papers/${id}/pdf`, { path, copy: $("#pdfcopy").checked });
    toast("已关联 PDF" + (r.abstract_from_pdf ? "，并从全文提取了摘要" : "")); UI.refresh();
  },
  async pdfUpload(file, id) {
    if (!file) return;
    const r = await api("POST", `/api/papers/${id}/pdf/upload?name=${encodeURIComponent(file.name)}`, await file.arrayBuffer(), true);
    toast("PDF 已存入库内" + (r.abstract_from_pdf ? "，并从全文提取了摘要" : "")); UI.refresh();
  },
  pdfDrop(ev, id) { ev.preventDefault(); ev.currentTarget.classList.remove("over"); const f = ev.dataTransfer.files[0]; if (f) UI.pdfUpload(f, id); },
  pdfUnlink(id) {
    const p = S.detail && S.detail.id === id ? S.detail : {}, managed = p.pdf_kind === "managed";
    const absLine = p.abstract_source === "pdf"
      ? "<li>摘要是从<b>这个 PDF</b> 提取的，会<b>一并清除</b>；关联新的 PDF 后会自动从新文件重新提取（也可点“尝试补全”从 OpenAlex 等获取）。</li>"
      : p.abstract ? `<li>摘要来自 ${esc(ABS_SRC[p.abstract_source] || p.abstract_source || "在线来源")}，与 PDF 无关，保持不变。</li>` : "";
    UI.showModal("取消关联 PDF", `<div class="small" style="word-break:break-all">${esc(p.pdf_path || "")}</div>
      <ul style="line-height:1.8;padding-left:20px">
        <li>解除这篇论文与该文件的关联（论文本身、笔记、分组都不受影响）。</li>${absLine}
        <li>${managed ? "这个文件是<b>库内副本</b>（library/pdfs）。默认保留；不再需要可勾选下面的选项删除，你原来的文件不受影响。" : "这个文件在你自己的目录里，<b>不会被删除或移动</b>。"}</li>
        ${p.ai_notes?.length ? `<li>⚠ 这篇论文已有 ${p.ai_notes.length} 份 AI 笔记。如果是基于错误的 PDF 生成的，请在“AI 分析笔记”里取消关联，换好 PDF 后重新加入精读队列。</li>` : ""}
      </ul>
      ${managed ? `<label class="row"><input type="checkbox" id="delcopy"> 同时删除库内副本</label>` : ""}
      <div class="row" style="justify-content:flex-end"><button class="btn" onclick="UI.closeModal()">算了</button>
        <button class="btn danger" onclick="UI.pdfUnlinkDo(${id})">取消关联</button></div>`, true);
  },
  async pdfUnlinkDo(id) {
    const del = $("#delcopy")?.checked;
    UI.closeModal();
    const r = await api("DELETE", `/api/papers/${id}/pdf?` + qs({ delete_copy: del ? 1 : "" }));
    toast("已取消关联" + (r.abstract_cleared ? "，并清除了从该 PDF 提取的摘要" : "") + (r.copy_deleted ? "，库内副本已删除" : ""));
    UI.refresh();
  },
  async viewNote(nid) {
    const r = await api("GET", `/api/ainote/${nid}`);
    const resolve = (src) => /^(https?:|data:)/i.test(src) ? src : `/api/ai-asset?note=${nid}&path=${encodeURIComponent(src)}`;
    UI.showModal(r.path, `<div class="md">${MD.render(r.text, { resolve })}</div>`);
  },
  async viewGuide() {
    const r = await api("GET", "/api/guide");
    UI.showModal("使用指南 — " + r.path, `<div class="md">${MD.render(r.text)}</div>`);
    // in-page anchors for the guide's table of contents
    document.querySelectorAll("#modal-body h2, #modal-body h3").forEach((el) => { el.id = "g-" + el.textContent.trim().replace(/\s+/g, "-"); });
  },
  async viewReport(sid) {
    const r = await api("GET", `/api/searches/${sid}/report`);
    UI.showModal(r.path, `<div class="md">${MD.render(r.text)}</div>`);
  },
  async noteLink(id) { const path = $("#mdpath").value.trim(); if (!path) return; await api("POST", `/api/papers/${id}/ainote`, { path }); toast("已关联笔记"); UI.refresh(); },
  async noteUnlink(nid, id) { if (confirm("取消关联这份 AI 笔记？（不会删除文件）")) { await api("DELETE", `/api/ainote/${nid}`); openPaper(id); } },
  noteChanged(id) {
    $("#notesaved").textContent = "编辑中…";
    clearTimeout(noteTimer);
    noteTimer = setTimeout(async () => { await api("PUT", `/api/papers/${id}/note`, { text: $("#mynote").value }); $("#notesaved").textContent = "已保存"; loadSide(); }, 800);
  },
  async relAdd(id) {
    const v = $("#reltarget").value, m = v.match(/\[#(\d+)\]\s*$/);
    if (!m) return toast("请从下拉列表中选择一篇库内论文");
    const p = await api("POST", `/api/papers/${id}/relations`, { dst_id: +m[1], rel_type: $("#reltype").value, note: $("#relnote").value });
    renderDetail(p); toast("已添加关联");
  },
  async relDelete(rid, id) { await api("DELETE", `/api/relations/${rid}`); openPaper(id); },
  async queueDelete(qid) { await api("DELETE", `/api/queue/${qid}`); renderQueue(); loadSide(); },
  async dblpSearch() {
    const D = S.dblp; D.q = $("#dq").value.trim(); D.tier = $("#dtier").value; D.year = $("#dyear").value.trim(); D.sel.clear();
    if (!D.q) return;
    const r = await api("GET", "/api/dblp/search?" + qs({ q: D.q, tier: D.tier, year: D.year, limit: 100 }));
    D.results = r.results; D.total = r.total_matches; renderDblp();
  },
  dblpSel(key, on) { on ? S.dblp.sel.add(key) : S.dblp.sel.delete(key); renderDblp(); },
  async dblpAdd() {
    const r = await api("POST", "/api/dblp/add", { keys: [...S.dblp.sel], group: $("#dgroup").value });
    toast(`已加入 ${r.paper_ids.length} 篇；可在库中选中后“补全摘要”`); S.dblp.sel.clear(); await loadSide(); await UI.dblpSearch();
  },
  showModal(title, html, small) {
    $("#modal-title").textContent = title; $("#modal-body").innerHTML = html;
    $("#modal").classList.toggle("small", !!small); $("#modal").classList.remove("hidden");
  },
  closeModal() { $("#modal").classList.add("hidden"); },
};

async function watchJob(jid) {
  for (let i = 0; i < 300; i++) {
    await new Promise((r) => setTimeout(r, 2000));
    const j = (await api("GET", "/api/jobs")).find((x) => x.id === jid);
    if (j && j.status !== "running") {
      let msg = j.kind + (j.status === "done" ? " 完成" : " 失败");
      try {
        const s = JSON.parse(j.output.trim().split("\n")[0]);
        msg += "：" + Object.entries(s).filter(([, v]) => typeof v !== "object").map(([k, v]) => `${JOB_KEYS[k] || k}=${v}`).join(", ");
        if (s.failures?.length) msg += "\n未提取到：" + s.failures.map((f) => `#${f.id}（${f.reason}）`).join("；");
      } catch (e) { /* plain output */ }
      toast(msg, 5000); UI.refresh(); return;
    }
  }
}
async function loadTitles() {
  const rows = await api("GET", "/api/papers?" + qs({ limit: 5000, exclude_hidden: 0 }));
  S.allTitles = rows.map((p) => ({ id: p.id, title: p.title }));
}
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { UI.closeModal(); VF.close(); } });
document.addEventListener("mousedown", (e) => { if (VF.el && !VF.el.contains(e.target) && !e.target.closest(".thf, .filterbar")) VF.close(); });
window.UI = UI; window.S = S; window.VF = VF; window.renderMain = renderMain;

/* ------------------------------------------------------------------ draggable divider: list | detail */
(function initResizer() {
  const r = $("#resizer"), d = $("#detail"), DEF = 470, MIN = 320, KEY = "ps.detailWidth";
  const clamp = (w) => Math.max(MIN, Math.min(w, innerWidth - 230 - 360));   // keep >= 360px for the list
  const apply = (w) => { d.style.width = clamp(w) + "px"; };
  apply(+localStorage.getItem(KEY) || DEF);
  r.addEventListener("mousedown", (e) => {
    e.preventDefault(); document.body.classList.add("resizing");
    const move = (ev) => apply(innerWidth - ev.clientX - 3);
    const up = () => {
      document.body.classList.remove("resizing");
      removeEventListener("mousemove", move); removeEventListener("mouseup", up);
      localStorage.setItem(KEY, parseInt(d.style.width));
    };
    addEventListener("mousemove", move); addEventListener("mouseup", up);
  });
  r.addEventListener("dblclick", () => { localStorage.removeItem(KEY); apply(DEF); });
  addEventListener("resize", () => apply(+localStorage.getItem(KEY) || DEF));
})();

(async function init() {
  // optional local KaTeX (put katex.min.js / katex.min.css under web/vendor/katex/ to render formulas)
  fetch("/vendor/katex/katex.min.js", { method: "HEAD" }).then((r) => {
    if (!r.ok) return;
    const l = document.createElement("link"); l.rel = "stylesheet"; l.href = "/vendor/katex/katex.min.css"; document.head.appendChild(l);
    const s = document.createElement("script"); s.src = "/vendor/katex/katex.min.js"; document.head.appendChild(s);
  }).catch(() => {});
  await loadSide(); await loadPapers(); await loadTitles();
  // deep links: #paper=12  #view=dblp|graph|searches|queue  #group=ID或大组/小组  #note=3  #dblp=query
  const h = new URLSearchParams(location.hash.slice(1));
  if (h.get("group")) {   // id or path ('大组/小组')
    const g = /^\d+$/.test(h.get("group")) ? G.find(h.get("group")) : G.flat().find((x) => x.path === h.get("group"));
    if (g) UI.setFilter({ group: g.id });
  }
  if (h.get("view")) UI.go(h.get("view"));
  if (h.get("dblp")) { UI.go("dblp"); $("#dq").value = h.get("dblp"); await UI.dblpSearch(); }
  if (h.get("paper")) await openPaper(+h.get("paper"));
  if (h.get("note")) await UI.viewNote(+h.get("note"));
  if (h.get("gmenu")) UI.groupMenu(+h.get("gmenu"));
  if (h.has("guide")) UI.viewGuide();
})();
