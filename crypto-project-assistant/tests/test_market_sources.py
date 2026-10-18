from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock, patch

from project_assistant.assistant import ProjectAssistant
from project_assistant.market_sources import paprika_peers
from project_assistant.open_research import OpenResearch
from project_assistant.public_sources import SourceError


class MarketSourceTests(unittest.TestCase):
    def setUp(self):
        self.fetcher = Mock()
        self.now = datetime.now(timezone.utc)
        self.data = {'id': 'example', 'name': 'Example', 'symbol': 'EXM', 'links': {'homepage': ['https://project.example/']}}
        self.profile = {'name': 'Example', 'market': {'as_of': self.now.date().isoformat(), 'price_usd': 10,
                                                    'market_cap_usd': 500, 'max_supply': 100, 'fdv_basis': 'max'}}
        self.tickers = [{'id': coin_id, 'name': name, 'symbol': symbol, 'last_updated': self.now.isoformat(),
                         'max_supply': supply, 'total_supply': 100, 'quotes': {'USD': {'price': 2, 'market_cap': 1000}}}
                        for coin_id, name, symbol, supply in [('exm-example', 'Example', 'EXM', 100),
                                                            ('one-peer', 'First Peer', 'ONE', 200), ('two-peer', 'Second Peer', 'TWO', 0)]]
        self.meta = {'id': 'exm-example', 'name': 'Example', 'symbol': 'EXM', 'links': {'website': ['https://project.example']},
                     'tags': [{'id': 'lending', 'coin_counter': 3}]}
        self.group = {'id': 'lending', 'name': 'Lending', 'type': 'functional', 'coins': [r['id'] for r in self.tickers]}

    def run_source(self, tickers=None, meta=None, group=None):
        self.fetcher.json.side_effect = [tickers if tickers is not None else self.tickers,
                                        meta if meta is not None else self.meta, group if group is not None else self.group]
        return paprika_peers(self.fetcher, self.data, self.profile)

    def test_named_functional_peers_use_same_day_usd_and_explicit_fdv_basis(self):
        docs, facts, _ = self.run_source()
        self.assertTrue(facts['valuation_comparable']['value'])
        self.assertTrue(facts['competition_analyzed']['value'])
        self.assertIn('FDV=1000', facts['valuation_comparable']['evidence'])
        self.assertIn('расчётный FDV=400', facts['valuation_comparable']['evidence'])
        self.assertIn('расчётный FDV=200', facts['valuation_comparable']['evidence'])
        self.assertIn('одинаковую технологию', facts['valuation_comparable']['evidence'])
        self.assertEqual(docs[0]['peer_names'], ['First Peer', 'Second Peer'])

    def test_ambiguous_asset_wrong_site_and_platform_tags_do_not_become_comparisons(self):
        for tickers, meta, group in [(self.tickers + [self.tickers[0]], self.meta, self.group),
                                     (self.tickers, {**self.meta, 'links': {'website': ['https://impostor.example/']}}, self.group),
                                     (self.tickers, self.meta, {**self.group, 'type': 'platform'})]:
            with self.assertRaises(SourceError):
                self.run_source(tickers, meta, group)

    def test_stale_future_and_duplicate_peers_do_not_inflate_coverage(self):
        for delta in (timedelta(days=-2), timedelta(hours=1)):
            tickers = deepcopy(self.tickers)
            for row in tickers[1:]:
                row['last_updated'] = (self.now + delta).isoformat()
            with self.assertRaises(SourceError):
                self.run_source(tickers=tickers)
        _, facts, _ = self.run_source(tickers=[self.tickers[0], self.tickers[1], self.tickers[1]])
        self.assertIn('valuation_comparable', facts)
        self.assertNotIn('competition_analyzed', facts)

    def test_coingecko_failure_runs_independent_comparison_provider(self):
        research = OpenResearch(ProjectAssistant(models=False).criteria, self.fetcher)
        expected = ([], {'valuation_comparable': {'value': True}}, [])
        with patch.object(research, 'coingecko_peers', side_effect=SourceError('HTTP 429')), \
                patch('project_assistant.open_research.paprika_peers', return_value=expected) as provider:
            _, facts, notes = research.peers(self.data, self.profile)
        provider.assert_called_once()
        self.assertTrue(facts['valuation_comparable']['value'])
        self.assertIn('429', notes[0])


if __name__ == '__main__':
    unittest.main()
