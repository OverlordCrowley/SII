# ИИ-помощник по диагностике компьютера

Учебный семестровый проект по дисциплине «Системы искусственного интеллекта».
По описанию симптомов программа выводит возможные причины неисправности,
объясняет сработавшие правила и предлагает дальнейшие действия.

Репозиторий проекта: [OverlordCrowley/SII](https://github.com/OverlordCrowley/SII).

## Быстрый запуск

В Windows откройте `run.bat` двойным щелчком: начнётся диалог на русском языке.
Скрипт использует Python из `.venv`, доступный runtime Codex или системный `py`.
Обученные модели уже включены в проект. Для их работы нужен NumPy.

Установка на другом компьютере с Python 3.12:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pc_assistant demo
.\.venv\Scripts\python.exe -m pc_assistant chat
```

Далее `python` в примерах обозначает выбранный интерпретатор. Все команды
выполняются из папки проекта. Логика, фреймы и сеть работают без внешних
библиотек: `python -m pc_assistant --no-models demo`.

```powershell
python -m pc_assistant ask "Компьютер перегревается, вентилятор шумит, сам выключается"
python -m pc_assistant ask "Компьютер включается, черный экран, пищит" --json
python -m pc_assistant symptoms
python -m pc_assistant rules
python -m unittest discover -s tests -v
```

## Пример результата

Запрос: «Компьютер включается, черный экран, пищит».

1. Из фраз извлекаются `power_on=true`, `screen_on=false`, `beeps=true`.
2. R02 выводит `display_problem`.
3. R03 выводит `ram_failure`, затем R19 выводит `check_memory`.
4. Ответ показывает возможную проблему памяти, дерево объяснения,
   сведения о компоненте и рекомендуемые действия.

Запрос «Компьютер включается, черный экран» не доказывает отсутствие
звуковых сигналов. Система уточнит, слышны ли они, и не применит R04
до явного ответа «нет».

## Реализованные модули

| Неделя | Результат | Файл |
|---|---|---|
| 4 | Предметная область, база знаний, исходная сеть | `data/knowledge.json` |
| 5 | Логические факты, правила и обратный вывод | `pc_assistant/logic.py` |
| 6 | AND, OR, NOT, таблицы истинности, объяснения | `pc_assistant/logic.py` |
| 7 | 23 продукционных правила и прямой вывод | `pc_assistant/production.py` |
| 8 | 15 фреймов, экземпляры и наследование | `pc_assistant/frames.py` |
| 9 | Поиск, изменение и сохранение слотов | `pc_assistant/frames.py` |
| 10 | 22 узла и 22 связи, поиск BFS | `pc_assistant/network.py` |
| 11 | Датасет и классификатор Бернулли | `pc_assistant/dataset.py`, `pc_assistant/ml.py` |
| 12 | Обученная нейросеть 18–16–7 | `pc_assistant/ml.py`, `models/neural.json` |
| 13 | Объединённая экспертная система | `pc_assistant/expert.py` |
| 14 | Анализ русского запроса, ответ и диалог | `pc_assistant/assistant.py`, `pc_assistant/cli.py` |
| 15 | Интеграционные проверки, документация и презентация | `tests/`, `docs/`, `presentation/` |

## Работа с логикой и фреймами

```powershell
python -m pc_assistant logic "a AND (b OR NOT c)" --table
python -m pc_assistant logic ram_failure --facts '{"power_on":true,"screen_on":false,"beeps":true}'
python -m pc_assistant frames show office_pc
python -m pc_assistant frames find has_battery true
python -m pc_assistant frames set office_pc ram_gb 24
python -m pc_assistant frames create my_laptop laptop --slots '{"ram_gb":16}'
```

Наследование: `device`, `computer`, `desktop`, `office_pc`.
У `office_pc` объём RAM переопределён локально, а ОС наследуется от
`computer`. Изменения сохраняются в `.state/frames.json` и переживают
перезапуск. Исходная база знаний сохраняет свою учебную конфигурацию.
Для поиска строкового значения JSON используйте, например, `'"Windows"'`.

## Семантическая сеть

```powershell
python -m pc_assistant network path disk_noise storage
python -m pc_assistant network related laptop --depth 2
python -m pc_assistant network path device laptop --undirected
python -m pc_assistant network add-node printer "Принтер"
python -m pc_assistant network add-edge printer connected-to computer
```

По умолчанию поиск учитывает направление рёбер. При `--undirected`
обратный проход помечается `inverse:`. Пользовательские изменения
сохраняются в `.state/network.json`.

## Обучение и ограничения

```powershell
python -m pc_assistant train
python -m pc_assistant train --generate-dataset
```

Датасет содержит **1 260 синтетических примеров**, 18 бинарных признаков
и 7 классов. Метки задаются генератором профилей, независимо от правил.
Групповой split даёт 840 обучающих, 176 валидационных и 244 тестовых
записи. Одинаковые векторы не пересекают части выборки.

На тестовой части: Bayes accuracy **95,90%**, macro F1 **0,9592**;
MLP accuracy **96,31%**, macro F1 **0,9634**. Лучшие веса MLP выбираются
по валидационной потере. Подробные результаты и матрицы ошибок:
[результаты моделей](docs/MODEL_RESULTS.md).

Эти показатели относятся к синтетическому набору, а не к реальным
компьютерам. Учебный рейтинг моделей дополняет правила и не заменяет
доказанный вывод. Наивный Байес маргинализирует неизвестные признаки;
MLP подставляет 0,5, и при обучении получает примеры с маскированием.
При менее чем трёх известных симптомах рейтинг скрыт.

Анализ текста выполняется по словарю фраз, без LLM и внешнего API.
Список поддерживаемых фраз выводит команда `symptoms`. Неизвестные
сведения не становятся фактами, противоречивые сообщения требуют
уточнения. NOT применяется только к исходным симптомам. Возможны
несколько гипотез; правила не гарантируют окончательный диагноз.

## Материалы для сдачи

- [Отчёты по лабораторным работам и контрольные вопросы](docs/LABS.md).
- [Архитектура и алгоритмы](docs/ARCHITECTURE.md).
- [Сценарий защиты и ответы на вопросы](docs/DEFENSE.md).
- [Соответствие заданию](docs/ASSIGNMENT.md).
- [История этапов](docs/COMMITS.md).
- [Проверка кода](docs/CODE_REVIEW.md).
- Презентация `presentation/PC_Diagnostic_Assistant_GitHub.pptx`.

В отчётах нужно заполнить ФИО, группу и данные преподавателя.
Коммиты создавались при подготовке этого проекта, без имитации
прошлых недель. История отражает реальные изменения, а не даты занятий.
Ссылки на репозиторий и коммиты указаны в документации.

## Репозиторий GitHub

Основная ветка — `main`. Для получения проекта через SSH:

```powershell
git clone git@github.com:OverlordCrowley/SII.git
cd SII
```

Для отправки последующих изменений после коммита: `git push origin main`.
Для клонирования без SSH можно использовать
`https://github.com/OverlordCrowley/SII.git`.
В `.gitignore` исключены окружение, локальные изменения фреймов и
сети, временные файлы и файлы проверки оформления. В репозиторий входят
код, датасет, обученные веса, документация и презентация.
