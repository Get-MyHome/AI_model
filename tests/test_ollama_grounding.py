from __future__ import annotations

from get_myhome_ai.candidates import CandidatePage
from get_myhome_ai.models import (
    AdditionalCost,
    AdditionalCostPayment,
    AdditionalCostType,
    Evidence,
    LoanArrangementStatus,
    PaymentStage,
)
from get_myhome_ai.providers.ollama_grounding import ground_ollama_draft


def _page(number: int, text: str, *categories: str) -> CandidatePage:
    return CandidatePage(
        number=number,
        text=text,
        score=100,
        categories=frozenset(categories),
    )


def test_balcony_inclusion_never_propagates_to_system_aircon(golden_cases) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    draft.additional_costs.append(draft.additional_costs[0].model_copy(deep=True))
    draft.additional_costs.append(
        AdditionalCost(
            type=AdditionalCostType.SYSTEM_AIR_CONDITIONER,
            name="시스템 에어컨",
            total_amount_manwon=200,
            required=False,
            included_in_sale_price=False,
            applicable_unit_type="39A",
            payments=[
                AdditionalCostPayment(
                    number=1,
                    stage=PaymentStage.CONTRACT,
                    amount_manwon=20,
                    due_date=None,
                    due_text="계약 시",
                ),
                AdditionalCostPayment(
                    number=2,
                    stage=PaymentStage.BALANCE,
                    amount_manwon=180,
                    due_date=None,
                    due_text="입주지정일",
                ),
            ],
            note=None,
        )
    )
    pages = [
        _page(
            10,
            "공급가에는 발코니 확장공사비가 포함되어 있습니다.\n"
            "시스템 에어컨\n39A 2,000,000 200,000 1,800,000",
            "cost",
        )
    ]

    actual = ground_ollama_draft(
        draft,
        pages=pages,
        unit_type_name="39A",
        sale_price_manwon=103_500,
    )

    balconies = [item for item in actual.additional_costs if item.type == "BALCONY_EXTENSION"]
    assert len(balconies) == 1
    balcony = balconies[0]
    aircon = next(item for item in actual.additional_costs if item.type == "SYSTEM_AIR_CONDITIONER")
    assert balcony.included_in_sale_price is True
    assert balcony.required is True
    assert balcony.total_amount_manwon is None
    assert balcony.payments == []
    assert aircon.included_in_sale_price is None
    assert aircon.required is None


def test_verified_bank_is_not_erased_by_loan_split(golden_cases) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    draft.interim_loan.bank_names = ["KB국민은행"]
    quote = (
        "KB국민은행을 중도금 대출 취급은행으로 지정합니다. "
        "총 공급 대금의 60% 중 총 공급 대금의 40% 범위 내에서 "
        "대출 알선이 가능하며 나머지 총 공급 대금의 20%는 직접 납부(자납)하여야 합니다."
    )
    draft.evidence.append(Evidence(field="/interim_loan/bank_names", page=20, raw_text=quote))

    actual = ground_ollama_draft(
        draft,
        pages=[_page(20, quote, "loan")],
        unit_type_name="39A",
        sale_price_manwon=103_500,
    )

    assert actual.interim_loan.bank_names == ["KB국민은행"]
    assert actual.interim_loan.arrangement_status == LoanArrangementStatus.BANK_SELECTED


def test_unrelated_alseon_word_does_not_mark_loan_as_planned(golden_cases) -> None:
    draft = golden_cases["2026000372"].expected.model_copy(deep=True)
    draft.interim_loan.bank_names = []
    draft.evidence = []
    text = (
        "대출은 중도금 (총 분양 대금의 40%) 범위 내에서 가능합니다. "
        "불법거래를 알선한 공인중개사는 처벌받습니다."
    )

    actual = ground_ollama_draft(
        draft,
        pages=[_page(31, text, "loan")],
        unit_type_name="59A",
        sale_price_manwon=108_650,
    )

    assert actual.interim_loan.arranged_ratio == 0.40
    assert actual.interim_loan.arrangement_status == LoanArrangementStatus.NOT_STATED


def test_unaligned_model_cost_schedule_is_removed(golden_cases) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    draft.additional_costs = [
        AdditionalCost(
            type=AdditionalCostType.SYSTEM_AIR_CONDITIONER,
            name="시스템 에어컨",
            total_amount_manwon=200,
            required=False,
            included_in_sale_price=False,
            applicable_unit_type="39A",
            payments=[
                AdditionalCostPayment(
                    number=1,
                    stage=PaymentStage.CONTRACT,
                    amount_manwon=20,
                    due_date=None,
                    due_text="계약 시",
                )
            ],
            note=None,
        )
    ]
    page = _page(
        40,
        "시스템 에어컨\n39A 2,000,000 200,000 1,800,000",
        "cost",
    )

    actual = ground_ollama_draft(
        draft,
        pages=[page],
        unit_type_name="39A",
        sale_price_manwon=103_500,
    )

    assert actual.additional_costs[0].total_amount_manwon == 200
    assert actual.additional_costs[0].payments == []
