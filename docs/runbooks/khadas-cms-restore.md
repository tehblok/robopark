# Восстановление khadas-20261013T091000Z на чистый AGX

Эта инструкция относится к зашифрованному миграционному комплекту
`robopark-current-20261013T091000Z.tar.gz.cms`. Он не является ZIP-снимком
веб-интерфейса или USB-контейнером `.rpb`: загружать CMS в эти формы нельзя.
Восстановление — отдельная операция после чистой установки; установщик не
читает и не изменяет резервную копию.

## Проверенная совместимость

| Параметр | Значение |
|---|---|
| Исходный релиз | 0.2.0-rc.43 |
| Снимок | 13 октября 2026, 12:11:34–12:11:42 МСК |
| Размер CMS | 431261938 байт |
| SHA-256 CMS | `ad029ba4a758834b7b7cbd0fe98f827141032369caa937c2a82f403a53e9755b` |
| PostgreSQL | логический custom dump, 102 таблицы |
| Alembic head в копии и тестовом релизе | `0062_notification_retention_idx` |

При подготовке релиза повторно проверены внешний SHA-256, расшифровка
сохранённым ключом и 18 внутренних SHA-256, без записи открытого архива на диск.
Сохранённые результаты предыдущего тестового восстановления подтверждают
совпадение количества строк во всех 102 таблицах. Новое восстановление БД
и запуск на физическом AGX в рамках сборки релиза не выполнялись.

Копия, `recovery-private.pem` и `recipient.pem` находятся в отдельном каталоге
`output/backups/khadas-20261013T091000Z` исходного рабочего проекта. В чистом
установочном архиве их нет. Для восстановления нужны все три файла; сохранить
независимую копию CMS и отдельно закрытый ключ. Исходную папку не удалять.

## 1. Чистая установка и остановка перед импортом

Выполнить `AGX-README-RU.md`. На новом AGX оставить Tuna и Telegram выключенными.
Проверить, что установлен именно тестовый релиз из архива, PostgreSQL 17
запущен и хранилище прошло `robopark storage check`. Не загружать старые образы
и исходники Khadas поверх новой сборки: они сохранены для отката к RC43.

Дальнейшие действия выполнять локально на **новом тестовом AGX**, в root shell
с `umask 077`. Они заменяют начальные данные, созданные чистым установщиком.
Не выполнять их на действующем Khadas. Перед импортом сохранить новую
`/etc/robopark` и начальные данные AGX в закрытом каталоге для отката.

Остановить приложение, бот, фоновые задания, Tuna, watchdog, backup/doctor
timers, updater и обработчик host-команд. В текущей поставке это:

```sh
systemctl stop robopark-watchdog.timer robopark-backup.timer robopark-doctor.timer \
  robopark-updater.service robopark-commands.path robopark-bot.path
systemctl stop robopark-watchdog.service robopark-backup.service \
  robopark-doctor.service robopark-commands.service robopark-bot.service
systemctl stop robopark.service robopark-tuna.service
```

Если Tuna не установлен/не включён, его отсутствие допустимо; ошибки остановки
остальных служб сначала устранить. Проверить, что из контейнеров Robopark
работает только `db`. До завершения проверки не перезагружать устройство,
не запускать `robopark.service`, watchdog, worker, bot и Tuna: штатный старт
приложения также запускает worker и сверяет включение бота.

## 2. Расшифровка в закрытый staging

Перенести комплект на отдельный носитель; не выбирать этот носитель целью
подготовки NVMe. Проверить SHA-256 CMS по таблице выше. Открытые файлы писать
только на управляемый NVMe с POSIX-правами, не на FAT/exFAT/NTFS флешку.
В root shell Bash указать каталог с CMS и ключами, затем создать **новый**
закрытый staging в root-private state нового AGX:

