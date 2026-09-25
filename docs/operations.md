# Эксплуатация

Канонический формат поставки — один `robopark-<версия>.ota`. Локальная установка
и диагностика описаны в [USB runbook](runbooks/usb-clean-install.md), обновление
royal — в [web OTA runbook](runbooks/web-ota-update.md), аварийные действия — в
[recovery runbook](runbooks/ota-recovery.md).

Не запускайте нагрузочные и soak-проверки на production-хосте. Обновление
сериализовано, сохраняет данные, делает snapshot и откатывается при отказе.
