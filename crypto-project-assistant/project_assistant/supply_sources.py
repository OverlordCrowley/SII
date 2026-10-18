"""Публичные резервные источники vesting и распределения адресов, без ключей."""

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation, localcontext
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit

from .public_sources import DocumentParser, SourceError, canonical_url, read_document


def fact(text, source, date):
    return {'value': True, 'evidence': text[:2000], 'source': source[:500], 'date': date[:10]}


def number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)) or len(str(value)) > 100:
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and 0 <= result <= Decimal('1e100') else None
    except InvalidOperation:
        return None


def material(url, text, received_at, key):
    return {'url': url, 'text': text, 'received_at': received_at, 'links': [],
            'primary': False, 'retrieved_for': key, 'targeted_criteria': [key]}


class RichTable(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.cells, self.cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.cells = []
        elif tag == 'td' and self.cells is not None:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag == 'td' and self.cell is not None:
            self.cells.append(' '.join(''.join(self.cell).split()))
            self.cell = None
        elif tag == 'tr' and self.cells is not None:
            if self.cells:
                self.rows.append(self.cells)
            self.cells = None


def coincarp_holders(fetcher, data):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,127}', str(data.get('id', ''))):
        return [], {}, []
    url = 'https://www.coincarp.com/currencies/' + data['id'] + '/richlist/'
    response = fetcher.get(url)
    raw = response['body'].decode('utf-8', errors='replace')
    title = re.search(r'<title[^>]*>(.*?)</title>', raw, re.I | re.S)
    if not title or not re.search(r'\b' + re.escape(data['name']) + r'\b', title[1], re.I):
        raise SourceError('CoinCarp: название монеты в таблице не совпало.')
    headers = re.findall(r'<h[12]\b[^>]*>(.*?)</h[12]>', raw, re.I | re.S)
    identity = ' '.join(re.sub(r'<[^>]+>', ' ', h) for h in headers)
    if not re.search(re.escape(data['name']) + r'\s*(?:Price\s*)?\(?\s*' + re.escape(data['symbol']) + r'\b', identity, re.I):
        raise SourceError('CoinCarp: имя и тикер монеты не подтверждены заголовком.')
    contracts = data.get('platforms') or {}
    contracts = [v for v in contracts.values() if isinstance(v, str) and len(v) >= 20] if isinstance(contracts, dict) else []
    if contracts and not any(contract.casefold() in raw.casefold() for contract in contracts):
        raise SourceError('CoinCarp: не найден точный контракт исследуемого токена.')
    homes = (data.get('links') or {}).get('homepage', [])
    home_hosts = {urlsplit(canonical_url(u)).hostname.removeprefix('www.') for u in homes if isinstance(u, str) and u}
    if home_hosts:
        page = DocumentParser(url)
        page.feed(raw)
        linked_hosts = {urlsplit(u).hostname for u, _ in page.links}
        if not any(host and host.removeprefix('www.') in home_hosts for host in linked_hosts):
            raise SourceError('CoinCarp: официальный сайт не совпал с CoinGecko.')
    parser = RichTable()
    parser.feed(raw)
    address_pattern = r'(?:0x[0-9a-fA-F]{40}|[a-z]{1,12}1[ac-hj-np-z02-9]{20,120}|[1-9A-HJ-NP-Za-km-z]{26,64})'
    rows, seen = [], set()
    for cells in parser.rows:
        if len(cells) < 4 or not cells[0].isdigit() or not re.fullmatch(r'\d+(?:\.\d+)?%', cells[3]):
            continue
        match = re.fullmatch('(' + address_pattern + r')(?:\s+(.+))?', cells[1])
        amount = number(cells[2].replace(',', ''))
        percent = number(cells[3][:-1])
        if not match or amount is None or percent is None or percent > 100 or match[1] in seen:
            continue
        seen.add(match[1])
        rows.append((match[1], match[2] or 'Без публичной метки', amount, percent))
        if len(rows) >= 100:
            break
    if len(rows) < 5:
        raise SourceError('CoinCarp: недостаточно валидных адресов и балансов.')
    text = f'CoinCarp, получено {response["received_at"]}: прочитано {len(rows)} крупных адресов {data["name"]}. '
    text += '; '.join(f'{a} — {label}: {amount} {data["symbol"].upper()}, доля источника {percent}%' for a, label, amount, percent in rows[:5]) + '. '
    text += ('Это адреса, не независимые владельцы. Биржевые метки относятся к кастодиальному адресу, средства могут принадлежать множеству клиентов. '
             'Без публичной метки владелец не установлен. Проценты приведены по базе CoinCarp, не пересчитаны по текущему circulating supply; '
             'источник не раскрыл время снимка и общий блок. Точность и актуальность меток независимо не подтверждены.')
    doc = material(url, text, response['received_at'], 'concentration_known')
    # An anonymous list alone cannot establish attributed ownership concentration.
    facts = {'concentration_known': fact(text, url, response['received_at'])} if any(r[1] != 'Без публичной метки' for r in rows) else {}
    return [doc], facts, []


