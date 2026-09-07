# Метаданные подписанного релиза

## Проверяемые метаданные

Перед тегом релиза измените `deploy/release-metadata.json` в том же коммите, что код
миграций. Это данные JSON, не shell/Python-код. Публичные интерфейсы сборки:

```sh
scripts/pack-release.sh --metadata deploy/release-metadata.json artifacts/release.zip
python3 scripts/release_pack.py --root . --repository --version VERSION \
  --git-sha COMMIT --metadata deploy/release-metadata.json \
  --signing-key /protected/release-key.pem --output /outside-source/release.zip
```

`VERSION` и `COMMIT` во втором примере заменяются реальными значениями. Обёртка
использует корневой `VERSION` и Git SHA; при сборке из извлечённого релиза SHA
берётся из проверенного manifest или `ROBOPARK_RELEASE_GIT_SHA`.
Закрытый ключ хранится вне дерева исходников; его путь для обёртки задаётся через
`ROBOPARK_SIGNING_KEY_FILE`. Ключи здесь не создаются и не выводятся.

Обязательные поля: `migration_head` и `migration_compatibility` с точными ключами
`from_heads` (список уникальных исходных ревизий) и `reversible` (JSON boolean).
Укажите только проверенные исходные схемы. `reversible: true` — утверждение
ответственного за релиз о проверенной совместимости и пути восстановления, а не
результат автоматического анализа downgrade-функции. Для неизменённой схемы допустимы
`from_heads: []`, `reversible: false`. Изменение схемы без явной проверенной
совместимости OTA отклонит; упаковщик сам совместимость не придумывает.

Упаковщик статически разбирает `revision`/`down_revision` всех Alembic-миграций и
требует единственную фактическую вершину, равную `migration_head`, связный граф без
циклов и существующие `from_heads`. Код миграций для этой проверки не исполняется.
Устаревший необязательный `--migration-head` служит только дополнительной проверкой
равенства, не заменяет JSON. Неизвестные поля, дубли JSON-ключей и неверные типы
отклоняются. Необязательны `update_notes`, `min_installer_version`,
`required_capabilities`; общие поля проверяют также API,
host и автономный verifier. Все значения входят в подписанный manifest.
GitHub workflow использует этот же файл и тот же упаковщик. E2E-кандидаты с новой
миграцией собираются через этот публичный интерфейс.