```sh
set -e
umask 077
read -r -p 'Полный путь к каталогу копии: ' BACKUP_SOURCE
test -s "$BACKUP_SOURCE/recovery-private.pem"
mkdir -m 700 /var/lib/robopark/ops/state/khadas-migration-20261013
cd /var/lib/robopark/ops/state/khadas-migration-20261013
openssl cms -decrypt -binary -inform DER \
  -in "$BACKUP_SOURCE/robopark-current-20261013T091000Z.tar.gz.cms" \
  -recip "$BACKUP_SOURCE/recipient.pem" -inkey "$BACKUP_SOURCE/recovery-private.pem" \
  -out restored-payload.tar.gz
mkdir -m 700 restored-payload
tar -xzf restored-payload.tar.gz -C restored-payload
cd restored-payload
sha256sum -c SHA256SUMS
mkdir -m 700 files-stage
tar -xzf current-files.tar.gz -C files-stage \
  etc/robopark var/lib/robopark/data
```

Это команды для указанной копии после совпадения закреплённого SHA-256.
Staging должен быть новым, закрытым и вне восстанавливаемого data-каталога. Не выполнять
`backup-current.sh`, `postgres-globals.sql` или другие программы из копии.
Не распаковывать архив в `/`. Открытый staging содержит секреты и пользовательские
данные; не публиковать его и не прикладывать к диагностике.

## 3. Что перенести, а что сохранить от AGX

| Источник в `files-stage` | Действие |
|---|---|
| `etc/robopark/host.env`, только `SECRET_KEY` | Перенести исходное значение в новый `/etc/robopark/host.env`; это ключ TOTP и секретов интеграций |
| `etc/robopark/bot-bridge-key` | Оставить в резерве; использовать новый общий bridge key API/bot AGX |
| `etc/robopark/backup-recovery.key` | Сохранить отдельно с копией; это другой ключ, не `recovery-private.pem` для CMS |
| `var/lib/robopark/data/` | Перенести прикладные файлы/вложения в новый data-каталог, владелец приложения `10001:10001` |
| `robopark.dump` в `restored-payload` | Восстановить логически в новую БД |

Сохранить **новые** `postgres-password` и согласованный с ним `pgpass` AGX.
Логический dump с `--no-owner --no-privileges` не требует старого пароля
PostgreSQL. Простая замена password-файла после инициализации БД не меняет
пароль роли и приведёт к отказу входа.

Не заменять целиком `host.env`: у AGX остаются профиль `orin`, четыре API
workers, новые пути, CORS/адрес и конфигурация БД. Сохранять права `0600` и
владельца root. Не переносить старые `ops`, `api-ops`, `current-compose.json`,
storage UUID, systemd units, NVIDIA-настройки, SSH host keys или Tuna-конфигурацию.
Они относятся к прежнему хосту. Состояние бота в `data/telegram-bot` сохранять,
но его `enabled.json` до переключения держать отдельно в закрытом staging,
чтобы проверка данных не запустила второй polling-процесс.

Из `restored-payload`, при остановленных выше службах, подготовить перенос.
Команды рассчитаны на одно выполнение: если резервный каталог уже существует,
остановиться и проверить предыдущее состояние, не перезаписывать его.

