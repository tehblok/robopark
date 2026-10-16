import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const source = readFileSync(new URL('../../macos/Resources/corporate-oauth.js', import.meta.url), 'utf8')
  .replace('__ROBOPARK_LOGIN_RUN__', 'test-login-run');
const token = `y1__${'a'.repeat(70)}`;
function page(t, url = 'https://oauth.yandex-team.ru/', html = '') {
  const dom = new JSDOM(html, { url, runScripts: 'outside-only' });
  t.after(() => dom.window.close());
  const messages = [];
  dom.window.webkit = { messageHandlers: { corporateOAuthCompletion: { postMessage: x => messages.push(x) } } };
  dom.window.HTMLElement.prototype.getClientRects = function () {
    return this.style.display === 'none' ? [] : [{}];
  };
  dom.window.eval(source);
  return { window: dom.window, messages };
}
test('provider copy dialog completes without clicking or reading clipboard', async t => {
  const { window, messages } = page(t);
  window.document.body.innerHTML = `<dialog open><input readonly value="${token}"><button>Скопировать</button></dialog>`;
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(messages.length, 1);
  assert.equal(messages[0].kind, 'displayed');
  assert.equal(messages[0].token, token);
  assert.equal(messages[0].run, 'test-login-run');
  window.document.body.appendChild(window.document.createElement('span'));
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(messages.length, 1);
});
test('property-only token update is captured automatically', async t => {
  const { window, messages } = page(t, undefined, '<input readonly>');
  window.document.querySelector('input').value = token;
  await new Promise(resolve => setTimeout(resolve, 550));
  assert.equal(messages.length, 1);
  assert.equal(messages[0].token, token);
});
test('callback with metadata takes precedence over displayed token', t => {
  const url = 'https://oauth.yandex-team.ru/#access_token=fake&expires_in=3600&state=test';
  const { messages } = page(t, url, `<input value="${token}">`);
  assert.equal(messages.length, 1);
  assert.equal(messages[0].kind, 'callback');
  assert.equal(messages[0].url, url);
});
test('provider keeps the copy dialog on the authorize page after Passport login', async t => {
  const url = 'https://oauth.yandex-team.ru/authorize?response_type=token&client_id=fixture&state=fixture';
  const { window, messages } = page(t, url);
  window.document.body.innerHTML = `<dialog open><input readonly value="${token}"><button>Скопировать</button></dialog>`;
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(messages.length, 1);
  assert.equal(messages[0].kind, 'displayed');
  assert.equal(messages[0].token, token);
  assert.equal(messages[0].url, url);
  window.history.replaceState(null, '', '/authorize');
  assert.equal(messages[0].url, url, 'metadata is bound to extraction document before history changes');
});
test('authorize fragment preserves callback metadata instead of treating it as an undated token', t => {
  const url = `https://oauth.yandex-team.ru/authorize?response_type=token&client_id=fixture&state=fixture#access_token=${token}&expires_in=3600&state=fixture`;
  const { messages } = page(t, url, `<input value="${token}">`);
  assert.equal(messages.length, 1);
  assert.equal(messages[0].kind, 'callback');
  assert.equal(messages[0].url, url);
});
test('no extraction on password, unrelated paths, insecure or unrelated hosts', t => {
  for (const url of ['https://passport.yandex-team.ru/', 'https://oauth.yandex-team.ru/client/new',
    'http://oauth.yandex-team.ru/', 'https://oauth.yandex-team.ru:444/', 'https://oauth.yandex-team.ru.evil.example/']) {
    assert.equal(page(t, url, `<input value="${token}">`).messages.length, 0);
  }
});
test('never takes hidden/password fields, placeholders or truncated/ambiguous tokens', t => {
  for (const html of [`<input type=password value="${token}">`, `<input type=hidden value="${token}">`,
    `<input value="${token}" style="display:none">`, `<input value="${token}" style="visibility:hidden">`,
    `<input placeholder="${token}">`, `<input value="${token.slice(0, 30)}…">`,
    `<input value="${token}"><input value="${token}b">`]) {
    assert.equal(page(t, undefined, html).messages.length, 0);
  }
});