def corporate_holders(fetcher, data):
    """Attributed disclosed treasuries; keep this subset separate from anonymous addresses."""
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,127}', str(data.get('id', ''))):
        return [], {}, []
    url = 'https://api.coingecko.com/api/v3/companies/public_treasury/' + data['id']
    payload = fetcher.json(url)
    if not isinstance(payload, dict) or not isinstance(payload.get('companies'), list):
        raise SourceError('CoinGecko Treasuries не вернул компании и раскрытые резервы.')
    rows, seen = [], set()
    for item in payload['companies'][:200]:
        if not isinstance(item, dict) or not isinstance(item.get('name'), str):
            continue
        name = item['name'][:150]
        holdings = number(item.get('total_holdings'))
        share = number(item.get('percentage_of_total_supply'))
        if not name.strip() or name.casefold() in seen or holdings is None or holdings <= 0 or share is None or share > 100:
            continue
        seen.add(name.casefold())
        rows.append((name, holdings, share))
    if len(rows) < 3:
        raise SourceError('CoinGecko Treasuries: недостаточно атрибутированных держателей.')
    rows.sort(key=lambda r: r[1], reverse=True)
    received = datetime.now(timezone.utc).isoformat()
    text = f'CoinGecko Public Treasuries, получено {received}: {len(rows)} именованных корпоративных держателей {data["name"]}. '
    text += '; '.join(f'{name}: {amount} {data["symbol"].upper()} ({share}% в поле percentage_of_total_supply источника)' for name, amount, share in rows[:5]) + '. '
    text += ('Разобран только раскрытый корпоративный сегмент, не все крупные владельцы. Данные основаны на отчётности и заявлениях; '
             'адреса компаний API не раскрыл, поэтому им не приписываются адреса из rich list. '
             'Даты индивидуальной отчётности и единая высота цепи не указаны; получение сегодня не доказывает свежесть каждого остатка. '
             'Доли использованы в определении агрегатора; это не полная доля китов, анонимных адресов, стейкинга или бирж.')
    return [material(url, text, received, 'concentration_known')], {'concentration_known': fact(text, url, received)}, []


