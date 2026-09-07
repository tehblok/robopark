Robopark — установка на Armbian (aarch64 / x86_64)

Сначала проверьте архив ДО распаковки и запуска install.sh.
Получите release-public-key.pem и verify-artifact.py отдельно из доверенного
checkout проекта. Не используйте ключ из ещё не проверенного архива.
На машине с Python 3.10+ и cryptography сохраните рядом все четыре файла:
robopark-installer-VERSION.tar.gz, .tar.gz.sig, .tar.gz.sha256, .tar.gz.json.
Без сетевого подключения выполните:
python3 verify-artifact.py --public-key release-public-key.pem robopark-installer-VERSION.tar.gz

После успешной проверки распакуйте архив в пустую папку и выполните:
sudo ./install.sh
Или для файла конфигурации с правами 0600:
sudo ./install.sh --non-interactive /защищённый/путь/install.conf
После прерывания: sudo ./install.sh --resume

Проверка подписи выполняется офлайн. Первоначальная установка требует сети
для системных пакетов, Docker-образов и Tuna. Секреты вводятся локально;
приватного ключа выпуска в комплекте нет. Вложенный ZIP проверяется повторно
перед установкой. Архивы одинаковы для обеих поддерживаемых архитектур.
