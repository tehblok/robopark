# Robopark: файлы установленного выпуска

Этот каталог содержит исходники установленного выпуска. Его версия указана в
файле VERSION. Полная документация и команда скачивания с проверкой SHA-256
находятся в [репозитории Robopark](https://github.com/tehblok/robopark/blob/main/README.md).
Для документации конкретного выпуска выберите соответствующий тег в Git.

Пакет установки — один исходный файл .ota; распаковывать его для запуска
не требуется. Если проверенный пакет сохранён как robopark.ota, меню установки
открывается командой:

    sudo python3 ./robopark.ota

На новом хосте выберите «Чистая установка». Для работающего хоста используйте
OTA-обновление в разделе «Система». Совместимые исходные версии перечислены
в manifest пакета; установщик проверяет их перед изменениями.

Для нового хоста владельца репозитория доступен профиль с прежними парками
и настройками бота. Нужен отдельный ключ зашифрованных настроек:

    sudo python3 ./robopark.ota install --preset robopark

Состав и ограничения описаны в
[руководстве профиля](https://github.com/tehblok/robopark/blob/main/docs/runbooks/owner-install-preset.md).

Руководства доступны онлайн:

- [Установка и восстановление](https://github.com/tehblok/robopark/blob/main/docs/runbooks/usb-clean-install.md).
- [GEACX1: NVMe и eMMC](https://github.com/tehblok/robopark/blob/main/docs/runbooks/geacx1-storage.md).
- [Локальный ИИ на AGX Orin](https://github.com/tehblok/robopark/blob/main/docs/runbooks/local-ai.md).

Эта инструкция не содержит хеш собственного архива: окончательный хеш
публикуется после сборки в репозитории и данных выпуска.