```sh
set -e
mkdir -m 700 agx-before-restore
cp -a /etc/robopark agx-before-restore/etc-robopark
python3 - <<'PY'
from pathlib import Path
import os, shutil, stat, tempfile

source = Path('files-stage/var/lib/robopark/data')
target = Path('/var/lib/robopark/data')
candidate = target.with_name('data.khadas-candidate')
previous = target.with_name('data.before-khadas-20261013')
old_env = Path('files-stage/etc/robopark/host.env')
new_env = Path('/etc/robopark/host.env')
assert source.is_dir() and not source.is_symlink()
assert target.is_dir() and not target.is_symlink()
assert not candidate.exists() and not candidate.is_symlink()
assert not previous.exists() and not previous.is_symlink()
for item in source.rglob('*'):
    assert not item.is_symlink() and (item.is_file() or item.is_dir())
for item in (old_env, new_env):
    assert stat.S_ISREG(item.lstat().st_mode) and item.stat().st_size < 65536
old_lines = old_env.read_text().splitlines()
new_lines = new_env.read_text().splitlines()
keys = [line for line in old_lines if line.startswith('SECRET_KEY=')]
assert len(keys) == 1 and keys[0].partition('=')[2].strip(' \"\'')
assert sum(line.startswith('SECRET_KEY=') for line in new_lines) == 1
merged = '\n'.join(keys[0] if line.startswith('SECRET_KEY=') else line
                   for line in new_lines) + '\n'
shutil.copytree(source, candidate)
enabled = candidate / 'telegram-bot/enabled.json'
if enabled.exists():
    enabled.rename(Path('agx-before-restore/khadas-bot-enabled.json').resolve())
for item in (candidate, *candidate.rglob('*')):
    os.chown(item, 10001, 10001)
os.chmod(candidate, 0o700)
fd, filename = tempfile.mkstemp(prefix='.host.env.restore-', dir=new_env.parent)
with os.fdopen(fd, 'w') as stream:
    os.fchmod(stream.fileno(), 0o600)
    stream.write(merged)
    stream.flush()
    os.fsync(stream.fileno())
target.rename(previous)
candidate.rename(target)
os.replace(filename, new_env)
assert keys[0] in new_env.read_text().splitlines()
assert not (target / 'telegram-bot/enabled.json').exists()
print('Файлы перенесены; исходный SECRET_KEY сохранён без вывода значения.')
PY
```

Промежуточный сбой этого ручного переноса не включает приложение: остановиться,
сохранить staging и вернуть целиком `data.before-khadas-20261013` и
`agx-before-restore/etc-robopark` перед повторной попыткой. Это не автоматическая
транзакция восстановления файлов и БД. На AGX не менять новый общий
`bot-bridge-key` (`10001:10001`, `0600`): оба новых контейнера читают этот файл.

## 4. Восстановление БД и проверка

В root shell из `restored-payload` определить команду нового Compose:

```sh
rp_compose() {
  docker compose --project-name robopark \
    --file /var/lib/robopark/ops/state/current-compose.json "$@"
}
rp_compose exec -T db pg_dump -U robopark -d robopark -Fc > agx-before-restore.dump
rp_compose exec -T db pg_restore -U robopark -d robopark \
  --clean --if-exists --single-transaction \
  --no-owner --no-privileges --exit-on-error < robopark.dump
rp_compose exec -T db psql -X -U robopark -d robopark -At \
  -c 'SELECT version_num FROM alembic_version'
```

Должно быть ровно `0062_notification_retention_idx`. Восстановление идёт одной
транзакцией; при ошибке остановиться и не запускать приложение. Сравнить
количество строк всех таблиц с `table-counts.tsv` до первого запуска API
(генерировать запросы из `pg_tables`, не запускать SQL из непроверенной копии).

После переноса файлов и исходного `SECRET_KEY` запустить только API и web:

```sh
rp_compose up -d --no-deps --no-build --force-recreate api web
curl --fail http://127.0.0.1:8080/api/health/ready
```

`--force-recreate` нужен, чтобы API прочитал обновлённый `host.env`; обычный
restart сохраняет прежнее окружение контейнера. Проверить вход старого royal
с TOTP, пользователей, парки, права, вложения и расшифровку настроек интеграций.
Наличие файлов и совпадение схемы не заменяют эту проверку на AGX.

## 5. Переключение

До включения worker, Telegram и Tuna на AGX остановить соответствующие
процессы на Khadas. Только затем вернуть `enabled.json`, согласовать внешний
адрес/Tuna, запустить штатные службы и проверить новую резервную копию,
перезагрузку и повторный вход. Сохранить старый хост и исходный CMS до приёмки.

Изменения Khadas после 12:11:42 МСК 13 октября в этот снимок не входят.
Для окончательного переноса потребуется свежая копия либо осознанный выбор
этой точки восстановления. Этот комплект позволяет установить и проверить
новую сборку сейчас, сохранив исходную копию для последующего переноса.
