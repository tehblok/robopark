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
