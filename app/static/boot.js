(() => {
  try {
    const theme = localStorage.getItem('jarvis.theme');
    if (theme) document.documentElement.dataset.theme = theme;
    if (localStorage.getItem('jarvis.sessionHint') === '1') {
      document.documentElement.dataset.boot = 'app';
    }
  } catch (err) {}
})();
