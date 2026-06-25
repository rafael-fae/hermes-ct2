/**
 * CT2 Scorecards Plugin — Tabela de performance por agente
 * 
 * Adiciona a aba "Scorecards" no dashboard CT2 e injeta
 * o Alpine.js component para a página de scorecards.
 * 
 * Dependências: Alpine.js, Tailwind CDN (já carregados pelo dashboard)
 * 
 * @author CT2 Team
 * @version 1.0.0
 */

(function () {
  'use strict';

  const PLUGIN_NAME = 'ct2-scorecards';
  const TAB_ID = 'tab-scorecards';
  const TAB_LABEL = '📊 Scorecards';

  // ─── Register tab via Hermes plugin API ─────────────────────────
  function registerScorecardsTab() {
    // Dispatch event for tab registration
    const event = new CustomEvent('hermes:register-tab', {
      detail: {
        id: TAB_ID,
        label: TAB_LABEL,
        url: '/scorecards',
        plugin: PLUGIN_NAME,
        sortOrder: 4,
        icon: '📊',
      },
      bubbles: true,
    });
    document.dispatchEvent(event);
  }

  // ─── Inject scorecards Alpine component into page ────────────────
  function injectAlpineComponent() {
    if (window.scorecardsComponentInjected) return;
    window.scorecardsComponentInjected = true;

    // The Alpine component is already defined inline in scorecards.html.
    // This plugin ensures the navigation tab points to the correct URL.
    // If the CT2 dashboard uses an SPA-like navigation, this plugin
    // re-registers the tab on nav changes.
  }

  // ─── Initialize ──────────────────────────────────────────────────
  function init() {
    // Wait for DOM and Alpine
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', () => {
        registerScorecardsTab();
        injectAlpineComponent();
      });
    } else {
      registerScorecardsTab();
      injectAlpineComponent();
    }

    // Re-register on dynamic navigation
    document.addEventListener('hermes:navigated', registerScorecardsTab);
  }

  // Expose for Hermes plugin system
  window.__CT2_PLUGINS__ = window.__CT2_PLUGINS__ || {};
  window.__CT2_PLUGINS__[PLUGIN_NAME] = {
    name: PLUGIN_NAME,
    version: '1.0.0',
    init: init,
    registerTab: registerScorecardsTab,
  };

  // Auto-init if Hermes plugin loader is present
  if (window.__HERMES_PLUGIN_LOADER__) {
    window.__HERMES_PLUGIN_LOADER__.register(window.__CT2_PLUGINS__[PLUGIN_NAME]);
  } else {
    // Fallback: auto-init on load
    init();
  }

  console.log(`[${PLUGIN_NAME}] Plugin carregado ✅`);
})();
