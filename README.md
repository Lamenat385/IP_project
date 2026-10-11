# IP_project

Набор независимых Python-модулей, образующих конвейер для работы с корпусом
PDF-документов (учебники, научные работы):

1. **поиск и загрузка** нужных PDF из интернета (`module_searcher`);
2. **преобразование** PDF в очищенный текст/markdown (`module_pdf2md`);
3. дальнейшая обработка — база знаний, обучение, веб-интерфейс, замеры
   (модули-заготовки, см. ниже).

Каждый модуль изолирован: у него собственный `requirements.txt`, своя папка
`source/` и (локально) своё виртуальное окружение `.venv/`. Виртуальные
окружения и крупные файлы моделей (`*.gguf`) в git не хранятся.

Лицензия: MIT (см. `LICENSE`).

---

## Структура репозитория

```
IP_project/
├── LICENSE
├── README.md
├── .gitignore
├── docs/                       # пустой плейсхолдер docs/pass (папка в git)
├── doab_pdfs/                  # локальная папка для книг открытого доступа (пусто, *.pdf игнорируются)
│
├── module_searcher/            # РЕАЛИЗОВАН: поиск + отбор + скачивание PDF
│   ├── run.py                  # точка входа
│   ├── Modelfile               # описание Ollama-модели (FROM ./X.gguf)
│   ├── requirements.txt
│   ├── themes.txt              # список тем (по строке на тему), сейчас пуст
│   ├── source/
│   │   ├── config.py           # Config + parse_args (все параметры CLI)
│   │   ├── pipeline.py         # оркестрация: темы → батчи → отбор → загрузка → проверка
│   │   ├── search.py           # поиск через DuckDuckGo (ddgs), фильтр *.pdf
│   │   ├── relevance.py        # отбор релевантных через локальную модель Ollama
│   │   ├── downloader.py       # скачивание с проверкой %PDF-магии и лимитом размера
│   │   ├── scan_detect.py      # определение сканов через PyMuPDF
│   │   └── logging_setup.py    # текстовый лог + события в JSONL
│   ├── downloads/              # скачанные PDF (локально, *.pdf игнорируются)
│   └── logs/                   # run_*.log и events_*.jsonl
│
├── module_pdf2md/              # РЕАЛИЗОВАН: PDF → один очищенный текст (docconvert)
│   ├── docconvert.cmd          # обёртка запуска для Windows
│   ├── pyproject.toml          # пакет docconvert, console_script docconvert
│   ├── requirements.txt
│   ├── README.md               # подробная документация модуля
│   ├── docs/                   # исходные PDF по умолчанию
│   └── source/docconvert/
│       ├── cli.py              # разбор аргументов и сборка итогового файла
│       ├── converter.py        # обёртка над docling (OCR, GPU, кэш)
│       ├── cleanup.py          # правила очистки документа и текста
│       └── __main__.py         # python -m docconvert
│
├── module_learning/            # ЗАГОТОВКА (только source/__init__.py)
│   ├── requirements.txt        # пуст
│   ├── source/__init__.py
│   └── Qwen3.8-27B-UD-Q4_K_M.gguf   # ~16 ГБ, локальная модель, в git НЕ хранится
│
├── module_database_maker/      # ЗАГОТОВКА (только source/__init__.py, requirements пуст)
├── module_web/                 # ЗАГОТОВКА (только source/__init__.py, requirements пуст)
└── module_bench/               # ЗАГОТОВКА (только source/__init__.py, requirements пуст)
```

В git отслеживаются только исходники и конфиги: `*.pdf`, `*.gguf`, `.venv/`,
`__pycache__/`, кэш docling и сгенерированные `converted.md/.txt` игнорируются
(см. `.gitignore`).

---

## module_searcher — поиск и загрузка PDF

Конвейер: для каждой темы из `themes.txt` выполняет поиск DuckDuckGo по запросу
`<тема> filetype:pdf`, отбирает релевантные документы локальной LLM через Ollama,
скачивает их и проверяет на «сканы» (изображения без текстового слоя), удаляя
сканы из папки загрузок.

Поток данных (`source/pipeline.py`):

```
themes.txt
   │  load_themes()
   ▼
для каждой темы:
   ├─ search.collect_batch()     DDGS-поиск, батчи, дедупликация URL, пагинация
   ├─ для каждого кандидата:
   │     relevance.check()       модель Ollama → YES/NO
   │     downloader.download()   потоковая загрузка, проверка %PDF, лимит размера
   └─ batch_delay                случайная пауза между батчами
   │
verify_downloads()
   └─ scan_detect.analyze_pdf()  скан? → удалить (или оставить при --keep-scans)
```

