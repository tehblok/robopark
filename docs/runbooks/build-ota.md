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
