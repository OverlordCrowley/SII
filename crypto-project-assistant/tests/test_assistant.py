import json
from datetime import date
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from project_assistant.assistant import ProjectAssistant, markdown_report, parse_profile
from project_assistant.knowledge import ROOT
from project_assistant.logic import evaluate, truth_table
from project_assistant.server import make_server
from project_assistant.tokenomics import calculate


class AssistantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assistant = ProjectAssistant()

    def example(self,name):
        return json.loads((ROOT/f'examples/{name}.json').read_text(encoding='utf-8'))

    def test_complete_crypto_profile(self):
        result=self.assistant.analyze(self.example('ready'))
        self.assertEqual(result['status'],'research')
        self.assertEqual((result['score']['min'],result['score']['max']),(100,100))
        proof=next(s['proof'] for s in result['strengths'] if s['id']=='research_ready')
        self.assertTrue(proof['children'])

    def test_tokenless_project_excludes_tokenomics(self):
        result=self.assistant.analyze({'facts':{'has_token':False}})
        self.assertFalse(next(c for c in result['criteria'] if c['id']=='vesting_disclosed')['applicable'])
        self.assertFalse(any(q['id']=='vesting_disclosed' for q in result['questions']))
        self.assertEqual(result['score']['total'],18)

    def test_tokenless_project_ignores_saved_token_facts_and_signals(self):
        profile = self.example('ready')
        profile['facts']['has_token'] = False
        profile['market'] = self.example('risky')['market']
        result = self.assistant.analyze(profile)
        conclusions = {item['id'] for item in result['strengths'] + result['findings']}
        self.assertFalse(conclusions & {'token_case', 'tokenomics_clear', 'fdv_gap', 'float_gap', 'unlock_size'})
        self.assertEqual(result['status'], 'research')
        self.assertTrue(result['project']['facts']['vesting_disclosed']['value'])
        self.assertTrue(any('не участвует в выводах' in warning for warning in result['warnings']))

    def test_minimal_or_proof_with_negative_fact(self):
        result=self.assistant.analyze({'facts':{'product_exists':False}})
        proof=next(f['proof'] for f in result['findings'] if f['id']=='product_gap')
        self.assertEqual([c['fact'] for c in proof['children']],['product_exists'])
        self.assertFalse(proof['children'][0]['value'])

    def test_unknown_is_not_negative(self):
        result=self.assistant.analyze(self.example('incomplete'))
        self.assertEqual(result['status'],'clarify')
        self.assertEqual(result['findings'],[])
        self.assertEqual(result['score'],{'min':0,'max':100,'coverage':0,'known':0,'total':26})
        self.assertEqual(result['questions'][0]['id'],'chain_asset_verified')
        self.assertEqual(len(result['questions']),18)
        self.assertIsNone(evaluate('NOT missing',{}))

    def test_text_conflict_and_override(self):
        description='Аудит опубликован. Аудит не опубликован.'
        result=self.assistant.analyze({'description':description})
        self.assertTrue(result['warnings'])
        self.assertNotIn('audit_available',result['project']['facts'])
        resolved=self.assistant.analyze({'description':description,'facts':{'audit_available':True}})
        self.assertEqual(resolved['warnings'],[])
        self.assertTrue(resolved['project']['facts']['audit_available']['value'])

    def test_explicit_negation_and_sources(self):
        result=self.assistant.analyze({'description':'У проекта есть токен. Vesting не раскрыт.'})
        self.assertTrue(result['project']['facts']['has_token']['value'])
        self.assertFalse(result['project']['facts']['vesting_disclosed']['value'])
        self.assertTrue(any(f['id']=='unlock_opacity' for f in result['findings']))
        self.assertEqual(result['project']['facts']['vesting_disclosed']['source'],'Распознано из описания')

    def test_profile_validation_and_report(self):
        for profile in ({'facts':{'code_public':1}},{'facts':{'code_public':'false'}},{'facts':{'unknown':True}},
                        {'type':'other'},{'facts':{'code_public':{'value':True,'date':'2026-02-31'}}}):
            with self.subTest(profile=profile),self.assertRaises(ValueError):self.assistant.analyze(profile)
        result=self.assistant.analyze({'name':'Тест | отчёта','facts':{'chain_asset_verified':{'value':False,'source':'Документация','date':'2026-10-04'}}})
        report=markdown_report(result)
        self.assertIn('R10',report)
        self.assertIn('Документация',report)
        self.assertIn('2026-10-04',report)
        self.assertIn('Тест \\| отчёта',report)

    def test_frames_and_graph(self):
        frames=self.assistant.frames
        self.assertEqual(frames.get('defi','quote_currency'),'USD')
        self.assertEqual(frames.describe('defi')['origins']['source_policy'],'crypto_project')
        original=frames.get('defi','focus')
        frames.set('defi','focus','Тестовое свойство')
        self.assertEqual(frames.find('focus','Тестовое свойство'),['defi'])
        frames.set('defi','focus',original)
        path=self.assistant.network.path('project','unlocks')
        self.assertEqual(len(path),4)
        table=truth_table('a AND (b OR NOT c)')
        self.assertEqual((len(table),sum(r['result'] for r in table)),(8,3))

    def test_numeric_findings_are_explained_as_calculations(self):
        result=self.assistant.analyze(self.example('risky'))
        self.assertEqual(result['status'],'blocked')
        self.assertEqual(result['tokenomics']['values']['fdv_to_cap'],10)
        finding=next(f for f in result['findings'] if f['id']=='unlock_size')
        self.assertEqual(finding['proof']['children'][0]['source'],'calculation')
        self.assertIn('расчёт по введённым числам',markdown_report(result))