События пишутся в `logs/events_<run_id>.jsonl`, подробный лог — в
`logs/run_<run_id>.log`. Итоговая статистика (`RunSummary`) выводится в конце.

### Ключевые параметры

| Группа | Параметр | По умолчанию | Назначение |
|---|---|---|---|
| Файлы | `--themes-file` | `themes.txt` | список тем, по строке на тему (`#` — комментарий) |
| | `--download-dir` | `downloads/` | куда складывать PDF |
| | `--log-dir` | `logs/` | логи и события |
| Модель | `--model` | `x-gguf` | имя модели в Ollama |
| | `--ollama-host` | `http://127.0.0.1:11434` | адрес Ollama |
| Поиск | `--region` | `ru-ru` | регион DuckDuckGo |
| | `--safesearch` | `moderate` | `on` / `moderate` / `off` |
| | `--batch-min` / `--batch-max` | 15 / 35 | размер батча кандидатов |
| | `--batch-delay-min` / `--batch-delay-max` | 3 / 5 | пауза между батчами, с |
| | `--max-batches-per-theme` | 20 | лимит батчей на тему |
| | `--max-results-per-theme` | 90 | лимит кандидатов на тему |
| | `--max-search-pages` | 8 | страниц выдачи за один батч |
| | `--search-timeout` | 10 | таймаут поиска, с |
| Загрузка | `--request-timeout` | 40 | таймаут запроса, с |
| | `--download-retries` | 3 | число попыток |
| | `--max-file-mb` | 120 | максимальный размер файла, МБ |
| | `--download-delay-min` / `--download-delay-max` | 0 / 0 | пауза между загрузками, с |
| Отбор | `--relevance-retries` | 2 | попытки запроса к модели |
| | `--relevance-timeout` | 120 | таймаут ответа модели, с |
| | `--model-error-relevant` | `false` | считать релевантным при ошибке модели |
| Сканы | `--scan-sample-pages` | 5 | сколько страниц проверять |
| | `--scan-min-text-per-page` | 80 | порог символов на страницу |
| | `--scan-min-image-coverage` | 0.55 | порог покрытия изображением |
| | `--keep-scans` | `false` | не удалять сканы |
| Прочее | `--dry-run` | `false` | только отбор, без загрузки |
| | `--log-level` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| | `--seed` | — | фиксировать ГПСЧ (батчи, паузы) |

Булевы флаги поддерживают форму `--flag` / `--no-flag` (`BooleanOptionalAction`).

### Запуск

```powershell
cd module_searcher
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# создать модель в Ollama из Modelfile (FROM ./X.gguf, temperature 0, num_ctx 8192)
ollama create x-gguf -f Modelfile

# заполнить themes.txt темами (по строке) и запустить
python run.py
```

Зависимости: `ddgs`, `requests`, `ollama`, `PyMuPDF`.

Проверка сканов основана на: числе извлекаемых символов на страницу и доле
площади страницы, занятой изображениями (через `pymupdf`). Если текстового слоя
почти нет, а изображений много — файл считается сканом и удаляется.

---

## module_pdf2md — PDF → очищенный текст (docconvert)

CLI-утилита на базе [docling](https://github.com/docling-project/docling):
преобразует PDF-учебники и статьи в **один очищенный `.md`/`.txt`**, поддерживает
OCR сканированных книг, работу на GPU (CUDA), JSON-кэш конвертаций и гибкую
очистку (колонтитулы, номера страниц, переносы, оглавление, список литературы).

```powershell
cd module_pdf2md
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .

# все PDF из docs/ → converted.md
.\docconvert.cmd
```

Полное описание — в [`module_pdf2md/README.md`](module_pdf2md/README.md)
(установка, все флаги CLI, OCR, GPU, кэширование, правила очистки).

---

## Модули-заготовки

Следующие модули сейчас содержат только пустой пакет `source/__init__.py` и
пустой `requirements.txt` — реализация ещё не начата:

- `module_database_maker/` — построение базы данных из собранного корпуса;
- `module_learning/` — обучение/работа с моделью (рядом лежит локальная
  `Qwen3.8-27B-UD-Q4_K_M.gguf`, ~16 ГБ);
- `module_web/` — веб-интерфейс;
- `module_bench/` — бенчмарки/сравнение моделей.

---

## Лицензия

MIT. См. файл [`LICENSE`](LICENSE).
