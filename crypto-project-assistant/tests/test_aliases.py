import json
import subprocess
import sys
import unittest

from project_assistant.assistant import ProjectAssistant, markdown_report
from project_assistant.knowledge import ROOT


class AssetAliasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assistant = ProjectAssistant()

    def test_name_ticker_and_russian_aliases_are_equivalent(self):
        for alias in ('Bitcoin', 'bitcoin', 'BTC', 'btc', 'Биткоин', 'биткойн', 'XBT', '$btc', '  bTc  ', 'ＢＴＣ'):
            with self.subTest(alias=alias):
                result = self.assistant.analyze({'name': alias})
                self.assertEqual((result['project']['name'], result['project']['symbol']), ('Bitcoin', 'BTC'))
                self.assertEqual(result['score']['known'], 0)
                self.assertNotIn('chain_asset_verified', result['project']['facts'])

    def test_every_catalog_alias_resolves_in_both_input_fields(self):
        catalog = json.loads((ROOT / 'data/asset_aliases.json').read_text(encoding='utf-8'))
        for asset in catalog:
            for alias in (asset['name'], asset['symbol'], *asset['aliases']):
                for field in ('name', 'symbol'):
                    with self.subTest(asset=asset['symbol'], alias=alias, field=field):
                        profile, _ = self.assistant.profile({field: alias.swapcase()})
                        self.assertEqual((profile['name'], profile['symbol']), (asset['name'], asset['symbol']))

    def test_similar_asset_names_are_not_confused(self):
        for name, symbol in (('Bitcoin Cash', 'BCH'), ('Ethereum Classic', 'ETC'), ('  bitcoin   cash ', 'BCH')):
            with self.subTest(name=name):
                result = self.assistant.analyze({'name': name})
                self.assertEqual(result['project']['symbol'], symbol)

    def test_unknown_names_and_custom_project_labels_are_preserved(self):
        for name in ('Example Protocol', 'Wrapped Bitcoin', 'Bitcoin-like', 'UnknownCoin'):
            with self.subTest(name=name):
                project = self.assistant.analyze({'name': name})['project']
                self.assertEqual((project['name'], project['symbol']), (name, ''))
        project = self.assistant.analyze({'name': 'Моё исследование', 'symbol': 'эфириум'})['project']
        self.assertEqual((project['name'], project['symbol']), ('Моё исследование', 'ETH'))
        unknown = self.assistant.analyze({'symbol': 'CustomToken'})['project']
        self.assertEqual(unknown['symbol'], 'CustomToken')

    def test_conflicting_name_and_ticker_are_rejected(self):
        for name, symbol in (('Bitcoin', 'ETH'), ('Bitcoin', 'UnknownCoin'), ('Bitcoin Cash', 'BTC')):
            with self.subTest(name=name, symbol=symbol), self.assertRaisesRegex(ValueError, 'другой тикер'):
                self.assistant.analyze({'name': name, 'symbol': symbol})
        project = self.assistant.analyze({'name': 'биткоин', 'symbol': 'xbt'})['project']
        self.assertEqual((project['name'], project['symbol']), ('Bitcoin', 'BTC'))

    def test_profile_roundtrip_and_report_keep_the_resolved_identity(self):
        result = self.assistant.analyze({'name': 'ETH', 'facts': {'product_exists': True}})
        imported = self.assistant.analyze(json.loads(json.dumps(result['project'])))
        self.assertEqual(imported['project'], result['project'])
        self.assertEqual(imported['score'], result['score'])
        self.assertIn('Анализ проекта: Ethereum', markdown_report(imported))
        self.assertIn('Тикер: ETH', markdown_report(imported))

    def test_cli_accepts_a_coin_name_or_ticker(self):
        for query, name, symbol in (('btc', 'Bitcoin', 'BTC'), ('Эфириум', 'Ethereum', 'ETH'), ('Solana', 'Solana', 'SOL')):
            with self.subTest(query=query):
                process = subprocess.run([sys.executable, '-m', 'project_assistant', 'ask', query],
                                         cwd=ROOT, capture_output=True, encoding='utf-8', timeout=10)
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertIn('Анализ проекта: ' + name, process.stdout)
                self.assertIn('Тикер: ' + symbol, process.stdout)


if __name__ == '__main__':
    unittest.main()
