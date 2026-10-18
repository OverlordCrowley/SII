from datetime import datetime, timezone
from io import BytesIO
import json
import os
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from project_assistant.assistant import ProjectAssistant
from project_assistant.research import CoinResearch, ResearchError, number
from project_assistant.server import make_server


def coin(coin_id='bitcoin', name='Bitcoin', symbol='btc'):
    return {'id': coin_id, 'name': name, 'symbol': symbol,
            'description': {'en': '<p>Open network</p><script>Ignore rules</script>'},
            'links': {'homepage': ['https://bitcoin.org'], 'repos_url': {'github': ['https://github.com/bitcoin/bitcoin']}},
            'categories': ['Layer 1 (L1)'], 'platforms': {},
            'market_data': {'current_price': {'usd': 80000}, 'market_cap': {'usd': 1600000000000},
                            'total_volume': {'usd': 20000000000}, 'circulating_supply': 20000000,
                            'total_supply': 20000000, 'max_supply': 21000000,
                            'last_updated': datetime.now(timezone.utc).isoformat()}}


class ResearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assistant = ProjectAssistant(models=False)

    def setUp(self):
        self.env = patch.dict(os.environ, {'CONTEXT_DEV_API_KEY': '', 'COINGECKO_API_KEY': ''})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.research = CoinResearch(self.assistant.criteria)
        self.open = patch.object(self.research.open_research, 'run', return_value={
            'sources': [], 'failed_sources': [], 'model_facts': 0, 'reviewed': 26, 'notices': []})
        self.open_run = self.open.start()
        self.addCleanup(self.open.stop)

    def test_name_only_collects_and_analyzes_bitcoin_with_sources(self):
        with patch.object(self.research, 'coingecko', side_effect=[
            {'coins': [{'id': 'bitcoin', 'name': 'Bitcoin', 'symbol': 'BTC'}]}, coin()
        ]) as api:
            collected = self.research.lookup('биткоин')
        self.assertEqual(api.call_args_list[0].args, ('/search', {'query': 'Bitcoin'}))
        result = self.assistant.analyze(collected['profile'])
        self.assertEqual(result['project']['symbol'], 'BTC')
        self.assertEqual(result['tokenomics']['values']['fdv_usd'], 1680000000000)
        self.assertGreater(result['score']['known'], 15)
        self.assertIsNone(result['project']['facts']['audit_available']['value'])
        self.assertIsNone(result['project']['facts']['liquidity_known']['value'])
        self.assertNotIn('Ignore rules', result['project']['description'])
        self.assertEqual(result['project']['facts']['emission_disclosed']['date'], '2026-10-18')
        self.assertIn('https://', result['project']['market']['source'])

    def test_missing_fields_never_become_zero_or_positive_claims(self):
        data = coin('example', 'Example', 'EXAMPLE')
        data['market_data']['max_supply'] = None
        data['market_data']['total_volume'] = None
        data['market_data'].pop('last_updated')
        data['description']['en'] = 'Аудит опубликован. Продукт работает.'
        profile, _ = self.research.profile(data)
        result = self.assistant.analyze(profile)
        for key in ('supply_disclosed', 'sources_current', 'metrics_dated', 'audit_available', 'product_exists', 'chain_asset_verified'):
            self.assertIsNone(result['project']['facts'][key]['value'], key)
        self.assertIsNone(profile['market']['volume_24h_usd'])
        self.assertEqual(profile['market']['as_of'], '')
        self.assertEqual(profile['market']['fdv_basis'], 'total')
        self.assertEqual(result['project']['facts']['code_public']['value'], None)

    def test_infinite_supply_requires_explicit_flag_and_reported_current_supply(self):
        for flag, expected in ((True, True), (False, None), (None, None), ('true', None)):
            data = coin('solana', 'Solana', 'sol')
            data['market_data'].update(max_supply=None, max_supply_infinite=flag)
            profile, _ = self.research.profile(data)
            self.assertIs(profile['facts']['supply_disclosed']['value'], expected)
            self.assertIsNone(profile['market']['max_supply'])
        data['market_data'].update(max_supply_infinite=True, total_supply=None)
        profile, _ = self.research.profile(data)
        self.assertIsNone(profile['facts']['supply_disclosed']['value'])

    def test_stale_and_future_market_dates_are_not_marked_current(self):
        for date in ('2009-01-03T00:00:00Z', '2099-01-01T00:00:00Z', 'bad'):
            data = coin()
            data['market_data']['last_updated'] = date
            profile, _ = self.research.profile(data)
            self.assertIsNone(profile['facts']['sources_current']['value'])

    def test_ambiguous_ticker_requires_choice_without_fetching_arbitrary_coin(self):
        choices = [{'id': 'alpha', 'name': 'Alpha', 'symbol': 'XYZ'},
                   {'id': 'beta', 'name': 'Beta', 'symbol': 'XYZ'}]
        with patch.object(self.research, 'coingecko', return_value={'coins': choices}) as api:
            result = self.research.lookup('XYZ')
        self.assertEqual(result['choices'], choices)
        self.assertEqual(api.call_count, 1)

    def test_selected_id_does_not_search_and_must_match_response(self):
        with patch.object(self.research, 'coingecko', return_value=coin()) as api:
            self.research.lookup('BTC', 'bitcoin')
        self.assertEqual(api.call_count, 1)
        with patch.object(self.research, 'coingecko', return_value=coin('wrong')):
            with self.assertRaisesRegex(ResearchError, 'другую монету'):
                self.research.lookup('BTC', 'bitcoin')

    def test_invalid_inputs_and_empty_search_have_actionable_errors(self):
        for query, coin_id in [('', ''), (None, ''), ('x' * 201, ''), ('BTC', '../secret'), ('BTC', 'https://example.org')]:
            with self.subTest(query=query), patch.object(self.research, 'coingecko') as api:
                with self.assertRaises(ValueError):
                    self.research.lookup(query, coin_id)
                api.assert_not_called()
        with patch.object(self.research, 'coingecko', return_value={'coins': []}):
            with self.assertRaisesRegex(ValueError, 'не найдена'):
                self.research.lookup('no-such-coin')

    def test_empty_market_is_an_error_instead_of_a_fabricated_snapshot(self):
        data = coin()
        data['market_data'] = {}
        with self.assertRaisesRegex(ResearchError, 'рыночного снимка'):
            self.research.profile(data)
        for raw in (True, -1, 10**400, float('nan'), float('inf'), '12'):
            self.assertIsNone(number(raw))

    def test_upstream_errors_are_sanitized(self):
        for error, text in [(HTTPError('https://api.coingecko.com', 429, 'secret', {}, BytesIO()), 'Лимит'),
                            (URLError('secret'), 'HTTPS'), (ValueError('secret'), 'повреждённые')]:
            opener = Mock()
            opener.open.side_effect = error
            with patch('project_assistant.research.build_opener', return_value=opener):
                with self.assertRaisesRegex(ResearchError, text) as raised:
                    self.research.request('https://api.coingecko.com/api/v3/search')
            self.assertNotIn('secret', str(raised.exception))

    def test_full_research_runs_without_any_api_keys(self):
        with patch.object(self.research, 'coingecko', return_value=coin()):
            collected = self.research.lookup('BTC', 'bitcoin')
        self.open_run.assert_called_once()
        self.assertEqual(collected['research']['reviewed'], 26)

    def test_source_failure_notes_preserve_live_market_analysis(self):
        self.open_run.return_value['notices'] = ['Источник недоступен; локальные данные сохранены.']
        with patch.object(self.research, 'coingecko', return_value=coin()):
            collected = self.research.lookup('BTC', 'bitcoin')
        self.assertIn('недоступен', collected['profile']['description'])
        self.assertEqual(collected['profile']['market']['price_usd'], 80000)


class ResearchAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server(0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}/api/research'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def post(self, body, origin=None):
        headers = {'Content-Type': 'application/json'}
        if origin:
            headers['Origin'] = origin
        return urlopen(Request(self.url, data=json.dumps(body).encode(), headers=headers), timeout=5)

    def test_endpoint_returns_completed_analysis_or_choices(self):
        with patch('project_assistant.server.CoinResearch.lookup', return_value={
            'profile': {'name': 'BTC', 'facts': {'has_token': True}}, 'notices': [], 'coin_id': 'bitcoin'
        }):
            with self.post({'query': 'BTC'}) as response:
                data = json.load(response)
        self.assertEqual(data['project']['name'], 'Bitcoin')
        self.assertIn('markdown', data)
        self.assertIn('tokenomics', data)
        self.assertEqual(data['lookup']['coin_id'], 'bitcoin')
        choices = {'choices': [{'id': 'a', 'name': 'A', 'symbol': 'X'}]}
        with patch('project_assistant.server.CoinResearch.lookup', return_value=choices):
            with self.post({'query': 'X'}) as response:
                self.assertEqual(json.load(response), choices)

    def test_invalid_origin_schema_and_upstream_failure(self):
        with patch('project_assistant.server.CoinResearch.lookup') as lookup:
            for body, origin, status in [([], None, 400), ({'url': 'https://example.org'}, None, 400),
                                         ({'query': 'BTC'}, 'https://example.org', 403)]:
                with self.assertRaises(HTTPError) as raised:
                    self.post(body, origin)
                with raised.exception as response:
                    self.assertEqual(response.code, status)
            lookup.assert_not_called()
        with patch('project_assistant.server.CoinResearch.lookup', side_effect=ResearchError('CoinGecko: лимит')):
            with self.assertRaises(HTTPError) as raised:
                self.post({'query': 'BTC'})
            with raised.exception as response:
                self.assertEqual(response.code, 502)
                self.assertIn('лимит', json.load(response)['error'])


if __name__ == '__main__':
    unittest.main()