def web_treasuries(fetcher, data):
    """Public HTML delivery fallback; do not imply an independent underlying dataset."""
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,127}', str(data.get('id', ''))):
        return [], {}, []
    url = 'https://www.datawallet.com/treasury/' + data['id']
    response = fetcher.get(url)
    raw = response['body'].decode('utf-8', errors='replace')
    title = re.search(r'<title[^>]*>(.*?)</title>', raw, re.I | re.S)
    expected = re.escape(data['name']) + r'\s*\(' + re.escape(data['symbol']) + r'\)\s+Treasury'
    if not title or not re.search(expected, title[1], re.I):
        raise SourceError('Datawallet: название и тикер treasury-таблицы не совпали.')
    header = re.search(r'<thead\b[^>]*>(.*?)</thead>', raw, re.I | re.S)
    if not header or not re.search(r'Total Coins', header[1], re.I) or not re.search(r'% Total Supply', header[1], re.I):
        raise SourceError('Datawallet: единицы столбцов таблицы изменились.')
    parser = RichTable()
    parser.feed(raw)
    rows, seen = [], set()
    for cells in parser.rows:
        if len(cells) != 8 or cells[1] != 'Company':
            continue
        name = re.sub(r'^\d+[\W_]*', '', cells[0]).strip()[:150]
        amount = re.fullmatch(r'([\d,.]+)([KMB]?)', cells[2])
        percent = re.fullmatch(r'([\d.]+)%', cells[6])
        balance = number(amount[1].replace(',', '')) if amount else None
        share = number(percent[1]) if percent else None
        if not name or name.casefold() in seen or balance is None or balance <= 0 or share is None or share > 100:
            continue
        seen.add(name.casefold())
        balance *= {'': 1, 'K': 1000, 'M': 1000000, 'B': 1000000000}[amount[2]]
        rows.append((name, balance, share))
    if len(rows) < 3:
        raise SourceError('Datawallet: недостаточно валидных именованных корпоративных резервов.')
    rows.sort(key=lambda r: r[1], reverse=True)
    received = response['received_at']
    text = f'Datawallet, получено {received}: {len(rows)} именованных компаний с раскрытыми резервами {data["name"]}. '
    text += '; '.join(f'{name}: около {amount} {data["symbol"].upper()}, доля источника {share}%' for name, amount, share in rows[:5]) + '. '
    text += ('Проверен только раскрытый корпоративный сегмент, не все крупные владельцы. Значения в таблице округлены; '
             'время отчётности каждой компании и база расчёта процентов отдельно не проверены, дата получения не доказывает свежесть остатков. '
             'Адреса этим компаниям не приписываются. Это резервный способ доставки публичной таблицы; независимость исходного набора от других агрегаторов не установлена.')
    return [material(url, text, received, 'concentration_known')], {'concentration_known': fact(text, url, received)}, []


