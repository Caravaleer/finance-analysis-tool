// Dark Mode Manager - Default Dark Mode with Purple Theme
(function () {
  const THEME_KEY = 'finance_tracker_theme';

  function getStoredTheme() {
    return localStorage.getItem(THEME_KEY);
  }

  function getPreferredTheme() {
    const stored = getStoredTheme();
    if (stored) {
      return stored;
    }
    // Default to Dark Mode as requested
    return 'dark';
  }

  function setTheme(theme) {
    document.documentElement.setAttribute('data-bs-theme', theme);
    localStorage.setItem(THEME_KEY, theme);
    updateToggleIcons(theme);

    // Update Chart.js if present
    if (window.Chart && window.Chart.instances) {
      const textColor = theme === 'dark' ? '#cbd5e1' : '#475569';
      const gridColor = theme === 'dark' ? 'rgba(139, 92, 246, 0.15)' : 'rgba(0, 0, 0, 0.08)';

      Object.values(window.Chart.instances).forEach(chart => {
        if (chart.options && chart.options.scales) {
          Object.values(chart.options.scales).forEach(scale => {
            if (scale.ticks) scale.ticks.color = textColor;
            if (scale.grid) scale.grid.color = gridColor;
          });
        }
        if (chart.options && chart.options.plugins && chart.options.plugins.legend) {
          chart.options.plugins.legend.labels.color = textColor;
        }
        chart.update();
      });
    }
  }

  function updateToggleIcons(theme) {
    const icons = document.querySelectorAll('.theme-toggle-icon');
    icons.forEach(icon => {
      if (theme === 'dark') {
        icon.className = 'bi bi-sun-fill theme-toggle-icon text-warning';
      } else {
        icon.className = 'bi bi-moon-stars-fill theme-toggle-icon text-white';
      }
    });
  }

  // Apply immediately before DOM render to prevent flash
  const initialTheme = getPreferredTheme();
  document.documentElement.setAttribute('data-bs-theme', initialTheme);

  document.addEventListener('DOMContentLoaded', () => {
    updateToggleIcons(initialTheme);

    const toggleBtns = document.querySelectorAll('.theme-toggle-btn');
    toggleBtns.forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        const currentTheme = document.documentElement.getAttribute('data-bs-theme') || 'dark';
        const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
        setTheme(newTheme);
      });
    });
  });
})();
