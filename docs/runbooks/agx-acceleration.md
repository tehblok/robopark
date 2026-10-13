# AGX: TensorRT, DLA, JPEG и сменные модели

Подготовлено 13 октября 2026 для GEACX1 из recovery-коммита
`7611b523e3311ba08d821449236736590ec23e35`. Модуль доступен как дополнение к
неизменённому RC43 и включён в чистую тестовую сборку `0.2.0-rc.44.dev…`.
Это не новый стабильный релиз. Моделей в комплекте нет.
На физическом AGX проверки ещё не запускались.

## Что подготовлено

| Возможность | Реализация | Когда считается работоспособной |
|---|---|---|
| TensorRT GPU | ONNX → engine, сохранение версии/хешей/форматов входов, inference | После численного smoke и проверки своей модели |
| DLA 0/1 | Выбор ядра при сборке **и** запуске; отдельный гибридный режим | После успешного строгого smoke без GPU fallback |
| JPEG | `nvjpegdec → nvvidconv → nvjpegenc`, проверка результата | После прогона JPEG с `--backend hardware` |
| CPU JPEG | Pillow, EXIF orientation, ограничение размеров | Доступен как явный режим или отмеченный fallback |
| LLM | Отдельный TensorRT Edge-LLM 0.11.0, локальный checkpoint, loopback API | После загрузки поддерживаемой модели и `edge-check` |
| LoRA/QLoRA | Учёт адаптера отдельно от base checkpoint; запуск merged checkpoint | После merge/export, регистрации и новой приёмки |
| База знаний | Контракт application-level RAG описан ниже | Пока выключена; retriever не подключён |

Наличие библиотеки, `/dev`-узла, ядер DLA либо exit code сборки не означает
готовность модели. `status` отдельно показывает обнаруженные возможности;
`smoke` строит небольшую FP16 Conv-сеть, запускает её и проверяет значения.
Модельные оптимизации и выигрыш в скорости на AGX пока не измерялись.

DLA предназначен здесь для совместимых ONNX-сетей, например компьютерного
зрения. Поддержка DLA не означает, что произвольную LLM можно перенести на него;
подготовленный LLM-провайдер использует GPU.

## DLA: уточнение по JetPack 7.2

В исходном JP7.2/R39.2 поставляется TensorRT **10.16.2**. Общая API DLA есть,
но штатный runtime может показывать ноль доступных DLA-ядер. Старая матрица
TensorRT 10.x сама по себе недостаточна для вывода о более новых пакетах.

