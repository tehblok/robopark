# Installed-client compatibility fixture

These two files are exact copies from commit `2d2ce191`:

- `apps/web/src/pwa/registerServiceWorker.ts`
- `apps/web/scripts/sw-template.js`

The fixture server transpiles the original client and substitutes only the cache
version/precache placeholders in the original worker. Keeping the sources here
allows the upgrade regression to run in shallow CI clones and disposable Docker
workspaces without Git history. Do not replace these with the current client:
the tests must exercise an already-installed old client upgrading safely.
