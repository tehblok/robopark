# Установка Robopark на Armbian и Ubuntu

Используйте единый `robopark-<версия>.ota` на Ubuntu 22.04+ или актуальном
Armbian, ARM64/AMD64, Python 3.10+. Номер ОС не зафиксирован: preflight проверяет
архитектуру, RAM, свободное место, systemd, Docker и доступные возможности.

```sh
python3 robopark-<версия>.ota verify --json
sudo python3 robopark-<версия>.ota
```

Меню показывается до любых проверок и предлагает чистую установку, обычное
обновление, диагностику, полное удаление и выход. Для перехода со старой ветки
выберите чистую установку; локальные данные не переносятся и backup не создаётся.
Точная фраза подтверждения: `УДАЛИТЬ ВСЕ ДАННЫЕ`.

После установки проверьте:

```sh
sudo python3 -I /opt/robopark/host-tools/robopark status
sudo python3 -I /opt/robopark/host-tools/robopark doctor
sudo systemctl is-active robopark.service robopark-tuna.service
curl -fsS http://127.0.0.1:8080/api/health/ready
```

Обычные последующие обновления выполняются тем же `.ota` с USB или royal на
странице «Система → Обновление». Они сохраняют данные, делают snapshot и
автоматически откатываются при ошибке. См. `docs/runbooks/`.

SHA-256 проверяет целостность, но не происхождение файла. Передавайте OTA только
по доверенному локальному каналу. Дополнительные ключи и sidecar-файлы не нужны.
