# Криптоскоп — ИИ-помощник для анализа криптопроектов

Учебная экспертная система на Python: факты, правила, объяснения,
фреймы, семантическая сеть, расчёты токеномики и обученные классификаторы. В репозитории находится
только Криптоскоп.

## Преподавателю: результаты недель 4–9

[Скачать отчёт Word](<crypto-project-assistant/docs/results/weeks_04_09_2026_10_14/Отчёт по лабкам.docx>)
и открыть его в Microsoft Word. В отчёте отдельно разобрана каждая неделя.

[Материалы и сохранённые результаты](crypto-project-assistant/docs/results/weeks_04_09_2026_10_14/README.md)
содержат пояснения, доказательства, цепочку правил и состояния фреймов.
[Понятный разбор недель 4–9](crypto-project-assistant/docs/WEEKS_04_09.md)
объясняет термины и команды проверки.

Снимок сделан с локальной версии на 14 октября 2026 года. Эта публикация
содержит отчётные материалы; новые исправления кода, интерфейса и Qwen
в неё не включены. Презентация потребуется к концу семестра.

## Запуск на macOS / Linux

На macOS откройте **`run.command`** двойным щелчком в Finder.
Он запускает веб-интерфейс и Qwen, затем автоматически открывает браузер.
Оставьте окно терминала открытым на время работы. В интерфейсе введите
`Bitcoin` или `BTC` в верхнее поле и нажмите Enter — данные и анализ загрузятся автоматически.
Там же можно задать вопрос Qwen и сохранить профиль или отчёт.

Для экспертной части нужен Python 3.10 или новее; дополнительные Python-пакеты
и API-ключи не нужны. Браузерный запуск автоматически поднимает Qwen:
нужны `llama-server` и `.build/local-ai/Qwen3.5-2B-Q4_K_M.gguf` внутри приложения.
См. [подготовку и автозапуск](crypto-project-assistant/docs/LOCAL_AI.md).
Для чтения whitepaper и аудитов в PDF установите `pypdf` из
`crypto-project-assistant/requirements.txt` в используемое окружение Python.
На этом компьютере пакет уже установлен в `.venv`.

```sh
cd crypto-project-assistant
python3 -m project_assistant
```

Интерфейс: http://127.0.0.1:8765, модель: http://127.0.0.1:8081.
Браузер открывается после готовности модели. Ctrl+C останавливает приложение
и запущенный им процесс Qwen; уже работающий внешний сервер не завершается.
На Windows можно открыть корневой `run.bat`.

## VS Code

Откройте `SII.code-workspace`. Выберите «Криптоскоп: интерфейс» и нажмите F5.
Для конфигурации macOS/Linux нужен интерпретатор `.venv/bin/python` в корне;
создать его можно командой `python3 -m venv .venv`.
На Windows используйте `run.bat` или выберите свой интерпретатор Python в VS Code.
Отладочный запуск использует порт 8766, чтобы не конфликтовать с обычным запуском.
Команда «Тесты: Криптоскоп» доступна через запуск задач.

## Проверка

Из папки `crypto-project-assistant`:

```sh
python3 -m unittest discover -s tests -v
python3 -m project_assistant demo
python3 -m project_assistant week56
python3 -m project_assistant week415
```

Для проверки браузерных вспомогательных функций: `node --test tests/frontend.test.cjs`
(Node.js нужен только для разработки). На 14 октября 2026 года прошли
84 теста Python и 14 тестов интерфейса. [Актуальная проверка](crypto-project-assistant/docs/VALIDATION.md).
Отчёты с датой 7 октября фиксируют предыдущую версию.

## Материалы

Начните с [понятного разбора недель 4–9](crypto-project-assistant/docs/WEEKS_04_09.md):
термины, файлы, примеры, команды и ожидаемые результаты по каждому заданию.
Для текущего этапа подготовлены [отдельные отчёты и результаты по неделям](crypto-project-assistant/docs/results/weeks_04_09_2026_10_14/README.md).

- [Описание функций и сценариев](crypto-project-assistant/README.md).
- [Логическая модель: недели 5–6](crypto-project-assistant/docs/WEEKS_05_06.md).
- [Шаблон исследования](crypto-project-assistant/docs/CRYPTO_RESEARCH_TEMPLATE.md).
- [Проверка программы](crypto-project-assistant/docs/VALIDATION.md).
- [Локальная ИИ: llama.cpp и лёгкая Qwen 2B](crypto-project-assistant/docs/LOCAL_AI.md).
- [Завершённый интерфейс, черновики и проверенные сценарии](crypto-project-assistant/docs/FINISHED_QA_2026_10_07.md).

Баллы отражают полноту исследования по введённым сведениям.
В поле «Анализ по названию монеты» введите название или тикер и нажмите Enter:
Загрузятся описание, рыночный снимок, открытые документы и доступные метрики
GitHub, DefiLlama и Coinbase; vesting — из открытого Tokenomist, крупные адреса —
из Blockscout с резервным Etherscan. Локальная Qwen сделает выписки с цитатами
по всем 26 критериям без API-ключей. При совпадении названий выберите монету. Неподтверждённые критерии
останутся неизвестными. Для автосбора нужен интернет; ручной анализ работает локально.

## Задания недель 4–9

Текущий учебный этап до недели 9 подробно описан в
[WEEKS_04_09.md](crypto-project-assistant/docs/WEEKS_04_09.md).

Полный показ: `python3 -m project_assistant week48` из папки
`crypto-project-assistant`; на Windows — `run.bat week48` из корня.
См. [выполнение каждого требования и сценарий защиты](crypto-project-assistant/docs/WEEKS_04_08.md).

## Полный план недель 4–15

[Требования и доказательства](crypto-project-assistant/docs/WEEKS_04_15.md), [результаты обучения](crypto-project-assistant/docs/MODEL_RESULTS.md), [сценарий итоговой защиты](crypto-project-assistant/docs/DEFENSE.md), [черновики итоговой презентации](crypto-project-assistant/presentation/README.md).

Из папки приложения:

```sh
python3 -m project_assistant week415
python3 -m project_assistant train --seed 42 --epochs 100
python3 -m project_assistant frame show defi
python3 -m project_assistant network path project unlocks
```

Модели уже обучены и загружаются при запуске. Их результаты видны во вкладке «Для защиты» и в Markdown. Датасет синтетический, метки получены из экспертных правил; это учебная классификация статуса исследования. Сценарий и черновики презентации относятся к будущей итоговой защите и потребуют обновления к концу семестра.
