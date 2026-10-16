/* Runs in WebKit's isolated client world, only in the owned login main frame.
 * Never reads a clipboard, password, cookies, storage or unrelated page text.
 * The native handler checks the run, frame origin and account owner again. */
(() => {
  'use strict';
  if (window !== window.top || location.protocol !== 'https:' ||
      location.hostname !== 'oauth.yandex-team.ru' ||
      (location.port && location.port !== '443') ||
      !['/', '/verification_code'].includes(location.pathname)) return;
  const run = '__ROBOPARK_LOGIN_RUN__';
  let done = false;
  let timer;
  const observer = new MutationObserver(scan);
  function send(payload) {
    if (done) return;
    done = true;
    observer.disconnect(); clearInterval(timer);
    window.webkit.messageHandlers.corporateOAuthCompletion.postMessage({run, ...payload});
  }
  function scan() {
    if (done) return;
    if (location.hash.includes('access_token=')) {
      send({kind: 'callback', url: location.href}); return;
    }
    const tokens = new Set();
    // Read the full DOM value, never the visually truncated text/placeholder.
    for (const input of document.querySelectorAll('input:not([type=password]):not([type=hidden]), textarea')) {
      const value = input.value;
      if (typeof value !== 'string' || value.length > 8192 || /[\r\n]/.test(value)) continue;
      if (!/^(?:y[0-9]+_[A-Za-z0-9_-]{20,}|[a-fA-F0-9]{32,128})$/.test(value)) continue;
      if (!input.getClientRects().length || getComputedStyle(input).visibility === 'hidden') continue;
      tokens.add(value);
    }
    if (tokens.size === 1) send({kind: 'displayed', token: [...tokens][0]});
  }
  observer.observe(document.documentElement, {childList: true, subtree: true, attributes: true});
  window.addEventListener('hashchange', scan);
  document.addEventListener('input', scan);
  timer = setInterval(scan, 500); // React may update .value without an attribute mutation.
  setTimeout(() => { observer.disconnect(); clearInterval(timer); }, 10 * 60 * 1000);
  scan();
})();
