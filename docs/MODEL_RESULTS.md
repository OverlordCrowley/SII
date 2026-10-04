# Результаты обучения моделей

Данные синтетические. Признаки генерируются по явно заданным профилям,
а метки задаются генератором. Результаты характеризуют этот учебный
набор и не являются оценкой реальной диагностики компьютеров.

## Выборки и обучение

| Часть | Записей |
|---|---:|
| train | 840 |
| validation | 176 |
| test | 244 |

Всего 1 260 записей, 18 бинарных признаков и 7 классов.
Группы одинаковых векторов не пересекают части выборки.
Seed генерации и обучения — 42. SHA-256 датасета:

`c4f08ff2457068d9b677e75fa10322b98ec1658dea0f7e36a9bdce3c259207f9`

MLP 18–16–7, tanh и softmax, 600 эпох, шаг 0,18.
Лучшие веса выбраны по validation на эпохе 591.
В 10% позиций обучения признак заменяется на 0,5.

## Итоговые показатели на test

| Модель | Accuracy | Macro F1 |
|---|---:|---:|
| Majority baseline | 8.1967% | — |
| bayes | 95.9016% | 0.9592 |
| neural | 96.3115% | 0.9634 |

## bayes по классам

| Класс | Precision | Recall | F1 | Support |
|---|---:|---:|---:|---:|
| power_failure | 0.9630 | 1.0000 | 0.9811 | 26 |
| display_failure | 0.9596 | 0.9596 | 0.9596 | 99 |
| ram_failure | 0.9600 | 0.8276 | 0.8889 | 29 |
| overheating | 0.9565 | 1.0000 | 0.9778 | 22 |
| disk_failure | 1.0000 | 0.9545 | 0.9767 | 22 |
| network_failure | 0.8696 | 1.0000 | 0.9302 | 20 |
| software_issue | 1.0000 | 1.0000 | 1.0000 | 26 |

### Матрица ошибок bayes

Строки — истинные классы, столбцы — предсказанные. Порядок классов:
power_failure, display_failure, ram_failure, overheating, disk_failure, network_failure, software_issue.

| true / predicted | power_failure | display_failure | ram_failure | overheating | disk_failure | network_failure | software_issue |
|---|---:|---:|---:|---:|---:|---:|---:|
| power_failure | 26 | 0 | 0 | 0 | 0 | 0 | 0 |
| display_failure | 1 | 95 | 1 | 0 | 0 | 2 | 0 |
| ram_failure | 0 | 4 | 24 | 1 | 0 | 0 | 0 |
| overheating | 0 | 0 | 0 | 22 | 0 | 0 | 0 |
| disk_failure | 0 | 0 | 0 | 0 | 21 | 1 | 0 |
| network_failure | 0 | 0 | 0 | 0 | 0 | 20 | 0 |
| software_issue | 0 | 0 | 0 | 0 | 0 | 0 | 26 |

## neural по классам

| Класс | Precision | Recall | F1 | Support |
|---|---:|---:|---:|---:|
| power_failure | 0.9286 | 1.0000 | 0.9630 | 26 |
| display_failure | 0.9694 | 0.9596 | 0.9645 | 99 |
| ram_failure | 0.9615 | 0.8621 | 0.9091 | 29 |
| overheating | 1.0000 | 1.0000 | 1.0000 | 22 |
| disk_failure | 1.0000 | 0.9545 | 0.9767 | 22 |
| network_failure | 0.8696 | 1.0000 | 0.9302 | 20 |
| software_issue | 1.0000 | 1.0000 | 1.0000 | 26 |

### Матрица ошибок neural

Строки — истинные классы, столбцы — предсказанные. Порядок классов:
power_failure, display_failure, ram_failure, overheating, disk_failure, network_failure, software_issue.

| true / predicted | power_failure | display_failure | ram_failure | overheating | disk_failure | network_failure | software_issue |
|---|---:|---:|---:|---:|---:|---:|---:|
| power_failure | 26 | 0 | 0 | 0 | 0 | 0 | 0 |
| display_failure | 1 | 95 | 1 | 0 | 0 | 2 | 0 |
| ram_failure | 1 | 3 | 25 | 0 | 0 | 0 | 0 |
| overheating | 0 | 0 | 0 | 22 | 0 | 0 | 0 |
| disk_failure | 0 | 0 | 0 | 0 | 21 | 1 | 0 |
| network_failure | 0 | 0 | 0 | 0 | 0 | 20 | 0 |
| software_issue | 0 | 0 | 0 | 0 | 0 | 0 | 26 |

## Интерпретация

Модели различают заданные синтетические профили лучше baseline.
Небольшая разница accuracy между ними не доказывает превосходство
на реальных данных. Один групповой split не заменяет полноценную
оценку устойчивости по нескольким реальным выборкам.

При неполном вводе Bayes исключает неизвестные признаки из
правдоподобия, а MLP получает значение 0,5. Оценки распределения
не калиброваны как вероятность настоящей неисправности.
При менее чем трёх известных симптомах рейтинг не выводится.

Для пересчёта: `python -m pc_assistant train`.
Для проверки сохранённых весов: `python -m unittest discover -s tests -v`.