class TokenomicsTests(unittest.TestCase):
    def test_formulas_and_basis(self):
        raw={'price_usd':2,'circulating_supply':10,'total_supply':50,'max_supply':100,'next_unlock_tokens':2,'as_of':'2026-10-04','unlock_date':'2026-10-20'}
        result=calculate(raw)
        self.assertEqual(result['values']['market_cap_usd'],20)
        self.assertEqual(result['values']['fdv_usd'],100)
        self.assertEqual(result['values']['circulating_share_pct'],20)
        self.assertEqual(result['values']['unlock_share_pct'],20)
        self.assertTrue(result['facts']['large_unlock'])
        self.assertEqual(calculate({**raw,'fdv_basis':'max'})['values']['fdv_usd'],200)

    def test_missing_zero_and_invalid_numbers(self):
        empty=calculate({})
        self.assertIsNone(empty['values']['fdv_to_cap'])
        self.assertEqual(empty['facts'],{})
        zero=calculate({'price_usd':0,'circulating_supply':0,'total_supply':0})
        self.assertEqual(zero['values']['market_cap_usd'],0)
        self.assertIsNone(zero['values']['fdv_to_cap'])
        self.assertIsNone(zero['values']['unlock_share_pct'])
        for raw in ({'price_usd':True},{'price_usd':float('nan')},{'price_usd':-1},{'quote_currency':'USDT'},
                    {'circulating_supply':20,'total_supply':10},{'total_supply':10,'max_supply':5}):
            with self.subTest(raw=raw),self.assertRaises(ValueError):calculate(raw)

    def test_conflicting_snapshot_and_past_unlock(self):
        result=calculate({'price_usd':1,'circulating_supply':10,'total_supply':100,'market_cap_usd':999,'fdv_usd':100,
                          'next_unlock_tokens':10,'as_of':'2026-10-04','unlock_date':'2026-10-03'})
        self.assertTrue(result['warnings'])
        self.assertIsNone(result['values']['fdv_to_cap'])
        self.assertNotIn('large_unlock',result['facts'])
        self.assertTrue(calculate({'price_usd':1,'as_of':'2000-01-01'})['warnings'])

    def test_fractional_snapshot_conflicts_and_one_percent_boundary(self):
        raw = {'price_usd': 0.01, 'circulating_supply': 10, 'total_supply': 100,
               'as_of': date.today().isoformat(), 'source': 'Regression test'}
        for field, value in (('market_cap_usd', 0.2), ('fdv_usd', 1.1)):
            with self.subTest(field=field):
                result = calculate({**raw, field: value})
                self.assertTrue(result['warnings'])
                self.assertIsNone(result['values']['fdv_to_cap'])
                self.assertNotIn('fdv_gap_high', result['facts'])
        for stated in (0.099, 0.1, 0.101):
            with self.subTest(stated=stated):
                result = calculate({**raw, 'market_cap_usd': stated})
                self.assertEqual(result['warnings'], [])
                self.assertIsNotNone(result['values']['fdv_to_cap'])
        zero = calculate({**raw, 'price_usd': 0, 'market_cap_usd': 0.001})
        self.assertTrue(zero['warnings'])

    def test_numbers_outside_range_raise_validation_errors(self):
        for value in (10**400, -(10**400), float('inf'), float('-inf'), float('nan')):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'price_usd'):
                calculate({'price_usd': value})

    def test_extreme_ratios_remain_unknown_and_json_serializable(self):
        result = calculate({'market_cap_usd': 1e-300, 'fdv_usd': 1e30,
                            'volume_24h_usd': 1e30, 'circulating_supply': 1e-300,
                            'next_unlock_tokens': 1e30, 'as_of': date.today().isoformat(),
                            'unlock_date': '9999-12-31', 'source': 'Regression test'})
        self.assertIsNone(result['values']['fdv_to_cap'])
        self.assertIsNone(result['values']['unlock_share_pct'])
        self.assertIsNone(result['values']['volume_to_cap_pct'])
        self.assertEqual(result['facts'], {})
        self.assertEqual(len(result['warnings']), 3)
        json.dumps(result, allow_nan=False)

    def test_underflow_does_not_turn_a_positive_capitalization_into_zero(self):
        result = calculate({'price_usd': 1e-200, 'circulating_supply': 1e-200,
                            'total_supply': 1e-100})
        self.assertIsNone(result['values']['market_cap_usd'])
        self.assertEqual(result['values']['fdv_usd'], 1e-300)
        self.assertTrue(any('вне точности' in warning for warning in result['warnings']))
        zero = calculate({'price_usd': 0, 'circulating_supply': 1e-200})
        self.assertEqual(zero['values']['market_cap_usd'], 0)

    def test_cli_reports_oversized_number_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / 'huge.json'
            profile.write_text(json.dumps({'market': {'price_usd': 10**400}}), encoding='utf-8')
            process = subprocess.run([sys.executable, '-m', 'project_assistant', 'analyze', str(profile)],
                                     cwd=ROOT, capture_output=True, encoding='utf-8', timeout=10)
        self.assertEqual(process.returncode, 2)
        self.assertIn('price_usd', process.stderr)
        self.assertNotIn('Traceback', process.stderr)

    def test_cli_reports_deep_json_without_traceback(self):
        valid = '[' * 64 + '0' + ']' * 64
        self.assertIsInstance(parse_profile(valid), list)
        quoted = {'description': '[{\\"' * 1000}
        self.assertEqual(parse_profile(json.dumps(quoted)), quoted)
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / 'nested.json'
            for depth in (65, 50000):
                with self.subTest(depth=depth):
                    profile.write_text('[' * depth + '0' + ']' * depth, encoding='utf-8')
                    process = subprocess.run([sys.executable, '-m', 'project_assistant', 'analyze', str(profile)],
                                             cwd=ROOT, capture_output=True, encoding='utf-8', timeout=10)
                    self.assertEqual(process.returncode, 2)
                    self.assertIn('слишком много вложенных', process.stderr)
                    self.assertNotIn('Traceback', process.stderr)


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=make_server(0)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.url=f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join(timeout=2)

    def test_html_and_analysis_api(self):
        with urlopen(self.url,timeout=5) as response:self.assertIn('Криптоскоп',response.read().decode('utf-8'))
        payload=json.dumps({'facts':{'chain_asset_verified':False}}).encode()
        with urlopen(Request(self.url+'/api/analyze',data=payload,headers={'Content-Type':'application/json'}),timeout=5) as response:result=json.load(response)
        self.assertEqual(result['status'],'blocked')
        self.assertIn('Сеть и актив не сверены',result['markdown'])

    def test_aliases_are_resolved_by_the_analysis_api(self):
        for profile in ({'name': 'btc'}, {'name': 'Биткоин'}, {'symbol': 'Bitcoin'}):
            with self.subTest(profile=profile):
                payload = json.dumps(profile).encode()
                with urlopen(Request(self.url+'/api/analyze', data=payload,
                                    headers={'Content-Type': 'application/json'}), timeout=5) as response:
                    result = json.load(response)
                self.assertEqual((result['project']['name'], result['project']['symbol']), ('Bitcoin', 'BTC'))

    def test_invalid_port_has_a_clear_cli_error(self):
        for port in (-1, 65536):
            with self.subTest(port=port):
                process = subprocess.run([sys.executable, '-m', 'project_assistant', 'serve',
                                          '--port', str(port), '--no-browser'],
                                         cwd=ROOT, capture_output=True, encoding='utf-8', timeout=10)
                self.assertEqual(process.returncode, 2)
                self.assertIn('Порт должен', process.stderr)
                self.assertNotIn('Traceback', process.stderr)

    def test_invalid_input_origin_and_paths(self):
        for path,body,headers,expected in [('/api/analyze',b'{"facts":{"code_public":1}}',{'Content-Type':'application/json'},400),
                                         ('/api/analyze',b'{}',{'Content-Type':'application/json','Origin':'https://example.org'},403),
                                         ('/../README.md',None,{},404)]:
            with self.subTest(path=path),self.assertRaises(HTTPError) as error:urlopen(Request(self.url+path,data=body,headers=headers),timeout=5)
            self.assertEqual(error.exception.code,expected)
            error.exception.close()

    def test_both_local_browser_addresses_can_analyze(self):
        for host in ('127.0.0.1', 'localhost'):
            with self.subTest(host=host):
                origin = f'http://{host}:{self.server.server_port}'
                with urlopen(Request(self.url+'/api/analyze', data=b'{}',
                                    headers={'Content-Type': 'application/json', 'Origin': origin}), timeout=5) as response:
                    self.assertEqual(json.load(response)['status'], 'clarify')

    def test_oversized_integer_returns_400_and_server_keeps_working(self):
        payload = json.dumps({'market': {'price_usd': 10**400}}).encode()
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(self.url+'/api/analyze', data=payload,
                            headers={'Content-Type': 'application/json'}), timeout=5)
        with error.exception as response:
            self.assertEqual(response.code, 400)
            self.assertIn('price_usd', json.load(response)['error'])
        with urlopen(Request(self.url+'/api/analyze', data=b'{}',
                            headers={'Content-Type': 'application/json'}), timeout=5) as response:
            self.assertEqual(json.load(response)['status'], 'clarify')

    def test_deep_json_returns_400_and_server_keeps_working(self):
        payloads = ['[' * 50000 + '0' + ']' * 50000,
                    '{"extra":' * 65 + '0' + '}' * 65]
        for payload in payloads:
            with self.subTest(size=len(payload)), self.assertRaises(HTTPError) as error:
                urlopen(Request(self.url+'/api/analyze', data=payload.encode(),
                                headers={'Content-Type': 'application/json'}), timeout=5)
            with error.exception as response:
                self.assertEqual(response.code, 400)
                self.assertIn('слишком много вложенных', json.load(response)['error'])
        with urlopen(Request(self.url+'/api/analyze', data=b'{}',
                            headers={'Content-Type': 'application/json'}), timeout=5) as response:
            self.assertEqual(json.load(response)['status'], 'clarify')

    def test_extreme_valid_numbers_return_a_finite_report(self):
        payload = json.dumps({'market': {'market_cap_usd': 1e-300, 'fdv_usd': 1e30}}).encode()
        with urlopen(Request(self.url+'/api/analyze', data=payload,
                            headers={'Content-Type': 'application/json'}), timeout=5) as response:
            result = json.load(response)
        self.assertIsNone(result['tokenomics']['values']['fdv_to_cap'])
        self.assertIn('вне точности', result['markdown'])
        self.assertNotIn('Infinity', result['markdown'])


if __name__=='__main__':unittest.main()
