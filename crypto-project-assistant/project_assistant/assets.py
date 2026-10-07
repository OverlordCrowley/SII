"""Точное распознавание названий и тикеров по локальному справочнику."""

import json
import unicodedata

from .knowledge import ROOT


def normalize_alias(value):
    value = unicodedata.normalize('NFKC', value).casefold().replace('ё', 'е')
    return ' '.join(value.strip().removeprefix('$').split())


def load_aliases():
    entries = json.loads((ROOT / 'data/asset_aliases.json').read_text(encoding='utf-8'))
    aliases = {}
    for entry in entries:
        for value in [entry['name'], entry['symbol'], *entry['aliases']]:
            alias = normalize_alias(value)
            if not alias:
                raise ValueError('Пустой алиас в справочнике монет')
            if alias in aliases and aliases[alias] != entry:
                raise ValueError(f'Алиас «{value}» указан для разных монет')
            aliases[alias] = entry
    return aliases


def resolve_identity(name, symbol, description, aliases):
    by_name = aliases.get(normalize_alias(name))
    by_symbol = aliases.get(normalize_alias(symbol))
    if by_name and symbol and by_symbol != by_name:
        raise ValueError(f'Название «{name}» соответствует {by_name["symbol"]}, '
                         f'но указан другой тикер «{symbol}». Уточните монету.')
    asset = by_name or by_symbol
    if not asset and not name and not symbol:
        asset = aliases.get(normalize_alias(description))
    if asset:
        name = asset['name'] if not name or by_name else name
        symbol = asset['symbol']
    return name or 'Проект без названия', symbol
