# Криптоскоп — ИИ-помощник для анализа криптопроектов

Учебная экспертная система на Python: факты, правила, объяснения,
фреймы, семантическая сеть, расчёты токеномики и обученные классификаторы. В репозитории находится
только Криптоскоп.

## Запуск на macOS / Linux

Нужен Python 3.10 или новее. Дополнительные пакеты и API-ключи не нужны.

```sh
cd crypto-project-assistant
python3 -m project_assistant
```

Интерфейс: http://127.0.0.1:8765. Для остановки — Ctrl+C.
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
(Node.js 22+, нужен только для разработки). [Итоговая ручная проверка и снимки](crypto-project-assistant/docs/FINISHED_QA_2026_10_07.md).

## Материалы

- [Описание функций и сценариев](crypto-project-assistant/README.md).
- [Логическая модель: недели 5–6](crypto-project-assistant/docs/WEEKS_05_06.md).
- [Шаблон исследования](crypto-project-assistant/docs/CRYPTO_RESEARCH_TEMPLATE.md).
- [Проверка программы](crypto-project-assistant/docs/VALIDATION.md).
- [Локальная ИИ: llama.cpp и лёгкая Qwen 2B](crypto-project-assistant/docs/LOCAL_AI.md).
- [Завершённый интерфейс, черновики и проверенные сценарии](crypto-project-assistant/docs/FINISHED_QA_2026_10_07.md).

Баллы отражают полноту исследования по введённым сведениям.
Сайты и котировки автоматически не загружаются.

## План недель 4–8

Полный показ: `python3 -m project_assistant week48` из папки
`crypto-project-assistant`; на Windows — `run.bat week48` из корня.
См. [выполнение каждого требования и сценарий защиты](crypto-project-assistant/docs/WEEKS_04_08.md).

## Полный план недель 4–15

[Требования и доказательства](crypto-project-assistant/docs/WEEKS_04_15.md), [результаты обучения](crypto-project-assistant/docs/MODEL_RESULTS.md), [сценарий защиты](crypto-project-assistant/docs/DEFENSE.md), [презентация](crypto-project-assistant/presentation/Cryptoscope_Weeks_04_15_Local_AI.pptx).

Из папки приложения:

```sh
python3 -m project_assistant week415
python3 -m project_assistant train --seed 42 --epochs 100
python3 -m project_assistant frame show defi
python3 -m project_assistant network path project unlocks
```

Модели уже обучены и загружаются при запуске. Их результаты видны во вкладке «Для защиты» и в Markdown. Датасет синтетический, метки получены из экспертных правил; это учебная классификация статуса исследования. Для защиты подготовлены презентация и сценарий демонстрации.
