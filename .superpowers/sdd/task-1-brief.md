### Task 1: Theme tokens + theme helper

**Files:**
- Create: `apps/web/src/theme.ts`
- Modify: `apps/web/src/index.css` (replace `:root` blue theme with light/dark tokens)
- Modify: `apps/web/src/main.tsx` (call `applyStoredTheme()` before render)
- Modify: `apps/web/src/i18n/ru.ts` (theme labels)

**Interfaces:**
- Produces: `export type Theme = 'light' | 'dark'`; `getStoredTheme(): Theme`; `setTheme(theme: Theme): void`; `applyStoredTheme(): void`; `THEME_STORAGE_KEY = 'robopark-theme'`

- [ ] **Step 1: Add theme helper**

```ts
// apps/web/src/theme.ts
export type Theme = 'light' | 'dark'

export const THEME_STORAGE_KEY = 'robopark-theme'

export function getStoredTheme(): Theme {
  const raw = localStorage.getItem(THEME_STORAGE_KEY)
  return raw === 'dark' ? 'dark' : 'light'
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme
}

export function setTheme(theme: Theme): void {
  localStorage.setItem(THEME_STORAGE_KEY, theme)
  applyTheme(theme)
}

export function applyStoredTheme(): void {
  applyTheme(getStoredTheme())
}
```

- [ ] **Step 2: Replace CSS tokens**

In `index.css`, keep Manrope import. Replace `:root` block with light defaults and `[data-theme='dark']` overrides. Exact tokens:

```css
:root,
[data-theme='light'] {
  color: #1a1a1a;
  background: #f5f5f5;
  font-family: Manrope, system-ui, sans-serif;
  --bg: #f5f5f5;
  --surface: #ffffff;
  --surface-muted: #eeeeee;
  --border: #e0e0e0;
  --text: #1a1a1a;
  --text-muted: #6b6b6b;
  --brand: #f15a24;
  --brand-strong: #d94a1a;
  --accent: #f15a24;
  --success: #22c55e;
  --danger: #dc2626;
  --shadow: 0 8px 24px rgb(0 0 0 / 6%);
  --radius: 1rem;
  --radius-pill: 999px;
  --sidebar-width: 15rem;
}

[data-theme='dark'] {
  color: #f5f5f5;
  background: #121212;
  --bg: #121212;
  --surface: #1e1e1e;
  --surface-muted: #2a2a2a;
  --border: #333333;
  --text: #f5f5f5;
  --text-muted: #a3a3a3;
  --brand: #f15a24;
  --brand-strong: #ff6a35;
  --accent: #f15a24;
  --success: #4ade80;
  --danger: #f87171;
  --shadow: 0 8px 24px rgb(0 0 0 / 40%);
}
```

Update `body { background: var(--bg); color: var(--text); }`. Do **not** restyle all components yet — enough that app is not broken (temporary map old `--brand` usages).

- [ ] **Step 3: Boot theme in main.tsx**

```ts
import { applyStoredTheme } from './theme'
applyStoredTheme()
```

- [ ] **Step 4: Verify build**

Run: `cd apps/web && npm run build`  
Expected: success

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/theme.ts apps/web/src/index.css apps/web/src/main.tsx apps/web/src/i18n/ru.ts
git commit -m "feat(web): light/dark theme tokens and persistence"
```

---

