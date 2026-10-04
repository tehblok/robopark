# Распределение браузерной проверки CI

Один job последовательно запускал repository gate, аудит зависимостей,
bot image, 7 207 responsive-сценариев, production PWA и три браузерных движка.
Теперь прикладные/host gates, четыре responsive-shard и PWA/cross-browser job
могут выполняться параллельно. PWA сохраняет последовательность и один worker:
его fixture изменяет общий указатель версии приложения.

Проверены реальные Playwright `--list --shard=1/4` … `4/4` при прежней
конфигурации: 1 802 + 1 802 + 1 802 + 1 801 = 7 207. Объединение идентификаторов
project/file/title совпало с полным списком; пропусков и повторов нет. Позиции
строк не используются как идентификаторы тестов. Большая route/role-матрица
уже использует `describe.configure({mode: 'parallel'})`. Параметр `fullyParallel`
по-прежнему не устанавливается глобально; тесты, retry и число workers не меняются.

Сохранены прежние команды проверок и закреплённый Linux-образ. Для каждого
независимого job устанавливаются frontend-зависимости перед launcher.
Matrix `fail-fast: false` позволяет собрать результаты всех частей. Прежнее
имя обязательной проверки `Verify repository` оставлено за итоговым job,
который требует success всех трёх ветвей, включая всю matrix.

Скриншоты/trace из `test-results` сохраняются отдельными артефактами на 14 дней
даже после провала теста. Новый upload action закреплён по commit SHA
`ea165f8d65b6e75b540449e92b4886f43607fa02`, проверенному через официальный
GitHub ref `actions/upload-artifact/v4`. Workflow token ограничен чтением contents.

Официальные контракты: [Playwright sharding](https://playwright.dev/docs/test-sharding),
[параллельность](https://playwright.dev/docs/test-parallel),
[GitHub status checks](https://docs.github.com/en/actions/reference/workflows-and-actions/expressions#status-check-functions).

Это проверка состава и конфигурации. Фактическое ускорение и полный результат
нового workflow будут измерены его первым запуском. Суммарная работа установки
зависимостей увеличивается из-за отдельных runners. Восьмичасовой тест не входит
в команды workflow и не запускается.

YAML разобран; извлечённый итоговый script реально выполнен с success, failure,
cancelled и skipped: только success даёт exit 0. Независимое ревью workflow
не обнаружило воспроизводимых ошибок после уточнения описания parallel-режима.


## First-run PWA fixture defect

The first PR run (37214898763, job 111473305432) reached the production PWA
fixture and failed before tests: the fixture required every nginx CSP header to
be identical. The isolated terminal intentionally has a stricter policy than
the SPA. This was a stale test-server assumption, not a reason to relax nginx.

The fixture now reads exactly one CSP from each exact document location and
serves the terminal policy only with the resolved terminal document. Missing or
ambiguous locations/headers reject startup. Production nginx is unchanged.
Two Node regressions failed against the old global-policy extraction; the full
38 script checks then passed. HTTP tests additionally verify both document
headers; six security checks passed in the pinned Chromium/Firefox/WebKit image.
Independent read-only review found no concrete issue in this narrow contract.

The complete production PWA run then passed: 45 tests in 4.2 minutes across
Chromium, Firefox and WebKit, including old-client activation and durable-work
preservation. The new terminal HTTP test was checked separately in all three
engines (six CSP tests total). No production security policy changed.

The independent cross-browser step also runs after a failed PWA step (unless
cancelled), so one failed gate no longer hides the workflow results. The job
and aggregate still fail when the PWA step fails; no test assertion or retry
policy is relaxed.

CI run 37215819846 also exposed a stale exact action allowlist and a terminal screenshot path outside the writable browser workspace. The allowlist now includes the same pinned upload-artifact SHA, and terminal screenshots use per-test Playwright output paths (preserved by the existing artifact upload). No assertion or required check was removed.

The follow-up focused run passed 21/21 browser cases. Route evidence now targets
the real assistant tab buttons and the current repair-outcome select; assistant
fixtures provide a ready status and drive the actual five-second refresh for
stale/denied states. Assistant spacing is included at all six widths. Terminal
screenshots no longer attempt to create `/work/output`. The CI governance suite
passed 21 tests with the exact four-shard workflow contract.
