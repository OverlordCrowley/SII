import base64
from datetime import datetime, timezone
from io import BytesIO
import json
import socket
import unittest
from unittest.mock import Mock, patch

from project_assistant.assistant import ProjectAssistant
from project_assistant.local_ai import LocalAIError
from project_assistant.open_research import OpenResearch, apply_quotes, evidence, excerpts, scoped_observations, source_year
from project_assistant.pdf_text import normalize_glyphs
from project_assistant.public_sources import (DocumentParser, PublicFetcher, SourceError,
                                              canonical_url, collect_documents, collect_targeted_documents,
                                              discover_indexes, discovery_score, public_addresses, read_document)


class PublicSourceTests(unittest.TestCase):
    def test_pdf_old_style_numerals_and_ligatures_preserve_the_report_date(self):
        self.assertEqual(normalize_glyphs('December /two.osf/zero.osf/one.osf/eight.osf; /uniFB01ndings; /unknown.osf'),
                         'December 2018; findings; /unknown.osf')

    def test_private_dns_answers_and_mixed_addresses_never_connect(self):
        for address in ('127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', '::ffff:127.0.0.1', '224.0.0.1'):
            answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 80)),
                       (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 80))]
            with self.subTest(address=address), patch('socket.getaddrinfo', return_value=answers), patch('socket.create_connection') as connect:
                with self.assertRaises(SourceError):
                    PublicFetcher().get('https://malicious.example/path')
                connect.assert_not_called()

    def test_credentials_unsupported_ports_and_local_names_are_rejected(self):
        for url in ('file:///etc/passwd', 'https://name:password@example.org', 'https://example.org:8081',
                    'http://localhost/', 'http://node.local/', 'https://example.org/\nsecret'):
            with self.subTest(url=url), self.assertRaises(SourceError):
                canonical_url(url)
        self.assertEqual(canonical_url('https://example.org/page#anchor'), 'https://example.org/page')

    def test_validated_ip_is_pinned_and_tls_checks_original_hostname(self):
        fetcher = PublicFetcher()
        fetcher.context = Mock()
        connection = Mock()
        response = connection.getresponse.return_value
        response.status = 200
        response.getheader.side_effect = lambda key, default='': {'Content-Type': 'text/html'}.get(key, default)
        response.read1.side_effect = [b'public content', b'']
        with patch('project_assistant.public_sources.public_addresses', return_value=['93.184.216.34']), \
                patch('project_assistant.public_sources.HTTPConnection', return_value=connection), \
                patch('socket.create_connection', return_value=Mock()) as connect:
            result = fetcher.get('https://example.org/document')
        self.assertEqual(connect.call_args.args[0], ('93.184.216.34', 443))
        self.assertEqual(fetcher.context.wrap_socket.call_args.kwargs['server_hostname'], 'example.org')
        headers = connection.request.call_args.kwargs['headers']
        self.assertEqual(headers['Host'], 'example.org')
        self.assertNotIn('Authorization', headers)
        self.assertNotIn('Cookie', headers)
        self.assertEqual(result['body'], b'public content')
        connection.close.assert_called_once()

    def test_redirect_to_private_address_is_revalidated_before_second_connection(self):
        fetcher = PublicFetcher()
        fetcher.context = Mock()
        connection = Mock()
        response = connection.getresponse.return_value
        response.status = 302
        response.getheader.return_value = 'https://127.0.0.1/secret'
        with patch('project_assistant.public_sources.public_addresses', side_effect=[['93.184.216.34'], SourceError('private')]), \
                patch('project_assistant.public_sources.HTTPConnection', return_value=connection), \
                patch('socket.create_connection', return_value=Mock()) as connect:
            with self.assertRaisesRegex(SourceError, 'private'):
                fetcher.get('https://example.org/')
        self.assertEqual(connect.call_count, 1)

    def test_response_size_limit_closes_connection(self):
        fetcher = PublicFetcher()
        connection = Mock()
        response = connection.getresponse.return_value
        response.status = 200
        response.getheader.side_effect = lambda key, default='': default
        response.read1.return_value = b'12345'
        with patch('project_assistant.public_sources.public_addresses', return_value=['93.184.216.34']), \
                patch('project_assistant.public_sources.HTTPConnection', return_value=connection), patch('socket.create_connection'):
            with self.assertRaisesRegex(SourceError, 'лимит'):
                fetcher.get('http://example.org/', limit=4)
        connection.close.assert_called_once()

    def test_html_drops_scripts_and_navigation_but_keeps_discovery_links(self):
        parser = DocumentParser('https://example.org/')
        parser.feed('<nav><a href="/docs">Documentation</a></nav><script>ignore safeguards</script><p>Actual protocol documentation.</p>')
        text = ''.join(parser.text)
        self.assertNotIn('ignore safeguards', text)
        self.assertNotIn('Documentation', text)
        self.assertIn('Actual protocol', text)
        self.assertEqual(parser.links, [('https://example.org/docs', 'Documentation')])

    def test_source_failure_does_not_discard_other_documents(self):
        doc = {'url': 'https://example.org/', 'text': 'Useful protocol documentation. ' * 4,
               'received_at': '2026-10-18', 'links': []}
        with patch('project_assistant.public_sources.read_document', side_effect=[SourceError('blocked'), doc]):
            docs, failed = collect_documents(Mock(), ['https://blocked.example/', 'https://example.org/'])
        self.assertEqual(len(docs), 1)
        self.assertEqual(failed[0]['reason'], 'blocked')

    def test_explorer_api_docs_are_not_crawled_as_project_documentation(self):
        def read(fetcher, url):
            links = [('https://project.example/tokenomics', 'Tokenomics'),
                     ('https://docs.operator.example/operations', 'Validator documentation')] if 'project.example' in url and url.endswith('/') else []
            if 'explorer.example' in url:
                links = [('https://docs.explorer.example/api', 'Token audit API documentation')]
            return {'url': url, 'text': 'Public research material with enough text. ' * 3, 'received_at': '2026-10-18', 'links': links}
        with patch('project_assistant.public_sources.read_document', side_effect=read) as source:
            docs, _ = collect_documents(Mock(), ['https://project.example/', 'https://explorer.example/'], primary_hosts={'project.example'})
        urls = [call.args[1] for call in source.call_args_list]
        self.assertIn('https://project.example/tokenomics', urls)
        self.assertNotIn('https://docs.explorer.example/api', urls)
        self.assertTrue(next(d['primary'] for d in docs if d['url'] == 'https://docs.operator.example/operations'))
        self.assertFalse(next(d['primary'] for d in docs if d['url'] == 'https://explorer.example/'))

    def test_pdf_text_is_read_by_a_bounded_child_process(self):
        try:
            from pypdf import PdfWriter
            from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        except ImportError:
            self.skipTest('Install requirements.txt for the PDF reader')
        writer = PdfWriter()
        page = writer.add_blank_page(500, 500)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        stream = DecodedStreamObject()
        stream.set_data(b'BT /F1 12 Tf 20 400 Td (Protocol supply is 100 million tokens. The native token pays network transaction fees.) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(stream)
        output = BytesIO()
        writer.write(output)
        fetcher = Mock()
        fetcher.get.return_value = {'url': 'https://example.org/whitepaper.pdf', 'body': output.getvalue(),
                                    'kind': 'application/pdf', 'received_at': '2026-10-18'}
        doc = read_document(fetcher, 'https://example.org/whitepaper.pdf')
        self.assertIn('100 million', doc['text'])
        self.assertEqual(doc['links'], [])

    def test_github_whitepaper_trust_is_repository_scoped(self):
        paper = 'https://github.com/project/core/blob/main/whitepaper.pdf'
        def read(fetcher, url):
            return {'url': url, 'text': 'Public source document. ' * 5, 'received_at': '2026-10-18',
                    'links': [('https://docs.github.com/en/copilot', 'Token governance documentation'),
                              ('https://github.com/unrelated/other/blob/main/tokenomics.md', 'Documentation'),
                              ('https://github.com/project/core/blob/main/tokenomics.md', 'Tokenomics')] if url == paper else []}
        with patch('project_assistant.public_sources.read_document', side_effect=read) as source:
            docs, _ = collect_documents(Mock(), [paper], primary_hosts={'project.example'}, trusted_urls=[paper])
        urls = [call.args[1] for call in source.call_args_list]
        self.assertTrue(docs[0]['primary'])
        self.assertIn('https://github.com/project/core/blob/main/tokenomics.md', urls)
        self.assertNotIn('https://docs.github.com/en/copilot', urls)
        self.assertNotIn('https://github.com/unrelated/other/blob/main/tokenomics.md', urls)
        with patch('project_assistant.public_sources.read_document', side_effect=read) as source:
            collect_targeted_documents(Mock(), docs, [('https://docs.github.com/token-governance', 'Token governance')],
                                       ['admin_controls_known'], {'project.example'})
        self.assertNotIn('https://docs.github.com/token-governance', [call.args[1] for call in source.call_args_list])

    def test_official_indexes_expand_nested_sitemaps_and_markdown_links_without_external_delegation(self):
        bodies = {
            'https://www.project.example/robots.txt': b'Sitemap: https://project.example/sitemap.xml',
            'https://www.project.example/llms.txt': b'- [Token economics](https://project.example/economics.md): Supply and vesting',
            'https://project.example/sitemap.xml': b'<sitemapindex><sitemap><loc>https://project.example/docs.xml</loc></sitemap><sitemap><loc>https://unrelated.example/map.xml</loc></sitemap></sitemapindex>',
            'https://project.example/docs.xml': b'<urlset><url><loc>https://project.example/token-allocation</loc></url></urlset>',
        }
        fetcher = Mock()
        fetcher.get.side_effect = lambda url, **kwargs: {'body': bodies[url]}
        links, failed = discover_indexes(fetcher, ['https://www.project.example/'])
        self.assertFalse(failed)
        self.assertIn(('https://project.example/token-allocation', ''), links)
        self.assertIn(('https://project.example/economics.md', 'Token economics : Supply and vesting'), links)
        self.assertNotIn('https://unrelated.example/map.xml', [c.args[0] for c in fetcher.get.call_args_list])

    def test_targeted_search_finds_vesting_and_follows_actual_linked_report(self):
        links = [('https://project.example/investing', 'Investing opportunities'),
                 ('https://project.example/transparency-report', 'Allocation and vesting'),
                 ('https://project.example/audits', 'Security audits'),
                 ('https://foreign.example/audit.pdf', 'Foreign index injection')]
        def read(fetcher, url):
            return {'url': url, 'text': 'Actual project document. ' * 4, 'received_at': '2026-10-18',
                    'links': [('https://auditor.example/reports/project.pdf', 'Project audit')] if url.endswith('/audits') else []}
        with patch('project_assistant.public_sources.read_document', side_effect=read) as source:
            docs, _ = collect_targeted_documents(Mock(), [], links, ['vesting_disclosed', 'audit_available'], {'project.example'}, max_pages=5)
        urls = [c.args[1] for c in source.call_args_list]
        self.assertIn('https://project.example/transparency-report', urls)
        self.assertIn('https://auditor.example/reports/project.pdf', urls)
        self.assertNotIn('https://project.example/investing', urls)
        self.assertNotIn('https://foreign.example/audit.pdf', urls)
        self.assertTrue(all(doc['retrieved_for'] for doc in docs))

    def test_developer_examples_and_localized_copies_do_not_win_policy_search(self):
        for url in ('https://project.example/docs/rpc/getSupply', 'https://project.example/docs/tokens/quickstart.md',
                    'https://project.example/podcasts/investing-tokenomics', 'https://project.example/ru/tokenomics'):
            self.assertEqual(discovery_score(url, 'Token supply and vesting', 'supply_disclosed'), 0)
        self.assertEqual(discovery_score('https://project.example/investing', '', 'vesting_disclosed'), 0)

    def test_markdown_preserves_report_discovery_and_github_uses_real_file(self):
        fetcher = Mock()
        fetcher.get.return_value = {'url': 'https://raw.githubusercontent.com/org/repo/main/README.md',
            'body': b'Published security audits of the project and their full reports.\n[Runtime audit](./audit.pdf)',
            'kind': 'text/plain', 'received_at': '2026-10-18'}
        doc = read_document(fetcher, 'https://github.com/org/repo/blob/main/README.md')
        self.assertEqual(fetcher.get.call_args.args[0], 'https://raw.githubusercontent.com/org/repo/main/README.md')
        self.assertEqual(doc['links'][0][0], 'https://raw.githubusercontent.com/org/repo/main/audit.pdf')


class OpenResearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.criteria = ProjectAssistant(models=False).criteria

    def setUp(self):
        self.profile = {'name': 'Example', 'symbol': 'EXM', 'network': 'Example mainnet', 'contract': '',
                        'facts': {k: evidence(None, 'unknown', '') for k in self.criteria},
                        'market': {'as_of': datetime.now(timezone.utc).date().isoformat(), 'market_cap_usd': 123},
                        'description': 'Profile'}
        self.research = OpenResearch(self.criteria, fetcher=Mock())

    def test_only_exact_relevant_citations_can_add_model_facts(self):
        text = 'The native token EXM is used to pay transaction fees. Supply is fixed at 100 million coins.'
        sources = [{'source_id': '0', 'url': 'https://example.org/docs', 'passages': text}]
        facts = [
            {'id': 'token_role_defined', 'value': True, 'source_id': '0', 'quote': 'The native token EXM is used to pay transaction fees.'},
            {'id': 'emission_disclosed', 'value': True, 'source_id': '0', 'quote': 'We burn 50 percent of fees.'},
            {'id': 'audit_available', 'value': True, 'source_id': '404', 'quote': text},
            {'id': 'made_up', 'value': True, 'source_id': '0', 'quote': text},
            {'id': 'product_exists', 'value': 'true', 'source_id': '0', 'quote': text},
            {'id': 'supply_disclosed', 'value': False, 'source_id': '0', 'quote': 'Supply is fixed at 100 million coins.'},
        ]
        self.assertEqual(apply_quotes(self.profile, json.dumps({'facts': facts}), sources), 1)
        self.assertTrue(self.profile['facts']['token_role_defined']['value'])
        for key in ('emission_disclosed', 'audit_available', 'supply_disclosed'):
            self.assertIsNone(self.profile['facts'][key]['value'])

    def test_thin_or_marketing_quotes_do_not_establish_identity_supply_or_users(self):
        cases = [('chain_asset_verified', 'The protocol is analyzed on a 1 gbps network, and this paper introduces it.'),
                 ('supply_disclosed', 'Issuances from a global, protocol-defined, inflation rate.'),
                 ('users_active', 'The number one platform with the most users and the highest TPS.'),
                 ('users_active', 'Example genesis supply has been distributed to past users in an airdrop.'),
                 ('holder_rights_clear', 'As an Example mainnet user or EXM holder, is there anything I need to do?'),
                 ('holder_rights_clear', 'A decentralized autonomous organization gives its members the right to spend funds and modify code.')]
        for key, text in cases:
            with self.subTest(key=key):
                sources = [{'source_id': '0', 'url': 'https://example.org/docs', 'passages': text}]
                self.assertEqual(apply_quotes(self.profile, json.dumps({'facts': [{'id': key, 'value': True, 'source_id': '0', 'quote': text}]}), sources), 0)

    def test_model_cannot_replace_known_negative_or_infer_depth_from_volume(self):
        self.profile['facts']['code_public']['value'] = False
        text = 'The code repository is public; reported trading volume is a billion dollars.'
        sources = [{'source_id': '0', 'url': 'https://example.org/', 'passages': text}]
        facts = [{'id': key, 'value': True, 'source_id': '0', 'quote': text} for key in ('code_public', 'liquidity_known')]
        self.assertEqual(apply_quotes(self.profile, json.dumps({'facts': facts}), sources), 0)
        self.assertFalse(self.profile['facts']['code_public']['value'])

    def test_explicit_project_descriptions_add_useful_facts_without_guessing_from_questions(self):
        docs = [{'url': 'https://example.org/docs', 'primary': True,
                 'text': 'Example is a decentralized network for electronic payments.\nEXM is used to pay transaction fees.'},
                {'url': 'https://other.example/', 'primary': False,
                 'text': 'Example token EXM has a perpetual inflation rate of 20%.'},
                {'url': 'https://example.org/whitepaper/', 'primary': True,
                 'text': 'Example inflation rate would be 30% in a hypothetical design.'}]
        self.research.documented_facts(self.profile, docs)
        for key in ('problem_defined', 'token_role_defined', 'token_value_link'):
            self.assertTrue(self.profile['facts'][key]['value'])
            self.assertEqual(self.profile['facts'][key]['source'], 'https://example.org/docs')
        self.assertIsNone(self.profile['facts']['emission_disclosed']['value'])

    def test_historical_whitepaper_does_not_establish_current_emissions(self):
        text = 'The linear issuance provides a permanent inflation rate of 16.5% per year.'
        sources = [{'source_id': '0', 'url': 'https://example.org/whitepaper/', 'passages': text}]
        payload = json.dumps({'facts': [{'id': 'emission_disclosed', 'value': True, 'source_id': '0', 'quote': text}]})
        self.assertEqual(apply_quotes(self.profile, payload, sources), 0)

    def test_historical_unlock_and_generic_account_instructions_are_not_current_token_facts(self):
        self.assertEqual(source_year({'text': 'FinanceJune 18, 2020byExample Foundation'}), 2020)
        text = 'EXM investors unlock 100 tokens in January 2021 under the vesting schedule.'
        sources = [{'source_id': '0', 'url': 'https://example.org/transparency', 'passages': text, 'source_year': 2020}]
        payload = json.dumps({'facts': [{'id': 'unlock_schedule_known', 'value': True, 'source_id': '0', 'quote': text}]})
        self.assertEqual(apply_quotes(self.profile, payload, sources), 0)
        sources[0].pop('source_year')
        self.assertEqual(apply_quotes(self.profile, payload, sources), 0)
        self.research.documented_facts(self.profile, [{'url': 'https://example.org/docs', 'primary': True,
            'text': 'The location in Example that can be used to find an account.'}])
        self.assertIsNone(self.profile['facts']['token_role_defined']['value'])

    def test_wrapped_primary_fee_and_native_asset_statements_are_recognized(self):
        doc = {'url': 'https://example.org/fees', 'primary': True, 'retrieved_for': 'revenue_disclosed',
               'text': 'The native currency of Example is EXM.\nThe base fee is split: 50% burned / 50% to the validator.'}
        self.research.documented_facts(self.profile, [doc])
        self.assertTrue(self.profile['facts']['chain_asset_verified']['value'])
        self.assertTrue(self.profile['facts']['revenue_disclosed']['value'])

    def test_headings_historical_votes_and_eip_numbers_are_not_evidence_of_users_or_emission_rate(self):
        for key, quote in [('users_active', 'Transactions | Example documentation. Transactions on this page.'),
                           ('supply_disclosed', 'Example Foundation Permanently Removes 11.365M From Token Supply.'),
                           ('emission_disclosed', 'Thanks to EIP-1559, EXM burn rates can surpass issuance.'),
                           ('holder_rights_clear', 'Any EXM holder was able to vote via a transaction in 2016.')]:
            sources = [{'source_id': '0', 'url': 'https://example.org/docs', 'passages': quote}]
            payload = json.dumps({'facts': [{'id': key, 'value': True, 'source_id': '0', 'quote': quote}]})
            self.assertEqual(apply_quotes(self.profile, payload, sources), 0)

    def test_labeled_address_distribution_explains_custody_and_deposit_contract_limitations(self):
        rows = ''.join(f'<tr><td>{i}</td><td><a href="/address/0x{i:040x}">short</a></td><td>{label}</td><td>100 ETH</td><td>1.2%</td><td>12</td></tr>'
                       for i, label in enumerate(['Beacon Deposit Contract', 'Wrapped Ether', 'Exchange 1', 'Exchange 2', ''], 1))
        self.research.fetcher.get.return_value = {'body': ('<table>' + rows + '</table>').encode(), 'received_at': '2026-10-18T18:00:00Z'}
        _, facts, _ = self.research.concentration({'id': 'ethereum'})
        self.assertIn('не конечных владельцев', facts['concentration_known']['evidence'])
        self.assertIn('накопленные депозиты', facts['concentration_known']['evidence'])
        self.assertEqual(self.research.concentration({'id': 'wrapped-ether'})[1], {})
        self.research.fetcher.get.return_value['body'] = b'<html>Bot challenge</html>'
        self.assertEqual(self.research.concentration({'id': 'ethereum'})[1], {})

    def test_actual_pdf_audit_includes_auditor_date_scope_and_version_limit(self):
        doc = {'url': 'https://auditor.example/reports/example.pdf', 'primary': True, 'format': 'pdf',
               'text': 'Example protocol security assessment by Halborn, 2025.\nThe scope of the code review is the Example core runtime at commit abc123.'}
        self.research.documented_facts(self.profile, [doc])
        fact = self.profile['facts']['audit_available']
        self.assertTrue(fact['value'])
        self.assertIn('2025', fact['evidence'])
        self.assertIn('commit abc123', fact['evidence'])
        self.assertIn('не подтверждает аудит всей сети', fact['evidence'])

    def test_incident_and_adjacent_resolution_are_cited_from_the_same_document(self):
        doc = {'url': 'https://example.org/governance', 'primary': True,
               'text': 'The Example network had an outage in 2024 that halted block confirmations.\nThe developers fixed the bug and restarted the network after four hours.\n' + 'Background context. ' * 40}
        self.research.documented_facts(self.profile, [doc])
        fact = self.profile['facts']['incident_history_known']
        self.assertTrue(fact['value'])
        self.assertIn('restarted the network', fact['evidence'])

    def test_a_model_batch_cannot_fill_unasked_criteria(self):
        text = 'The native token EXM is used to pay transaction fees.'
        payload = json.dumps({'facts': [{'id': 'token_role_defined', 'value': True, 'source_id': '0', 'quote': text}]})
        sources = [{'source_id': '0', 'url': 'https://example.org/docs', 'passages': text}]
        self.assertEqual(apply_quotes(self.profile, payload, sources, allowed={'product_exists'}), 0)

    def test_living_governance_process_is_bounded_and_draft_is_not_current(self):
        doc = {'url': 'https://raw.githubusercontent.com/project/proposals/main/process.md', 'primary': True,
               'targeted_criteria': ['admin_controls_known'],
               'text': 'title: Example Proposal Process\nstatus: Draft\ncreated: 2022-10-18\nOnce consensus is met by the core contributors, the proposal can either be accepted or withdrawn.'}
        self.research.documented_facts(self.profile, [doc])
        self.assertIsNone(self.profile['facts']['admin_controls_known']['value'])
        doc['text'] = doc['text'].replace('status: Draft', 'status: Living')
        self.research.documented_facts(self.profile, [doc])
        self.assertTrue(self.profile['facts']['admin_controls_known']['value'])
        self.assertIn('не доказательство отсутствия', self.profile['facts']['admin_controls_known']['evidence'])

    def test_targeted_postmortem_requires_completed_recovery_beyond_initial_context(self):
        doc = {'url': 'https://example.org/news/outage-report', 'primary': True,
               'targeted_criteria': ['incident_history_known'],
               'text': 'On 2024-02-06 Example block finalization halted.\n' + 'Background details of the investigation. ' * 35}
        self.research.documented_facts(self.profile, [doc])
        self.assertIsNone(self.profile['facts']['incident_history_known']['value'])
        doc['text'] += '\nConsensus progress resumed again at 14:55 UTC, resulting in a total incident duration of five hours.'
        self.research.documented_facts(self.profile, [doc])
        self.assertTrue(self.profile['facts']['incident_history_known']['value'])
        self.assertIn('resumed again', self.profile['facts']['incident_history_known']['evidence'])

    def test_local_model_uses_only_public_documents_and_requires_no_key(self):
        doc = {'url': 'https://example.org/docs', 'text': 'The native token EXM is used to pay transaction fees.', 'primary': True}
        response = {'choices': [{'message': {'content': json.dumps({'facts': [{'id': 'token_role_defined', 'value': True,
            'source_id': '0', 'quote': doc['text']}]})}}]}
        with patch('project_assistant.open_research.LocalAI') as ai, patch('project_assistant.open_research.load_config', return_value={'enabled': True}):
            ai.return_value.check.return_value = {'model': 'qwen-local'}
            ai.return_value.request.return_value = response
            count, _ = self.research.select_facts(self.profile, [doc])
        self.assertEqual(count, 1)
        payload = ai.return_value.request.call_args.args[1]
        self.assertEqual(payload['model'], 'qwen-local')
        self.assertEqual(payload['chat_template_kwargs']['enable_thinking'], False)
        self.assertNotIn('Authorization', json.dumps(payload))

    def test_model_failure_is_reported_without_fabricating_answers(self):
        with patch('project_assistant.open_research.LocalAI') as ai, patch('project_assistant.open_research.load_config', return_value={}):
            ai.return_value.check.side_effect = LocalAIError('not ready')
            count, note = self.research.select_facts(self.profile, [{'url': 'https://example.org', 'text': 'A useful native token for network fees and payments.', 'primary': True}])
        self.assertEqual(count, 0)
        self.assertIn('недоступны', note)
        self.assertTrue(all(f['value'] is None for f in self.profile['facts'].values()))

    def test_liquidity_uses_actual_usd_depth_and_rejects_old_or_auction_books(self):
        data = {'id': 'example', 'tickers': [{'coin_id': 'example', 'base': 'EXM', 'target': 'USD', 'market': {'identifier': 'gdax'}}]}
        book = {'bids': [['99', '2', 3], ['90', '100', 1]], 'asks': [['101', '3', 2], ['110', '100', 1]],
                'time': datetime.now(timezone.utc).isoformat()}
        self.research.fetcher.json.return_value = book
        _, facts, _ = self.research.liquidity(data)
        self.assertTrue(facts['liquidity_known']['value'])
        self.assertIn('198.00 USD', facts['liquidity_known']['evidence'])
        self.assertIn('303.00 USD', facts['liquidity_known']['evidence'])
        for change in ({'auction_mode': True}, {'time': '2009-01-03T00:00:00Z'}):
            self.research.fetcher.json.return_value = {**book, **change}
            with self.assertRaises(SourceError):
                self.research.liquidity(data)
        data['tickers'][0]['coin_id'] = 'different-asset'
        self.assertEqual(self.research.liquidity(data)[1], {})

    def test_github_legitimate_rename_is_verified_against_final_repository_id(self):
        meta = {'id': 123, 'full_name': 'Example/current', 'private': False, 'size': 100, 'archived': False}
        self.research.fetcher.get.return_value = {'url': 'https://api.github.com/repositories/123', 'body': json.dumps(meta).encode()}
        self.research.fetcher.json.return_value = {}
        _, facts, _ = self.research.github({'links': {'repos_url': {'github': ['https://github.com/Example/old']}}})
        self.assertTrue(facts['code_public']['value'])
        self.assertEqual(facts['code_public']['source'], 'https://api.github.com/repos/Example/current')
        self.research.fetcher.get.return_value['url'] = 'https://unrelated.example/repo'
        with self.assertRaisesRegex(SourceError, 'другой репозиторий'):
            self.research.github({'links': {'repos_url': {'github': ['https://github.com/Example/old']}}})

    def test_defi_version_requires_parent_symbol_and_official_domain(self):
        entry = {'name': 'Uniswap V3', 'slug': 'uniswap-v3', 'parentProtocol': 'parent#uniswap',
                 'symbol': 'UNI', 'url': 'https://app.uniswap.org/', 'gecko_id': None, 'tvl': 12345}
        data = {'id': 'uniswap', 'symbol': 'uni', 'links': {'homepage': ['https://uniswap.org/']}}
        self.research.fetcher.json.side_effect = [[entry], {}]
        _, facts, _ = self.research.defi(data)
        self.assertTrue(facts['usage_evidence']['value'])
        self.assertIn('не сумме всех версий', facts['usage_evidence']['evidence'])
        self.assertEqual(facts['usage_evidence']['source'], 'https://api.llama.fi/protocols')
        entry['url'] = 'https://fake-uniswap.example/'
        self.research.fetcher.json.side_effect = [[entry]]
        self.assertEqual(self.research.defi(data)[1], {})

    def test_all_criteria_receive_an_outcome_even_when_sources_and_model_fail(self):
        doc = {'url': 'https://example.org/docs', 'text': 'Token allocation and vesting schedule require further research.',
               'received_at': '2026-10-18', 'links': []}
        with patch('project_assistant.open_research.collect_documents', return_value=([doc], [])), \
                patch.object(self.research, 'github', side_effect=SourceError('unavailable')), \
                patch.object(self.research, 'liquidity', return_value=([], {}, [])), \
                patch.object(self.research, 'defi', return_value=([], {}, [])), \
                patch.object(self.research, 'peers', return_value=([], {}, [])), \
                patch.object(self.research, 'select_facts', return_value=(0, 'Qwen unavailable')):
            result = self.research.run(self.profile, {'id': 'example'})
        self.assertEqual(result['reviewed'], 26)
        self.assertEqual(len(result['sources']), 1)
        self.assertTrue(all(f['value'] is None for f in self.profile['facts'].values()))
        self.assertIn('Связанный фрагмент', self.profile['facts']['vesting_disclosed']['evidence'])
        self.assertTrue(all('Проверены открытые источники' in f['evidence'] for f in self.profile['facts'].values()))
        self.assertIn('Qwen unavailable', result['notices'])
        self.assertEqual(set(result['criteria']), set(self.criteria))
        self.assertTrue(all(c['status'] == 'insufficient_evidence' for c in result['criteria'].values()))

    def test_okx_fallback_uses_exact_coin_market_and_keeps_stablecoin_units(self):
        data = {'id': 'new-token', 'tickers': [
            {'coin_id': 'new-token', 'base': 'NEW', 'target': 'USD', 'market': {'identifier': 'gdax'}},
            {'coin_id': 'new-token', 'base': 'NEW', 'target': 'USDT', 'market': {'identifier': 'okex'}}]}
        book = {'code': '0', 'data': [{'ts': str(int(datetime.now(timezone.utc).timestamp()*1000)), 'bids': [['99', '2', '0', '1']], 'asks': [['101', '3', '0', '1']]}]}
        self.research.fetcher.json.side_effect = [SourceError('Coinbase unavailable'), book]
        _, facts, notes = self.research.liquidity(data)
        self.assertIn('198.00 USDT', facts['liquidity_known']['evidence'])
        self.assertIn('не в USD', facts['liquidity_known']['evidence'])
        self.assertIn('instId=NEW-USDT', facts['liquidity_known']['source'])
        self.assertIn('Coinbase unavailable', notes)
        data['tickers'] = [{**data['tickers'][1], 'coin_id': 'impostor'}]
        self.assertEqual(self.research.liquidity(data)[1], {})

    def test_repository_tree_discovers_reports_without_asset_specific_urls(self):
        self.research.fetcher.get.return_value = {'url': 'https://api.github.com/repos/project/core', 'body': json.dumps({
            'full_name': 'project/core', 'private': False, 'size': 100, 'default_branch': 'main'}).encode()}
        self.research.fetcher.json.side_effect = [
            {'encoding': 'base64', 'content': base64.b64encode(b'[Security](./security/audits.md)').decode(), 'html_url': 'https://github.com/project/core/blob/main/README.md'},
            SourceError('No releases'), {'tree': [{'type': 'blob', 'path': 'security/reports/new-audit.pdf'}, {'type': 'blob', 'path': 'images/audit.png'}]}]
        docs, facts, _ = self.research.github({'links': {'repos_url': {'github': ['https://github.com/project/core']}}})
        self.assertTrue(facts['code_public']['value'])
        links = [u for d in docs for u, _ in d['links']]
        self.assertIn('https://github.com/project/core/blob/main/security/reports/new-audit.pdf', links)
        self.assertIn('https://raw.githubusercontent.com/project/core/main/security/audits.md', links)
        self.assertFalse(any('audit.png' in u for u in links))

    def test_repository_public_html_is_a_bounded_fallback_to_api_limit(self):
        self.research.fetcher.get.side_effect = SourceError('HTTP 403')
        doc = {'url': 'https://github.com/project/core', 'text': 'GitHub - project/core: Example source code',
               'links': [('https://github.com/project/core/tree/main/contracts', 'contracts')], 'received_at': '2026-10-18'}
        with patch('project_assistant.open_research.read_document', return_value=doc):
            _, facts, notes = self.research.github({'links': {'repos_url': {'github': ['https://github.com/project/core']}}})
        self.assertTrue(facts['code_public']['value'])
        self.assertNotIn('roadmap_delivery', facts)
        self.assertIn('GitHub API недоступен', notes[0])
        with patch('project_assistant.open_research.read_document', return_value={**doc, 'url': 'https://unrelated.example/project/core'}), self.assertRaises(SourceError):
            self.research.github({'links': {'repos_url': {'github': ['https://github.com/project/core']}}})

    def test_public_release_needs_exact_repository_one_timestamp_and_a_past_date(self):
        response = {'url': 'https://github.com/project/core/releases/tag/v1.2.3',
                    'body': b'<h1>Release v1.2.3</h1><relative-time datetime="2024-10-12T16:56:55Z"></relative-time>'}
        self.research.fetcher.get.return_value = response
        _, fact = self.research.github_public_release('project/core')
        self.assertTrue(fact['value'])
        self.assertEqual(fact['date'], '2024-10-12')
        self.assertIn('не выполнение всего roadmap', fact['evidence'])
        for bad in ({**response, 'url': 'https://github.com/other/repo/releases/tag/v1'},
                    {**response, 'body': response['body'].replace(b'2024-', b'2099-')},
                    {**response, 'body': response['body'] + b'<relative-time datetime="2023-01-01T00:00:00Z">'}):
            self.research.fetcher.get.return_value = bad
            with self.assertRaises(SourceError):
                self.research.github_public_release('project/core')

    def test_comparison_uses_discovered_peer_names_for_new_projects(self):
        docs = [{'url': 'https://market.example', 'primary': False, 'peer_names': ['New Peer'], 'text': ''},
                {'url': 'https://project.example/comparison', 'primary': True, 'text': 'Unlike New Peer, Example uses different collateral rules in its lending design.'}]
        self.research.documented_facts(self.profile, docs)
        self.assertTrue(self.profile['facts']['competition_analyzed']['value'])

    def test_emission_and_staking_use_the_requested_ticker(self):
        text = 'The Example network issues 25 EXM per block according to its published emission policy.'
        sources = [{'source_id': '0', 'url': 'https://project.example/emission', 'passages': text}]
        content = json.dumps({'facts': [{'id': 'emission_disclosed', 'value': True, 'source_id': '0', 'quote': text}]})
        self.assertEqual(apply_quotes(self.profile, content, sources), 1)
        self.research.documented_facts(self.profile, [{'url': 'https://project.example/staking', 'primary': True,
                                                     'text': 'Validators stake EXM to secure the Example mainnet network.'}])
        self.assertTrue(self.profile['facts']['token_role_defined']['value'])

    def test_generic_governance_article_does_not_establish_asset_admin_powers(self):
        text = 'Governance tokens represent voting power, granting holders the right to influence decisions ranging from fee adjustments to full protocol upgrades.'
        sources = [{'source_id': '0', 'url': 'https://project.example/article/onchain-governance', 'passages': text}]
        content = json.dumps({'facts': [{'id': 'admin_controls_known', 'value': True, 'source_id': '0', 'quote': text}]})
        self.assertEqual(apply_quotes(self.profile, content, sources), 0)

    def test_peer_group_prefers_function_over_chain_location(self):
        data = {'id': 'example', 'categories': ['Base Native', 'Decentralized Finance (DeFi)', 'Oracle']}
        self.research.fetcher.json.side_effect = [[{'name': n, 'category_id': key} for n, key in
                                                  [('Base Native', 'base-native'), ('Decentralized Finance (DeFi)', 'defi'), ('Oracle', 'oracle')]],
                                                [{'id': 'peer', 'name': 'Peer', 'market_cap': 100, 'last_updated': self.profile['market']['as_of'] + 'T00:00:00Z'}]]
        docs, facts, _ = self.research.peers(data, self.profile)
        self.assertIn('category=oracle', docs[0]['url'])
        self.assertTrue(facts['valuation_comparable']['value'])

    def test_dated_operational_metrics_do_not_become_human_users_or_future_promises(self):
        year = datetime.now(timezone.utc).year - 1
        text = f'Through August {year}, the Example platform has securely enabled over $34 trillion in transaction value across 80+ blockchains.'
        doc = {'url': 'https://project.example/report', 'primary': True, 'text': text, 'targeted_criteria': ['usage_evidence', 'product_exists']}
        facts = scoped_observations(self.profile, doc)
        self.assertTrue(facts['usage_evidence']['value'])
        self.assertTrue(facts['product_exists']['value'])
        self.assertNotIn('users_active', facts)
        self.assertIn('не равен выручке', facts['usage_evidence']['evidence'])
        for bad in (text.replace('has securely enabled', 'could have enabled'), text.replace(str(year), str(year + 2)), text.replace('Example', 'Other'), text.replace('transaction value', 'token trading volume')):
            self.assertFalse(scoped_observations(self.profile, {**doc, 'text': bad}))

    def test_generic_token_utility_is_not_a_user_example_but_completed_adoption_is(self):
        generic = 'The EXM token is used by developers and organizations to pay fees to access Example services.'
        doc = {'url': 'https://project.example/utility', 'primary': True, 'text': generic}
        self.research.documented_facts(self.profile, [doc])
        self.assertIsNone(self.profile['facts']['users_active']['value'])
        sources = [{'source_id': '0', 'url': doc['url'], 'passages': generic}]
        content = json.dumps({'facts': [{'id': 'users_active', 'value': True, 'source_id': '0', 'quote': generic}]})
        self.assertEqual(apply_quotes(self.profile, content, sources), 0)
        doc['text'] = 'Lido, an established staking protocol, upgraded to Example as its official cross-chain infrastructure.'
        self.research.documented_facts(self.profile, [doc])
        self.assertTrue(self.profile['facts']['users_active']['value'])

    def test_protocol_encoding_and_gas_accounting_are_not_real_user_examples(self):
        for text in ('Developer utility tool to convert binary RLP dumps, the data encoding used by the Example protocol.',
                     'Gas used by a transaction on Example is exactly the same as the gas used by the same transaction on Ethereum.',
                     'This capability enables private chains to be integrated with the public Example platform.'):
            self.research.documented_facts(self.profile, [{'url': 'https://project.example/docs', 'primary': True, 'text': text}])
            self.assertIsNone(self.profile['facts']['users_active']['value'])

    def test_release_rate_is_not_mint_or_a_guessed_unlock_event(self):
        doc = {'url': 'https://project.example/supply', 'text': 'Example circulating supply.\nToken release schedule is currently 7% of the total supply per year.\nThe total supply is capped at 1,000,000,000 EXM.',
               'targeted_criteria': ['emission_disclosed']}
        facts = scoped_observations(self.profile, doc)
        self.assertEqual(list(facts), ['emission_disclosed'])
        self.assertIn('не означает mint', facts['emission_disclosed']['evidence'])
        self.assertFalse(scoped_observations(self.profile, {**doc, 'text': doc['text'].replace('EXM.', 'OTHER.')}))

    def test_staking_rights_and_upgrade_controls_include_exceptions_and_withdrawal_authority(self):
        text = ('Example Staking.\nYes, the Example Staking contracts are non-custodial, meaning stakers have complete control over their staked EXM position, which cannot be unstaked by any other entity.\n'
                'All security-critical configuration changes and upgrades to Example Staking must pass through a timelock smart contract with a delay of several weeks.\n'
                'Only emergency functions designed to protect stakers can be called without a time delay.\n'
                'Unstaking EXM can only be initiated using the original private key used to stake.')
        doc = {'url': 'https://project.example/staking', 'text': text, 'targeted_criteria': ['holder_rights_clear', 'admin_controls_known']}
        facts = scoped_observations(self.profile, doc)
        self.assertTrue(facts['holder_rights_clear']['value'])
        self.assertTrue(facts['admin_controls_known']['value'])
        self.assertIn('Only emergency', facts['admin_controls_known']['evidence'])
        self.assertIn('Состав подписантов', facts['admin_controls_known']['evidence'])
        self.assertNotIn('admin_controls_known', scoped_observations(self.profile, {**doc, 'text': text.split('Only emergency')[0]}))

    def test_targeted_historical_incident_can_have_publication_date_separate_from_cause(self):
        doc = {'url': 'https://project.example/incident', 'text': 'Example incident report\nFebruary 21, 2020\nWe identified a pricing anomaly on one of our oracle networks for the XAG/USD price pair.\nThe price pair was updated to broadcast the correct information.',
               'targeted_criteria': ['incident_history_known']}
        facts = scoped_observations(self.profile, doc)
        self.assertTrue(facts['incident_history_known']['value'])
        self.assertIn('2020', facts['incident_history_known']['evidence'])
        for change in ('The price pair will be updated to broadcast the correct information.', 'An updated tutorial is available for operators.'):
            self.assertNotIn('incident_history_known', scoped_observations(self.profile, {**doc, 'text': doc['text'].rsplit('\n', 1)[0] + '\n' + change}))


if __name__ == '__main__':
    unittest.main()