def blockscout_holders(fetcher, data, *, root='https://eth.blockscout.com/api/v2', platform='ethereum', native=None):
    """EVM native balances or an exact ERC-20 contract; never choose by ticker alone."""
    native = data.get('id') == 'ethereum' if native is None else native
    platforms = data.get('platforms')
    address = platforms.get(platform) if isinstance(platforms, dict) else None
    if not native and (not isinstance(address, str) or not re.fullmatch(r'0x[0-9a-fA-F]{40}', address)):
        return [], {}, []
    if native:
        url = root + '/addresses'
        payload = fetcher.json(url)
        if not isinstance(payload, dict):
            raise SourceError('Blockscout вернул неподходящий формат адресов.')
        supply, divisor = number(payload.get('total_supply')), Decimal(10) ** 18
    else:
        token_url = root + '/tokens/' + address
        token = fetcher.json(token_url)
        if (not isinstance(token, dict) or str(token.get('address_hash', '')).casefold() != address.casefold()
                or str(token.get('symbol', '')).casefold() != str(data.get('symbol', '')).casefold()
                or token.get('type') != 'ERC-20'):
            raise SourceError('Blockscout: контракт, тикер или тип токена не совпали.')
        # Both token supply and holder balances are in the same smallest unit.
        supply, divisor = number(token.get('total_supply')), Decimal(1)
        url = token_url + '/holders'
        payload = fetcher.json(url)
    if supply is None or supply <= 0:
        raise SourceError('Blockscout не раскрыл пригодное предложение для расчёта долей.')
    if not isinstance(payload, dict) or not isinstance(payload.get('items'), list):
        raise SourceError('Blockscout не вернул таблицу держателей.')
    rows, seen = [], set()
    for item in payload.get('items', [])[:100]:
        if not isinstance(item, dict):
            continue
        owner = item if native else item.get('address', {})
        if not isinstance(owner, dict):
            continue
        holder = owner.get('hash', '')
        balance = number(item.get('coin_balance') if native else item.get('value'))
        if not isinstance(holder, str) or not re.fullmatch(r'0x[0-9a-fA-F]{40}', holder) or holder.casefold() in seen or balance is None:
            continue
        share = balance / divisor / supply * 100
        if not 0 <= share <= 100:
            continue
        seen.add(holder.casefold())
        metadata = owner.get('metadata')
        tags = metadata.get('tags') or [] if isinstance(metadata, dict) else []
        tags = tags if isinstance(tags, list) else []
        labels = [t['name'] for t in tags if isinstance(t, dict) and t.get('tagType') == 'name' and isinstance(t.get('name'), str)]
        public_tags = owner.get('public_tags')
        labels += [t.get('display_name') or t.get('label') for t in public_tags if isinstance(t, dict)] if isinstance(public_tags, list) else []
        label = next((s[:120] for s in labels + [owner.get('name')] if isinstance(s, str) and s.strip()), 'Без публичной метки')
        kind = 'контракт' if owner.get('is_contract') is True else 'адрес'
        rows.append((holder, label, kind, share, owner.get('block_number_balance_updated_at')))
    if len(rows) < 5 or not any(r[1] != 'Без публичной метки' for r in rows):
        raise SourceError('Blockscout: недостаточно валидных крупных адресов с публичными метками.')
    received = datetime.now(timezone.utc).isoformat()
    text = f'Blockscout, снимок получен {received}: рассмотрено {len(rows)} крупных адресов {data.get("name")}.'
    text += ' ' + '; '.join(f'{a} — {label} ({kind}): {share:.4f}%' for a, label, kind, share, _ in rows[:5]) + '. '
    text += ('Доли вычислены по балансу и предложению одного источника. Это распределение адресов, не конечных владельцев. '
             'Биржи хранят активы клиентов, мосты и wrapped-контракты — обеспечение. '
             'Beacon Deposit Contract отражает накопленные депозиты, не долю одного владельца или текущую долю стейкинга. '
             'Метки третьей стороны не доказывают принадлежность; неразмеченные адреса не атрибутированы. '
             'API не гарантирует один общий блок всех балансов; дата получения не является датой измерения.')
    docs = [material(url, text, received, 'concentration_known')]
    facts = {'concentration_known': fact(text, url, received)}
    if not native:
        identity = f'Blockscout: точный контракт {address} в сети {platform}, тип ERC-20 и тикер {data.get("symbol")} совпали с данными CoinGecko. Это идентификация контракта, не аудит его безопасности; другие сети и мосты этим не проверены.'
        docs.append(material(token_url, identity, received, 'chain_asset_verified'))
        facts['chain_asset_verified'] = fact(identity, token_url, received)
    return docs, facts, []


def multichain_holders(fetcher, data):
    """Resolve exact platform/contract through public registries, never a ticker guess."""
    platforms = fetcher.json('https://api.coingecko.com/api/v3/asset_platforms')
    chains = fetcher.json('https://raw.githubusercontent.com/blockscout/chainscout/main/data/chains.json')
    if not isinstance(platforms, list) or not isinstance(chains, dict):
        raise SourceError('Реестры сетей CoinGecko/Blockscout недоступны.')
    contracts = data.get('platforms') or {}
    if not isinstance(contracts, dict):
        return [], {}, []
    candidates = []
    for platform in platforms:
        if not isinstance(platform, dict):
            continue
        chain_id = platform.get('chain_identifier')
        if type(chain_id) is not int or chain_id <= 0:
            continue
        chain = chains.get(str(chain_id))
        if not isinstance(chain, dict) or chain.get('isTestnet') is not False:
            continue
        native = platform.get('native_coin_id') == data.get('id')
        address = contracts.get(platform.get('id'))
        token = isinstance(address, str) and re.fullmatch(r'0x[0-9a-fA-F]{40}', address)
        if not token and not native:
            continue
        if native and str(chain.get('native_currency', '')).casefold() != str(data.get('symbol', '')).casefold():
            continue
        for explorer in chain.get('explorers', [])[:2]:
            if not isinstance(explorer, dict) or not isinstance(explorer.get('url'), str):
                continue
            root = canonical_url(explorer['url']).rstrip('/') + '/api/v2'
            if root == 'https://eth.blockscout.com/api/v2':
                continue  # Already attempted by the fast Ethereum provider.
            candidates.append((root, platform['id'], bool(native), chain_id))
    notes = []
    for root, platform, native, chain_id in candidates[:3]:
        try:
            docs, facts, messages = blockscout_holders(fetcher, data, root=root, platform=platform, native=native)
            for item in facts.values():
                item['evidence'] = (f'Сеть {platform}, chain_id={chain_id}; ' + item['evidence'])[:2000]
            if facts:
                return docs, facts, notes + messages
        except (SourceError, ValueError, TypeError, KeyError) as error:
            notes.append(f'Blockscout {platform}: {str(error)[:150]}')
    return [], {}, notes


