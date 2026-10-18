"""Independent public market comparisons, with identity and measurement checks."""

from datetime import datetime, timezone
import re
from urllib.parse import urlsplit

from .public_sources import SourceError, canonical_url
from .supply_sources import fact, material, number
from .tokenomics import calculate


def paprika_peers(fetcher, data, profile):
    root = 'https://api.coinpaprika.com/v1'
    url = root + '/tickers'
    tickers = fetcher.json(url, limit=10 * 1024 * 1024)
    if not isinstance(tickers, list):
        raise SourceError('CoinPaprika: не получены рыночные снимки.')
    matches = [x for x in tickers[:10000] if isinstance(x, dict)
               and str(x.get('name', '')).casefold() == data['name'].casefold()
               and str(x.get('symbol', '')).casefold() == data['symbol'].casefold()]
    if len(matches) != 1 or not re.fullmatch(r'[a-z0-9-]{1,128}', str(matches[0].get('id', ''))):
        raise SourceError('CoinPaprika: имя и тикер не дают однозначной идентификации.')
    coin_id = matches[0]['id']
    meta_url = root + '/coins/' + coin_id
    meta = fetcher.json(meta_url)
    if (not isinstance(meta, dict) or meta.get('id') != coin_id
            or str(meta.get('name', '')).casefold() != data['name'].casefold()
            or str(meta.get('symbol', '')).casefold() != data['symbol'].casefold()):
        raise SourceError('CoinPaprika: идентификатор метаданных не совпал.')
    hosts = lambda urls: {urlsplit(canonical_url(u)).hostname.removeprefix('www.') for u in urls if isinstance(u, str) and u}
    own_hosts = hosts((data.get('links') or {}).get('homepage', []))
    linked_hosts = hosts((meta.get('links') or {}).get('website', []))
    if not own_hosts or not own_hosts.intersection(linked_hosts):
        raise SourceError('CoinPaprika: официальный сайт не совпал.')
    # Purposes only: platform standards, consensus algorithms and portfolios are not peer groups.
    purposes = {'layer-1-l1', 'layer-2-l2', 'smart-contracts', 'cryptocurrency', 'payments', 'defi',
                'lending', 'off-chain', 'oracle', 'privacy', 'gaming', 'stablecoin', 'decentralized-exchange'}
    tags = [x for x in meta.get('tags', []) if isinstance(x, dict) and x.get('id') in purposes]
    if not tags:
        raise SourceError('CoinPaprika: нет подходящей функциональной категории.')
    tags.sort(key=lambda x: (x['id'] in ('cryptocurrency', 'defi', 'smart-contracts'), x.get('coin_counter', 100000)))
    group = None
    for tag in tags[:2]:
        candidate = fetcher.json(root + '/tags/' + tag['id'] + '?additional_fields=coins')
        if (isinstance(candidate, dict) and candidate.get('id') == tag['id'] and candidate.get('type') == 'functional'
                and isinstance(candidate.get('coins'), list) and coin_id in candidate['coins']):
            group = candidate
            break
    if group is None:
        raise SourceError('CoinPaprika: функциональная группа не подтвердилась.')
    members = {x for x in group['coins'] if isinstance(x, str)}
    day = profile['market']['as_of']
    peers, seen = [], set()
    for row in tickers[:10000]:
        if not isinstance(row, dict) or row.get('id') == coin_id or row.get('id') not in members or row.get('id') in seen or not isinstance(row.get('name'), str):
            continue
        try:
            measured = datetime.fromisoformat(str(row.get('last_updated', '')).replace('Z', '+00:00'))
        except ValueError:
            continue
        if not measured.tzinfo or measured.date().isoformat() != day or not -300 <= (datetime.now(timezone.utc) - measured).total_seconds() <= 86400:
            continue
        quote = (row.get('quotes') or {}).get('USD') or {}
        cap, price = number(quote.get('market_cap')), number(quote.get('price'))
        supply = number(row.get('max_supply')) or number(row.get('total_supply'))
        if cap is not None and cap > 0:
            seen.add(row['id'])
            peers.append((row['name'], cap, price * supply if price is not None and price > 0 and supply is not None else None, row['last_updated']))
    peers.sort(key=lambda x: x[1], reverse=True)
    peers = peers[:3]
    own = calculate(profile['market'])['values']
    if not peers or own['market_cap_usd'] is None or own['market_cap_usd'] <= 0:
        raise SourceError('CoinPaprika: нет сопоставимых капитализаций на дату снимка.')
    text = f'CoinPaprika, функциональная группа {group.get("name", group["id"])}; дата {day}; USD. '
    text += f'{profile["name"]}: капитализация={own["market_cap_usd"]}, FDV={own["fdv_usd"]} (исходный рыночный снимок проекта). '
    text += '; '.join(f'{name}: капитализация={cap}, расчётный FDV={fdv}, обновление={stamp}' for name, cap, fdv, stamp in peers) + '. '
    text += 'FDV аналогов = цена × положительное max supply, иначе total supply; неизвестные величины не заменяются нулём. '
    text += 'Категория задана агрегатором и не означает одинаковую технологию, риск или справедливую цену; источники могут различаться методикой.'
    received = datetime.now(timezone.utc).isoformat()
    doc = material(url, text, received, 'valuation_comparable')
    doc['peer_names'] = [x[0] for x in peers]
    facts = {'valuation_comparable': fact(text, url, received)}
    if len(peers) >= 2:
        facts['competition_analyzed'] = fact('Экономическое сравнение с именованными проектами одной функциональной группы. ' + text, url, received)
    return [doc, material(meta_url, 'Идентификация по имени, тикеру и официальному сайту.', received, 'valuation_comparable'),
            material(root + '/tags/' + group['id'] + '?additional_fields=coins', 'Подтверждён состав функциональной группы ' + str(group.get('name')), received, 'valuation_comparable')], facts, []
