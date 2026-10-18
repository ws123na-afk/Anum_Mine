// Applies the saved theme before the first paint (docs/design/anum360.md#theme), so a page never
// flashes the other theme while the app loads. A separate file because the CSP allows only
// same-origin scripts. The rules match resolveTheme in src/lib/theme.ts: "light", "dark" (the
// default) or "system" (the device's setting); src/lib/theme.ts takes over once the app starts.
(function () {
  var choice = null;
  try { choice = window.localStorage.getItem('anum.theme'); } catch (e) { /* storage blocked: the default */ }
  var light = choice === 'light' || (choice === 'system' && window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches);
  var root = document.documentElement;
  root.setAttribute('data-theme', light ? 'light' : 'dark');
  root.style.colorScheme = light ? 'light' : 'dark';
  var meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', light ? '#f7f8fa' : '#0b0d12');
})();