def answer(text, question):
    match = re.search(re.escape(question) + r'\s*\n([^\n]+)', text, re.I)
    return match[1].strip() if match else ''


def tokenomist_unlocks(fetcher, data):
    coin_id, name, symbol = data.get('id', ''), data.get('name', ''), data.get('symbol', '')
    if not re.fullmatch(r'[a-z0-9-]{1,128}', coin_id) or not name or not symbol:
        return [], {}, [], {}
    slug = {'avalanche-2': 'avalanche', 'ethereum': 'ethereum/eth-unlocks', 'arbitrum': 'arbitrum'}.get(coin_id, coin_id)
    url = 'https://tokenomist.ai/' + slug
    doc = read_document(fetcher, url)
    if (doc['url'].rstrip('/') != url or not re.search(re.escape(name) + r'\s*\(' + re.escape(symbol) + r'\)', doc['text'][:300], re.I)):
        raise SourceError('Tokenomist: страница или идентификаторы монеты не совпали.')
    info = doc['text'].split('Token Information')[-1]
    stamp = re.search(r'Last updated\s+(\d{2}/\d{2}/\d{2}\s+\d{2}:\d{2})\s+UTC', info)
    if not stamp:
        raise SourceError('Tokenomist не указал дату обновления сведений о монете.')
    updated = datetime.strptime(stamp[1], '%m/%d/%y %H:%M').replace(tzinfo=timezone.utc)
    if not -300 <= (datetime.now(timezone.utc) - updated).total_seconds() <= 7 * 86400:
        raise SourceError('Tokenomist: сведения о vesting устарели или датированы будущим.')
    status = answer(info, f'When is the next {name} unlock?')
    allocation = answer(info, f'How is {name} allocated across stakeholders?')
    schedule = answer(info, f'What is the {name} token vesting schedule?')
    facts, updates, notes = {}, {}, []
    context = f'Tokenomist, обновление {updated.isoformat()}. '
    if allocation and schedule and re.search(r'\d+(?:\.\d+)?%', allocation):
        facts['vesting_disclosed'] = fact(context + 'Распределение: «' + allocation[:650] + '». Описание vesting: «' + schedule[:450] + '». Это данные агрегатора; первичные договоры отдельно не проверены.', url, updated.isoformat())
    if re.fullmatch(re.escape(name) + r' is fully unlocked\.', status, re.I):
        supply = answer(info, f'How much {name} supply is currently unlocked?')
        if re.search(r'remaining tokens are still locked', supply, re.I):
            raise SourceError('Tokenomist противоречит себе: полностью unlocked и одновременно остаются locked-токены.')
        text = context + 'Статус планового vesting, цитата: «' + status + '». В рамках опубликованного графика агрегатор не указывает следующего vesting-unlock. '
        text += 'Это не прекращение эмиссии, не отсутствие заблокированного стейка и не прогноз выходов валидаторов. Частные договоры и все блокировки on-chain этим не проверены.'
        facts['unlock_schedule_known'] = fact(text, url, updated.isoformat())
        updates['next_unlock_tokens'] = 0
    else:
        next_date = re.search(r'is scheduled for ([A-Za-z]+ \d{1,2}, \d{4})\.', status)
        if next_date:
            date = datetime.strptime(next_date[1], '%B %d, %Y').date()
            if date <= datetime.now(timezone.utc).date():
                raise SourceError('Tokenomist: указанная следующая разблокировка уже в прошлом.')
            updates['unlock_date'] = date.isoformat()
            notes.append(context + status + ' Объём ближайшего события в открытом тексте не раскрыт; ноль не подставлен. ' + url)
    text = context + status + '\n' + schedule + '\n' + allocation
    doc = material(doc['url'], text, doc['received_at'], 'unlock_schedule_known')
    doc['targeted_criteria'].append('vesting_disclosed')
    return [doc], facts, notes, updates


