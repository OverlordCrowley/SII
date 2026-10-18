from datetime import datetime, timezone
import json
import unittest
from unittest.mock import Mock, patch

from project_assistant.public_sources import PublicFetcher, SourceError
from project_assistant.supply_sources import HEDGEY_VESTING, OP_RPC, abi_words, hedgey_steps, hedgey_unlocks, token_units


TOKEN = '0x4200000000000000000000000000000000000042'
OWNER = '0x1a944c47372f52ffa4fea738ac0ca5bcf7424c15'


def encode(*words):
    return '0x' + ''.join(f'{w:064x}' for w in words)


class VestingEventsTests(unittest.TestCase):
    def test_cliff_first_second_and_raw_token_precision(self):
        words = [int(TOKEN, 16), 90300 * 10**18, 200, 200, 90300 * 10**18, 1, 1, 1]
        self.assertEqual(hedgey_steps(words, 100), [(201, 90300 * 10**18)])
        self.assertEqual(hedgey_steps(words, 201), [])
        self.assertEqual(token_units(123456789012345678901234567890, 18), '123456789012.34567890123456789')
        self.assertEqual(token_units(1, 18), '0.000000000000000001')

    def test_catchup_at_cliff_and_partial_last_period(self):
        self.assertEqual(hedgey_steps([1, 25, 100, 125, 10, 10, 1, 0], 110), [(125, 20), (130, 5)])
        self.assertEqual(hedgey_steps([1, 5, 100, 0, 10, 10, 1, 0], 100), [(110, 5)])

    def test_running_continuous_stream_is_not_next_discrete_cliff(self):
        self.assertEqual(hedgey_steps([1, 1000000, 100, 0, 1, 1, 1, 1], 200), [])
        self.assertEqual(hedgey_steps([1, 1000, 100, 300, 1, 1, 1, 1], 200), [(300, 200)])

    def test_invalid_abi_and_plan_parameters_are_rejected(self):
        for response in ('0x', '0xZZ', encode(1, 2), None):
            with self.subTest(response=response), self.assertRaises(SourceError):
                abi_words(response, 1)
        for words in ([1, 0, 100, 0, 1, 10, 1, 0], [1, 10, 100, 0, 0, 10, 1, 0],
                      [1, 10, 100, 0, 1, 0, 1, 0], [1, 10, 100, 300, 1, 1, 1, 0]):
            with self.subTest(words=words), self.assertRaises(SourceError):
                hedgey_steps(words, 100)

    def test_rpc_only_allows_reading_and_checks_response_identity(self):
        fetcher = PublicFetcher()
        with patch.object(fetcher, '_request') as request:
            for method in ('eth_sendRawTransaction', 'eth_sendTransaction', 'personal_unlockAccount'):
                with self.assertRaises(SourceError):
                    fetcher.rpc(OP_RPC, method, [])
            request.assert_not_called()
            request.return_value = {'body': b'{"jsonrpc":"2.0","id":1,"result":"0xa"}'}
            self.assertEqual(fetcher.rpc(OP_RPC, 'eth_chainId', []), '0xa')
            body = json.loads(request.call_args.kwargs['rpc_body'])
            self.assertEqual(body['method'], 'eth_chainId')
            for raw in (b'{"id":2,"result":"0xa"}', b'{"id":1,"error":{"message":"failed"}}', b'null', b'not-json'):
                request.return_value = {'body': raw}
                with self.assertRaises(SourceError):
                    fetcher.rpc(OP_RPC, 'eth_chainId', [])

    def adapter(self, wrong_token=False, wrong_owner=False, old_block=False):
        fetcher = Mock()
        now = int(datetime.now(timezone.utc).timestamp())
        page = {'url': 'https://www.vestream.io/token/10/' + TOKEN,
                'text': 'OP on Optimism. Full contract: ' + TOKEN, 'received_at': datetime.now(timezone.utc).isoformat(),
                'links': [('https://optimistic.etherscan.io/address/' + OWNER, OWNER)]}
        fetcher.json.return_value = {'items': [{'id': '465', 'token': {'address_hash': HEDGEY_VESTING}}], 'next_page_params': None}
        def rpc(url, method, params):
            if method == 'eth_chainId': return '0xa'
            if method == 'eth_getBlockByNumber': return {'number': '0x123', 'timestamp': hex(now - (1000 if old_block else 0))}
            self.assertEqual(params[1], '0x123')
            selector = params[0]['data'][:10]
            if selector == '0x313ce567': return encode(18)
            if selector == '0x6352211e': return encode(int(OWNER, 16) + (1 if wrong_owner else 0))
            if selector == '0xb1620616': return encode(int(TOKEN, 16) + (1 if wrong_token else 0), 90300 * 10**18, now + 86400, now + 86400, 90300 * 10**18, 1, 1, 1)
            self.fail('Unexpected read')
        fetcher.rpc.side_effect = rpc
        return fetcher, page, now

    def test_adapter_verifies_identity_owner_units_and_one_block(self):
        fetcher, page, now = self.adapter()
        data = {'name': 'Optimism', 'symbol': 'op', 'platforms': {'optimistic-ethereum': TOKEN}}
        with patch('project_assistant.supply_sources.read_document', return_value=page):
            docs, facts, notes, updates = hedgey_unlocks(fetcher, data)
        self.assertIs(facts['unlock_schedule_known']['value'], True)
        self.assertEqual(updates['next_unlock_tokens'], 90300)
        events = [event for d in docs for event in d.get('unlock_events', [])]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['amount'], '90300')
        self.assertEqual(events[0]['at'], datetime.fromtimestamp(now + 86401, timezone.utc).isoformat())
        self.assertEqual(events[0]['recipient'], OWNER)
        self.assertIn('не полный календарь', facts['unlock_schedule_known']['evidence'])
        self.assertIn('отозвать', facts['unlock_schedule_known']['evidence'])
        self.assertIn('/instance/465', facts['unlock_schedule_known']['source'])

    def test_wrong_token_owner_or_stale_block_cannot_fill_unlock(self):
        data = {'name': 'Optimism', 'symbol': 'op', 'platforms': {'optimistic-ethereum': TOKEN}}
        for kwargs in ({'wrong_token': True}, {'wrong_owner': True}, {'old_block': True}):
            fetcher, page, _ = self.adapter(**kwargs)
            with self.subTest(kwargs=kwargs), patch('project_assistant.supply_sources.read_document', return_value=page):
                try:
                    _, facts, notes, updates = hedgey_unlocks(fetcher, data)
                except SourceError:
                    continue
                self.assertNotIn('unlock_schedule_known', facts)
                self.assertEqual(updates, {})

    def test_other_chain_is_not_queried(self):
        fetcher = Mock()
        self.assertEqual(hedgey_unlocks(fetcher, {'platforms': {'ethereum': TOKEN}}), ([], {}, [], {}))
        fetcher.rpc.assert_not_called()
        fetcher.get.assert_not_called()


if __name__ == '__main__':
    unittest.main()
