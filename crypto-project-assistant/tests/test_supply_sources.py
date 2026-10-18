from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock, patch

from project_assistant.assistant import ProjectAssistant
from project_assistant.open_research import OpenResearch, evidence
from project_assistant.public_sources import SourceError
from project_assistant.supply_sources import blockscout_holders, coincarp_holders, corporate_holders, multichain_holders, research_unlocks, tokenomist_unlocks, web_treasuries
from project_assistant.tokenomics import calculate


class SupplySourceTests(unittest.TestCase):
    def setUp(self):
        self.data = {'id': 'ethereum', 'name': 'Ethereum', 'symbol': 'eth', 'platforms': {}}
        self.fetcher = Mock()

    def page(self, name='Ethereum', symbol='ETH', status='Ethereum is fully unlocked.', age=0):
        stamp = (datetime.now(timezone.utc) - timedelta(days=age)).strftime('%m/%d/%y %H:%M')
        return {'url': 'https://tokenomist.ai/ethereum/eth-unlocks', 'received_at': datetime.now(timezone.utc).isoformat(),
                'text': f'{name} ({symbol}) | Tokenomics & Vesting Schedule\n'
                        'Advert: Next ARB unlock July 15, 2026, 92.65 million tokens.\n'
                        f'Token Information\nLast updated {stamp} UTC\n'
                        f'When is the next {name} unlock?\n{status}\n'
                        f'What is the {name} token vesting schedule?\n{name} followed vesting for the team and investors.\n'
                        f'How is {name} allocated across stakeholders?\nThe {name} allocation includes Investors at 80% and Team at 20%.\n',
                'links': []}

    def holders(self, native=True):
        items = []
        for i, label in enumerate(['Eth 2.0 Deposit Contract', 'Wrapped Ether', 'Exchange 1', '', 'Exchange 2'], 1):
            owner = {'hash': f'0x{i:040x}', 'name': None, 'is_contract': i <= 2,
                     'metadata': {'tags': [{'name': label, 'tagType': 'name'}] if label else []}}
            if native:
                items.append({**owner, 'coin_balance': str(i * 10**18)})
            else:
                items.append({'address': owner, 'value': str(i * 100)})
        return {'total_supply': '100', 'items': items}

    def test_richlist_requires_attribution_and_preserves_unknown_snapshot_date(self):
        data = {'id': 'solana', 'name': 'Solana', 'symbol': 'sol'}
        for label in ('', ' Exchange'):
            rows = ''.join(f'<tr><td>{i}</td><td>{"1" * 31}{i}{label}</td><td>1,000</td><td>1.01%</td></tr>' for i in range(1, 6))
            self.fetcher.get.return_value = {'body': ('<title>Solana Rich List</title><h2>Solana SOL</h2><table>' + rows + '</table>').encode(), 'received_at': '2026-10-18T00:00:00Z'}
            docs, facts, _ = coincarp_holders(self.fetcher, data)
            self.assertEqual(bool(facts), bool(label))
            self.assertIn('источник не раскрыл время снимка', docs[0]['text'])
        self.fetcher.get.return_value['body'] = b'<title>Other asset</title>'
        with self.assertRaises(SourceError):
            coincarp_holders(self.fetcher, data)

    def test_corporate_reserves_are_a_separate_attributed_subset(self):
        self.fetcher.json.return_value = {'companies': [
            {'name': name, 'total_holdings': amount, 'percentage_of_total_supply': share}
            for name, amount, share in [('A', 100, 1), ('B', 200, 2), ('C', 300, 3), ('Invalid', 'NaN', 4), ('A', 100, 1)]]}
        _, facts, _ = corporate_holders(self.fetcher, {'id': 'solana', 'name': 'Solana', 'symbol': 'sol'})
        text = facts['concentration_known']['evidence']
        self.assertIn('3 именованных', text)
        self.assertIn('не все крупные владельцы', text)
        self.assertIn('не приписываются адреса', text)
        self.fetcher.json.return_value['companies'] = self.fetcher.json.return_value['companies'][:2]
        with self.assertRaises(SourceError):
            corporate_holders(self.fetcher, {'id': 'solana', 'name': 'Solana', 'symbol': 'sol'})

    def test_completed_vesting_is_an_explicit_dated_status_not_a_guessed_future_event(self):
        with patch('project_assistant.supply_sources.read_document', return_value=self.page()):
            _, facts, _, updates = research_unlocks(self.fetcher, self.data)
        self.assertTrue(facts['unlock_schedule_known']['value'])
        self.assertTrue(facts['vesting_disclosed']['value'])
        self.assertEqual(updates, {'next_unlock_tokens': 0})
        self.assertIn('не отсутствие заблокированного стейка', facts['unlock_schedule_known']['evidence'])
        result = calculate({'circulating_supply': 100, **updates})
        self.assertEqual(result['values']['unlock_share_pct'], 0)
        self.assertFalse(any('Дата разблокировки' in warning for warning in result['warnings']))
        self.assertNotIn('large_unlock', result['facts'])

    def test_wrong_asset_stale_info_and_inconsistent_claims_do_not_become_zero_unlock(self):
        cases = [self.page(symbol='ARB'), self.page(name='Ethena', symbol='ENA'), self.page(age=8),
                 self.page(status='The next Ethereum unlock is not available.'),
                 self.page(status='Shanghai allows staking withdrawals. Ethereum withdrawals are now available.')]
        for doc in cases:
            with self.subTest(text=doc['text'][:50]), patch('project_assistant.supply_sources.read_document', return_value=doc):
                try:
                    _, facts, _, updates = tokenomist_unlocks(self.fetcher, self.data)
                except SourceError:
                    continue
                self.assertNotIn('unlock_schedule_known', facts)
                self.assertNotIn('next_unlock_tokens', updates)
        doc = self.page()
        doc['text'] += '\nHow much Ethereum supply is currently unlocked?\nThe remaining tokens are still locked.\n'
        with patch('project_assistant.supply_sources.read_document', return_value=doc), self.assertRaises(SourceError):
            tokenomist_unlocks(self.fetcher, self.data)

    def test_date_without_amount_stays_partial_and_an_independent_source_supplies_the_event(self):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).date()
        status = f'The next Ethereum unlock is scheduled for {future.strftime("%B")} {future.day}, {future.year}. Released to Investors.'
        first = self.page(status=status)
        other = {'url': 'https://defillama.com/unlocks/ethereum', 'received_at': datetime.now(timezone.utc).isoformat(),
                 'text': 'Ethereum (ETH) Token Unlocks\nNext ETH unlock: ' + future.strftime('%b') + f' {future.day}, {future.year}, 1.5m ETH ($3m), for Team and Investors.', 'links': []}
        with patch('project_assistant.supply_sources.read_document', side_effect=[first, other]):
            _, facts, notes, updates = research_unlocks(self.fetcher, self.data)
        self.assertTrue(facts['unlock_schedule_known']['value'])
        self.assertEqual(updates['next_unlock_tokens'], 1500000)
        self.assertEqual(updates['unlock_date'], future.isoformat())
        self.assertTrue(any('ноль не подставлен' in note for note in notes))

    def test_no_access_no_such_coin_and_past_events_never_establish_absence_of_unlocks(self):
        with patch('project_assistant.supply_sources.read_document', side_effect=SourceError('HTTP 403')):
            _, facts, notes, updates = research_unlocks(self.fetcher, self.data)
        self.assertFalse(facts)
        self.assertFalse(updates)
        self.assertEqual(len(notes), 2)
        stale = self.page(status='The next Ethereum unlock is scheduled for January 1, 2020. Released to Investors.')
        with patch('project_assistant.supply_sources.read_document', return_value=stale), self.assertRaises(SourceError):
            tokenomist_unlocks(self.fetcher, self.data)

    def test_native_balances_use_wei_units_labels_and_address_level_limitations(self):
        self.fetcher.json.return_value = self.holders()
        _, facts, _ = blockscout_holders(self.fetcher, self.data)
        text = facts['concentration_known']['evidence']
        self.assertIn('1.0000%', text)
        self.assertIn('накопленные депозиты', text)
        self.assertIn('не конечных владельцев', text)
        self.assertIn('не гарантирует один общий блок', text)
        self.assertIn('Без публичной метки', text)

    def test_new_token_resolves_network_from_registries_and_exact_contract(self):
        address = '0x' + 'a' * 40
        data = {'id': 'new-token', 'name': 'New Token', 'symbol': 'NEW', 'platforms': {'new-chain': address}}
        platforms = [{'id': 'new-chain', 'chain_identifier': 12345, 'native_coin_id': 'other'}]
        chains = {'12345': {'isTestnet': False, 'native_currency': 'ETH', 'explorers': [{'url': 'https://explorer.example/'}]}}
        token = {'address_hash': address, 'symbol': 'NEW', 'type': 'ERC-20', 'total_supply': '10000'}
        self.fetcher.json.side_effect = [platforms, chains, token, self.holders(native=False)]
        _, facts, _ = multichain_holders(self.fetcher, data)
        self.assertIn('chain_id=12345', facts['concentration_known']['evidence'])
        self.assertIn('https://explorer.example/api/v2/tokens/' + address, facts['concentration_known']['source'])
        self.assertIn('1.0000%', facts['concentration_known']['evidence'])
        chains['12345']['isTestnet'] = True
        self.fetcher.json.reset_mock()
        self.fetcher.json.side_effect = [platforms, chains]
        self.assertEqual(multichain_holders(self.fetcher, data)[1], {})
        self.assertEqual(self.fetcher.json.call_count, 2)

    def test_generic_richlist_checks_symbol_contract_and_official_site(self):
        address = '0x' + 'a' * 40
        data = {'id': 'new-token', 'name': 'New Token', 'symbol': 'NEW',
                'platforms': {'new-chain': address}, 'links': {'homepage': ['https://project.example/']}}
        rows = ''.join(f'<tr><td>{i}</td><td>0x{i:040x} Custodian</td><td>100</td><td>1%</td></tr>' for i in range(1, 6))
        raw = '<title>New Token Rich List</title><h2>New Token NEW</h2><a href="https://project.example/">Site</a>' + address + '<table>' + rows + '</table>'
        self.fetcher.get.return_value = {'body': raw.encode(), 'received_at': '2026-10-18'}
        self.assertTrue(coincarp_holders(self.fetcher, data)[1]['concentration_known']['value'])
        for wrong in (raw.replace('New Token NEW', 'New Token OTHER'), raw.replace(address, '0x' + 'b' * 40), raw.replace('project.example', 'other.example')):
            self.fetcher.get.return_value['body'] = wrong.encode()
            with self.assertRaises(SourceError):
                coincarp_holders(self.fetcher, data)

    def test_html_treasuries_validate_identity_units_and_preserve_rounded_subset_scope(self):
        data = {'id': 'solana', 'name': 'Solana', 'symbol': 'SOL'}
        rows = ''.join(f'<tr><td>{i}🇺🇸Company {i}</td><td>Company</td><td>{i}.5M</td><td>$0</td><td>$0</td><td>N/A</td><td>1.2%</td><td>N/A</td></tr>' for i in range(1, 4))
        raw = '<title>Solana (SOL) Treasury Companies</title><table><thead><tr><th>Total Coins</th><th>% Total Supply</th></tr></thead>' + rows + '</table>'
        self.fetcher.get.return_value = {'body': raw.encode(), 'received_at': '2026-10-18'}
        _, facts, _ = web_treasuries(self.fetcher, data)
        text = facts['concentration_known']['evidence']
        self.assertIn('3500000.0 SOL', text)
        self.assertIn('округлены', text)
        self.assertIn('не все крупные владельцы', text)
        self.assertIn('независимость исходного набора', text)
        for bad in (raw.replace('(SOL)', '(OTHER)'), raw.replace('Total Coins', 'USD Value'), raw.replace('1.2%', 'NaN%')):
            self.fetcher.get.return_value['body'] = bad.encode()
            with self.assertRaises(SourceError):
                web_treasuries(self.fetcher, data)

    def test_corporate_api_rate_limit_falls_back_to_public_html(self):
        research = OpenResearch(ProjectAssistant(models=False).criteria, self.fetcher)
        with patch('project_assistant.open_research.blockscout_holders', return_value=([], {}, [])), \
                patch.object(research, 'etherscan_concentration', return_value=([], {}, [])), \
                patch('project_assistant.open_research.multichain_holders', return_value=([], {}, [])), \
                patch('project_assistant.open_research.coincarp_holders', return_value=([], {}, [])), \
                patch('project_assistant.open_research.corporate_holders', side_effect=SourceError('HTTP 429')), \
                patch('project_assistant.open_research.web_treasuries', return_value=([], {'concentration_known': evidence(True, 'Corporate subset', 'https://www.datawallet.com/treasury/solana')}, [])):
            _, facts, notes = research.concentration({'id': 'solana', 'name': 'Solana', 'symbol': 'SOL'})
        self.assertTrue(facts['concentration_known']['value'])
        self.assertTrue(any('429' in note for note in notes))

    def test_token_holders_require_exact_contract_and_supply_units(self):
        address = '0x' + 'a' * 40
        data = {'id': 'example', 'name': 'Example', 'symbol': 'EXM', 'platforms': {'ethereum': address}}
        token = {'address_hash': address, 'symbol': 'EXM', 'type': 'ERC-20', 'total_supply': '10000'}
        self.fetcher.json.side_effect = [token, self.holders(native=False)]
        _, facts, _ = blockscout_holders(self.fetcher, data)
        self.assertIn('1.0000%', facts['concentration_known']['evidence'])
        self.assertIn(address + '/holders', facts['concentration_known']['source'])
        for invalid in ({**token, 'address_hash': '0x' + 'b' * 40}, {**token, 'symbol': 'OTHER'}):
            self.fetcher.json.side_effect = [invalid]
            with self.assertRaises(SourceError):
                blockscout_holders(self.fetcher, data)
        self.assertEqual(blockscout_holders(self.fetcher, {'id': 'wrapped-ethereum', 'platforms': {}})[1], {})

    def test_duplicate_anonymous_or_malformed_holder_tables_are_rejected(self):
        bad = [[], {'items': [], 'total_supply': '100'}, {'items': [], 'total_supply': 'NaN'},
               {'items': [self.holders()['items'][0]] * 5, 'total_supply': '100'}]
        for response in bad:
            with self.subTest(response=response), self.assertRaises(SourceError):
                self.fetcher.json.return_value = response
                blockscout_holders(self.fetcher, self.data)

    def test_etherscan_failure_does_not_prevent_automatic_26_of_26(self):
        assistant = ProjectAssistant(models=False)
        research = OpenResearch(assistant.criteria, self.fetcher)
        profile = {'name': 'Ethereum', 'symbol': 'ETH', 'network': 'Ethereum', 'type': 'layer1',
                   'contract': 'Native ETH', 'description': 'Profile', 'market': {},
                   'facts': {key: evidence(key not in ('unlock_schedule_known', 'concentration_known') or None, 'Known source', '') for key in assistant.criteria}}
        self.fetcher.json.return_value = self.holders()
        with patch('project_assistant.supply_sources.read_document', return_value=self.page()), \
                patch('project_assistant.open_research.collect_documents', return_value=([], [])), \
                patch('project_assistant.open_research.discover_indexes', return_value=([], [])), \
                patch.object(research, 'etherscan_concentration', side_effect=SourceError('Etherscan offline')) as etherscan, \
                patch.object(research, 'github', return_value=([], {}, [])), \
                patch.object(research, 'liquidity', return_value=([], {}, [])), \
                patch.object(research, 'defi', return_value=([], {}, [])), \
                patch.object(research, 'peers', return_value=([], {}, [])), \
                patch.object(research, 'select_facts', return_value=(0, 'No missing textual facts')):
            research.run(profile, self.data)
        etherscan.assert_not_called()
        result = assistant.analyze(profile)
        self.assertEqual(result['score']['known'], 26)
        self.assertEqual(result['project']['market']['next_unlock_tokens'], 0)


if __name__ == '__main__':
    unittest.main()
