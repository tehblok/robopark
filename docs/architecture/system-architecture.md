# Архитектура поставки

`scripts/build-ota.sh` создаёт детерминированный ZIP-приложение Python 3.10 с
каноническим manifest и SHA-256 каждого файла. Тот же verifier используется при
USB-запуске, API finalize и root admission.

Веб-клиент считает SHA-256 в worker и загружает bounded chunks. API хранит
метаданные возобновления и передаёт immutable UUID root-agent. Root-agent
повторно проверяет пакет, сериализует host-операцию, делает snapshot,
переключает candidate и выполняет rollback при ошибке. Ни браузер, ни API не
буферизуют весь файл в памяти.
