"""Сбор датированных данных монеты перед обычным экспертным анализом."""

from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
import json
from math import isfinite
import os
from pathlib import Path
import re
import ssl
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from .assets import load_aliases, normalize_alias
from .knowledge import ROOT
from .open_research import OpenResearch

COINGECKO = 'https://api.coingecko.com/api/v3'
MAX_RESPONSE = 2 * 1024 * 1024


class ResearchError(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        elif tag in ('p', 'br', 'div', 'li'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(value):
    parser = PlainText()
    parser.feed(value if isinstance(value, str) else '')
    return unescape(''.join(parser.parts)).strip()[:12000]


def public_url(value):
    if not isinstance(value, str) or len(value) > 500:
        return ''
    try:
        url = urlsplit(value)
        if url.scheme in ('https', 'http') and url.hostname and not url.username and not url.password:
            return value
    except ValueError:
        pass
    return ''


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, AttributeError, TypeError):
        return None


def number(value):
    return value if type(value) in (int, float) and 0 <= value <= 1e30 and isfinite(value) else None


class CoinResearch:
    def __init__(self, criteria):
        self.criteria = criteria
        self.aliases = load_aliases()
        self.open_research = OpenResearch(criteria)

    def request(self, url, payload=None, headers=None, timeout=12):
        context = ssl.create_default_context()
        # Python.org builds on macOS may lack their optional certificate bundle.
        if sys.platform == 'darwin' and Path('/etc/ssl/cert.pem').is_file():
            context.load_verify_locations('/etc/ssl/cert.pem')
        opener = build_opener(HTTPSHandler(context=context), NoRedirect())
        request_headers = {'User-Agent': 'Cryptoscope/1.0', 'Accept': 'application/json', **(headers or {})}
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
            request_headers['Content-Type'] = 'application/json'
        try:
            with opener.open(Request(url, data=body, headers=request_headers), timeout=timeout) as response:
                raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise ResearchError('Источник вернул слишком большой ответ. Повторите позже.')
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ResearchError('Источник вернул неподходящий формат данных.')
            return data
        except HTTPError as error:
            error.close()
            provider = 'CoinGecko'
            messages = {
                401: 'Проверьте API-ключ сервиса на сервере.',
                402: 'На аккаунте недостаточно кредитов.',
                403: 'Доступ ограничен. Проверьте разрешения или API-ключ сервиса.',
                404: 'Монета или страница не найдена.',
                429: 'Лимит запросов исчерпан. Подождите минуту и повторите.',
            }
            raise ResearchError(provider + ': ' + messages.get(error.code, 'Источник временно недоступен. Повторите позже.')) from error
        except (URLError, OSError, TimeoutError) as error:
            raise ResearchError('Не удалось получить данные по HTTPS. Проверьте интернет, сертификаты Python и повторите запрос.') from error
        except (ValueError, UnicodeError) as error:
            if isinstance(error, ResearchError):
                raise
            raise ResearchError('Источник вернул повреждённые данные. Повторите позже.') from error

    def coingecko(self, path, params=None):
        key = os.environ.get('COINGECKO_API_KEY', '').strip()
        headers = {'x-cg-demo-api-key': key} if key else {}
        return self.request(COINGECKO + path + ('?' + urlencode(params) if params else ''), headers=headers)

    def lookup(self, query='', coin_id=''):
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
            raise ValueError('Введите название монеты или тикер, до 200 символов.')
        if not isinstance(coin_id, str) or (coin_id and not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,127}', coin_id)):
            raise ValueError('Некорректный идентификатор монеты.')
        if not coin_id:
            alias = self.aliases.get(normalize_alias(query))
            search = alias['name'] if alias else query.strip()
            result = self.coingecko('/search', {'query': search})
            coins = result.get('coins')
            if not isinstance(coins, list):
                raise ResearchError('CoinGecko вернул некорректный результат поиска.')
            coins = [c for c in coins if isinstance(c, dict)
                     and isinstance(c.get('id'), str) and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,127}', c['id'])
                     and isinstance(c.get('name'), str) and isinstance(c.get('symbol'), str)]
            # Exact names take precedence; ambiguous tickers always require a choice.
            exact = [c for c in coins if normalize_alias(c['name']) == normalize_alias(search)
                     or normalize_alias(c['id']) == normalize_alias(search)]
            matches = exact or [c for c in coins if normalize_alias(c['symbol']) == normalize_alias(search)] or coins
            if not matches:
                raise ValueError('Монета не найдена. Попробуйте английское название или тикер.')
            if len(matches) != 1 or not (exact or normalize_alias(matches[0]['symbol']) == normalize_alias(search)):
                return {'choices': [{key: str(c[key])[:200] for key in ('id', 'name', 'symbol')} for c in matches[:8]]}
            coin_id = matches[0]['id']
        data = self.coingecko('/coins/' + coin_id, {
            'localization': 'false', 'tickers': 'true', 'community_data': 'false',
            'developer_data': 'false', 'sparkline': 'false',
        })
        if data.get('id') != coin_id or not isinstance(data.get('name'), str) or not isinstance(data.get('symbol'), str):
            raise ResearchError('Источник вернул другую монету или неполную идентификацию.')
        profile, notices = self.profile(data)
        research = self.open_research.run(profile, data)
        notices.extend(research['notices'])
        profile['description'] += '\n\n' + '\n'.join(notices)
        return {'profile': profile, 'notices': notices, 'coin_id': coin_id, 'research': research}

    def profile(self, data):
        now = datetime.now(timezone.utc)
        source = 'https://www.coingecko.com/en/coins/' + data['id']
        market = data.get('market_data')
        if not isinstance(market, dict):
            raise ResearchError('Для этой монеты источник не предоставил рыночных данных.')
        updated = timestamp(market.get('last_updated') or data.get('last_updated'))
        day = updated.date().isoformat() if updated else ''
        facts = {key: {'value': None, 'evidence': 'Автосбор не нашёл достаточного подтверждения этого критерия.',
                       'source': source, 'date': now.date().isoformat()} for key in self.criteria}

        def fact(key, value, evidence, origin=source, date=day):
            facts[key] = {'value': value, 'evidence': evidence[:2000], 'source': origin[:500], 'date': date}

        def usd(key):
            raw = market.get(key)
            return number(raw.get('usd')) if isinstance(raw, dict) else None

        numbers = {key: number(market.get(key)) for key in ('circulating_supply', 'total_supply', 'max_supply')}
        # Unknown supply stays unknown, including assets without a reported maximum.
        values = {**numbers, 'price_usd': usd('current_price'), 'market_cap_usd': usd('market_cap'),
                  'volume_24h_usd': usd('total_volume')}
        if not any(value is not None for value in values.values()):
            raise ResearchError('Для этой монеты нет доступного рыночного снимка.')
        fact('has_token', True, 'Источник идентифицирует собственный торгуемый актив ' + data['symbol'].upper() + '.')
        infinite_supply = market.get('max_supply_infinite') is True and numbers['max_supply'] is None
        supply_known = all(v is not None for v in numbers.values()) or (
            infinite_supply and numbers['circulating_supply'] is not None and numbers['total_supply'] is not None)
        fact('supply_disclosed', True if supply_known else None,
             'Предложение по агрегатору: ' + '; '.join(k + '=' + str(v) for k, v in numbers.items())
             + ('. Источник явно сообщает max_supply_infinite=true: фиксированного максимума нет.' if infinite_supply else '. Отсутствующий максимум не подставляется.')
             + ' Потерянные монеты отдельно не определены.')
        fact('metrics_dated', True if updated else None,
             'Рыночный снимок источника: ' + (updated.isoformat() if updated else 'дата не предоставлена') + '.')
        age = (now - updated).total_seconds() if updated else None
        fact('sources_current', True if age is not None and -300 <= age <= 86400 else None,
             'Дата получения: ' + now.isoformat() + '. Свежесть рыночного снимка проверена отдельно от даты получения.')
        links = data.get('links') if isinstance(data.get('links'), dict) else {}
        homes = links.get('homepage') if isinstance(links.get('homepage'), list) else []
        homepage = next((public_url(u) for u in homes if public_url(u)), '')
        repositories = links.get('repos_url') if isinstance(links.get('repos_url'), dict) else {}
        github = repositories.get('github') if isinstance(repositories.get('github'), list) else []
        repo = next((public_url(u) for u in github if public_url(u)), '')
        if repo:
            fact('code_public', None, 'Агрегатор указал репозиторий ' + repo + '. Содержимое и соответствие развёрнутой версии ещё не проверены.')
        raw_platforms = data.get('platforms') if isinstance(data.get('platforms'), dict) else {}
        platforms = [(k, v) for k, v in raw_platforms.items() if isinstance(k, str) and isinstance(v, str) and k and v]
        categories = data.get('categories') if isinstance(data.get('categories'), list) else []
        layer1 = 'Layer 1 (L1)' in categories
        network = ', '.join(k for k, _ in platforms)[:250] or (data['name'] if layer1 else '')
        contract = '; '.join(k + ': ' + v for k, v in platforms)[:250] or ('Нативный актив; адрес контракта не применяется' if layer1 else '')
        desc = data.get('description') if isinstance(data.get('description'), dict) else {}
        description = plain_text(desc.get('ru') or desc.get('en'))
        notices = ['Данные загружены автоматически. Агрегатор не заменяет независимую проверку проекта.',
                   'Биржевой объём не подтверждает реальных пользователей, глубину рынка, аудит или безопасность.',
                   'FDV рассчитывается по max supply, когда оно известно, иначе по total supply. Unlock не подставляется без датированного источника.']
        if data['id'] == 'bitcoin':
            # Reviewed technical evidence has its own date; live prices never come from this file.
            reference = json.loads((ROOT / 'data/bitcoin_research.json').read_text(encoding='utf-8'))
            facts.update(reference)
            layer1, network, contract = True, 'Bitcoin mainnet', 'Нативный BTC; адрес контракта не применяется'
        profile = {'name': data['name'][:200], 'symbol': data['symbol'].upper()[:250],
                   'type': 'layer1' if layer1 else 'crypto_project', 'network': network, 'contract': contract,
                   'description': 'Автоматический профиль CoinGecko: ' + source + '\n'
                                  + 'Рыночное обновление: ' + (updated.isoformat() if updated else 'не указано')
                                  + '\nОфициальный сайт по данным агрегатора: ' + (homepage or 'не указан')
                                  + '\n\nОписание источника (требует проверки):\n' + description,
                   'facts': facts,
                   'market': {**values, 'quote_currency': 'USD', 'fdv_basis': 'max' if numbers['max_supply'] else 'total',
                              'as_of': day, 'source': source + '; last_updated=' + (updated.isoformat() if updated else 'не указано'),
                              'unlock_date': ''}}
        return profile, notices
