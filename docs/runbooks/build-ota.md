# Сборка единого OTA

Требуются Git, Python 3.10+, uv и Node.js из lock-файлов проекта. Рабочее
дерево должно быть чистым. Каталог результата обязан находиться вне репозитория.

```sh
./scripts/build-ota.sh /absolute/output/directory
python3 /absolute/output/directory/robopark-<версия>.ota verify --json
shasum -a 256 /absolute/output/directory/robopark-<версия>.ota
```

Результат — ровно один `robopark-<версия>.ota`, без ключей и sidecar-файлов.
Сборка детерминирована; повторная сборка того же commit даёт тот же SHA-256.
SHA-256 обнаруживает изменение байтов, но не подтверждает личность издателя.
Передавайте OTA только по доверенному локальному каналу.

## Архив для чистой установки из текущего рабочего дерева

Пока работа не закоммичена, обычный сборщик выше сознательно отказывает.
Чтобы проверить именно текущую базовую систему, выполните:

```sh
python3 scripts/build_workspace_install.py /absolute/output/directory
```

Результат — `robopark-<версия>-install.tar.gz` с `INSTALL.sh`, `.ota`,
`README-RU.md`, `SHA256SUMS` и `SOURCE-SNAPSHOT.json`. Этот способ включает
только отслеживаемые исходники. Локальные базы, файлы `.env`, тесты и кэш
в пакет не входят. Новые runtime-файлы нужно сначала просмотреть и добавить
в индекс Git обычным `git add`: неотслеживаемые файлы и маркеры `git add -N`
вызывают отказ сборки. Номер версии снимка и согласованные версии API/web
формируются во временном каталоге; оригинальное дерево не изменяется.

Хэш в `SOURCE-SNAPSHOT.json` относится к выбранным исходным файлам до
версионных замен. Хэш самого архива публикуйте отдельно: внутренние SHA-256
подтверждают целостность файлов, но не подлинность отправителя. Это пакет для
чистой установки, без обещания переноса данных старой системы.

Перед следующим OTA добавьте точную установленную версию из
`SOURCE-SNAPSHOT.json` в `compatible_from_versions` нового релиза и проверьте
миграционную совместимость. Пакет базовой установки сам имеет пустой список
совместимых прежних версий и не обновит существующую базу.

## Локальное обновление снимка рабочего дерева

Для уже установленного снимка используйте отдельный сборщик:

```sh
python3 scripts/build_workspace_update.py /absolute/output/directory --from-archive /absolute/previous-install-or-update.tar.gz
```

`--from-archive` можно указать несколько раз. Каждый прежний OTA проверяется
по собственному manifest и всем SHA-256; миграции должны побайтно совпасть
с текущими. Разрешение обновления включает только точные версии этих пакетов.
При изменении миграций сборщик откажет: нужен отдельный проверенный путь переноса.
Следующий RC выбирается выше всех указанных прежних снимков.

Результат — `robopark-<версия>-update.tar.gz` с `UPDATE.sh`, `.ota`, инструкцией,
снимком происхождения и контрольными суммами. Запуск на хосте: `sh UPDATE.sh`;
скрипт вызывает только обновление после проверки и явного подтверждения.
Сборка выполняется вне репозитория и не меняет исходные версии рабочего дерева.

## Browser development files

The regular OTA omits `apps/web/e2e` and `apps/web/e2e-production`, including
PNG baselines. Installation, production web builds and host updates retain all
required runtime files. Run `test:e2e*`, the production PWA fixture and
`demo:operational` from a full Git checkout, not an installed OTA source tree.
Release acceptance still hashes those browser test definitions, so changing a
test invalidates its evidence even though the test is not shipped to the host.
