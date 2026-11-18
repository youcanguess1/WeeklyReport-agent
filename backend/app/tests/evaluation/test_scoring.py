from __future__ import annotations

from app.evaluation.scoring import canonical_dataset_hash, parse_judge_json, score_report


def _case():
    return {
        "report_period": "2026-10-19 至 2026-10-23",
        "required_sections": ["本周完成", "进行中", "风险与阻塞", "下周计划", "待确认事项"],
        "required_facts": [{"text": "commit 8ab21cd"}, {"text": "进度60%"}],
        "forbidden_claims": ["运维已介入"],
        "expected_classification": {"commit 8ab21cd": ["本周完成"], "进度60%": ["进行中"]},
        "pending_items": ["短信沙箱开通时间"],
        "revision_instruction": "请将进度改为60%",
        "revision_check": {"must_include": ["60%"], "must_exclude": ["80%"]},
    }


def test_scores_five_sections_period_facts_status_revision_and_forbidden_claims():
    report = """报告周期：2026-10-19 至 2026-10-23
## 本周完成
commit 8ab21cd 已合入。
## 进行中
进度60%。
## 风险与阻塞
短信沙箱仍未开通。
## 下周计划
待短信沙箱开通后验证。
## 待确认事项
短信沙箱开通时间待确认。
"""
    scored = score_report(_case(), report)
    assert scored["section_completeness"] == 1
    assert scored["period_accuracy"] is True
    assert scored["critical_fact_recall"] == 1
    assert scored["status_classification_accuracy"] == 1
    assert scored["forbidden_claim_hits"] == []
    assert scored["revision_success"] is True
    assert scored["items_to_confirm_coverage"] == 1


def test_status_in_wrong_section_is_not_counted():
    report = "## 本周完成\n进度60%。\ncommit 8ab21cd 已合入。\n## 进行中\n\n## 风险与阻塞\n\n## 下周计划\n\n## 待确认事项\n"
    scored = score_report(_case(), report)
    assert scored["status_classification_accuracy"] == 0.5


def test_section_parser_accepts_markdown_and_chinese_numbered_titles():
    report = """**一、本周完成**
commit 8ab21cd 已合入。
### 2、进行中
进度60%。
## （三）风险与阻塞
短信沙箱仍未开通。
#### 四. 下周计划
待短信沙箱开通后验证。
## 五、待确认事项
短信沙箱开通时间待确认。
"""
    scored = score_report(_case(), report)
    assert scored["section_completeness"] == 1
    assert scored["status_classification_accuracy"] == 1
    assert scored["items_to_confirm_coverage"] == 1


def test_classification_anchors_allow_fact_preserving_paraphrases():
    case = _case()
    case["classification_anchors"] = {
        "commit 8ab21cd": [["8ab21cd", "合入"]],
        "进度60%": [["60%"]],
    }
    report = """## 本周完成
变更已合入，commit 8ab21cd。
## 进行中
当前进度约60%。
"""
    scored = score_report(case, report)
    assert scored["status_classification_accuracy"] == 1


def test_pending_item_anchors_allow_equivalent_wording():
    case = _case()
    case["pending_anchors"] = {"短信沙箱开通时间": [["短信沙箱", "何时"]]}
    report = "## 待确认事项\n需确认短信沙箱预计何时开通。"
    assert score_report(case, report)["items_to_confirm_coverage"] == 1


def test_candidate_pending_coverage_recognizes_test_02_paraphrase_without_changing_baseline():
    case = _case()
    case["id"] = "TEST-02"
    case["pending_items"] = ["样本编码补录规则"]
    report = "## 待确认事项\n样本编码是否允许补录需要实验室确认。"

    baseline = score_report(case, report)
    candidate = score_report(case, report, include_candidate_pending_coverage=True)["candidate_pending_coverage"]

    assert baseline["items_to_confirm_coverage"] == 0
    assert candidate["coverage"] == 1
    assert candidate["items"] == [{
        "item": "样本编码补录规则",
        "covered": True,
        "match_type": "candidate_anchor",
        "matched_anchors": ["样本编码", "补录", "确认"],
        "unmatched_candidate_patterns": [],
        "reason": None,
    }]


def test_candidate_pending_coverage_rejects_incomplete_or_unrelated_terms():
    case = _case()
    case["id"] = "TEST-02"
    case["pending_items"] = ["样本编码补录规则"]
    reports = [
        "## 待确认事项\n样本编码仍在登记中，补录尚未开始。",
        "## 待确认事项\n需要实验室确认相关规则。",
        "## 下周计划\n样本编码是否允许补录需要实验室确认。\n## 待确认事项\n暂无。",
    ]

    for report in reports:
        candidate = score_report(case, report, include_candidate_pending_coverage=True)["candidate_pending_coverage"]
        assert candidate["coverage"] == 0
        assert candidate["items"][0]["match_type"] == "none"


def test_candidate_pending_coverage_keeps_exact_match_type():
    case = _case()
    case["id"] = "TEST-02"
    case["pending_items"] = ["样本编码补录规则"]
    report = "## 待确认事项\n样本编码补录规则待实验室确认。"

    scored = score_report(case, report, include_candidate_pending_coverage=True)

    assert scored["items_to_confirm_coverage"] == 1
    assert scored["candidate_pending_coverage"]["coverage"] == 1
    assert scored["candidate_pending_coverage"]["items"][0]["match_type"] == "exact_item"


def test_forbidden_claim_is_reported():
    scored = score_report(_case(), "## 风险与阻塞\n运维已介入。")
    assert scored["forbidden_claim_hits"] == ["运维已介入"]


def test_malformed_judge_json_is_safe():
    result = parse_judge_json("{broken")
    assert result["valid"] is False
    assert "Malformed Judge JSON" in result["error"]


def test_judge_json_fence_and_score_validation():
    assert parse_judge_json('```json\n{"groundedness":4,"completeness":3}\n```')["valid"] is True
    assert parse_judge_json('{"groundedness":7,"completeness":3}')["valid"] is False


def test_dataset_hash_is_repeatable_and_sensitive(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text('[{"id":"A"}]', encoding="utf-8")
    first = canonical_dataset_hash(path)
    assert first == canonical_dataset_hash(path)
    path.write_text('[{"id":"B"}]', encoding="utf-8")
    assert first != canonical_dataset_hash(path)


def test_dataset_hash_includes_attachment_bytes(tmp_path):
    (tmp_path / "assets").mkdir()
    image = tmp_path / "assets" / "board.png"
    image.write_bytes(b"image-a")
    dataset = tmp_path / "cases.json"
    dataset.write_text('[{"id":"A","attachment":"assets/board.png"}]', encoding="utf-8")
    first = canonical_dataset_hash(dataset)
    image.write_bytes(b"image-b")
    assert first != canonical_dataset_hash(dataset)
