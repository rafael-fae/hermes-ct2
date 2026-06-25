/**
 * CT2 Tasks Plugin — Aba de Tasks (multi-projeto)
 *
 * Adiciona a aba "📋 Tasks" no dashboard CT2 com dois modos de visualização:
 *   1. 📋 Por Sprint — agrupado por sprint → dia → wave (original)
 *   2. 📅 Por Dia — agrupado apenas por dia, multi-projeto, com detalhes
 *
 * Dados via API: /api/projects/<slug>/tasks + /api/projects/<slug>/sprints
 *
 * @version 2.0.0
 */
(function () {
  'use strict';

  const PLUGIN_NAME = 'ct2-tasks';
  const TAB_ID = 'tasks';
  const TAB_LABEL = '📋 Tasks';
  const DEFAULT_PROJECT = 'oeste-gestao';

  let initialized = false;

  // ─── Register tab ────────────────────────────────────
  function registerTasksTab() {
    document.dispatchEvent(new CustomEvent('hermes:register-tab', {
      detail: {
        id: TAB_ID,
        label: TAB_LABEL,
        url: '/tasks',
        plugin: PLUGIN_NAME,
        sortOrder: 5,
        icon: '📋',
      },
      bubbles: true,
    }));
  }

  // ─── Alpine component ────────────────────────────────
  function defineTasksComponent() {
    if (typeof Alpine === 'undefined') return;
    if (Alpine._ct2TasksDefined) return;
    Alpine._ct2TasksDefined = true;

    Alpine.data('ct2Tasks', () => ({
      allTasks: [],
      sprints: [],
      agents: [],
      projects: [],
      loading: false,
      error: null,

      // View mode: 'sprint' | 'day'
      viewMode: 'day',

      // Filters
      filterProject: 'all',
      filterSprint: 'all',
      filterStatus: 'all',
      filterAgent: 'all',
      filterExecucao: 'all',
      filterAuditoria: 'all',

      // Init
      init() {
        this.loadData();
      },

      // ── Load data from CT2 API (multi-project) ──
      async loadData() {
        this.loading = true;
        this.error = null;
        try {
          // Load projects list
          const projRes = await fetch('/api/projects');
          if (!projRes.ok) throw new Error('API indisponível (HTTP ' + projRes.status + ')');
          this.projects = await projRes.json();

          // Load tasks + sprints for each project
          const allTasks = [];
          const allSprints = [];
          const allAgents = new Set();

          for (const proj of this.projects) {
            try {
              const [tasksRes, sprintsRes] = await Promise.all([
                fetch('/api/projects/' + proj.slug + '/tasks?limit=2000'),
                fetch('/api/projects/' + proj.slug + '/sprints'),
              ]);
              if (tasksRes.ok) {
                const tasks = await tasksRes.json();
                tasks.forEach(t => {
                  t._project = proj.slug;
                  t._projectName = proj.name || proj.slug;
                  if (t.agent && t.agent.trim()) allAgents.add(t.agent);
                });
                allTasks.push(...tasks);
              }
              if (sprintsRes.ok) {
                const sprints = await sprintsRes.json();
                sprints.forEach(s => { s._project = proj.slug; });
                allSprints.push(...sprints);
              }
            } catch (e) {
              console.warn('[' + PLUGIN_NAME + '] Erro ' + proj.slug + ':', e.message);
            }
          }

          this.allTasks = allTasks;
          this.sprints = allSprints;
          this.agents = Array.from(allAgents).sort();
        } catch (e) {
          console.error('[' + PLUGIN_NAME + '] Error:', e);
          this.error = e.message;
        } finally {
          this.loading = false;
        }
      },

      // ── Unique projects for filter ──
      get uniqueProjects() {
        const seen = new Set();
        return this.allTasks.filter(t => {
          const k = t._project;
          if (seen.has(k)) return false;
          seen.add(k);
          return true;
        }).map(t => ({ slug: t._project, name: t._projectName }));
      },

      // ── Filtered tasks ──
      get filteredTasks() {
        let tasks = this.allTasks;
        if (this.filterProject !== 'all')
          tasks = tasks.filter(t => t._project === this.filterProject);
        if (this.filterSprint !== 'all')
          tasks = tasks.filter(t => String(t.sprint_number) === String(this.filterSprint));
        if (this.filterStatus !== 'all')
          tasks = tasks.filter(t => t.status === this.filterStatus);
        if (this.filterAgent !== 'all')
          tasks = tasks.filter(t => t.agent === this.filterAgent);
        if (this.filterExecucao !== 'all')
          tasks = tasks.filter(t => t.status_execucao === this.filterExecucao);
        if (this.filterAuditoria !== 'all')
          tasks = tasks.filter(t => t.status_auditoria === this.filterAuditoria);
        return tasks;
      },

      // ── SPRINT view: group by sprint → day → wave ──
      get sprintGroups() {
        const tasks = this.filteredTasks;
        const sprintMap = {};
        const sprintInfo = {};
        this.sprints.forEach(s => { sprintInfo[s.number] = s; });

        tasks.forEach(task => {
          const sNum = task.sprint_number || 0;
          if (!sprintMap[sNum]) sprintMap[sNum] = [];
          sprintMap[sNum].push(task);
        });

        const groups = [];
        const sortedNums = Object.keys(sprintMap).map(Number).sort((a, b) => b - a);

        sortedNums.forEach(sNum => {
          const sprintTasks = sprintMap[sNum];
          const info = sprintInfo[sNum] || { title: '', status: 'unknown' };

          const dayMap = {};
          sprintTasks.forEach(task => {
            const day = task.day || 'Sem data';
            if (!dayMap[day]) dayMap[day] = [];
            dayMap[day].push(task);
          });

          const dayGroups = [];
          const sortedDays = Object.keys(dayMap).sort((a, b) => b.localeCompare(a));

          sortedDays.forEach(day => {
            const dayTasks = dayMap[day];
            const waveMap = {};
            dayTasks.forEach(task => {
              const wNum = task.wave_number || 0;
              if (!waveMap[wNum]) waveMap[wNum] = [];
              waveMap[wNum].push(task);
            });

            const waveGroups = [];
            Object.keys(waveMap).map(Number).sort((a, b) => a - b).forEach(wNum => {
              waveGroups.push({ waveNumber: wNum, tasks: waveMap[wNum] });
            });

            dayGroups.push({
              day,
              expanded: true,
              waveGroups,
              counts: {
                total: dayTasks.length,
                executed: dayTasks.filter(t => t.status_execucao === '✅').length,
                audited: dayTasks.filter(t => t.status_auditoria === '👁').length,
              },
            });
          });

          groups.push({
            sprintNumber: sNum,
            sprintTitle: info.title || '',
            sprintStatus: info.status || '',
            expanded: info.status === 'active',
            dayGroups,
            counts: {
              total: sprintTasks.length,
              done: sprintTasks.filter(t => t.status === 'done').length,
              audited: sprintTasks.filter(t => t.status_auditoria === '👁').length,
            },
          });
        });
        return groups;
      },

      // ── DAY view: group by day only (flat, multi-project) ──
      get dayGroups() {
        const tasks = this.filteredTasks;
        const dayMap = {};

        tasks.forEach(task => {
          const day = task.day || 'Sem data';
          if (!dayMap[day]) dayMap[day] = [];
          dayMap[day].push(task);
        });

        const groups = [];
        const sortedDays = Object.keys(dayMap).sort((a, b) => {
          if (a === 'Sem data') return 1;
          if (b === 'Sem data') return -1;
          return b.localeCompare(a);
        });

        sortedDays.forEach(day => {
          const dayTasks = dayMap[day];

          // Group by project within day
          const projMap = {};
          dayTasks.forEach(task => {
            const key = task._project || '?';
            if (!projMap[key]) projMap[key] = { slug: key, name: task._projectName || key, tasks: [] };
            projMap[key].tasks.push(task);

            // Sort tasks within project by task_number
            projMap[key].tasks.sort((a, b) => (a.task_number || 0) - (b.task_number || 0));
          });

          const executed = dayTasks.filter(t => t.status_execucao === '✅').length;
          const audited = dayTasks.filter(t => t.status_auditoria === '👁').length;

          groups.push({
            day,
            expanded: groups.length < 3,
            projects: Object.values(projMap).sort((a, b) => a.name.localeCompare(b.name)),
            counts: { total: dayTasks.length, executed, audited },
          });
        });
        return groups;
      },

      // ── Helpers ──
      formatDay(dayStr) {
        if (!dayStr || dayStr === 'Sem data') return 'Sem data';
        const parts = dayStr.split('-');
        if (parts.length === 3) {
          const meses = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];
          const m = parseInt(parts[1], 10) - 1;
          return parts[2] + ' ' + (meses[m] || parts[1]) + ' ' + parts[0];
        }
        return parts.length === 3 ? parts[2] + '/' + parts[1] + '/' + parts[0] : dayStr;
      },

      formatHash(hash) {
        if (!hash || hash === '—') return '—';
        return String(hash).replace(/[`']/g, '').trim().substring(0, 7);
      },

      statusBadge(status) {
        const s = (status || '').toLowerCase();
        if (s === 'done') return 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400 border-green-200 dark:border-green-500/30';
        if (s === 'in_progress') return 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400 border-blue-200 dark:border-blue-500/30';
        if (s === 'blocked') return 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400 border-red-200 dark:border-red-500/30';
        return 'bg-neutral-100 text-neutral-500 dark:bg-neutral-800 dark:text-neutral-400 border-neutral-200 dark:border-neutral-700';
      },

      statusLabel(status) {
        const s = (status || '').toLowerCase();
        if (s === 'done') return 'Concluída';
        if (s === 'in_progress') return 'Em execução';
        if (s === 'blocked') return 'Bloqueada';
        return 'A fazer';
      },

      toggleDay(day) {
        const g = this.dayGroups.find(d => d.day === day);
        if (g) g.expanded = !g.expanded;
      },

      totalFiltered() { return this.filteredTasks.length; },
      totalExecuted() { return this.filteredTasks.filter(t => t.status_execucao === '✅').length; },
      totalAudited() { return this.filteredTasks.filter(t => t.status_auditoria === '👁').length; },
    }));
  }

  // ─── Inject into dashboard ───────────────────────────
  function injectIntoDashboard() {
    if (initialized) return;
    initialized = true;

    const root = document.querySelector('[x-data="app()"]');
    if (!root || typeof Alpine === 'undefined') {
      setTimeout(injectIntoDashboard, 200);
      return;
    }

    try {
      const data = Alpine.$data(root);
      if (!data || !data.tabs) {
        setTimeout(injectIntoDashboard, 200);
        return;
      }

      const exists = data.tabs.some(t => t.id === TAB_ID);
      if (!exists) {
        data.tabs.push({ id: TAB_ID, label: TAB_LABEL });
      }

      if (document.getElementById('ct2-tasks-container')) return;

      const container = document.createElement('div');
      container.id = 'ct2-tasks-container';
      container.setAttribute('x-show', "activeTab === '" + TAB_ID + "'");
      container.setAttribute('x-data', 'ct2Tasks()');
      container.setAttribute('x-init', 'init()');
      container.innerHTML = buildTemplate();

      const progressModule = root.querySelector('.mt-4.bg-white.dark\\:bg-neutral-900\\/50');
      const projectDiv = root.querySelector('[x-show="activeProject === p.slug"]') ||
                         root.querySelector('[x-show^="activeProject"]');

      if (progressModule && progressModule.parentNode) {
        progressModule.parentNode.insertBefore(container, progressModule);
      } else if (projectDiv && projectDiv.parentNode) {
        projectDiv.parentNode.appendChild(container);
      } else {
        root.appendChild(container);
      }

      console.log('[' + PLUGIN_NAME + '] v2 Tab + content injected ✅');
    } catch (e) {
      console.error('[' + PLUGIN_NAME + '] Inject error:', e);
      setTimeout(injectIntoDashboard, 500);
    }
  }

  // ─── Build HTML template ──────────────────────────────
  function buildTemplate() {
    return `
    <div class="bg-white dark:bg-neutral-900/50 border border-neutral-200 dark:border-neutral-800/80 rounded-xl p-5 mb-6">
      <!-- Header + Toggle -->
      <div class="flex flex-wrap items-start justify-between gap-3 mb-4">
        <div>
          <h3 class="text-sm font-bold text-neutral-900 dark:text-white flex items-center gap-2">
            <span class="w-5 h-5 rounded bg-primary-100 dark:bg-primary-600/20 flex items-center justify-center text-xs">📋</span>
            Tasks
            <span x-show="!loading" class="px-2 py-0.5 rounded-full bg-primary-100 dark:bg-primary-600/20 text-primary-700 dark:text-primary-300 text-[10px] font-mono" x-text="filteredTasks.length + ' tasks'"></span>
          </h3>
          <p class="text-xs text-neutral-500 mt-1" x-show="viewMode === 'sprint'">Agrupadas por sprint → dia → wave</p>
          <p class="text-xs text-neutral-500 mt-1" x-show="viewMode === 'day'">
            <span x-text="totalFiltered()"></span> tasks ·
            <span class="text-green-600 dark:text-green-400" x-text="totalExecuted()"></span> ✅ ·
            <span class="text-amber-600 dark:text-amber-400" x-text="totalAudited()"></span> 👁
          </p>
        </div>
        <div class="flex items-center gap-2">
          <!-- View toggle -->
          <div class="flex bg-neutral-100 dark:bg-neutral-800 rounded-lg p-0.5 border border-neutral-200 dark:border-neutral-700">
            <button @click="viewMode = 'sprint'"
              :class="viewMode === 'sprint' ? 'bg-white dark:bg-neutral-700 shadow-sm text-neutral-900 dark:text-white' : 'text-neutral-500 hover:text-neutral-700 dark:hover:text-neutral-300'"
              class="px-3 py-1 text-xs font-medium rounded-md transition-all">📋 Sprint</button>
            <button @click="viewMode = 'day'"
              :class="viewMode === 'day' ? 'bg-white dark:bg-neutral-700 shadow-sm text-neutral-900 dark:text-white' : 'text-neutral-500 hover:text-neutral-700 dark:hover:text-neutral-300'"
              class="px-3 py-1 text-xs font-medium rounded-md transition-all">📅 Dia</button>
          </div>
          <button @click="loadData()" :disabled="loading"
            class="px-3 py-1.5 text-xs font-medium rounded-lg border border-neutral-200 dark:border-neutral-700 text-neutral-600 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800 transition-colors disabled:opacity-50">
            <span x-show="!loading">↻ Atualizar</span>
            <span x-show="loading" class="flex items-center gap-1.5">
              <span class="w-3 h-3 border-2 border-neutral-300 border-t-primary-500 rounded-full animate-spin"></span>
              Carregando...
            </span>
          </button>
        </div>
      </div>

      <!-- Filters -->
      <div class="flex flex-wrap items-center gap-2 mb-4 pb-4 border-b border-neutral-100 dark:border-neutral-800">
        <!-- Project filter (day view only) -->
        <select x-show="viewMode === 'day'" x-model="filterProject"
          class="text-xs bg-neutral-100 dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700/60 text-neutral-700 dark:text-neutral-200 rounded-lg px-2 py-1.5 pr-6 appearance-none cursor-pointer focus:outline-none focus:ring-2 focus:ring-primary-500/40">
          <option value="all">🏢 Todos os projetos</option>
          <template x-for="p in uniqueProjects" :key="p.slug">
            <option :value="p.slug" x-text="p.name"></option>
          </template>
        </select>

        <!-- Sprint filter (sprint view only) -->
        <select x-show="viewMode === 'sprint'" x-model="filterSprint"
          class="text-xs bg-neutral-100 dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700/60 text-neutral-700 dark:text-neutral-200 rounded-lg px-2 py-1.5 pr-6 appearance-none cursor-pointer focus:outline-none focus:ring-2 focus:ring-primary-500/40">
          <option value="all">Todas as Sprints</option>
          <template x-for="s in sprints" :key="s.id">
            <option :value="s.number" x-text="'Sprint ' + s.number + (s.status === 'active' ? ' 🟢' : ' ✅')"></option>
          </template>
        </select>

        <select x-model="filterStatus"
          class="text-xs bg-neutral-100 dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700/60 text-neutral-700 dark:text-neutral-200 rounded-lg px-2 py-1.5 pr-6 appearance-none cursor-pointer focus:outline-none focus:ring-2 focus:ring-primary-500/40">
          <option value="all">Todos os Status</option>
          <option value="todo">⬜ A fazer</option>
          <option value="in_progress">🔄 Em execução</option>
          <option value="done">✅ Concluída</option>
          <option value="blocked">🚫 Bloqueada</option>
        </select>

        <select x-model="filterAgent"
          class="text-xs bg-neutral-100 dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700/60 text-neutral-700 dark:text-neutral-200 rounded-lg px-2 py-1.5 pr-6 appearance-none cursor-pointer focus:outline-none focus:ring-2 focus:ring-primary-500/40">
          <option value="all">Todos os Agentes</option>
          <template x-for="a in agents" :key="a">
            <option :value="a" x-text="'👤 ' + a"></option>
          </template>
        </select>

        <button @click="filterExecucao = (filterExecucao === 'all' ? '✅' : filterExecucao === '✅' ? '⬜' : 'all')"
          :class="filterExecucao === 'all' ? 'bg-neutral-100 dark:bg-neutral-800 border-neutral-300 dark:border-neutral-600' : filterExecucao === '✅' ? 'bg-green-100 dark:bg-green-500/20 border-green-300 dark:border-green-500/50 text-green-700 dark:text-green-300' : 'bg-neutral-100 dark:bg-neutral-800/50 border-neutral-200 dark:border-neutral-700/50 text-neutral-500'"
          class="px-2 py-1 text-[11px] font-medium rounded-lg border transition-all">
          <span x-text="filterExecucao === 'all' ? 'Execução' : filterExecucao === '✅' ? '✅ Executadas' : '⬜ Pendentes'"></span>
        </button>

        <button @click="filterAuditoria = (filterAuditoria === 'all' ? '👁' : filterAuditoria === '👁' ? '⬜' : 'all')"
          :class="filterAuditoria === 'all' ? 'bg-neutral-100 dark:bg-neutral-800 border-neutral-300 dark:border-neutral-600' : filterAuditoria === '👁' ? 'bg-primary-100 dark:bg-primary-500/20 border-primary-300 dark:border-primary-500/50 text-primary-700 dark:text-primary-300' : 'bg-neutral-100 dark:bg-neutral-800/50 border-neutral-200 dark:border-neutral-700/50 text-neutral-500'"
          class="px-2 py-1 text-[11px] font-medium rounded-lg border transition-all">
          <span x-text="filterAuditoria === 'all' ? 'Auditoria' : filterAuditoria === '👁' ? '👁 Auditadas' : '⬜ Não auditadas'"></span>
        </button>

        <span class="text-[11px] text-neutral-500 ml-auto" x-show="!loading" x-text="'Exibindo ' + filteredTasks.length + ' de ' + allTasks.length + ' tasks'"></span>
      </div>

      <!-- Loading -->
      <div x-show="loading" class="flex items-center justify-center py-16">
        <div class="flex flex-col items-center gap-3">
          <div class="w-8 h-8 border-4 border-neutral-200 dark:border-neutral-700 border-t-primary-500 rounded-full animate-spin"></div>
          <p class="text-sm text-neutral-500">Carregando tasks...</p>
        </div>
      </div>

      <!-- Error -->
      <div x-show="error && !loading" class="bg-red-50 dark:bg-red-500/10 border border-red-200 dark:border-red-500/30 rounded-lg p-4 mb-4">
        <p class="text-sm text-red-600 dark:text-red-400 font-medium">❌ <span x-text="error"></span></p>
        <p class="text-xs mt-1 text-red-500">Verifique se o CT2 Server está rodando (<code>python3 ct2.py serve</code>)</p>
      </div>

      <!-- Empty -->
      <div x-show="!loading && !error && filteredTasks.length === 0" class="flex flex-col items-center justify-center py-16 text-neutral-500 gap-3">
        <span class="text-3xl">📋</span>
        <p class="text-sm font-medium">Nenhuma task encontrada</p>
        <p class="text-xs">Tente alterar os filtros acima ou clique em "Atualizar"</p>
      </div>

      <!-- ═══════════════════════════════════════════════ -->
      <!-- SPRINT VIEW (original)                          -->
      <!-- ═══════════════════════════════════════════════ -->
      <div x-show="!loading && !error && viewMode === 'sprint' && filteredTasks.length > 0" class="space-y-4">
        <template x-for="sg in sprintGroups" :key="sg.sprintNumber">
          <div class="border border-neutral-200 dark:border-neutral-700/60 rounded-xl overflow-hidden"
               :class="sg.sprintStatus === 'active' ? 'ring-1 ring-primary-500/30 dark:ring-primary-400/20' : ''">
            <div @click="sg.expanded = !sg.expanded"
                 class="flex items-center justify-between px-4 py-3 cursor-pointer select-none transition-colors"
                 :class="sg.sprintStatus === 'active' ? 'bg-primary-50/70 dark:bg-primary-900/10 hover:bg-primary-50 dark:hover:bg-primary-900/20' : 'bg-neutral-50 dark:bg-neutral-900/50 hover:bg-neutral-100 dark:hover:bg-neutral-800/50'">
              <div class="flex items-center gap-3">
                <span x-text="sg.expanded ? '▼' : '▶'" class="text-xs text-neutral-400"></span>
                <span class="text-sm font-bold text-neutral-900 dark:text-white" x-text="'Sprint ' + sg.sprintNumber + (sg.sprintTitle ? ' — ' + sg.sprintTitle : '')"></span>
                <span :class="sg.sprintStatus === 'active' ? 'bg-green-100 dark:bg-green-500/20 text-green-600 dark:text-green-400 border-green-200 dark:border-green-500/30' : 'bg-neutral-100 dark:bg-neutral-800 text-neutral-500 dark:text-neutral-400 border-neutral-200 dark:border-neutral-700'"
                      class="text-[10px] px-1.5 py-0.5 rounded-full font-medium border"
                      x-text="sg.sprintStatus === 'active' ? '🟢 Ativa' : '✅ Concluída'"></span>
              </div>
              <div class="flex items-center gap-4 text-xs text-neutral-500">
                <span x-text="sg.counts.total + ' tasks'"></span>
                <span class="flex items-center gap-0.5">
                  <span class="text-green-600 dark:text-green-400 font-mono" x-text="sg.counts.done"></span>✅
                  <span class="text-primary-600 dark:text-primary-400 font-mono ml-1" x-text="sg.counts.audited"></span>👁
                </span>
              </div>
            </div>
            <div x-show="sg.expanded" x-transition:enter="transition ease-out duration-200" x-transition:enter-start="opacity-0" x-transition:enter-end="opacity-100">
              <div class="border-t border-neutral-100 dark:border-neutral-800 divide-y divide-neutral-100 dark:divide-neutral-800/60">
                <template x-for="dg in sg.dayGroups" :key="dg.day">
                  <div>
                    <div @click="dg.expanded = !dg.expanded"
                         class="flex items-center gap-2 px-4 py-2 bg-neutral-50/50 dark:bg-neutral-900/30 cursor-pointer hover:bg-neutral-100 dark:hover:bg-neutral-800/40 transition-colors">
                      <span x-text="dg.expanded ? '▼' : '▶'" class="text-[10px] text-neutral-400"></span>
                      <span class="text-xs font-semibold text-neutral-700 dark:text-neutral-300" x-text="formatDay(dg.day)"></span>
                      <span class="text-[10px] text-neutral-500" x-text="dg.counts.total + ' tasks'"></span>
                      <span class="flex-1"></span>
                      <span class="text-[10px] text-neutral-400">✅<span x-text="dg.counts.executed" class="font-mono"></span> 👁<span x-text="dg.counts.audited" class="font-mono ml-0.5"></span></span>
                    </div>
                    <div x-show="dg.expanded" x-transition>
                      <template x-for="wg in dg.waveGroups" :key="dg.day + '-w' + wg.waveNumber">
                        <div>
                          <div class="flex items-center gap-2 px-6 py-1.5 text-[11px] text-neutral-500">
                            <span>🌊 Wave <span x-text="wg.waveNumber" class="font-mono font-semibold text-neutral-600 dark:text-neutral-400"></span></span>
                            <span class="text-neutral-400" x-text="'(' + wg.tasks.length + ')'"></span>
                          </div>
                          <div class="space-y-1 px-4 pb-2">
                            <template x-for="task in wg.tasks" :key="task.id">
                              <div class="flex items-start gap-3 bg-white dark:bg-neutral-950/50 border border-neutral-200 dark:border-neutral-800/60 rounded-lg p-3 transition-colors hover:border-neutral-300 dark:hover:border-neutral-700/80">
                                <span class="text-sm shrink-0 mt-0.5 w-5 text-center"
                                  :title="task.status_execucao === '✅' && task.status_auditoria === '👁' ? 'Executada e Auditada' : task.status_execucao === '✅' ? 'Executada' : 'Pendente'"
                                  x-text="task.status_execucao === '✅' && task.status_auditoria === '👁' ? '👁' : task.status_execucao === '✅' ? '✅' : '⬜'"></span>
                                <div class="flex-1 min-w-0">
                                  <div class="flex items-center gap-2">
                                    <span class="font-mono text-xs text-primary-600 dark:text-primary-400 font-semibold shrink-0" x-text="'#' + task.task_number"></span>
                                    <span class="text-sm font-medium text-neutral-900 dark:text-neutral-100 truncate" x-text="task.title"></span>
                                    <span :class="statusBadge(task.status)" class="shrink-0 text-[10px] px-1.5 py-0.5 rounded-full font-medium border" x-text="statusLabel(task.status)"></span>
                                  </div>
                                  <div class="flex flex-wrap items-center gap-x-3 gap-y-0.5 mt-1 text-[11px] text-neutral-500">
                                    <span x-show="task.agent" class="flex items-center gap-1">👤 <span x-text="task.agent"></span></span>
                                    <span x-show="task.modulo" class="flex items-center gap-1">📦 <span x-text="task.modulo" class="font-medium text-neutral-600 dark:text-neutral-400"></span></span>
                                    <span x-show="task.commit_hash && task.commit_hash !== '—' && task.commit_hash" class="flex items-center gap-1 font-mono text-primary-600/70 dark:text-primary-400/70">🔗 <span x-text="formatHash(task.commit_hash)"></span></span>
                                    <span class="flex items-center gap-1" :class="task.status_execucao === '✅' ? 'text-green-600 dark:text-green-400' : 'text-neutral-500'"><span x-text="task.status_execucao === '✅' ? '✅ Executada' : '⬜ Pendente'"></span></span>
                                    <span class="flex items-center gap-1" :class="task.status_auditoria === '👁' ? 'text-primary-600 dark:text-primary-400' : 'text-neutral-500'"><span x-text="task.status_auditoria === '👁' ? '👁 Auditada' : '⬜ Não auditada'"></span></span>
                                  </div>
                                </div>
                              </div>
                            </template>
                          </div>
                        </div>
                      </template>
                    </div>
                  </div>
                </template>
              </div>
            </div>
          </div>
        </template>
      </div>

      <!-- ═══════════════════════════════════════════════ -->
      <!-- DAY VIEW (new)                                  -->
      <!-- ═══════════════════════════════════════════════ -->
      <div x-show="!loading && !error && viewMode === 'day' && filteredTasks.length > 0" class="space-y-4">
        <template x-for="group in dayGroups" :key="group.day">
          <div class="border border-neutral-200 dark:border-neutral-800 rounded-xl overflow-hidden">
            <!-- Day header -->
            <button @click="toggleDay(group.day)"
              class="w-full flex items-center justify-between px-4 py-3 bg-neutral-50 dark:bg-neutral-900/60 hover:bg-neutral-100 dark:hover:bg-neutral-800/80 transition-colors text-left">
              <div class="flex items-center gap-3">
                <span class="text-lg font-bold text-neutral-800 dark:text-white" x-text="formatDay(group.day)"></span>
                <span class="text-xs text-neutral-500">
                  (<span x-text="group.counts.total"></span> tasks ·
                  <span class="text-green-600 dark:text-green-400" x-text="group.counts.executed"></span> ✅ ·
                  <span class="text-amber-600 dark:text-amber-400" x-text="group.counts.audited"></span> 👁)
                </span>
              </div>
              <span class="text-neutral-400 transition-transform" :class="group.expanded ? 'rotate-180' : ''">▾</span>
            </button>

            <!-- Day body -->
            <div x-show="group.expanded" class="divide-y divide-neutral-100 dark:divide-neutral-800">
              <template x-for="proj in group.projects" :key="proj.slug">
                <div>
                  <!-- Project sub-header -->
                  <div class="px-4 py-1.5 bg-neutral-100/50 dark:bg-neutral-800/50 text-xs font-semibold text-neutral-500 uppercase tracking-wider"
                    x-text="proj.name"></div>
                  <!-- Tasks -->
                  <template x-for="task in proj.tasks" :key="task.id || task.task_number">
                    <div class="px-4 py-2.5 hover:bg-neutral-50 dark:hover:bg-neutral-800/30 transition-colors">
                      <div class="flex items-start gap-3">
                        <!-- Status -->
                        <div class="flex items-center gap-0.5 shrink-0 mt-0.5">
                          <span class="text-sm" :title="task.status_execucao === '✅' ? 'Executada' : 'Pendente'"
                            x-text="task.status_execucao || '⬜'"></span>
                          <span class="text-sm" :title="task.status_auditoria === '👁' ? 'Auditada' : 'Não auditada'"
                            x-text="task.status_auditoria || '⬜'"></span>
                        </div>
                        <!-- Task details -->
                        <div class="flex-1 min-w-0">
                          <div class="flex items-center gap-2 flex-wrap">
                            <span class="font-mono text-xs font-bold text-primary-600 dark:text-primary-400"
                              x-text="'task_' + task.task_number"></span>
                            <span class="text-sm text-neutral-800 dark:text-neutral-200 truncate"
                              x-text="task.title || '—'"></span>
                            <span :class="statusBadge(task.status)" class="shrink-0 text-[10px] px-1.5 py-0.5 rounded-full font-medium border"
                              x-text="statusLabel(task.status)"></span>
                          </div>
                          <div class="flex flex-wrap items-center gap-x-3 gap-y-0.5 mt-1 text-[11px] text-neutral-500">
                            <span x-show="task.agent" class="flex items-center gap-1">👤 <span x-text="task.agent"></span></span>
                            <span x-show="task.motor" class="flex items-center gap-1">⚙️ <span x-text="(task.motor || '').split(' —')[0].substring(0, 30)"></span></span>
                            <span x-show="task.sprint_number" class="flex items-center gap-1">📋 Sprint <span x-text="task.sprint_number"></span></span>
                            <span x-show="task.wave_number" class="flex items-center gap-1">🌊 Wave <span x-text="task.wave_number"></span></span>
                            <span x-show="task.modulo" class="flex items-center gap-1">📦 <span x-text="task.modulo"></span></span>
                            <span x-show="task.commit_hash && task.commit_hash !== '—' && task.commit_hash" class="flex items-center gap-1 font-mono">🔗 <span x-text="formatHash(task.commit_hash)"></span></span>
                            <span x-show="task.data_conclusao" class="flex items-center gap-1 text-neutral-400">📅 <span x-text="task.data_conclusao"></span></span>
                          </div>
                        </div>
                      </div>
                    </div>
                  </template>
                </div>
              </template>
            </div>
          </div>
        </template>
      </div>
    </div>`;
  }

  // ─── Initialize ──────────────────────────────────────
  function init() {
    if (window.__CT2_TASKS_INITIALIZED__) return;
    window.__CT2_TASKS_INITIALIZED__ = true;

    if (typeof Alpine !== 'undefined' && Alpine.version) {
      defineTasksComponent();
      injectIntoDashboard();
    } else {
      document.addEventListener('alpine:init', defineTasksComponent);
      const waitForAlpine = () => {
        if (document.querySelector('[x-data="app()"]') && typeof Alpine !== 'undefined') {
          injectIntoDashboard();
        } else {
          setTimeout(waitForAlpine, 300);
        }
      };
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', waitForAlpine);
      } else {
        waitForAlpine();
      }
    }

    registerTasksTab();
    document.addEventListener('hermes:navigated', registerTasksTab);
    console.log('[' + PLUGIN_NAME + '] v2 Plugin carregado ✅ (sprint + day views)');
  }

  // ─── Expose for Hermes plugin system ────────────────
  window.__CT2_PLUGINS__ = window.__CT2_PLUGINS__ || {};
  window.__CT2_PLUGINS__[PLUGIN_NAME] = {
    name: PLUGIN_NAME,
    version: '2.0.0',
    init: init,
    registerTab: registerTasksTab,
  };

  if (window.__HERMES_PLUGIN_LOADER__) {
    window.__HERMES_PLUGIN_LOADER__.register(window.__CT2_PLUGINS__[PLUGIN_NAME]);
  } else {
    init();
  }
})();
