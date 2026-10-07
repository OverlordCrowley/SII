"""Расчёты по введённым числам, с явной валютой, датой и базой FDV."""

from datetime import date
from math import isfinite
import re

NUMBERS = ('price_usd', 'circulating_supply', 'total_supply', 'max_supply',
           'market_cap_usd', 'fdv_usd', 'volume_24h_usd', 'next_unlock_tokens')


def iso_date(value, label):
    if not isinstance(value, str) or (value and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value)):
        raise ValueError(f'{label}: требуется дата YYYY-MM-DD')
    if value:
        try:
            date.fromisoformat(value)
        except ValueError as error:
            raise ValueError(f'{label}: неверная календарная дата') from error
    return value


def calculate(raw):
    if not isinstance(raw, dict) or set(raw) - set(NUMBERS) - {'quote_currency','fdv_basis','as_of','source','unlock_date'}:
        raise ValueError('Некорректные поля рыночных данных')
    if raw.get('quote_currency', 'USD') != 'USD':
        raise ValueError('Все денежные показатели должны быть в USD. USDT не подставляется вместо USD.')
    market = {'quote_currency':'USD', 'fdv_basis':raw.get('fdv_basis','total')}
    if market['fdv_basis'] not in ('total','max'):
        raise ValueError('База FDV должна быть total или max')
    for key in NUMBERS:
        value = raw.get(key)
        if value is not None and (type(value) not in (int,float) or not 0 <= value <= 1e30):
            raise ValueError(f'{key}: требуется конечное неотрицательное число до 1e30 или null')
        market[key] = value
    market['as_of'] = iso_date(raw.get('as_of',''), 'Дата данных')
    market['unlock_date'] = iso_date(raw.get('unlock_date',''), 'Дата разблокировки')
    source = raw.get('source','')
    if not isinstance(source,str) or len(source)>2000:
        raise ValueError('Источник чисел должен быть строкой до 2000 символов')
    market['source'] = source.strip()
    circ,total,maximum = (market[k] for k in ('circulating_supply','total_supply','max_supply'))
    if circ is not None and total is not None and circ > total:
        raise ValueError('Circulating supply не может превышать total supply')
    if maximum is not None and ((total is not None and total > maximum) or (circ is not None and circ > maximum)):
        raise ValueError('Известное предложение не может превышать max supply')
    price = market['price_usd']
    basis = total if market['fdv_basis']=='total' else maximum
    warnings=[]

    def finite_result(value, label, positive):
        if not isfinite(value) or (positive and value == 0):
            warnings.append(f'{label}: результат вне точности вычислений; показатель остаётся неизвестным.')
            return None
        return value

    calculated_cap = None if price is None or circ is None else finite_result(price*circ, 'Капитализация', price>0 and circ>0)
    calculated_fdv = None if price is None or basis is None else finite_result(price*basis, 'FDV', price>0 and basis>0)
    cap = market['market_cap_usd'] if market['market_cap_usd'] is not None else calculated_cap
    fdv = market['fdv_usd'] if market['fdv_usd'] is not None else calculated_fdv
    conflict=False
    for label,stated,computed in [('Капитализация',market['market_cap_usd'],calculated_cap),('FDV',market['fdv_usd'],calculated_fdv)]:
        if stated is not None and computed is not None and not computed*0.99 <= stated <= computed*1.01:
            warnings.append(f'{label} отличается от расчёта более чем на 1%: проверьте дату и базу предложения.')
            conflict=True
    ratio = finite_result(fdv/cap, 'FDV / капитализация', fdv>0) if cap is not None and cap>0 and fdv is not None and not conflict else None
    circulating_share = finite_result(circ/basis*100, 'Доля обращения', circ>0) if circ is not None and basis is not None and basis>0 else None
    unlock = market['next_unlock_tokens']
    unlock_share = finite_result(unlock/circ*100, 'Unlock / обращение', unlock>0) if unlock is not None and circ is not None and circ>0 else None
    volume = market['volume_24h_usd']
    volume_share = finite_result(volume/cap*100, 'Объём / капитализация', volume>0) if volume is not None and cap is not None and cap>0 and not conflict else None
    has_numbers = any(market[key] is not None for key in NUMBERS)
    if has_numbers and not market['as_of']:
        warnings.append('Числа введены без даты. Уточните дату снимка перед сравнением.')
    if has_numbers and not market['source']:
        warnings.append('Источник чисел не указан. Добавьте первичный источник или ссылку на данные.')
    if market['as_of']:
        age=(date.today()-date.fromisoformat(market['as_of'])).days
        if age<0: warnings.append('Дата рыночного снимка находится в будущем: проверьте её.')
        elif age>30: warnings.append('Рыночному снимку больше 30 дней. Для текущего сравнения соберите новый снимок.')
    dated_unlock = bool(market['as_of'] and market['unlock_date'] and market['unlock_date']>market['as_of'])
    if unlock is not None and not dated_unlock:
        warnings.append('Дата разблокировки отсутствует или не позже даты снимка; сигнал будущего unlock не выводится.')
    facts={}
    if ratio is not None: facts['fdv_gap_high']=ratio>=3
    if circulating_share is not None: facts['low_float']=circulating_share<25
    if unlock_share is not None and dated_unlock: facts['large_unlock']=unlock_share>=10
    values={'market_cap_usd':cap,'fdv_usd':fdv,'calculated_market_cap_usd':calculated_cap,'calculated_fdv_usd':calculated_fdv,
            'fdv_to_cap':ratio,'circulating_share_pct':circulating_share,'unlock_share_pct':unlock_share,'volume_to_cap_pct':volume_share}
    return {'input':market,'values':values,'facts':facts,'warnings':warnings,
            'formulas':{'market_cap_usd':'Цена USD × circulating supply','fdv_usd':f"Цена USD × {market['fdv_basis']} supply",
                        'circulating_share_pct':f"Circulating / {market['fdv_basis']} supply × 100%",'unlock_share_pct':'Объём unlock / circulating supply × 100%'},
            'thresholds':'Учебные пороги: FDV/капитализация ≥3; обращение <25% базы FDV; будущий unlock ≥10% обращения.'}
