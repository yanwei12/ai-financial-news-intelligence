"""No model downloads or news database access required."""
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.api.paragraphs import router
from src.processing.cleaner import prepare_article, resolve_evidence

class ParagraphTests(unittest.TestCase):
    def test_original_numbers_and_wrapping(self):
        raw = '  營收 3.5%，U.S. 業務。\r\n「尚未確定！」\r\n \t\r\n只限部分產品。  '
        a = prepare_article('獨立標題', raw)
        self.assertEqual(a.original_text, raw)
        self.assertEqual([p.text for p in a.paragraphs], ['營收 3.5%，U.S. 業務。\n「尚未確定！」', '只限部分產品。'])
        self.assertEqual([p.id for p in a.paragraphs], ['P001','P002'])
    def test_punctuation_does_not_split(self):
        raw = '第一句。真的嗎？价格 3.5%，尚未定案！'
        a = prepare_article('標題', raw)
        self.assertEqual(len(a.paragraphs), 1)
        self.assertEqual(a.paragraphs[0].text, raw)
    def test_line_mode(self):
        a = prepare_article('標題', '\n甲\r乙\r\n\n丙\n', 'line_breaks')
        self.assertEqual([p.text for p in a.paragraphs], ['甲','乙','丙'])
    def test_version_rejects_changed_title_body_or_mode(self):
        a = prepare_article('標題', '甲\n\n乙')
        self.assertEqual(a, prepare_article('標題', '甲\n\n乙'))
        for b in [prepare_article('新標題', a.original_text), prepare_article(a.title, '甲\n\n丙'), prepare_article(a.title, a.original_text, 'line_breaks')]:
            with self.assertRaises(ValueError): resolve_evidence(b, a.document_id, ['P001'])
    def test_evidence(self):
        a = prepare_article('標題','原文甲\n\n原文乙')
        self.assertEqual([p.text for p in resolve_evidence(a,a.document_id,['P002','P002'])], ['原文乙'])
        self.assertEqual(resolve_evidence(a,a.document_id,[]), [])
        with self.assertRaises(ValueError): resolve_evidence(a,a.document_id,['P001','P999'])
    def test_invalid_inputs(self):
        for args in [(' ','正文','blank_lines'), ('標題','\n\t','blank_lines'), ('標題','正文','invalid')]:
            with self.assertRaises(ValueError): prepare_article(*args)
    def test_no_truncation(self):
        a = prepare_article('標題','\n\n'.join(str(i) for i in range(1001)))
        self.assertEqual(len(a.paragraphs),1001)
        self.assertEqual(a.paragraphs[-1].id,'P1001')

class APITests(unittest.TestCase):
    def setUp(self):
        app=FastAPI();app.include_router(router);self.client=TestClient(app)
    def test_roundtrip(self):
        raw='第一段。\r\n\r\n第二段。'
        r=self.client.post('/articles/prepare',json={'title':'標題','content':raw})
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['original_text'],raw)
        self.assertEqual(r.json()['paragraphs'],[{'id':'P001','text':'第一段。'},{'id':'P002','text':'第二段。'}])
        self.assertEqual(r.json()['document_id'],prepare_article('標題',raw).document_id)
    def test_validation(self):
        for payload in [{},{'title':'標題','content':' '},{'title':'標題','content':'字'*200001},{'title':'標題','content':'正文','paragraph_mode':'sentences'}]:
            self.assertEqual(self.client.post('/articles/prepare',json=payload).status_code,422)
    def test_plain_text_preserved(self):
        raw='<script>alert(1)</script>\n\n金額 < 100'
        r=self.client.post('/articles/prepare',json={'title':'標題','content':raw})
        self.assertEqual(r.json()['original_text'],raw)
        self.assertEqual(r.json()['paragraphs'][0]['text'],'<script>alert(1)</script>')
    def test_page(self):
        r=self.client.get('/paragraphs')
        self.assertEqual(r.status_code,200)
        self.assertIn('id="settings"',r.text)
        self.assertNotIn('產生段落預覽',r.text)
        self.assertIn('/static/paragraphs.js',r.text)

if __name__ == '__main__': unittest.main()