def llama_unlocks(fetcher, data):
    """Read the public web page, not the subscription API. 403/paid gates remain failures."""
    slug = {'arbitrum': 'arbitrum-foundation'}.get(data.get('id'), data.get('id', ''))
    if not re.fullmatch(r'[a-z0-9-]{1,128}', slug):
        return [], {}, [], {}
    url = 'https://defillama.com/unlocks/' + slug
    doc = read_document(fetcher, url)
    symbol = re.escape(str(data.get('symbol', '')))
    pattern = r'Next ' + symbol + r' unlock:\s*([A-Za-z]{3} \d{1,2}, \d{4}),\s*([\d,.]+)([kmb]?)\s+' + symbol + r'\b([^\n]{0,500})'
    match = re.search(pattern, doc['text'], re.I)
    if not match:
        return [], {}, ['DefiLlama: в открытой странице не найдено полного ближайшего события. ' + url], {}
    # A URL and ticker alone cannot distinguish a similarly named project.
    if not re.search(r'\b' + re.escape(str(data.get('name', ''))) + r'\b', doc['text'][:300], re.I):
        raise SourceError('DefiLlama Unlocks: название монеты не совпало.')
    date = datetime.strptime(match[1], '%b %d, %Y').date()
    amount = number(match[2].replace(',', ''))
    amount = amount * {'': 1, 'k': 1000, 'm': 1000000, 'b': 1000000000}[match[3].lower()] if amount is not None else None
    if date <= datetime.now(timezone.utc).date() or amount is None or not 0 < amount <= Decimal('1e30') or not re.search(r'\bfor\b', match[4]):
        raise SourceError('DefiLlama: событие не содержит будущей даты, объёма и получателей.')
    text = 'Публичная страница DefiLlama, получено ' + doc['received_at'] + '. Ближайшее событие: «' + match[0] + '». Дата обновления самого графика отдельно не указана. Проверено одно ближайшее событие, не все возможные договоры.'
    return [material(doc['url'], text, doc['received_at'], 'unlock_schedule_known')], {'unlock_schedule_known': fact(text, doc['url'], doc['received_at'])}, [], {'next_unlock_tokens': float(amount), 'unlock_date': date.isoformat()}


HEDGEY_VESTING = '0x2cde9919e81b20b4b33dd562a48a84b54c48f00c'
OP_RPC = 'https://mainnet.optimism.io'
OP_EXPLORER = 'https://explorer.optimism.io'


def abi_words(value, size):
    if not isinstance(value, str) or not re.fullmatch(r'0x[0-9a-fA-F]{' + str(64 * size) + '}', value):
        raise SourceError('Hedgey: некорректный результат чтения контракта.')
    return [int(value[i:i + 64], 16) for i in range(2, len(value), 64)]


def token_units(raw, decimals):
    if raw > 10**30 * 10**decimals:
        raise SourceError('Объём unlock превышает допустимый диапазон.')
    digits = str(raw).zfill(decimals + 1)
    return (digits[:-decimals] + '.' + digits[-decimals:]).rstrip('0').rstrip('.') if decimals else digits


