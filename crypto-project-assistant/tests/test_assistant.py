import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from project_assistant.assistant import ProjectAssistant, markdown_report
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

    def test_invalid_input_origin_and_paths(self):
        for path,body,headers,expected in [('/api/analyze',b'{"facts":{"code_public":1}}',{'Content-Type':'application/json'},400),
                                         ('/api/analyze',b'{}',{'Content-Type':'application/json','Origin':'https://example.org'},403),
                                         ('/../README.md',None,{},404)]:
            with self.subTest(path=path),self.assertRaises(HTTPError) as error:urlopen(Request(self.url+path,data=body,headers=headers),timeout=5)
            self.assertEqual(error.exception.code,expected)


if __name__=='__main__':unittest.main()
