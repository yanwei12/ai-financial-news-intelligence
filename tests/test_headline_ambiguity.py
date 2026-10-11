import json

import pytest

from src.ai import headline
from tests.test_headline import article, output


def ambiguous_output():
    data=json.loads(output())
    data['claims'][0].update(verdict='misleading',gap_type='ambiguity',
                            explanation='時間修飾對象有歧義，正文只支持年資讀法。')
    return data


def test_ambiguity_is_distinct_from_supported(article):
    result=headline.validate_result(json.dumps(ambiguous_output()),article)
    assert result['verdict']=='misleading'
    assert result['claims'][0]['evidence'][0]['text']==article.paragraphs[0].text


@pytest.mark.parametrize('mutation',['missing_evidence','incorrect_gap','supported_ambiguity'])
def test_ambiguity_requires_evidence_and_consistent_classification(article,mutation):
    data=ambiguous_output();claim=data['claims'][0]
    if mutation=='missing_evidence':claim['evidence_ids']=[]
    if mutation=='incorrect_gap':claim['gap_type']='scope'
    if mutation=='supported_ambiguity':claim['verdict']='supported'
    with pytest.raises(headline.AnalysisError):headline.validate_result(json.dumps(data),article)


def test_one_supported_claim_cannot_hide_ambiguity(article):
    data=ambiguous_output();data['claims'].append(json.loads(output('supported'))['claims'][0])
    assert headline.validate_result(json.dumps(data),article)['verdict']=='misleading'
    data['claims'].append(json.loads(output('contradicted'))['claims'][0])
    assert headline.validate_result(json.dumps(data),article)['verdict']=='contradicted'
