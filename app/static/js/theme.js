(function () {
  const themeLink = document.getElementById('theme-css');
  const extraLink = document.getElementById('theme-extra-css');
  const key = 'vinylcat_theme';

  const THEMES = {
    // dark
    darkly: { bootswatch: 'darkly', mode: 'dark' },
    cyborg: { bootswatch: 'cyborg', mode: 'dark' },
    slate: { bootswatch: 'slate', mode: 'dark' },
    superhero: { bootswatch: 'superhero', mode: 'dark' },
    vapor: { bootswatch: 'vapor', mode: 'dark' },

    // light
    flatly: { bootswatch: 'flatly', mode: 'light' },
    lumen: { bootswatch: 'lumen', mode: 'light' },
    minty: { bootswatch: 'minty', mode: 'light' },
    sandstone: { bootswatch: 'sandstone', mode: 'light' },

    // custom hybrid: Lumen colors on a dark surface
    'lumen-dark': { bootswatch: 'lumen', mode: 'dark', extra: '/static/css/lumen-dark.css' },
  };

  function applyMode(name, mode) {
    const html = document.documentElement;
    html.dataset.themeName = name;
    html.setAttribute('data-bs-theme', mode);
  }

  function setTheme(name) {
    const cfg = THEMES[name] || THEMES['lumen'];
    const href = `https://cdn.jsdelivr.net/npm/bootswatch@5.3.3/dist/${cfg.bootswatch}/bootstrap.min.css`;
    themeLink.setAttribute('href', href);

    if (extraLink) {
      extraLink.setAttribute('href', cfg.extra || '');
    }

    applyMode(name, cfg.mode);
    localStorage.setItem(key, name);
  }

  const saved = localStorage.getItem(key);
  if (saved && THEMES[saved]) {
    setTheme(saved);
  } else {
    // Default to Lumen (light)
    setTheme('lumen');
  }

  document.querySelectorAll('.theme-item').forEach((el) => {
    el.addEventListener('click', (e) => {
      e.preventDefault();
      setTheme(el.dataset.theme);
    });
  });
})();