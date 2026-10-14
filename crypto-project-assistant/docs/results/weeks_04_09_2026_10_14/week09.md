# Неделя 9 — поиск, изменение и сохранение фреймов

**Задание:** реализовать программу работы с фреймами, поиск и изменение свойств.

**Реализовано:** CLI-команды `create`, `set`, `find`, `show`, сохранение JSON
и повторная загрузка. Поиск учитывает унаследованные свойства и тип значения.
Веб-редактора фреймов пока нет; задание выполняется через командную строку.

## Где смотреть код

- [Команды](../../../project_assistant/workbench.py): `add_commands`, `run`.
- [Фреймы и хранение](../../../project_assistant/frames.py): `find`, `set`, `save`, `load`.
- [Атомарная запись JSON](../../../project_assistant/knowledge.py): `save_json`.

## Сохранённый результат

Каждая команда запускалась отдельным Python-процессом с общим файлом состояния:

| Операция | Реальный результат | Журнал |
|---|---|---|
| create my_protocol defi | Создан потомок defi | [create](week09-create.txt) |
| set my_protocol reviewed true | Слот reviewed=true | [set](week09-set.txt) |
| find reviewed true | Найден my_protocol | [find](week09-find.txt) |
| show my_protocol | Из файла прочитаны reviewed=true и наследуемая USD | [show](week09-show.txt) |
| find quote_currency USD | Найдены семь фреймов, включая my_protocol | [поиск наследуемого слота](week09-find-inherited.txt) |

[week09.json](week09.json) объединяет ответы всех операций.
[week09.frames.json](week09.frames.json) — фактически записанный файл состояния.
Повторное чтение в другом процессе подтверждает сохранение между запусками.
Учебный слот `reviewed` не является критерием оценки и не меняет балл исследования.

## Как повторить

Из папки приложения, с **новым отдельным файлом**:

```sh
python3 -m project_assistant frame --state demo-frames.json create my_protocol defi
python3 -m project_assistant frame --state demo-frames.json set my_protocol reviewed true
python3 -m project_assistant frame --state demo-frames.json find reviewed true
python3 -m project_assistant frame --state demo-frames.json show my_protocol
python3 -m project_assistant frame --state demo-frames.json find quote_currency '"USD"'
```

Параметр `--state` ставится перед действием. `create` выполняется один раз
для нового имени: повтор будет отклонён. Значения — JSON; пример кавычек
для строки выше предназначен для macOS/Linux. На Windows удобно запускать
эти же команды в PowerShell, передавая JSON-строку согласно правилам оболочки.
Не используйте файлы снимка как рабочее состояние повторной демонстрации.

**Результат задания:** создание, поиск, изменение, наследование и сохранение проверены.
[К списку недель](README.md).