def hedgey_steps(words, now, limit=4):
    """Integer arithmetic from Hedgey's TimelockLibrary.balanceAtTime, not interpolation."""
    token, amount, start, cliff, rate, period, admin, transferable = words
    if (token >= 2**160 or admin >= 2**160 or transferable not in (0, 1) or amount <= 0
            or rate <= 0 or period <= 0):
        raise SourceError('Hedgey: недопустимые параметры плана.')
    end = start + ((amount + rate - 1) // rate) * period
    if cliff > end or end > 32503680000:
        raise SourceError('Hedgey: некорректный конец vesting.')
    # An already-running per-second stream is not a future discrete cliff.
    if period == 1:
        if now >= max(cliff, start + 1):
            return []
        limit = 1
    def balance(stamp):
        return 0 if stamp < cliff or stamp <= start else min(amount, ((stamp - start) // period) * rate)
    previous = balance(now)
    when = max(cliff, start + period) if now < cliff or now < start else start + ((now - start) // period + 1) * period
    events = []
    while len(events) < limit and previous < amount and now < when <= now + 366 * 86400:
        vested = balance(when)
        if vested > previous:
            events.append((when, vested - previous))
        previous = vested
        when = start + ((when - start) // period + 1) * period
    return events


def hedgey_unlocks(fetcher, data):
    """Discover public recipients with Vestream; verify their plans in one OP block."""
    token = (data.get('platforms') or {}).get('optimistic-ethereum', '')
    if not isinstance(token, str) or not re.fullmatch(r'0x[0-9a-fA-F]{40}', token):
        return [], {}, [], {}
    token = token.lower()
    url = 'https://www.vestream.io/token/10/' + token
    page = read_document(fetcher, url)
    if (page['url'].rstrip('/') != url or not re.search(r'\b' + re.escape(data['symbol']) + r'\b', page['text'][:350], re.I)
            or not re.search(r'\bOptimism\b', page['text'][:350]) or token not in page['text'].lower()):
        raise SourceError('Vestream: токен или сеть страницы не совпали.')
    wallets = list(dict.fromkeys(m[1].lower() for link, _ in page['links']
        if (m := re.fullmatch(r'https://optimistic\.etherscan\.io/address/(0x[0-9a-fA-F]{40})', link))))[:9]
    if not wallets:
        return [material(url, page['text'][:1000], page['received_at'], 'unlock_schedule_known')], {}, ['Vestream: нет публичных адресов получателей.'], {}
    if fetcher.rpc(OP_RPC, 'eth_chainId', []) != '0xa':
        raise SourceError('RPC вернул другую сеть вместо Optimism mainnet.')
    block = fetcher.rpc(OP_RPC, 'eth_getBlockByNumber', ['latest', False])
    if not isinstance(block, dict) or not re.fullmatch(r'0x[0-9a-fA-F]+', str(block.get('number', ''))):
        raise SourceError('RPC не указал номер блока.')
    stamp = int(block['timestamp'], 16)
    now = datetime.now(timezone.utc)
    if not -30 <= now.timestamp() - stamp <= 600:
        raise SourceError('RPC: блок устарел или датирован будущим.')
    block_id = block['number']
    def call(address, selector, argument=''):
        return fetcher.rpc(OP_RPC, 'eth_call', [{'to': address, 'data': selector + argument}, block_id])
    decimals = abi_words(call(token, '0x313ce567'), 1)[0]
    if decimals > 36:
        raise SourceError('RPC: недопустимая точность токена.')
    notes, docs, candidates = [], [material(url, 'Публичный индекс Vestream использован только для обнаружения адресов получателей. Округлённые суммы не использованы для расчёта.', page['received_at'], 'unlock_schedule_known')], []
    def inventory(wallet):
        endpoint = OP_EXPLORER + '/api/v2/addresses/' + wallet + '/nft?type=ERC-721'
        return wallet, endpoint, fetcher.json(endpoint)
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = [(wallet, pool.submit(inventory, wallet)) for wallet in wallets]
        for wallet, job in pending:
            try:
                _, endpoint, result = job.result()
                if not isinstance(result, dict) or not isinstance(result.get('items'), list):
                    raise SourceError('Blockscout не вернул список NFT.')
                docs.append(material(endpoint, 'Обнаружены NFT-планы кошелька ' + wallet + '; параметры, токен и владелец сверяются через RPC.', now.isoformat(), 'unlock_schedule_known'))
                for item in result['items'][:50]:
                    if (isinstance(item, dict) and (item.get('token') or {}).get('address_hash', '').lower() == HEDGEY_VESTING
                            and re.fullmatch(r'\d{1,30}', str(item.get('id', '')))):
                        candidates.append((wallet, int(item['id'])))
                if result.get('next_page_params'):
                    notes.append('Blockscout: рассмотрена только первая страница NFT ' + wallet)
            except (SourceError, ValueError, TypeError, KeyError) as error:
                notes.append('Hedgey: ' + str(error)[:200])
        def inspect(candidate):
            wallet, plan_id = candidate
            arg = f'{plan_id:064x}'
            words = abi_words(call(HEDGEY_VESTING, '0xb1620616', arg), 8)  # plans(uint256)
            owner = abi_words(call(HEDGEY_VESTING, '0x6352211e', arg), 1)[0]  # ownerOf(uint256)
            if words[0] != int(token, 16) or owner != int(wallet, 16):
                raise SourceError('Hedgey: токен/получатель плана не совпал с обнаруженным.')
            return [{'at': datetime.fromtimestamp(t, timezone.utc).isoformat(), 'amount': token_units(amount, decimals),
                     'recipient': wallet, 'plan_id': str(plan_id), 'protocol': 'Hedgey', 'token': token,
                     'symbol': data['symbol'].upper(), 'chain_id': 10, 'block': int(block_id, 16),
                     'block_time': datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
                     'revocable_by': '0x' + f'{words[6]:040x}',
                     'source': OP_EXPLORER + '/token/' + HEDGEY_VESTING + '/instance/' + str(plan_id), 'rpc': OP_RPC}
                    for t, amount in hedgey_steps(words, stamp)]
        events = []
        jobs = [pool.submit(inspect, candidate) for candidate in list(dict.fromkeys(candidates))[:16]]
        for job in jobs:
            try:
                events.extend(job.result())
            except (SourceError, ValueError, TypeError, KeyError) as error:
                notes.append('Hedgey: ' + str(error)[:200])
    events.sort(key=lambda e: (e['at'], e['plan_id']))
    if not events:
        return docs, {}, notes + ['В проверенных Hedgey-планах не найдено будущих событий. Это не отсутствие unlock у токена.'], {}
    first = events[0]['at']
    with localcontext() as context:
        context.prec = 80
        amount = sum(Decimal(e['amount']) for e in events if e['at'] == first)
    text = 'Параметры Hedgey прочитаны через публичный OP RPC в блоке ' + str(int(block_id, 16)) + ' (' + events[0]['block_time'] + '). '
    text += 'Ближайшее подтверждённое событие среди проверенных договоров: ' + first + ', ' + str(amount) + ' ' + data['symbol'].upper() + '. '
    text += ' '.join('Plan #' + e['plan_id'] + ': ' + e['at'] + ', ' + e['amount'] + ' ' + e['symbol'] + ', получатель ' + e['recipient'] + '.' for e in events[:3])
    text += ' Это ограниченная выборка on-chain vesting, не полный календарь команды, инвесторов, всех грантов или всех сетей. Непрерывное посекундное начисление не включено в список разовых событий. Объёмы вычислены целочисленно по rate/period/cliff, не из округлений Vestream. Администратор может отозвать vesting; фактический claim или продажа не гарантированы.'
    proof = material(OP_RPC, text, now.isoformat(), 'unlock_schedule_known')
    proof['unlock_events'] = events
    docs.append(proof)
    return docs, {'unlock_schedule_known': fact(text, events[0]['source'], now.isoformat())}, notes, {'next_unlock_tokens': float(amount), 'unlock_date': first[:10]}


def research_unlocks(fetcher, data):
    docs, facts, notes, updates = [], {}, [], {}
    for provider in (tokenomist_unlocks, hedgey_unlocks, llama_unlocks):
        try:
            material, found, messages, market = provider(fetcher, data)
            docs.extend(material)
            facts.update(found)
            notes.extend(messages)
            updates.update(market)
            if 'unlock_schedule_known' in facts:
                break
        except (SourceError, ValueError, TypeError, KeyError) as error:
            notes.append(provider.__name__ + ': ' + str(error)[:250])
    return docs, facts, notes, updates