В [ответе NVIDIA от 8 октября](https://forums.developer.nvidia.com/t/dla-runtime-unavailable-on-jetpack-7-2-l4t-r39-2-cannot-create-dla-engine/373788/16)
поддержка подтверждена для отдельного TensorRT package; включение в JetPack
**7.2.2** [анонсировано на конец октября](https://forums.developer.nvidia.com/t/dla-runtime-unavailable-on-jetpack-7-2-l4t-r39-2-cannot-create-dla-engine/373788/18).
Это не подтверждение совместимости нового пакета с vendor BSP GEACX1.

Код не запрещает DLA по номеру JetPack. Проверяет runtime и пытается построить
движок при неизвестной доступности. Явный `--trtexec` позволяет проверить
отдельную установку TensorRT, не подменяя системный бинарник. Её CUDA/libnvinfer
должны быть согласованы отдельно; один путь к бинарнику не изолирует библиотеки.
TensorRT 11 использует типы ONNX: удалённый `--fp16` ему не передаётся.
Движки строятся на целевом AGX; заранее собранных на Mac plan-файлов нет.

Edge-LLM в этом комплекте закреплён на **10.16.2**. Его окружение нельзя
обновлять до standalone TRT 11 ради DLA. Скрипт подготовки APT отклоняет такой
план. Изменение compute stack требует отдельной проверки vendor BSP.

## Установка файлов после RC43

Для чистого тестового install-архива использовать `INSTALL-AGX.sh` по
`AGX-README-RU.md`; он берёт модуль из установленного нового релиза.
Следующие команды относятся к отдельному companion-архиву для старого RC43.

Сначала выполнить [перенос RC43](geacx1-rc43-migration.md), включая NVMe layout.
Команды ниже выполняются **на AGX**, из корня распакованного нового комплекта.

```sh
sha256sum -c SHA256SUMS
sudo python3 -I robopark-agx.py check
sudo python3 -I robopark-agx.py prepare --acceleration
```

Последняя команда только рассчитывает APT-план для Python/ONNX/Pillow,
GStreamer и TensorRT 10.16.2 из уже настроенных репозиториев. После проверки:

```sh
sudo python3 -I robopark-agx.py prepare --acceleration --apply
sudo python3 -I deploy/accelerator/install.py
sudo python3 -I deploy/accelerator/install.py --apply --start
```

Все изменяемые зависимости закрепляются версиями из симуляции. Ядро, DTB,
firmware, vendor fan, драйверные пакеты, APT sources и power mode не меняются.
Если APT предлагает другой TensorRT/CUDA либо изменение защищённого пакета,
операция отклоняется. Для отсутствующего NVIDIA GStreamer нужен совместимый
vendor `nvidia-l4t-gstreamer`; этот BSP-пакет автоматически не заменяется.
Его наличие и совместимость проверяются отдельно через `gst-inspect-1.0`.

Installer копирует код в `/opt/robopark-accelerator/releases/<SHA256>` и
атомарно переключает `current`. Данные лежат на проверенном managed NVMe
в `/var/lib/robopark/accelerator`. Основной сервис работает от `robopark-accel`,
последовательно выполняет задачи, ограничен 4 GiB RAM, без swap.
Edge-LLM unit устанавливается **выключенным**, ждёт модель, venv и ключ.
GPU-пути не требуют `privileged` контейнера или Docker socket.

## Проверка GPU/DLA и JPEG

```sh
sudo -u robopark-accel /usr/bin/python3 -I /opt/robopark-accelerator/current/robopark-accel status
sudo -u robopark-accel /usr/bin/python3 -I /opt/robopark-accelerator/current/robopark-accel smoke --backend gpu
sudo -u robopark-accel /usr/bin/python3 -I /opt/robopark-accelerator/current/robopark-accel smoke --backend dla0
sudo -u robopark-accel /usr/bin/python3 -I /opt/robopark-accelerator/current/robopark-accel smoke --backend dla1
```

Каждая успешная проверка сохраняет отдельный JSON в `acceptance/`.
Ошибка DLA не вызывает автоматический повтор на GPU. Политика гибридного
движка задаётся только флагом `build --allow-gpu-fallback`; такой результат
помечен `dla0-hybrid`/`dla1-hybrid`, а не успешной строгой проверкой DLA.

Для JPEG положить тестовое фото в `models/sample.jpg`, доступное сервисному
пользователю, и указать новый выходной путь:

```sh
sudo -u robopark-accel /usr/bin/python3 -I /opt/robopark-accelerator/current/robopark-accel thumbnail \
  --input /var/lib/robopark/accelerator/models/sample.jpg \
  --output /var/lib/robopark/accelerator/work/sample-small.jpg \
  --width 640 --height 480 --backend hardware
```

Оригинал сохраняется. Нормализация EXIF и проверка изображения используют CPU;
это не zero-copy pipeline. Для маленьких JPEG аппаратный путь может оказаться
медленнее — `auto` означает функциональный fallback, а не выбор по бенчмарку.
Видео/NVENC/NVDEC/VPI пока не подключены: в RC43 нет потребляющего их конвейера.

## ONNX после появления модели

Поддерживаются самодостаточные ONNX с фиксированными входными размерами,
типами float32/float16/int8, без внешних shards и custom plugins. TensorRT 11
берёт точность из ONNX: FP32 допустим для GPU, FP16/mixed precision требует
соответствующих типов/Cast/QDQ в графе. INT8 без проверенной калибровки не включается.

1. Положить модель в managed `models/`; зарегистрировать:
   `robopark-accel register --id vision-v1 --kind onnx --path /абсолютный/model.onnx`.
2. Создать JSON с точными входами, например `{"image":[1,3,224,224]}`.
3. `robopark-accel build --id vision-v1 --shapes /абсолютный/shapes.json --backend gpu`.
4. Сохранить возвращённый `id`; выполнить `infer --engine-id ID --input image=/абсолютный/input.bin --output /новый/каталог`.

Это аргументы того же launcher, запускаемого через `sudo -u robopark-accel`
как выше. Raw input обязан иметь точный размер и тип из ONNX. Предобработка,
цветовой порядок, нормализация и смысл выходов определяются моделью; модуль
не угадывает их. При изменении бинарника TensorRT/версии или engine SHA
запуск отклоняется, требуется новая сборка.

## Локальный API ускорения

Сокет `/run/robopark-accel/accelerator.sock`, разрешены UID 0, 10001 (Robopark)
и собственный service UID. HTTP endpoints:

- `GET /health` — жив ли сервис, не проверка модели;
- `POST /v1/thumbnail`: `jpeg` (base64), `width`, `height`, `backend`;
- `POST /v1/infer`: `engine_id`, `inputs` (имя тензора → base64 raw bytes).

В комплекте `robopark_accel/client.py` со стандартной библиотекой Python.
Пути с клиента не принимаются; ответ содержит фактический backend.
Ограничения: request/response 12 MiB, один blob 8 MiB, очередь 4 соединения,
тайм-аут subprocess, проверка размера входов, ограничение пикселей JPEG.
Socket-каталог можно монтировать в потребляющий контейнер; текущий API RC43
автоматически не меняется и оригиналы вложений не перезаписываются.

## LLM: модель выбирается позже

Нет привязки к Gemma: `model_type` берётся из checkpoint; фактическую поддержку
архитектуры проверяет закреплённый TensorRT Edge-LLM. Требуется полный локальный
Hugging Face checkpoint с config/tokenizer/safetensors. GGUF и одиночный plan
этому серверу не подходят. Custom Python-код и pickle не допускаются.

Когда будет выбран checkpoint, положить его **в `models/` на NVMe**, дать
`robopark-accel` права чтения. В домашнем каталоге модель не заработает из-за
`ProtectHome` и проверки профиля. Подготовить отдельный venv на AGX:

```sh
sudo -u robopark-accel /usr/bin/python3 -m venv --system-site-packages /var/lib/robopark/accelerator/edge-venv
sudo -u robopark-accel /var/lib/robopark/accelerator/edge-venv/bin/python -m pip install \
  --only-binary=:all: -r deploy/accelerator/edge-requirements.txt
sudo -u robopark-accel /var/lib/robopark/accelerator/edge-venv/bin/python -m pip check
```

Установка venv требует сети; после неё inference работает с `HF_HUB_OFFLINE=1`
и `TRANSFORMERS_OFFLINE=1`. TensorRT берётся из system packages, произвольный
pip TensorRT и export/tools extras не устанавливать. Сохранить `pip freeze`
для повторения конкретного эксперимента; транзитивные зависимости разрешаются
на целевой платформе, готового offline wheelhouse в комплекте нет.

Запускать `register --id model-v1 --kind checkpoint --path ...`, затем
`edge-configure --id model-v1`. Команда создаёт закрытый API key; ключ не
печатается и не передаётся в argv. Запуск: `sudo systemctl start robopark-edge-llm`.
Первая сборка может быть длительной. Проверка: `edge-check` от service user.
Она требует health, правильный model id и настоящий структурированный tool call.
Обычный текст, похожий на JSON-команду, не принимается.
Это проверка HTTP-протокола, а не аттестация процесса и загруженных весов:
в отчёте версии/хеши помечены `expected_*`, `process_identity_verified=false`.

Провайдер слушает только `127.0.0.1:18082`, ограничен 16 GiB и одним batch.
Это **экспериментальный endpoint**, пока не выбран вместо production-помощника
RC43. У RC43 broker есть отдельный llama.cpp token/template protocol; у
Edge-LLM нет `/tokenize` и `/apply-template`. Простая подмена URL некорректна.
Подключение выбранной модели к production broker потребует согласованного
подсчёта контекста и приёмки tool calling. Готовность этого шага не заявляется.

## LoRA/QLoRA и база знаний

QLoRA — способ дообучения; для serving важно, какой итоговый артефакт получен.
Адаптер можно зарегистрировать `--kind adapter` с `adapter_config.json` и
`adapter_model.safetensors`. Он остаётся отдельной записью и не выдаётся за
полную модель. Текущий Python server Edge-LLM не переключает PEFT adapters
через API: здесь используется заранее merged checkpoint. Dynamic LoRA у
NVIDIA — отдельный ONNX/C++ workflow с ограничениями rank/batch; он в этот
launcher не включён. Обучение на 32 GiB не запускается автоматически.

База знаний не зашивается в веса. Для RAG нужны отдельные источники с правами,
индексация, retriever, ссылки на найденные фрагменты и бюджет контекста. RAG
может сочетаться с адаптером. Старые retrieval-модули RC43 автоматически
не включены: текущий application flow их отключил. Профиль явно содержит
`rag.enabled=false`, пока не определены источники и политика доступа.

Первые сравнения после появления моделей: одна и та же задача/контекст для
base, merged adapter и base+RAG; точность, tool calls, peak RAM, время первого
токена, tokens/s и температура. Только после этого выбирать production backend.

## Источники контрактов

- [JetPack 7.2](https://developer.nvidia.com/embedded/jetpack/downloads/archive-7.2).
- [TensorRT Edge-LLM v0.11.0](https://github.com/NVIDIA/TensorRT-Edge-LLM/tree/95515c2f87fba8982db5a519f9022277667b3cc9).
- [Server config exact tag](https://github.com/NVIDIA/TensorRT-Edge-LLM/blob/v0.11.0/experimental/server/config.py).
- [LoRA workflow](https://github.com/NVIDIA/TensorRT-Edge-LLM/blob/v0.11.0/docs/source/user_guide/features/lora.md).
- [Jetson r39.2 GStreamer](https://docs.nvidia.com/jetson/archives/r39.2/DeveloperGuide/SD/Multimedia/AcceleratedGstreamer.html).
