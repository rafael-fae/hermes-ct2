/**
 * CT2 Dashboard — Hermes Plugin v2
 *
 * Control Tower V2 frontend: project grid, detailed task table, and audit timeline.
 * Plain IIFE — no build step. Uses window.__HERMES_PLUGIN_SDK__ for React.
 */
(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) return;

  const { React } = SDK;
  const h = React.createElement;
  const { useState, useEffect, useCallback, useMemo } = SDK.hooks;

  const API = "/api/plugins/ct2";

  // ---- Helpers ---------------------------------------------------------------

  function fmtDate(dateStr) {
    if (!dateStr) return "—";
    // Handle DD/MM/YYYY BR format (with optional time)
    var brMatch = dateStr.match(/^(\d{2})\/(\d{2})\/(\d{4})/);
    if (brMatch) {
      var timePart = dateStr.split(" ").slice(1).join(" ");
      var timeClean = timePart ? timePart.replace(/[\(\)]/g, "").trim().substring(0, 5) : "";
      return brMatch[1] + "/" + brMatch[2] + "/" + brMatch[3] + (timeClean ? " - " + timeClean : "");
    }
    // Handle ISO format (with optional time)
    var isoMatch = dateStr.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (isoMatch) {
      var timeFromISO = "";
      var tPart = dateStr.split("T")[1];
      if (tPart) timeFromISO = tPart.substring(0, 5);
      return isoMatch[3] + "/" + isoMatch[2] + "/" + isoMatch[1] + (timeFromISO ? " - " + timeFromISO : "");
    }
    return dateStr;
  }

  function truncate(str, len) {
    if (!str) return "—";
    return str.length > len ? str.slice(0, len) + "…" : str;
  }

  function statusBadgeClass(status) {
    var map = { in_progress: "blue", done: "green", blocked: "red" };
    var mod = map[status] || "";
    return "ct2-badge" + (mod ? " ct2-badge--" + mod : "");
  }

  function formatStatus(status) {
    var map = { in_progress: "In Progress", todo: "Todo", done: "Done", blocked: "Blocked" };
    return map[status] || status || "—";
  }

  function shortHash(hash) {
    if (!hash || hash === "—") return "—";
    return String(hash).replace(/[`]/g, "").trim().substring(0, 7);
  }

  function renderMarkdown(md) {
    if (!md) return "";
    var html = md
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      // Headings
      .replace(/^### (.+)$/gm, "<h4>$1</h4>")
      .replace(/^## (.+)$/gm, "<h3>$1</h3>")
      .replace(/^# (.+)$/gm, "<h2>$1</h2>")
      // Bold + italic
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*(.+?)\*/g, "<em>$1</em>")
      // Inline code
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      // Code blocks
      .replace(/```(\w*)\n([\s\S]*?)```/g, "<pre><code>$2</code></pre>")
      // Links
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, "<a href=\"$2\" target=\"_blank\">$1</a>")
      // Lists
      .replace(/^- (.+)$/gm, "<li>$1</li>")
      .replace(/(<li>[\s\S]*?<\/li>)/g, "<ul>$1</ul>")
      // Paragraphs (double newline)
      .replace(/\n\n/g, "</p><p>")
      // Single newline
      .replace(/\n/g, "<br>");
    return "<p>" + html + "</p>";
  }

  // ---- Shared micro-components -----------------------------------------------

  function ErrorBanner(_a) {
    var message = _a.message, onRetry = _a.onRetry;
    return h("div", { className: "ct2-error", role: "alert" },
      h("span", { className: "ct2-error__icon", "aria-hidden": "true" }, "⚠"),
      h("span", { className: "ct2-error__message" }, message || "Erro desconhecido"),
      onRetry && h("button", { className: "ct2-error__retry", onClick: onRetry }, "Tentar novamente"),
    );
  }

  function EmptyState(_a) {
    var message = _a.message;
    return h("div", { className: "ct2-empty", role: "status" },
      h("span", { className: "ct2-empty__icon", "aria-hidden": "true" }, "📭"),
      h("p", { className: "ct2-empty__message" }, message || "Nenhum resultado encontrado"),
    );
  }

  function SkeletonProjectCards(_a) {
    var count = _a.count;
    return Array.from({ length: count }, function (_, i) {
      return h("div", { key: i, className: "ct2-project-card ct2-project-card--skeleton", "aria-hidden": "true" },
        h("span", { className: "ct2-skeleton ct2-skeleton--title" }),
        h("span", { className: "ct2-skeleton ct2-skeleton--meta" }),
        h("span", { className: "ct2-skeleton ct2-skeleton--meta ct2-skeleton--short" }),
      );
    });
  }

  function SkeletonTableRows(_a) {
    var count = _a.count;
    return Array.from({ length: count }, function (_, i) {
      return h("tr", { key: i, className: "ct2-task-row", "aria-hidden": "true" },
        h("td", null, h("span", { className: "ct2-skeleton ct2-skeleton--inline" })),
        h("td", null, h("span", { className: "ct2-skeleton ct2-skeleton--inline" })),
        h("td", null, h("span", { className: "ct2-skeleton ct2-skeleton--inline" })),
        h("td", null, h("span", { className: "ct2-skeleton ct2-skeleton--meta ct2-skeleton--short" })),
        h("td", null, h("span", { className: "ct2-skeleton ct2-skeleton--meta ct2-skeleton--short" })),
        h("td", null, h("span", { className: "ct2-skeleton ct2-skeleton--meta ct2-skeleton--short" })),
        h("td", null, h("span", { className: "ct2-skeleton ct2-skeleton--meta ct2-skeleton--short" })),
        h("td", null, h("span", { className: "ct2-skeleton ct2-skeleton--meta ct2-skeleton--short" })),
      );
    });
  }

  function SkeletonAuditEntries(_a) {
    var count = _a.count;
    return Array.from({ length: count }, function (_, i) {
      return h("div", { key: i, className: "ct2-audit-entry ct2-audit-entry--skeleton", "aria-hidden": "true" },
        h("div", { className: "ct2-audit-entry__dot" }),
        h("div", { className: "ct2-audit-entry__body" },
          h("span", { className: "ct2-skeleton ct2-skeleton--title" }),
          h("span", { className: "ct2-skeleton ct2-skeleton--meta" }),
        ),
      );
    });
  }

  function StatsBar(_a) {
    var stats = _a.stats;
    if (!stats) return null;
    var s = stats.tasks_by_status || {};
    var items = [
      { label: "Total",       value: stats.total_tasks || 0 },
      { label: "Todo",        value: s.todo || 0 },
      { label: "In Progress", value: s.in_progress || 0, mod: "in_progress" },
      { label: "Done",        value: s.done || 0,        mod: "done" },
      { label: "Blocked",     value: s.blocked || 0,     mod: "blocked" },
    ];
    return h("div", { className: "ct2-stats-bar", role: "region", "aria-label": "Resumo" },
      items.map(function (_a2) {
        var label = _a2.label, value = _a2.value, mod = _a2.mod;
        return h("div", { className: "ct2-stat", key: label },
          h("span", { className: "ct2-stat__value" + (mod ? " ct2-stat__value--" + mod : "") }, String(value)),
          h("span", { className: "ct2-stat__label" }, label),
        );
      })
    );
  }

  function ProjectSelect(_a) {
    var projects = _a.projects, value = _a.value, onChange = _a.onChange, withAll = _a.withAll;
    return h("select", {
      className: "ct2-select",
      value: value,
      onChange: function (e) { return onChange(e.target.value); },
      "aria-label": "Selecionar projeto",
    },
      withAll && h("option", { value: "" }, "Todos os projetos"),
      projects.map(function (p) { return h("option", { key: p.slug, value: p.slug }, p.name); }),
    );
  }

  // ---- Tab 1: Projetos -------------------------------------------------------

  function ProjectsTab(_a) {
    var selectedSlug = _a.selectedSlug, onSelect = _a.onSelect;
    var _b = useState(null), projects = _b[0], setProjects = _b[1];
    var _c = useState(null), stats = _c[0], setStats = _c[1];
    var _d = useState(true), loading = _d[0], setLoading = _d[1];
    var _e = useState(null), error = _e[0], setError = _e[1];

    var load = useCallback(function () {
      setLoading(true);
      setError(null);
      Promise.all([
        SDK.fetchJSON(API + "/projects"),
        SDK.fetchJSON(API + "/stats"),
      ])
        .then(function (_a2) { var p = _a2[0], s = _a2[1]; setProjects(Array.isArray(p) ? p : []); setStats(s); })
        .catch(function (err) { return setError(err.message || String(err)); })
        .finally(function () { return setLoading(false); });
    }, []);

    useEffect(function () { load(); }, [load]);

    return h("div", { className: "ct2-tab-content" },
      error
        ? h(ErrorBanner, { message: "Falha ao carregar projetos: " + error, onRetry: load })
        : h(StatsBar, { stats: stats }),

      h("div", {
        className: "ct2-projects-grid",
        role: "list",
        "aria-label": "Projetos",
        "aria-busy": loading ? "true" : "false",
      },
        loading && !projects
          ? h(SkeletonProjectCards, { count: 6 })
          : projects && projects.length === 0
            ? h("div", { style: { gridColumn: "1 / -1" } }, h(EmptyState, { message: "Nenhum projeto encontrado" }))
            : (projects || []).map(function (proj) {
                return h("div", {
                  key: proj.slug || proj.id,
                  className: "ct2-project-card" + (selectedSlug === proj.slug ? " ct2-project-card--selected" : ""),
                  role: "listitem",
                  tabIndex: 0,
                  "aria-selected": selectedSlug === proj.slug,
                  onClick: function () { return onSelect(proj.slug); },
                  onKeyDown: function (e) {
                    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(proj.slug); }
                  },
                },
                  h("div", { className: "ct2-project-card__header" },
                    h("span", { className: "ct2-project-card__name", title: proj.name }, proj.name),
                    proj.stack && h("span", { className: "ct2-badge ct2-badge--stack" }, proj.stack),
                  ),
                  h("div", { className: "ct2-project-card__meta" },
                    h("span", { className: "ct2-project-card__tasks" }, (proj.task_count || 0) + " tasks"),
                    proj.last_scan && h("span", { className: "ct2-project-card__scan" }, "scan " + fmtDate(proj.last_scan)),
                  ),
                );
              })
      ),
    );
  }

  // ---- Tab 2: Tasks (detailed, grouped by day) -------------------------------

  function TasksTab(_a) {
    var selectedSlug = _a.selectedSlug, projects = _a.projects;
    var _b = useState(null), tasks = _b[0], setTasks = _b[1];
    var _c = useState(false), loading = _c[0], setLoading = _c[1];
    var _d = useState(null), error = _d[0], setError = _d[1];
    var _e = useState(selectedSlug || ""), projectSlug = _e[0], setProjectSlug = _e[1];
    var _f = useState(""), statusFilter = _f[0], setStatusFilter = _f[1];
    var _g = useState({}), expanded = _g[0], setExpanded = _g[1];
    var _mdTask = useState(null), modalTask = _mdTask[0], setModalTask = _mdTask[1];
    var _mdLoad = useState(false), mdLoading = _mdLoad[0], setMdLoading = _mdLoad[1];

    useEffect(function () { if (selectedSlug) setProjectSlug(selectedSlug); }, [selectedSlug]);

    var load = useCallback(function () {
      if (!projectSlug) { setTasks([]); return; }
      setLoading(true);
      setError(null);
      var url = API + "/projects/" + projectSlug + "/tasks?limit=200";
      if (statusFilter) url += "&status=" + encodeURIComponent(statusFilter);
      SDK.fetchJSON(url)
        .then(function (data) { return setTasks(Array.isArray(data) ? data : (data.tasks || [])); })
        .catch(function (err) { return setError(err.message || String(err)); })
        .finally(function () { return setLoading(false); });
    }, [projectSlug, statusFilter]);

    useEffect(function () { load(); }, [load]);

    // Group tasks by day
    var dayGroups = useMemo(function () {
      var rows = tasks || [];
      if (rows.length === 0) return [];
      var map = {};
      rows.forEach(function (t) {
        var day = t.day || "Sem data";
        if (!map[day]) map[day] = [];
        map[day].push(t);
      });
      var days = Object.keys(map).sort(function (a, b) {
        if (a === "Sem data") return 1;
        if (b === "Sem data") return -1;
        return b.localeCompare(a);
      });
      return days.map(function (day) {
        return { day: day, tasks: map[day] };
      });
    }, [tasks]);

    function toggleDay(day) {
      setExpanded(function (prev) {
        var next = Object.assign({}, prev);
        next[day] = !prev[day];
        return next;
      });
    }

    function isExpanded(day) {
      return expanded[day] !== false;
    }

    function openTaskMd(t) {
      var slug = projectSlug || t._project || t.project_slug || "";
      var taskNumber = t.task_number;
      if (!slug || !taskNumber) return;
      // Abrir na mesma aba — botão "Voltar" na página retorna ao Dashboard
      window.open(API + "/tasks/" + slug + "/" + taskNumber, "_self");
    }

    function openAuditDetail(a) {
      var auditId = a.id;
      if (!auditId) return;
      window.open(API + "/auditorias/" + auditId, "_blank");
    }

    function renderTaskRow(t, i) {
      var commitHash = t.commit_hash && t.commit_hash !== "—" ? shortHash(t.commit_hash) : null;
      var agentName = t.agent ? t.agent.split("(")[0].trim() : null;
      var motorName = t.motor ? t.motor.split("—")[0].trim().substring(0, 25) : null;
      return h("tr", {
        className: "ct2-task-row",
        key: t.id || i,
        style: { cursor: "pointer" },
        title: "Clique para ver detalhes da task",
        onClick: function () { return openTaskMd(t); },
      },
        h("td", { className: "ct2-task-id" },
          h("code", null, t.task_number || t.id || "—")),
        h("td", { className: "ct2-task-title", title: t.title },
          truncate(t.title, 55)),
        h("td", null,
          h("span", { className: statusBadgeClass(t.status) }, formatStatus(t.status))),
        h("td", { className: "ct2-task-agent" },
          agentName || "—"),
        h("td", { className: "ct2-task-motor" },
          motorName || "—"),
        h("td", { className: "ct2-task-exec" },
          t.status_execucao === "✅" ? "✅" : "⬜"),
        h("td", { className: "ct2-task-audit" },
          t.status_auditoria === "👁" ? "👁" : "⬜"),
        h("td", { className: "ct2-task-commit" },
          commitHash ? h("code", { title: t.commit_hash }, commitHash) : "—"),
        h("td", { className: "ct2-task-date" },
          t.data_conclusao ? fmtDate(t.data_conclusao) : "—"),
      );
    }

    var totalTasks = (tasks || []).length;
    var doneTasks = (tasks || []).filter(function (t) { return t.status_execucao === "✅"; }).length;
    var auditedTasks = (tasks || []).filter(function (t) { return t.status_auditoria === "👁"; }).length;

    return h("div", { className: "ct2-tab-content" },
      // Filters bar
      h("div", { className: "ct2-filters-bar" },
        projects.length > 0 && h("label", { className: "ct2-filter-label" },
          "Projeto ",
          h(ProjectSelect, { projects: projects, value: projectSlug, onChange: setProjectSlug, withAll: true }),
        ),
        h("label", { className: "ct2-filter-label" },
          "Status ",
          h("select", {
            className: "ct2-select ct2-select--sm",
            value: statusFilter,
            onChange: function (e) { return setStatusFilter(e.target.value); },
            "aria-label": "Filtrar por status",
          },
            h("option", { value: "" }, "Todos"),
            h("option", { value: "todo" }, "Todo"),
            h("option", { value: "in_progress" }, "In Progress"),
            h("option", { value: "done" }, "Done"),
            h("option", { value: "blocked" }, "Blocked"),
          ),
        ),
        !loading && totalTasks > 0 && h("span", { className: "ct2-tasks-summary" },
          totalTasks + " tasks · " + doneTasks + " ✅ · " + auditedTasks + " 👁"),
      ),

      // Content
      error
        ? h(ErrorBanner, { message: "Falha ao carregar tasks: " + error, onRetry: load })
        : !projectSlug
          ? h(EmptyState, { message: "Selecione um projeto para ver as tasks" })
          : loading
            ? h("div", { className: "ct2-task-table-wrap" },
                h("table", { className: "ct2-task-table" },
                  h("thead", null, h("tr", null,
                    h("th", null, "#"), h("th", null, "Título"), h("th", null, "Status"),
                    h("th", null, "Agente"), h("th", null, "Motor"),
                    h("th", null, "Exec"), h("th", null, "Audit"),
                    h("th", null, "Commit"), h("th", null, "Concluído"))),
                  h("tbody", null, h(SkeletonTableRows, { count: 5 })),
                ),
              )
            : dayGroups.length === 0
              ? h(EmptyState, { message: "Nenhuma task encontrada" })
              : h("div", { className: "ct2-day-groups" },
                  dayGroups.map(function (group) {
                    return h("div", { key: group.day, className: "ct2-day-group" },
                      h("button", {
                        className: "ct2-day-header",
                        onClick: function () { return toggleDay(group.day); },
                      },
                        h("span", { className: "ct2-day-header__arrow" }, isExpanded(group.day) ? "▾" : "▸"),
                        h("span", { className: "ct2-day-header__date" }, fmtDate(group.day)),
                        h("span", { className: "ct2-day-header__count" },
                          group.tasks.length + " tasks" +
                          " · " + group.tasks.filter(function (t) { return t.status_execucao === "✅"; }).length + " ✅" +
                          " · " + group.tasks.filter(function (t) { return t.status_auditoria === "👁"; }).length + " 👁"),
                      ),
                      isExpanded(group.day) && h("div", { className: "ct2-task-table-wrap" },
                        h("table", { className: "ct2-task-table" },
                          h("thead", null, h("tr", null,
                            h("th", { scope: "col" }, "#"),
                            h("th", { scope: "col" }, "Título"),
                            h("th", { scope: "col" }, "Status"),
                            h("th", { scope: "col" }, "Agente"),
                            h("th", { scope: "col" }, "Motor"),
                            h("th", { scope: "col", title: "Execução" }, "Ex"),
                            h("th", { scope: "col", title: "Auditoria" }, "Au"),
                            h("th", { scope: "col" }, "Commit"),
                            h("th", { scope: "col" }, "Concluído"),
                          )),
                          h("tbody", null, group.tasks.map(function (t, i) { return renderTaskRow(t, i); })),
                        ),
                      ),
                    );
                  })
                ),
      // Markdown Modal
      modalTask && h("div", {
        className: "ct2-modal-overlay",
        style: { position: "fixed", top: 0, left: 0, right: 0, bottom: 0, background: "rgba(0,0,0,0.6)", zIndex: 9999, display: "flex", alignItems: "center", justifyContent: "center" },
        onClick: function () { setModalTask(null); },
      },
        h("div", {
          className: "ct2-modal",
          style: { background: "var(--color-bg, #fff)", borderRadius: 12, maxWidth: 800, width: "90%", maxHeight: "85vh", overflow: "auto", padding: 24, position: "relative" },
          onClick: function (e) { e.stopPropagation(); },
        },
          h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 } },
            h("h3", { style: { margin: 0, fontSize: 16 } }, modalTask.title || "Detalhes da Task"),
            h("button", {
              onClick: function () { setModalTask(null); },
              style: { background: "none", border: "none", fontSize: 20, cursor: "pointer", padding: "4px 8px", borderRadius: 4 },
            }, "✕")
          ),
          mdLoading
            ? h("div", { style: { textAlign: "center", padding: 40 } }, "Carregando...")
            : modalTask.content
              ? h("div", {
                  className: "ct2-markdown-body",
                  style: { lineHeight: 1.7, fontSize: 14 },
                  dangerouslySetInnerHTML: { __html: renderMarkdown(modalTask.content) },
                })
              : h("div", { style: { textAlign: "center", padding: 40, color: "#999" } }, "Conteúdo não disponível")
        )
      ),
    );
  }

  // ---- Tab 3: Auditorias -----------------------------------------------------

  function AuditoriasTab(_a) {
    var selectedSlug = _a.selectedSlug, projects = _a.projects;
    var _b = useState(null), audits = _b[0], setAudits = _b[1];
    var _c = useState(false), loading = _c[0], setLoading = _c[1];
    var _d = useState(null), error = _d[0], setError = _d[1];
    var _e = useState(selectedSlug || ""), projectSlug = _e[0], setProjectSlug = _e[1];
    var _f = useState(20), limit = _f[0], setLimit = _f[1];

    useEffect(function () { if (selectedSlug) setProjectSlug(selectedSlug); }, [selectedSlug]);

    var load = useCallback(function () {
      if (!projectSlug) { setAudits([]); return; }
      setLoading(true);
      setError(null);
      SDK.fetchJSON(API + "/projects/" + projectSlug + "/auditorias?limit=" + limit)
        .then(function (data) { return setAudits(Array.isArray(data) ? data : (data.auditorias || data.items || [])); })
        .catch(function (err) { return setError(err.message || String(err)); })
        .finally(function () { return setLoading(false); });
    }, [projectSlug, limit]);

    useEffect(function () { load(); }, [load]);

    var rows = audits || [];

    return h("div", { className: "ct2-tab-content" },
      h("div", { className: "ct2-filters-bar" },
        projects.length > 0 && h("label", { className: "ct2-filter-label" },
          "Projeto ",
          h(ProjectSelect, { projects: projects, value: projectSlug, onChange: setProjectSlug, withAll: true }),
        ),
        h("label", { className: "ct2-filter-label" },
          "Limite ",
          h("select", {
            className: "ct2-select ct2-select--sm",
            value: limit,
            onChange: function (e) { return setLimit(Number(e.target.value)); },
            "aria-label": "Limite de auditorias",
          },
            h("option", { value: 10 }, "10"),
            h("option", { value: 20 }, "20"),
            h("option", { value: 50 }, "50"),
          ),
        ),
      ),

      !projectSlug
        ? h(EmptyState, { message: "Selecione um projeto para ver as auditorias" })
        : error
          ? h(ErrorBanner, { message: "Falha ao carregar auditorias: " + error, onRetry: load })
          : !loading && h("div", { className: "ct2-audit-header" },
              h("span", { className: "ct2-audit-header__label" },
                "Exibindo " + rows.length + " de " + limit + " auditorias"),
            ),

      h("div", {
        className: "ct2-audit-timeline",
        role: "list",
        "aria-label": "Timeline de auditorias",
        "aria-busy": loading ? "true" : "false",
      },
        loading && !audits
          ? h(SkeletonAuditEntries, { count: 5 })
          : rows.length === 0 && !loading
            ? h(EmptyState, { message: "Nenhuma auditoria encontrada" })
            : rows.map(function (a, i) {
                return h("div", { key: a.id || i, className: "ct2-audit-entry", role: "listitem", style: { cursor: "pointer" }, title: "Clique para detalhes da auditoria", onClick: function () { return openAuditDetail(a); } },
                  h("div", { className: "ct2-audit-entry__dot", "aria-hidden": "true" }),
                  h("div", { className: "ct2-audit-entry__body" },
                    h("div", { className: "ct2-audit-entry__header" },
                      h("span", { className: "ct2-audit-entry__project" }, a.project_slug || projectSlug),
                      a.veredito && h("span", { className: "ct2-badge ct2-badge--" + (a.veredito === "aprovado" ? "green" : a.veredito === "rejeitado" ? "red" : "yellow") }, a.veredito),
                      a.audit_hash && h("code", { className: "ct2-audit-entry__commit" }, shortHash(a.audit_hash)),
                    ),
                    a.task_title && h("div", { className: "ct2-audit-entry__task" }, truncate(a.task_title, 80)),
                    h("div", { className: "ct2-audit-entry__meta" },
                      h("span", null, fmtDate(a.created_at)),
                      a.observacoes && h("span", null, truncate(a.observacoes, 60)),
                    ),
                  ),
                );
              }),
      ),
    );
  }

  // ---- Root component --------------------------------------------------------

  function ControlTowerDashboard() {
    var _a = useState("projetos"), activeTab = _a[0], setActiveTab = _a[1];
    var _b = useState(null), selectedSlug = _b[0], setSelectedSlug = _b[1];
    var _c = useState([]), projects = _c[0], setProjects = _c[1];

    useEffect(function () {
      SDK.fetchJSON(API + "/projects")
        .then(function (data) { return setProjects(Array.isArray(data) ? data : []); })
        .catch(function () {});
    }, []);

    function handleProjectSelect(slug) {
      setSelectedSlug(slug);
      setActiveTab("tasks");
    }

    var TABS = [
      { id: "projetos",   label: "Projetos" },
      { id: "tasks",      label: "Tasks" },
    ];

    return h("div", { className: "ct2-dashboard" },
      h("div", { className: "ct2-header" },
        h("h2", { className: "ct2-header__title" }, "Control Tower V2"),
        selectedSlug && h("span", { className: "ct2-header__project" }, selectedSlug),
      ),

      h("div", { className: "ct2-tabs", role: "tablist", "aria-label": "CT2 views" },
        TABS.map(function (tab) {
          return h("button", {
            key: tab.id,
            className: "ct2-tab" + (activeTab === tab.id ? " ct2-tab--active" : ""),
            role: "tab",
            "aria-selected": activeTab === tab.id,
            "aria-controls": "ct2-panel-" + tab.id,
            id: "ct2-tab-" + tab.id,
            onClick: function () { return setActiveTab(tab.id); },
          }, tab.label);
        }),
      ),

      h("div", {
        className: "ct2-panel",
        id: "ct2-panel-" + activeTab,
        role: "tabpanel",
        "aria-labelledby": "ct2-tab-" + activeTab,
      },
        activeTab === "projetos"   && h(ProjectsTab,   { selectedSlug: selectedSlug, onSelect: handleProjectSelect }),
        activeTab === "tasks"      && h(TasksTab,      { selectedSlug: selectedSlug, projects: projects }),
      ),
    );
  }

  // ---- Register --------------------------------------------------------------

  if (window.__HERMES_PLUGINS__ && window.__HERMES_PLUGINS__.register) {
    window.__HERMES_PLUGINS__.register("ct2", ControlTowerDashboard);
  }

})();
