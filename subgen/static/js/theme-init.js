// Applied before first paint (classic, render-blocking script) to avoid a theme flash.
(function () {
  try {
    var theme = window.localStorage.getItem('subgen-theme');
    if (theme === 'light' || theme === 'dark') document.documentElement.setAttribute('data-theme', theme);
  } catch (e) {
    /* storage unavailable: follow the system preference */
  }
})();
