"""Ограниченное чтение публичных источников без cookies и API-ключей."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.client import HTTPConnection, HTTPException
import ipaddress
import json
from pathlib import Path
import re
import socket
import ssl
import subprocess
import sys
from threading import Lock
import time
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
import xml.etree.ElementTree as ET


class SourceError(ValueError):
    pass


# These words choose what to read; they never turn a criterion into True.
TOPICS = {
    'product_exists': ('mainnet', 'network', 'protocol', 'product', 'overview', 'docs', 'documentation'),
    'problem_defined': ('whitepaper', 'introduction', 'what-is', 'problem', 'permissionless', 'peer-to-peer', 'payments', 'decentralized'),
    'users_active': ('ecosystem', 'adoption', 'users', 'transactions', 'activity'),
    'usage_evidence': ('statistics', 'metrics', 'tvl', 'activity', 'transactions'),
    'revenue_disclosed': ('fees', 'revenue', 'economics', 'economy'),
    'competition_analyzed': ('comparison', 'compared', 'versus', 'differences', 'unlike', 'bitcoin and ethereum'),
    'team_identified': ('team', 'foundation', 'contributors', 'founder', 'maintainer', 'co-founder'),
    'roadmap_delivery': ('roadmap', 'upgrade', 'release', 'history'),
    'has_token': ('token', 'currency', 'coin'),
    'token_role_defined': ('utility', 'token', 'staking', 'fees', 'ether'),
    'token_value_link': ('utility', 'demand', 'staking', 'fees', 'value'),
    'holder_rights_clear': ('holder', 'governance', 'voting', 'rights', 'stake'),
    'supply_disclosed': ('supply', 'inflation', 'tokenomics', 'issuance'),
    'emission_disclosed': ('inflation', 'issuance', 'emission', 'burn', 'halving'),
    'vesting_disclosed': ('distribution', 'allocation', 'vesting', 'tokenomics', 'transparency', 'endowment'),
    'unlock_schedule_known': ('unlock', 'vesting', 'cliff', 'transparency'),
    'concentration_known': ('holders', 'richlist', 'distribution', 'concentration'),
    'liquidity_known': ('liquidity', 'orderbook', 'spread', 'depth'),
    'valuation_comparable': ('valuation', 'market cap', 'comparison', 'benchmark'),
    'code_public': ('github', 'source code', 'repository', 'open-source'),
    'audit_available': ('audit', 'auditor', 'assessment'),
    'admin_controls_known': ('governance', 'admin', 'upgrade', 'multisig', 'control'),
    'incident_history_known': ('incident', 'outage', 'vulnerability', 'advisory', 'postmortem', 'security'),
    'chain_asset_verified': ('contract', 'address', 'mainnet', 'native', 'network'),
    'sources_current': ('updated', 'release', 'publication'),
    'metrics_dated': ('timestamp', 'snapshot', 'date'),
}


def canonical_url(value):
    if not isinstance(value, str) or not value or len(value) > 500 or any(ord(c) < 32 for c in value):
        raise SourceError('Некорректный адрес источника.')
    try:
        parts = urlsplit(value)
        expected = 443 if parts.scheme == 'https' else 80
        if (parts.scheme not in ('http', 'https') or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.port not in (None, expected)):
            raise ValueError()
        host = parts.hostname.encode('idna').decode('ascii')
        if host.lower() == 'localhost' or host.lower().endswith(('.localhost', '.local', '.internal')):
            raise ValueError()
        return urlunsplit((parts.scheme, parts.netloc, parts.path or '/', parts.query, ''))
    except (ValueError, UnicodeError) as error:
        raise SourceError('Разрешены только публичные HTTP(S)-источники на стандартном порту.') from error


def public_addresses(host, port):
    try:
        answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(a[4][0] for a in answers))
        if not addresses or any(not ipaddress.ip_address(a).is_global or ipaddress.ip_address(a).is_multicast for a in addresses):
            raise SourceError('Источник ведёт на непубличный сетевой адрес.')
        return sorted(addresses, key=lambda a: ':' in a)
    except (OSError, ValueError) as error:
        if isinstance(error, SourceError):
            raise
        raise SourceError('Не удалось определить публичный адрес источника.') from error


class PublicFetcher:
    def __init__(self):
        self.context = ssl.create_default_context()
        if sys.platform == 'darwin' and Path('/etc/ssl/cert.pem').is_file():
            self.context.load_verify_locations('/etc/ssl/cert.pem')
        self.cache = {}
        self.lock = Lock()

    def get(self, url, limit=2 * 1024 * 1024, ttl=300):
        return self._request(url, limit, ttl)

    def _request(self, url, limit, ttl, rpc_body=None):
        url = canonical_url(url)
        cache_key = url if rpc_body is None else (url, rpc_body)
        with self.lock:
            cached = self.cache.get(cache_key)
            if cached and time.monotonic() - cached[0] < ttl and len(cached[1]['body']) <= limit:
                return cached[1]
        deadline = time.monotonic() + 12
        for _ in range(4):
            parts = urlsplit(url)
            port = 443 if parts.scheme == 'https' else 80
            addresses = public_addresses(parts.hostname, port)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SourceError('Источник не ответил за отведённое время.')
            connection = HTTPConnection(parts.hostname, port, timeout=min(5, remaining))
            try:
                # Pin the validated IP. HTTP Host and TLS SNI still use the original host.
                sock = socket.create_connection((addresses[0], port), timeout=min(5, remaining))
                connection.sock = sock
                if parts.scheme == 'https':
                    connection.sock = self.context.wrap_socket(sock, server_hostname=parts.hostname)
                connection.request('GET' if rpc_body is None else 'POST', urlunsplit(('', '', parts.path or '/', parts.query, '')), body=rpc_body,
                                   headers={'Host': parts.netloc, 'User-Agent': 'Cryptoscope/1.0',
                                            'Accept': 'text/html,application/json,text/plain;q=0.9',
                                            'Accept-Encoding': 'identity', **({'Content-Type': 'application/json'} if rpc_body is not None else {})})
                response = connection.getresponse()
                if response.status in (301, 302, 303, 307, 308):
                    if rpc_body is not None:
                        raise SourceError('Перенаправления RPC-запросов не поддерживаются.')
                    target = canonical_url(urljoin(url, response.getheader('Location', '')))
                    if parts.scheme == 'https' and urlsplit(target).scheme != 'https':
                        raise SourceError('Источник перенаправляет на незащищённый адрес.')
                    url = target
                    continue
                if response.status != 200:
                    raise SourceError(f'Источник недоступен: HTTP {response.status}.')
                if response.getheader('Content-Encoding', 'identity') not in ('', 'identity'):
                    raise SourceError('Источник вернул неподдерживаемое сжатие.')
                chunks, size = [], 0
                while True:
                    if time.monotonic() > deadline:
                        raise SourceError('Источник не ответил за отведённое время.')
                    chunk = response.read1(min(65536, limit + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > limit:
                        raise SourceError('Источник превышает лимит размера.')
                result = {'url': url, 'body': b''.join(chunks),
                          'kind': response.getheader('Content-Type', '').lower(),
                          'received_at': datetime.now(timezone.utc).isoformat()}
                with self.lock:
                    # ponytail: bounded in-memory cache; use a shared cache only for multi-process hosting.
                    if len(self.cache) >= 32:
                        self.cache.pop(next(iter(self.cache)))
                    self.cache[cache_key] = (time.monotonic(), result)
                return result
            except (OSError, HTTPException) as error:
                raise SourceError('Не удалось прочитать публичный источник по HTTP(S).') from error
            finally:
                connection.close()
        raise SourceError('Слишком много перенаправлений источника.')

    def rpc(self, url, method, params):
        if method not in ('eth_chainId', 'eth_blockNumber', 'eth_getBlockByNumber', 'eth_call'):
            raise SourceError('Разрешены только операции чтения RPC.')
        body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}, separators=(',', ':')).encode()
        if len(body) > 4096:
            raise SourceError('Слишком большой RPC-запрос.')
        response = self._request(url, 256 * 1024, 5, rpc_body=body)
        try:
            result = json.loads(response['body'])
            if result.get('id') != 1 or 'error' in result or 'result' not in result:
                raise ValueError()
            return result['result']
        except (ValueError, TypeError, AttributeError, RecursionError) as error:
            raise SourceError('RPC не вернул корректный результат чтения.') from error

    def json(self, url, limit=2 * 1024 * 1024):
        result = self.get(url, limit=limit, ttl=60)
        try:
            return json.loads(result['body'])
        except (ValueError, UnicodeError, RecursionError) as error:
            raise SourceError('Источник вернул некорректный JSON.') from error


class DocumentParser(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.text = []
        self.links = []
        self.hidden = 0
        self.anchor = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'style', 'nav', 'footer', 'header'):
            self.hidden += 1
        # Navigation links help discover documents even when their labels are omitted from the corpus.
        if tag == 'a':
            self.anchor = [urljoin(self.base, attrs.get('href', '')), []]
        if not self.hidden and tag in ('p', 'li', 'div', 'br', 'h1', 'h2', 'h3', 'h4', 'tr'):
            self.text.append('\n')

    def handle_endtag(self, tag):
        if tag == 'a' and self.anchor:
            self.links.append((self.anchor[0], ' '.join(self.anchor[1])))
            self.anchor = None
        if tag in ('script', 'style', 'nav', 'footer', 'header'):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if self.anchor:
            self.anchor[1].append(data.strip())
        if not self.hidden:
            self.text.append(data)


def topic_score(text, key):
    text = text.lower()
    return sum(bool(re.search(r'(?<!\w)' + re.escape(word) + r's?(?!\w)', text)) for word in TOPICS.get(key, ()))


def read_document(fetcher, url):
    parts = urlsplit(url)
    if parts.hostname == 'github.com' and '/blob/' in parts.path and re.search(r'\.(?:pdf|md|txt)$', parts.path, re.I):
        # GitHub's HTML preview is not the report; retrieve the actual public file.
        url = urlunsplit(('https', 'raw.githubusercontent.com', parts.path.replace('/blob/', '/', 1), '', ''))
    result = fetcher.get(url, limit=8 * 1024 * 1024) if urlsplit(url).path.lower().endswith('.pdf') else fetcher.get(url)
    is_pdf = 'application/pdf' in result['kind'] or result['body'].startswith(b'%PDF-')
    if is_pdf:
        try:
            parsed = subprocess.run([sys.executable, str(Path(__file__).with_name('pdf_text.py'))],
                                    input=result['body'], capture_output=True, timeout=8, check=True)
            text = parsed.stdout.decode('utf-8', errors='replace')[:100000]
        except (subprocess.SubprocessError, OSError) as error:
            raise SourceError('Не удалось прочитать PDF. Нужен pypdf из requirements.txt; сканы без текста не поддерживаются.') from error
        if len(text.strip()) < 80:
            raise SourceError('PDF не содержит достаточного текстового слоя.')
        return {'url': result['url'], 'text': text, 'received_at': result['received_at'], 'links': [], 'format': 'pdf'}
    if not any(kind in result['kind'] for kind in ('text/', 'html', 'json', 'xml')):
        raise SourceError('Формат документа не поддерживается; нужен открытый текст или HTML.')
    raw = result['body'].decode('utf-8', errors='replace')
    parser = DocumentParser(result['url'])
    # Discourse's public JSON identifies one authored post. Replies are not evidence
    # for the topic author's statements (nor is a proposal an executed decision).
    if 'json' in result['kind'] and re.fullmatch(r'/t/[^/]+/\d+(?:/\d+)?\.json', parts.path):
        try:
            topic = json.loads(raw)
            requested = re.search(r'/\d+/(\d+)\.json$', parts.path)
            number = int(requested[1]) if requested else 1
            posts = [p for p in topic['post_stream']['posts'] if p.get('post_number') == number]
            if len(posts) != 1 or not isinstance(posts[0].get('cooked'), str):
                raise ValueError()
            post = posts[0]
            parser.feed(post['cooked'])
            text = '\n'.join([topic['title'], 'Author: ' + post['username'],
                              'Published: ' + post['created_at'], 'Updated: ' + post['updated_at'],
                              ''.join(parser.text)])
        except (KeyError, TypeError, ValueError, RecursionError) as error:
            raise SourceError('Форум не вернул запрошенное сообщение с автором и датой.') from error
    elif 'html' in result['kind']:
        parser.feed(raw)
        text = '\n'.join(line.strip() for line in ''.join(parser.text).splitlines() if line.strip())
    else:
        text = raw
        parser.links = [(urljoin(result['url'], target), title) for title, target in
                        re.findall(r'\[([^\]\n]{1,300})\]\(([^\s)]+)\)', raw)]
    if len(text.strip()) < 80:
        raise SourceError('Страница не содержит достаточно открытого текста (возможно, нужен JavaScript).')
    # CSV exports may redirect to an expiring download URL; retain the stable
    # requested source, while the fetcher still validates every redirect.
    csv = 'text/csv' in result['kind']
    return {'url': canonical_url(url) if csv else result['url'], 'text': text[:100000],
            'received_at': result['received_at'], 'links': parser.links[:600],
            **({'format': 'csv'} if csv else {})}


def discover_indexes(fetcher, seeds):
    """Read bounded official indexes, including URLs absent from the home page."""
    origins = []
    for value in seeds:
        try:
            parts = urlsplit(canonical_url(value))
            origin = urlunsplit((parts.scheme, parts.netloc, '', '', ''))
            if origin not in origins:
                origins.append(origin)
        except SourceError:
            continue
    origins = origins[:2]
    links, failures = [], []

    def read_origin(origin):
        found, errors, sitemaps = [], [], [origin + '/sitemap.xml']
        try:
            robots = fetcher.get(origin + '/robots.txt')['body'].decode('utf-8', errors='replace')
            sitemaps = re.findall(r'^Sitemap:\s*(\S+)', robots, re.I | re.M)[:3] or sitemaps
        except SourceError:
            pass  # robots.txt and llms.txt are optional discovery indexes.
        try:
            raw = fetcher.get(origin + '/llms.txt')['body'].decode('utf-8', errors='replace')
            found.extend((urljoin(origin, target), title + ' ' + description) for title, target, description in
                         re.findall(r'\[([^\]\n]+)\]\(([^\s)]+)\)([^\n]*)', raw))
        except SourceError:
            pass
        seen = set()
        while sitemaps and len(seen) < 4:
            url = sitemaps.pop(0)
            try:
                url = canonical_url(url)
                # An index is not allowed to delegate crawling to an unrelated site.
                if urlsplit(url).hostname.removeprefix('www.') != urlsplit(origin).hostname.removeprefix('www.') or url in seen:
                    continue
                seen.add(url)
                raw = fetcher.get(url, limit=8 * 1024 * 1024)['body']
                if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
                    raise SourceError('Неподдерживаемый XML карты сайта.')
                root = ET.fromstring(raw)
                locations = [e.text for e in root.iter() if e.tag.rsplit('}', 1)[-1] == 'loc' and e.text][:25000]
                if root.tag.rsplit('}', 1)[-1] == 'sitemapindex':
                    sitemaps.extend(locations[:4])
                else:
                    found.extend((u, '') for u in locations)
            except (SourceError, ET.ParseError, ValueError) as error:
                errors.append({'url': url, 'reason': 'Карта сайта: ' + str(error)[:160]})
        return found, errors

    with ThreadPoolExecutor(max_workers=2) as pool:
        for found, errors in pool.map(read_origin, origins):
            links.extend(found)
            failures.extend(errors)
    return list(dict.fromkeys(links)), failures


def discovery_score(url, title, key):
    text = unquote(url + ' ' + title).lower().replace('-', ' ').replace('_', ' ')
    path = urlsplit(url).path.lower()
    # RPC examples and token-creation tutorials describe developer APIs, not the asset's policy.
    if re.search(r'/(?:rpc|cookbook|tutorials?|podcasts|videos|tools|bootcamp|tokens|apps)(?:/|\.|$)', path):
        return 0
    if re.search(r'\.(?:png|jpe?g|svg|webp|gif|zip|exe|dmg)$', path):
        return 0
    if key == 'users_active' and '/docs/' in path:
        return 0  # Instructions for building a payment app are not evidence of actual customers.
    locale = path.strip('/').split('/')[0]
    if re.fullmatch(r'[a-z]{2}(?:-[a-z]{2})?', locale) and locale != 'en':
        return 0
    terms = tuple(word.replace('-', ' ') for word in TOPICS.get(key, ()))
    contains = lambda word: bool(re.search(r'(?<!\w)' + re.escape(word) + r's?(?!\w)', text))
    score = sum(3 for word in terms if contains(word))
    focused = {
        'chain_asset_verified': ('native token', 'native asset', 'what is sol', 'terminology', 'ether', 'currency'),
        'team_identified': ('founder', 'founding', 'team', 'about'),
        'vesting_disclosed': ('transparency report', 'tokenomics', 'vesting', 'allocation'),
        'unlock_schedule_known': ('transparency report', 'vesting', 'unlock'),
        'supply_disclosed': ('tokenomics', 'monetary policy', 'supply', 'economics'),
        'audit_available': ('security audit', 'security assessment', 'audit report'),
        'admin_controls_known': ('governance', 'protocol upgrade', 'upgrade authority', 'consensus'),
        'competition_analyzed': ('comparison', 'differences', 'migrate', 'versus'),
        'incident_history_known': ('postmortem', 'outage report', 'incident report'),
        'users_active': ('case study', 'case studies', 'adoption', 'payments'),
        'revenue_disclosed': ('fee structure', 'transaction fees', 'fees'),
    }
    score += sum(6 for word in focused.get(key, ()) if contains(word))
    if not score:
        return 0
    return score + (12 if path.endswith('.pdf') else 2 if '/docs/' in path else 0) - (2 if '/news/' in path else 0)


def collect_targeted_documents(fetcher, existing, index_links, criteria, primary_hosts, max_pages=30, priority_sources=()):
    """Round-robin missing questions; follow actual report links rather than keyword home pages."""
    documents, failures = [], []
    seen = {d['url'] for d in existing}
    hosts = set(primary_hosts)
    candidates, reports, priority = {}, set(), {}
    github_roots = {'/' + '/'.join(urlsplit(d['url']).path.strip('/').split('/')[:2])
                    for d in existing + list(priority_sources) if urlsplit(d['url']).hostname in ('github.com', 'raw.githubusercontent.com')}
    for doc in existing:
        host = urlsplit(doc['url']).hostname
        if doc.get('primary') and host not in ('github.com', 'raw.githubusercontent.com', 'api.github.com', 'docs.github.com'):
            hosts.add(host)
    def official(url):
        parts = urlsplit(url)
        host = parts.hostname or ''
        if host in ('api.github.com', 'docs.github.com'):
            return False
        if host in ('github.com', 'raw.githubusercontent.com'):
            return any(parts.path == root or parts.path.startswith(root + '/') for root in github_roots)
        return any(host == h or host.endswith('.' + h) for h in hosts)
    def include_links(doc):
        for url, title in doc.get('links', []):
            # Follow reports explicitly linked by official documentation, including auditor-hosted PDFs.
            title = title[:300]
            report_link = (re.search(r'audit|assessment|postmortem|transparency', url + ' ' + title, re.I)
                           and re.search(r'\.pdf(?:\?|$)|/audits?/|/reports?/|github\.com/.+audit', url, re.I))
            if official(url) or report_link:
                candidates[url] = title
                if not official(url):
                    reports.add(url)
    for url, title in index_links:
        try:
            if official(canonical_url(url)):
                candidates[url] = title
        except SourceError:
            continue
    for doc in existing:
        if doc.get('primary'):
            include_links(doc)
    for source in priority_sources:
        url = canonical_url(source['url'])
        candidates[url] = source.get('title', '')
        reports.add(url)
        priority[url] = set(source['criteria'])
        for doc in existing:
            if doc['url'] == url:
                doc['targeted_criteria'] = sorted(set(doc.get('targeted_criteria', [])) | priority[url])
                doc['primary'] = True
    count = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        for _ in range(4):
            batch = []
            for key in criteria:
                ranked = []
                for url, title in candidates.items():
                    try:
                        url = canonical_url(url)
                    except SourceError:
                        continue
                    if url in seen:
                        continue
                    if not official(url) and url not in reports:
                        continue
                    score = discovery_score(url, title, key)
                    if key in priority.get(url, set()):
                        score += 100
                    if score > 0:
                        ranked.append((score, -len(url), url))
                if ranked and count < max_pages:
                    url = max(ranked)[2]
                    seen.add(url)
                    batch.append((url, key))
                    count += 1
            if not batch:
                break
            pending = [(u, key, pool.submit(read_document, fetcher, u)) for u, key in batch]
            for url, key, job in pending:
                try:
                    doc = job.result()
                    doc['primary'] = True  # Selected from an official index or an explicit report link.
                    doc['retrieved_for'] = key
                    doc['targeted_criteria'] = sorted(priority.get(url, {key}))
                    if doc['url'] not in {d['url'] for d in existing + documents}:
                        documents.append(doc)
                    seen.add(doc['url'])
                    include_links(doc)
                except (SourceError, ValueError) as error:
                    failures.append({'url': url, 'reason': str(error)})
    return documents, failures


def collect_documents(fetcher, seeds, max_pages=12, primary_hosts=None, trusted_urls=()):
    documents, failures, seen = [], [], set()
    allowed_hosts = set(primary_hosts or [])
    restricted = primary_hosts is not None
    github_roots = {'/' + '/'.join(urlsplit(u).path.strip('/').split('/')[:2]) for u in trusted_urls
                    if urlsplit(u).hostname in ('github.com', 'raw.githubusercontent.com')}
    def official(url):
        parts = urlsplit(url)
        host = parts.hostname or ''
        if restricted and host in ('docs.github.com', 'api.github.com'):
            return False
        if restricted and host in ('github.com', 'raw.githubusercontent.com'):
            return any(parts.path == root or parts.path.startswith(root + '/') for root in github_roots)
        return not restricted or any(host == root or host.endswith('.' + root) for root in allowed_hosts)
    frontier = list(dict.fromkeys(seeds))[:4]
    with ThreadPoolExecutor(max_workers=4) as pool:
        while frontier and len(seen) < max_pages:
            batch = []
            for url in frontier:
                try:
                    url = canonical_url(url)
                except SourceError:
                    continue
                if url not in seen and len(seen) < max_pages:
                    seen.add(url)
                    batch.append(url)
            if not batch:
                break
            pending = [(url, pool.submit(read_document, fetcher, url)) for url in batch]
            for url, future in pending:
                try:
                    doc = future.result()
                    doc['primary'] = official(url) or official(doc['url'])
                    seen.add(doc['url'])
                    if not any(existing['url'] == doc['url'] for existing in documents):
                        documents.append(doc)
                except (SourceError, ValueError) as error:
                    failures.append({'url': url, 'reason': str(error)})
            candidates = {}
            for doc in documents:
                if not official(doc['url']):
                    continue  # An explorer's API documentation describes the explorer, not the researched coin.
                for url, title in doc['links']:
                    try:
                        url = canonical_url(url)
                    except SourceError:
                        continue
                    if url in seen or re.search(r'\.(?:zip|png|jpg|svg|exe|dmg)(?:\?|$)', url, re.I):
                        continue
                    if not official(url):
                        host = urlsplit(url).hostname or ''
                        if re.search(r'audit|assessment|security.report', url + ' ' + title, re.I) or (host.startswith('docs.') and host not in ('docs.google.com', 'docs.github.com')):
                            if host in ('github.com', 'raw.githubusercontent.com'):
                                github_roots.add('/' + '/'.join(urlsplit(url).path.strip('/').split('/')[:2]))
                            else:
                                allowed_hosts.add(host)
                        else:
                            continue
                    topics = {key for key in TOPICS if topic_score(url + ' ' + title, key)}
                    if topics:
                        candidates[url] = topics
            covered = {key for doc in documents for key in TOPICS if topic_score(doc['url'], key)}
            frontier = []
            # Prefer documents covering different research questions over repeated copies of one topic.
            while candidates and len(frontier) < min(4, max_pages - len(seen)):
                url = max(candidates, key=lambda u: (len(candidates[u] - covered) + (3 if re.search(r'/docs/?$|://docs\.', u) else 0), len(candidates[u]), -len(u)))
                covered.update(candidates.pop(url))
                frontier.append(url)
    return documents, failures
