import csv
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
import unittest
from unittest.mock import Mock

from project_assistant.knowledge import load_knowledge
from project_assistant.open_research import OpenResearch, circulation_forecast, scoped_observations
from project_assistant.public_sources import SourceError, collect_targeted_documents, read_document


def profile(name='Aave', symbol='AAVE'):
    return {'name': name, 'symbol': symbol, 'facts': {
        key: {'value': None} for key in load_knowledge()['criteria']}}


def doc(text, targets, url='https://governance.example.org/t/report/123.json'):
    return {'url': url, 'text': text, 'primary': True, 'targeted_criteria': targets}


class ProtocolDocumentTests(unittest.TestCase):
    def test_priority_criteria_apply_to_an_already_fetched_document(self):
        d = doc('Optimism governance documentation already read in the first pass.', [])
        fetcher = Mock()
        documents, failures = collect_targeted_documents(fetcher, [d], [], ['admin_controls_known'],
            {'governance.example.org'}, max_pages=0,
            priority_sources=[{'url': d['url'], 'criteria': ['admin_controls_known']}])
        self.assertEqual(documents, [])
        self.assertEqual(failures, [])
        self.assertEqual(d['targeted_criteria'], ['admin_controls_known'])
        fetcher.get.assert_not_called()

    def test_discourse_reads_requested_author_not_other_replies(self):
        posts = [{'post_number': i, 'username': name, 'cooked': '<p>' + text + '</p>',
                  'created_at': '2026-08-01T00:00:00Z', 'updated_at': '2026-08-02T00:00:00Z'}
                 for i, name, text in [(1, 'reporter', 'This is a published report with a documented incident and its resolution.'),
                                       (2, 'commenter', 'This comment is a proposal and is not the report author’s statement.')]]
        fetcher = Mock()
        fetcher.get.return_value = {'url': 'https://forum.example.org/t/report/123/2.json',
                                   'kind': 'application/json', 'received_at': '2026-08-03',
                                   'body': json.dumps({'title': 'Report', 'post_stream': {'posts': posts}}).encode()}
        result = read_document(fetcher, fetcher.get.return_value['url'])
        self.assertIn('Author: commenter', result['text'])
        self.assertNotIn('documented incident', result['text'])
        self.assertIn('Published: 2026-08-01', result['text'])
        with self.assertRaises(SourceError):
            read_document(fetcher, 'https://forum.example.org/t/report/123/3.json')

    def test_csv_retains_stable_public_export_url(self):
        fetcher = Mock()
        fetcher.get.return_value = {'url': 'https://download.example.org/expiring-file', 'kind': 'text/csv',
                                   'body': b'OP Token Allocation,Governance Fund,Investors\n' * 3, 'received_at': '2026-10-14'}
        result = read_document(fetcher, 'https://docs.example.org/export?format=csv')
        self.assertEqual(result['format'], 'csv')
        self.assertEqual(result['url'], 'https://docs.example.org/export?format=csv')

    def forecast(self, age=0, symbol='OP', contributor='47,600,000'):
        now = datetime.now(timezone.utc) - timedelta(days=age)
        stream = StringIO()
        csv.writer(stream).writerows([
            [symbol + ' Token Allocation'],
            ['', '', 'Governance Fund', 'Partner Fund', 'Airdrops', 'RPGF', 'Early Core Contributors', 'Investors', 'Cumulative Total'],
            ['Circulating Supply at - ' + now.strftime('%d %b %Y')],
            ['FY 5 - Forecast', f'May {now.year} - April {now.year + 1}', '10,000,000', '200,000,000', '-', '-', contributor, '15,330,396', '2,504,489,639'],
            ['These numbers are provided for illustrative purposes only. You should not rely on the accuracy of these estimates for any purpose.'],
            [f'Does not account for inflation programmed into the {symbol} token smart contract, which is currently set at 0% and subject to change via governance vote.'],
        ])
        return doc(stream.getvalue(), ['unlock_schedule_known', 'emission_disclosed'])

    def test_forecast_is_explicitly_not_an_exact_cliff_or_zero_unlock(self):
        found = circulation_forecast(profile('Optimism', 'OP'), self.forecast())
        self.assertIs(found['unlock_schedule_known']['value'], False)
        self.assertIn('47,600,000 OP', found['unlock_schedule_known']['evidence'])
        self.assertIn('15,330,396 OP', found['unlock_schedule_known']['evidence'])
        self.assertIs(found['emission_disclosed']['value'], True)
        self.assertIn('governance vote', found['emission_disclosed']['evidence'])

    def test_forecast_rejects_wrong_asset_stale_future_and_missing_amounts(self):
        for d in (self.forecast(symbol='OTHER'), self.forecast(age=40),
                  self.forecast(age=-2), self.forecast(contributor='-')):
            with self.subTest(text=d['text'][:100]):
                self.assertEqual(circulation_forecast(profile('Optimism', 'OP'), d), {})

    def test_governance_balances_and_voting_describe_rights_not_dividends(self):
        d = doc('Aave Governance v3. The network where accounting of voting power resides '
                '(e.g. Ethereum, as balances of AAVE, stkAAVE, aAAVE are stored there).\n'
                'Everybody with voting power on Ethereum is able to vote on the Voting Network, by interacting with the Voting Machine smart contract.',
                ['holder_rights_clear', 'token_role_defined'])
        found = scoped_observations(profile(), d)
        self.assertIs(found['holder_rights_clear']['value'], True)
        self.assertIs(found['token_role_defined']['value'], True)
        self.assertNotIn('token_value_link', found)

    def test_computational_settlement_is_not_token_demand(self):
        p = profile()
        d = doc('Aave Governance. On the Voting Network, a permissionless settlement of global data to be used for vote validation will need to be done, by the Aave Robot, or any other address.', [])
        OpenResearch(load_knowledge()['criteria']).documented_facts(p, [d])
        self.assertIsNone(p['facts']['token_value_link']['value'])
        self.assertIsNone(p['facts']['token_role_defined']['value'])

    def test_live_integration_preserves_metric_scope_and_rejects_future(self):
        year = datetime.now(timezone.utc).year
        text = f'Aave development update {year}.\nEtherFi Cash went live on Aave V4 on Optimism and reported more than 100,000 cardholders.'
        found = scoped_observations(profile(), doc(text, ['users_active', 'roadmap_delivery']))
        for key in ('users_active', 'usage_evidence', 'product_exists', 'roadmap_delivery'):
            self.assertIs(found[key]['value'], True)
        self.assertIn('названному приложению', found['usage_evidence']['evidence'])
        future = text.replace('went live on', 'will go live on')
        self.assertEqual(scoped_observations(profile(), doc(future, ['users_active'])), {})

    def test_founder_identity_survives_unrelated_proposal_context(self):
        text = 'Aave disclosure. Stani Kulechov, co-founder of Aave, is an early angel investor of BOB and advised on some technical parts of this proposal but did not receive any compensation related to this proposal.'
        found = scoped_observations(profile(), doc(text, ['team_identified']))
        self.assertIn('Stani Kulechov', found['team_identified']['evidence'])

    def test_past_vulnerability_requires_completed_resolution(self):
        text = 'Optimism. This document describes a critical bug in our system which we were alerted to on February 2nd, 2022.\nWe confirmed the issue, implemented a fix, and upgraded our infrastructure within 3 hours.'
        d = doc(text, ['incident_history_known'], 'https://github.com/example/optimism/postmortems/report.md')
        self.assertIs(scoped_observations(profile('Optimism', 'OP'), d)['incident_history_known']['value'], True)
        d['text'] = text.replace('We confirmed the issue, implemented a fix, and upgraded our infrastructure within 3 hours.', 'The issue has not yet been resolved and a fix will be implemented later.')
        self.assertEqual(scoped_observations(profile('Optimism', 'OP'), d), {})


if __name__ == '__main__':
    unittest.main()
