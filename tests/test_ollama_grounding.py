from __future__ import annotations

from get_myhome_ai.candidates import CandidatePage
from get_myhome_ai.models import (
    AdditionalCost,
    AdditionalCostPayment,
    AdditionalCostType,
    Evidence,
    ExceptionFlag,
    InterestType,
    LoanArrangementStatus,
    PaymentStage,
)
from get_myhome_ai.providers.ollama_grounding import (
    ground_ollama_draft,
    reground_review_metadata,
)


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


def test_paid_option_section_adds_non_blocking_scope_flag(golden_cases) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    pages = [
        _page(
            40,
            "추가선택품목 (유상옵션)\n시스템 에어컨\n39A 2,000,000 200,000 1,800,000",
            "cost",
        )
    ]

    actual = ground_ollama_draft(
        draft,
        pages=pages,
        unit_type_name="39A",
        sale_price_manwon=103_500,
    )

    assert ExceptionFlag.ADDITIONAL_COST_SCOPE_LIMITED in actual.exception_flags
    assert any(
        item.field == "/exception_flags" and item.page == 40
        for item in actual.evidence
    )


def test_balcony_only_section_does_not_add_limited_scope_flag(golden_cases) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)

    actual = ground_ollama_draft(
        draft,
        pages=[_page(38, "발코니 확장 공사비\n39A 15,000,000", "cost")],
        unit_type_name="39A",
        sale_price_manwon=103_500,
    )

    assert ExceptionFlag.ADDITIONAL_COST_SCOPE_LIMITED not in actual.exception_flags


def test_incidental_system_aircon_text_does_not_add_limited_scope_flag(
    golden_cases,
) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)

    actual = ground_ollama_draft(
        draft,
        pages=[_page(38, "시스템에어컨 배관이 노출될 수 있습니다.", "cost")],
        unit_type_name="39A",
        sale_price_manwon=103_500,
    )

    assert ExceptionFlag.ADDITIONAL_COST_SCOPE_LIMITED not in actual.exception_flags


def test_additional_cost_due_text_and_header_evidence_are_source_grounded(
    golden_cases,
) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    page = _page(
        38,
        "발코니 확장 공사비\n"
        "주택형 총액 계약금(10%) 중도금1차(10%) 잔금(80%)\n"
        "계약시 2027.02.19 입주일\n"
        "39A 30,000,000 3,000,000 3,000,000 24,000,000",
    )
    draft.additional_costs[0].total_amount_manwon = 3_000
    draft.additional_costs[0].payments[0].amount_manwon = 300
    draft.additional_costs[0].payments[1].amount_manwon = 300
    draft.additional_costs[0].payments[-1].amount_manwon = 2_400
    draft.evidence.append(
        Evidence(
            field="/additional_costs/0",
            page=38,
            raw_text="39A 30,000,000 3,000,000 3,000,000 24,000,000",
        )
    )

    actual = reground_review_metadata(
        draft,
        pages=[page],
        unit_type_name="39A",
    )

    assert actual.additional_costs[0].payments[-1].due_text == "입주일"
    assert any(
        item.field == "/additional_costs/0/payments"
        and item.page == 38
        and "2027.02.19" in item.raw_text
        and "입주일" in item.raw_text
        for item in actual.evidence
    )
    assert reground_review_metadata(
        actual,
        pages=[page],
        unit_type_name="39A",
    ) == actual


def test_auto_grounding_records_cost_header_as_payment_evidence(golden_cases) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    draft.evidence = []
    page = _page(
        38,
        "발코니 확장 공사비\n"
        "주택형 총액 계약금(10%) 중도금1차(10%) 잔금(80%)\n"
        "계약시 2027.02.19 입주일\n"
        "39A 30,000,000 3,000,000 3,000,000 24,000,000",
        "cost",
    )

    actual = ground_ollama_draft(
        draft,
        pages=[page],
        unit_type_name="39A",
        sale_price_manwon=103_500,
    )

    assert any(
        item.field == "/additional_costs/0/payments"
        and item.page == 38
        and "2027.02.19" in item.raw_text
        and "입주일" in item.raw_text
        for item in actual.evidence
    )


def test_cost_schedule_does_not_invent_contract_or_balance_due_text(
    golden_cases,
) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    draft.evidence.append(
        Evidence(
            field="/additional_costs/0",
            page=38,
            raw_text="39A 30,000,000 3,000,000 3,000,000 24,000,000",
        )
    )
    draft.additional_costs[0].total_amount_manwon = 3_000
    draft.additional_costs[0].payments[0].amount_manwon = 300
    draft.additional_costs[0].payments[1].amount_manwon = 300
    draft.additional_costs[0].payments[-1].amount_manwon = 2_400
    page = _page(
        38,
        "발코니 확장 공사비\n"
        "주택형 총액 계약금(10%) 중도금1차(10%) 잔금(80%)\n"
        "39A 30,000,000 3,000,000 3,000,000 24,000,000",
    )

    actual = reground_review_metadata(
        draft,
        pages=[page],
        unit_type_name="39A",
    )

    assert actual.additional_costs[0].payments[0].due_text is None
    assert actual.additional_costs[0].payments[-1].due_text is None


def test_review_cost_stage_mismatch_does_not_receive_header_evidence(
    golden_cases,
) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    draft.evidence.append(
        Evidence(
            field="/additional_costs/0",
            page=38,
            raw_text="39A 30,000,000 3,000,000 3,000,000 24,000,000",
        )
    )
    draft.additional_costs[0].total_amount_manwon = 3_000
    draft.additional_costs[0].payments[0].amount_manwon = 300
    draft.additional_costs[0].payments[1].amount_manwon = 300
    draft.additional_costs[0].payments[-1].amount_manwon = 2_400
    draft.additional_costs[0].payments[1].stage = PaymentStage.BALANCE
    page = _page(
        38,
        "발코니 확장 공사비\n"
        "주택형 총액 계약금(10%) 중도금1차(10%) 잔금(80%)\n"
        "계약시 2027.02.19 입주일\n"
        "39A 30,000,000 3,000,000 3,000,000 24,000,000",
    )

    actual = reground_review_metadata(
        draft,
        pages=[page],
        unit_type_name="39A",
    )

    assert not any(
        item.field == "/additional_costs/0/payments" for item in actual.evidence
    )


def test_review_cost_schedule_uses_exact_evidence_row_not_first_pdf_match(
    golden_cases,
) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    draft.evidence.append(
        Evidence(
            field="/additional_costs/0",
            page=42,
            raw_text="39A 30,000,000 3,000,000 6,000,000 21,000,000",
        )
    )
    draft.additional_costs[0].total_amount_manwon = 3_000
    draft.additional_costs[0].payments[0].amount_manwon = 300
    draft.additional_costs[0].payments[1].amount_manwon = 600
    draft.additional_costs[0].payments[-1].amount_manwon = 2_100
    pages = [
        _page(
            8,
            "발코니 확장\n주택형 공급금액 계약금 중도금 잔금\n"
            "39A 1,035,000,000 103,500,000 621,000,000 310,500,000",
        ),
        _page(
            42,
            "발코니 확장 공사비\n"
            "약식표기 공급금액 계약시(10%) 중도금(20%) 잔금(70%)\n"
            "계약시 2028.09.20 입주지정일\n"
            "39A 30,000,000 3,000,000 6,000,000 21,000,000",
        ),
    ]

    actual = reground_review_metadata(
        draft,
        pages=pages,
        unit_type_name="39A",
    )

    assert actual.additional_costs[0].payments[1].due_date.isoformat() == "2028-09-20"
    assert actual.additional_costs[0].payments[-1].due_text == "입주지정일"
    assert any(
        item.field == "/additional_costs/0/payments" and item.page == 42
        for item in actual.evidence
    )


def test_review_cost_schedule_does_not_reanchor_without_exact_amount_evidence(
    golden_cases,
) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    original_due_text = draft.additional_costs[0].payments[-1].due_text
    page = _page(
        42,
        "발코니 확장 공사비\n"
        "약식표기 공급금액 계약금(10%) 중도금(20%) 잔금(70%)\n"
        "계약시 2028.09.20 입주일\n"
        "39A 31,000,000 3,100,000 6,200,000 21,700,000",
    )

    actual = reground_review_metadata(
        draft,
        pages=[page],
        unit_type_name="39A",
    )

    assert actual.additional_costs[0].payments[-1].due_text == original_due_text
    assert not any(
        item.field == "/additional_costs/0/payments" for item in actual.evidence
    )


def test_individual_review_uses_person_specific_evidence(golden_cases) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    grounded = reground_review_metadata(
        draft,
        pages=[
            _page(
                8,
                "계약자의 부담으로 납부합니다. 사업주체 및 시공사의 사정으로 "
                "대출 알선 불가",
                "loan",
            ),
            _page(
                41,
                "본인의 사유(보증제한, 신용불량 등)로 인하여 대출이 불가한 계약자는 "
                "납부일정에 맞추어 본인이 직접 납부하여야 합니다.",
                "loan",
            ),
        ],
    )

    clause = next(
        item for item in grounded.risk_clauses if item.code == "INDIVIDUAL_REVIEW_REQUIRED"
    )
    assert clause.evidence[0].page == 41
    assert "보증제한" in clause.evidence[0].raw_text
    assert ExceptionFlag.INDIVIDUAL_REVIEW_NOTED in grounded.exception_flags


def test_individual_review_covers_real_announcement_wording(golden_cases) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    phrases = [
        "추후 금융기관 심사를 통하여 대출여부가 결정됩니다.",
        "개인의 사정 등으로 대출 한도가 개인별로 상이하거나 대출이 불가할 수 있습니다.",
        "계약자 본인의 개인적인 사정에 의해 제한되고 중도금 대출이 불가하거나 "
        "대출한도가 부족할 수 있습니다.",
        "계약자의 대출 적격사유를 고려하여 추후 금융기관 심사를 통하여 결정됩니다.",
        "개인의 사정 및 자격가능여부 등으로 대출한도가 계약자별로 상이하거나 "
        "대출이 불가할 수 있습니다.",
    ]

    for phrase in phrases:
        grounded = reground_review_metadata(
            draft,
            pages=[_page(20, phrase, "loan")],
        )
        assert any(
            item.code == "INDIVIDUAL_REVIEW_REQUIRED"
            for item in grounded.risk_clauses
        ), phrase


def test_business_party_circumstances_do_not_imply_individual_review(
    golden_cases,
) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)

    grounded = reground_review_metadata(
        draft,
        pages=[
            _page(
                8,
                "정부정책, 금융기관, 사업주체 및 시공사의 사정으로 "
                "대출 취급기관과 조건이 변경될 수 있습니다.",
                "loan",
            )
        ],
    )

    assert not any(
        item.code == "INDIVIDUAL_REVIEW_REQUIRED"
        for item in grounded.risk_clauses
    )


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


def test_payment_account_bank_is_not_treated_as_loan_bank(golden_cases) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    draft.interim_loan.bank_names = ["국민은행"]
    quote = "분양대금(계약금, 중도금, 잔금) 납부계좌 국민은행 649701-01-000000"
    draft.evidence = [Evidence(field="/interim_loan/bank_names", page=20, raw_text=quote)]

    actual = ground_ollama_draft(
        draft,
        pages=[_page(20, quote, "loan")],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert actual.interim_loan.bank_names == []
    assert actual.interim_loan.arrangement_status == LoanArrangementStatus.NOT_STATED
    assert not any(item.field == "/interim_loan/bank_names" for item in actual.evidence)


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


def test_sub_manwon_cost_payments_are_not_rounded_into_false_exact_values(
    golden_cases,
) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    draft.additional_costs = [
        AdditionalCost(
            type=AdditionalCostType.SYSTEM_AIR_CONDITIONER,
            name="시스템 에어컨",
            total_amount_manwon=7_150_000,
            required=False,
            included_in_sale_price=False,
            applicable_unit_type="39A",
            payments=[
                AdditionalCostPayment(
                    number=1,
                    stage=PaymentStage.CONTRACT,
                    amount_manwon=715_000,
                    due_date=None,
                    due_text="계약 시",
                ),
                AdditionalCostPayment(
                    number=2,
                    stage=PaymentStage.BALANCE,
                    amount_manwon=6_435_000,
                    due_date=None,
                    due_text="입주지정일",
                ),
            ],
            note=None,
        )
    ]
    page = _page(
        40,
        "시스템 에어컨\n주택형 총 금액 계약금 잔금\n"
        "39A 7,150,000 715,000 6,435,000",
        "cost",
    )

    actual = ground_ollama_draft(
        draft,
        pages=[page],
        unit_type_name="39A",
        sale_price_manwon=103_500,
    )

    assert actual.additional_costs[0].total_amount_manwon == 715
    assert [item.amount_manwon for item in actual.additional_costs[0].payments] == [
        None,
        None,
    ]


def test_sub_manwon_supply_payments_abstain_while_exact_values_are_preserved(
    golden_cases,
) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    draft.evidence = []
    page = _page(
        8,
        "■ 공급금액 및 납부일정 계약금(5%) 중도금(60%) 잔금(35%) "
        "1차(10%) 2차(10%) 3차(10%) 4차(10%) 5차(10%) 6차(10%) "
        "2027.01.10. 2027.07.10. 2028.01.10. 2028.07.10. "
        "2029.01.10. 2029.07.10. 입주지정일\n"
        "586,300,000 29,315,000 58,630,000 58,630,000 58,630,000 "
        "58,630,000 58,630,000 58,630,000 205,205,000",
        "payment",
        "balance",
    )

    actual = ground_ollama_draft(
        draft,
        pages=[page],
        unit_type_name="84B",
        sale_price_manwon=58_630,
    )

    assert actual.payment_schedule.down_payment.total_amount_manwon is None
    assert actual.payment_schedule.down_payment.installments[0].amount_manwon is None
    assert actual.payment_schedule.interim_payment.total_amount_manwon == 35_178
    assert [
        item.amount_manwon for item in actual.payment_schedule.interim_payment.installments
    ] == [5_863] * 6
    assert actual.payment_schedule.balance_payment.total_amount_manwon is None


def test_payment_grounder_accepts_contiguous_cha_installment_headers(golden_cases) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    draft.evidence = []
    page = _page(
        6,
        "공급금액 계약금(10%) 중도금(60%) 잔금 "
        "1차(10%) 2차(10%) 3차(10%) 4차(10%) 5차(10%) 6차(10%) "
        "2027.02.05. 2027.09.03. 2028.04.07. 2028.12.08. 2029.06.08. 2029.12.07. "
        "입주지정일",
        "payment",
        "balance",
    )

    actual = ground_ollama_draft(
        draft,
        pages=[page],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert [item.number for item in actual.payment_schedule.interim_payment.installments] == [
        1,
        2,
        3,
        4,
        5,
        6,
    ]
    assert [item.ratio for item in actual.payment_schedule.interim_payment.installments] == [
        0.1,
    ] * 6


def test_payment_grounder_accepts_plain_cha_installment_headers(golden_cases) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    draft.evidence = []
    page = _page(
        6,
        "■ 공급금액 표 계약금(5%) 중도금(60%) 잔금(35%) "
        "1차 2차 3차 4차 5차 6차 "
        "2026.10.30 2027.02.19 2027.07.20 2027.12.20 2028.03.20 2028.07.20 "
        "입주일",
        "payment",
        "balance",
    )

    actual = ground_ollama_draft(
        draft,
        pages=[page],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert [row.ratio for row in actual.payment_schedule.interim_payment.installments] == [None] * 6
    assert [str(row.due_date) for row in actual.payment_schedule.interim_payment.installments] == [
        "2026-10-30",
        "2027-02-19",
        "2027-07-20",
        "2027-12-20",
        "2028-03-20",
        "2028-07-20",
    ]
    assert actual.payment_schedule.balance_payment.due_text == "입주일"


def test_payment_grounder_normalizes_two_digit_table_years(golden_cases) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    draft.evidence = []
    page = _page(
        9,
        "공급금액 계약금(10%) 중도금(60%) 잔금 "
        "1회 2회 3회 4회 5회 6회 "
        "`27.02.25. `27.08.25. `28.02.25. `28.08.25. `29.02.23. `29.08.24. "
        "입주지정일\n"
        "129,960,170 530,863,480 53,086,350 713,910,000 "
        "10,000,000 61,391,000 "
        "71,391,000 71,391,000 71,391,000 71,391,000 71,391,000 71,391,000 "
        "214,173,000",
        "payment",
        "balance",
    )

    actual = ground_ollama_draft(
        draft,
        pages=[page],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert [
        str(item.due_date) for item in actual.payment_schedule.interim_payment.installments
    ] == [
        "2027-02-25",
        "2027-08-25",
        "2028-02-25",
        "2028-08-25",
        "2029-02-23",
        "2029-08-24",
    ]
    assert [item.ratio for item in actual.payment_schedule.interim_payment.installments] == [
        0.1
    ] * 6
    assert all(
        item.amount_manwon is None for item in actual.payment_schedule.interim_payment.installments
    )
    assert actual.payment_schedule.interim_payment.total_amount_manwon is None
    assert actual.payment_schedule.interim_payment.basis.value == "RATIO"
    assert any(
        item.raw_text.startswith("129,960,170")
        for item in actual.evidence
        if item.field == "/payment_schedule/interim_payment/installments"
    )


def test_payment_header_tolerates_pdf_word_spacing(golden_cases) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    draft.evidence = []
    page = _page(
        6,
        "공급금액 계약 금(10%) 중도 금(60%) 잔 금(30%) "
        "1회 (10%) 2회 (10%) 3회 (10%) 4회 (10%) 5회 (10%) 6회 (10%) "
        "2027.01.15. 2027.07.15. 2028.01.17. 2028.07.17. 2028.12.15. 2029.05.15. "
        "입주지정일",
        "payment",
        "balance",
    )

    actual = ground_ollama_draft(
        draft,
        pages=[page],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert actual.payment_schedule.down_payment.total_ratio == 0.1
    assert actual.payment_schedule.interim_payment.total_ratio == 0.6
    assert actual.payment_schedule.balance_payment.total_ratio == 0.3
    assert [item.ratio for item in actual.payment_schedule.interim_payment.installments] == [
        0.1
    ] * 6


def test_main_supply_schedule_wins_over_option_payment_table(golden_cases) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    draft.evidence = []
    option_page = _page(
        1,
        "유상옵션 공급금액 계약금(10%) 중도금(10%) 잔금(80%) ",
        "payment",
        "cost",
    )
    supply_page = _page(
        9,
        "■ 공급금액 및 납부일정 공급금액 계약금 "
        "중도금(60%) 잔금(30%) (10%) 1차(10%) 2차(10%) 3차(10%) "
        "4차(10%) 5차(10%) 6차(10%) "
        "2027.01.15. 2027.07.15. 2028.01.17. 2028.07.17. "
        "2028.12.15. 2029.05.15. 입주지정일",
        "payment",
        "balance",
    )

    actual = ground_ollama_draft(
        draft,
        pages=[option_page, supply_page],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert actual.payment_schedule.down_payment.total_ratio == 0.1
    assert actual.payment_schedule.interim_payment.total_ratio == 0.6
    assert actual.payment_schedule.balance_payment.total_ratio == 0.3


def test_main_heading_must_introduce_the_selected_payment_table(golden_cases) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    draft.evidence = []
    correction_page = _page(
        1,
        "계약금(10%) 중도금(10%) 잔금(80%)\n300자 후에 다른 목차 항목: 공급금액 표",
        "payment",
        "cost",
    )
    supply_page = _page(
        9,
        "■ 공급금액 및 납부일정 계약금 공급금액 중도금(60%) 잔금(30%) "
        "(10%) 1차(10%) 2차(10%) 3차(10%) 4차(10%) 5차(10%) 6차(10%) "
        "2027.01.30. 2027.05.31. 2027.10.29. 2028.03.30. 2028.08.30. "
        "2029.01.30. 지정일",
        "payment",
        "balance",
    )

    actual = ground_ollama_draft(
        draft,
        pages=[correction_page, supply_page],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert actual.payment_schedule.down_payment.total_ratio == 0.1
    assert actual.payment_schedule.interim_payment.total_ratio == 0.6
    assert actual.payment_schedule.balance_payment.total_ratio == 0.3


def test_dated_second_contract_installment_is_skipped(golden_cases) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    draft.evidence = []
    page = _page(
        9,
        "■ 공급금액 및 납부일정 계약금(5%) 중도금(60%) 잔금(35%) "
        "1차 2차 1차(10%) 2차(10%) 3차(10%) 4차(10%) 5차(10%) 6차(10%) "
        "계약 시 2026.10.07. 2026.11.20. 2027.12.10. 2029.03.12. 2030.06.10. "
        "2030.10.10. 2031.05.12. 입주지정일",
        "payment",
        "balance",
    )

    actual = ground_ollama_draft(
        draft,
        pages=[page],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert [str(row.due_date) for row in actual.payment_schedule.interim_payment.installments] == [
        "2026-11-20",
        "2027-12-10",
        "2029-03-12",
        "2030-06-10",
        "2030-10-10",
        "2031-05-12",
    ]


def test_nondated_second_contract_installment_does_not_shift_dates(golden_cases) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    draft.evidence = []
    page = _page(
        8,
        "■ 공급금액 표 계약금(5%) 중도금(60%) 잔금(35%) "
        "1차 2차 1회(10%) 2회(10%) 3회(10%) 4회(10%) 5회(10%) 6회(10%) "
        "계약 시 계약 후 30일 이내 2026-12-10 2027-04-12 2027-09-10 "
        "2028-05-10 2028-10-10 2029-03-12 입주지정기간 해당 1차 2차 "
        "만료일 또는 공급금액 세대수 실입주일 중 30일 이내 빠른 날",
        "payment",
        "balance",
    )

    actual = ground_ollama_draft(
        draft,
        pages=[page],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert str(actual.payment_schedule.interim_payment.installments[0].due_date) == "2026-12-10"
    assert actual.payment_schedule.balance_payment.due_text == (
        "입주지정기간 만료일 또는 실입주일 중 빠른 날"
    )


def test_supply_amount_table_handles_interim_first_column_order(golden_cases) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    draft.evidence = []
    page = _page(
        7,
        "■ 공급금액 표 중도금(60%) 공급금액 계약금(10%) 잔금(30%) "
        "1차(10%) 2차(10%) 3차(10%) 4차(10%) 5차(10%) 6차(10%) "
        "2027.01.15. 2027.05.17. 2027.09.17. 2028.03.15. "
        "2028.07.18. 2028.12.15. 입주 시",
        "payment",
        "balance",
    )

    actual = ground_ollama_draft(
        draft,
        pages=[page],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert actual.payment_schedule.down_payment.total_ratio == 0.1
    assert actual.payment_schedule.interim_payment.total_ratio == 0.6
    assert actual.payment_schedule.balance_payment.total_ratio == 0.3
    assert len(actual.payment_schedule.interim_payment.installments) == 6
    assert actual.payment_schedule.balance_payment.due_text == "입주 시"


def test_interim_headers_ignore_contract_installment_prefix(golden_cases) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    draft.evidence = []
    page = _page(
        7,
        "■ 공급금액 표 계약금(10%) 중도금(60%) 잔금(30%) "
        "1차(5%) 2차(5%) 1차(10%) 2차(10%) 3차(10%) "
        "4차(10%) 5차(10%) 6차(10%) "
        "2027.03.10. 2027.08.10. 2028.01.10. 2028.04.10. "
        "2028.07.10. 2028.10.10. 입주지정일",
        "payment",
        "balance",
    )

    actual = ground_ollama_draft(
        draft,
        pages=[page],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert [row.ratio for row in actual.payment_schedule.interim_payment.installments] == [0.1] * 6


def test_interim_headers_abstain_when_longest_ratio_vectors_conflict(golden_cases) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    draft.evidence = []
    page = _page(
        7,
        "■ 공급금액 표 계약금(10%) 중도금(60%) 잔금(30%) "
        "1차(10%) 2차(10%) 3차(10%) 4차(10%) 5차(10%) 6차(10%) "
        "1차(5%) 2차(5%) 3차(10%) 4차(10%) 5차(15%) 6차(15%) "
        "2027.03.10. 2027.08.10. 2028.01.10. 2028.04.10. "
        "2028.07.10. 2028.10.10. 입주지정일",
        "payment",
        "balance",
    )

    actual = ground_ollama_draft(
        draft,
        pages=[page],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert actual.payment_schedule.interim_payment.installments == []


def test_interim_headers_accept_duplicate_identical_ratio_vectors(golden_cases) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    draft.evidence = []
    page = _page(
        7,
        "■ 공급금액 표 계약금(10%) 중도금(60%) 잔금(30%) "
        "1차(10%) 2차(10%) 3차(10%) 4차(10%) 5차(10%) 6차(10%) "
        "1차(10%) 2차(10%) 3차(10%) 4차(10%) 5차(10%) 6차(10%) "
        "2027.03.10. 2027.08.10. 2028.01.10. 2028.04.10. "
        "2028.07.10. 2028.10.10. 입주지정일",
        "payment",
        "balance",
    )

    actual = ground_ollama_draft(
        draft,
        pages=[page],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert [row.ratio for row in actual.payment_schedule.interim_payment.installments] == [0.1] * 6


def test_unsupported_model_payment_values_are_cleared(golden_cases) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    draft.payment_schedule.down_payment.total_amount_manwon = 1_000_000
    draft.payment_schedule.interim_payment.total_ratio = 0.6
    draft.payment_schedule.balance_payment.total_amount_manwon = 1_000_000
    draft.evidence = []

    actual = ground_ollama_draft(
        draft,
        pages=[_page(1, "모집공고일 현재", "payment")],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert actual.payment_schedule.down_payment.total_amount_manwon is None
    assert actual.payment_schedule.interim_payment.total_ratio is None
    assert actual.payment_schedule.balance_payment.total_amount_manwon is None
    assert actual.payment_schedule.interim_payment.installments == []


def test_representative_row_abstains_when_component_ratios_conflict(golden_cases) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    draft.evidence = []
    page = _page(
        9,
        "공급금액 계약금(10%) 중도금(60%) 잔금(30%) "
        "1회 2회 3회 4회 5회 6회 "
        "2027.02.25. 2027.08.25. 2028.02.25. 2028.08.25. 2029.02.23. 2029.08.24. "
        "입주지정일\n"
        "1,000,000 100,000 100,000 100,000 100,000 100,000 100,000 100,000 300,000\n"
        "2,000,000 200,000 400,000 160,000 160,000 160,000 160,000 160,000 600,000",
        "payment",
        "balance",
    )

    actual = ground_ollama_draft(
        draft,
        pages=[page],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert [item.ratio for item in actual.payment_schedule.interim_payment.installments] == [
        None
    ] * 6


def test_planned_status_is_grounded_even_without_a_ratio(golden_cases) -> None:
    draft = golden_cases["2026000372"].expected.model_copy(deep=True)
    draft.evidence = []
    draft.interim_loan.arranged_ratio = 0.4
    draft.interim_loan.self_funding_ratio = 0.2
    text = "사업주체가 알선한 대출취급기관의 중도금 대출을 통해 납입할 수 있습니다."

    actual = ground_ollama_draft(
        draft,
        pages=[_page(7, text, "loan")],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert actual.interim_loan.arrangement_status == LoanArrangementStatus.PLANNED
    assert actual.interim_loan.arranged_ratio is None
    assert actual.interim_loan.self_funding_ratio is None
    assert actual.interim_loan.prepay_requirement_ratio is None


def test_project_ratio_and_contract_completion_are_grounded(golden_cases) -> None:
    draft = golden_cases["2026000372"].expected.model_copy(deep=True)
    draft.evidence = []
    text = (
        "계약자는 계약금 1차 및 계약금 2차 완납 후 사업주체가 알선한 "
        "중도금 대출을 통해 납입할 수 있습니다. "
        "중도금 대출에 대한 이자는 중도금 이자후불제 조건으로 "
        "전체 공급대금의 중도금 60% 범위 내에서 시행할 예정입니다."
    )

    actual = ground_ollama_draft(
        draft,
        pages=[
            _page(
                6,
                "■ 공급금액 및 납부일정 계약금(10%) 중도금(60%) 잔금(30%)",
                "payment",
            ),
            _page(31, text, "loan"),
        ],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert actual.interim_loan.arranged_ratio == 0.6
    assert actual.interim_loan.prepay_requirement_ratio == 0.1


def test_contract_completion_supports_line_broken_after_particle(golden_cases) -> None:
    draft = golden_cases["2026000372"].expected.model_copy(deep=True)
    draft.evidence = []
    text = (
        "계약자는 계약금 1차 및 계약금 2차 완납 이\n"
        "후 사업주체가 알선한 중도금 대출을 통해 납입할 수 있습니다."
    )

    actual = ground_ollama_draft(
        draft,
        pages=[
            _page(
                6,
                "■ 공급금액 및 납부일정 계약금(10%) 중도금(60%) 잔금(30%)",
                "payment",
            ),
            _page(31, text, "loan"),
        ],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert actual.interim_loan.prepay_requirement_ratio == 0.1


def test_ratio_before_arrangement_and_paid_prepay_are_grounded(golden_cases) -> None:
    draft = golden_cases["2026000372"].expected.model_copy(deep=True)
    draft.evidence = []
    text = (
        "계약자는 분양대금의 5% 납입 시 중도금 대출을 실행할 수 있습니다. "
        "총 공급금액 중 60% 이내 중도금에 대하여 사업주체가 알선한 금융기관에 "
        "지정기간 내 대출 시 중도금 무이자 조건입니다."
    )

    actual = ground_ollama_draft(
        draft,
        pages=[_page(41, text, "loan")],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert actual.interim_loan.arranged_ratio == 0.6
    assert actual.interim_loan.prepay_requirement_ratio == 0.05
    assert actual.interim_loan.interest_type == InterestType.INTEREST_FREE


def test_full_installment_loan_ratio_and_contract_ratio_are_grounded(golden_cases) -> None:
    draft = golden_cases["2026000372"].expected.model_copy(deep=True)
    draft.evidence = []
    text = (
        "계약금(공급대금의 5%) 완납이후 중도금 대출이 가능합니다. "
        "사업주체는 이자후불제 조건으로 중도금대출을 알선할 수 있으며, "
        "중도금(1~6회차 중도금)(공급대금의 60%)은 중도금 대출금으로 납부됩니다."
    )

    actual = ground_ollama_draft(
        draft,
        pages=[_page(21, text, "loan")],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert actual.interim_loan.arrangement_status == LoanArrangementStatus.PLANNED
    assert actual.interim_loan.arranged_ratio == 0.6
    assert actual.interim_loan.prepay_requirement_ratio == 0.05


def test_interim_cap_with_interest_and_intervening_contract_clause_is_grounded(
    golden_cases,
) -> None:
    draft = golden_cases["2026000372"].expected.model_copy(deep=True)
    draft.evidence = []
    text = (
        "계약금(분양대금의 10%) 완납 및 분양계약 체결 후 중도금 대출을 신청합니다. "
        "중도금 대출은 이자후불제이며, 공급대금의 60% 이내에서 "
        "이자후불제 조건으로 중도금대출을 알선할 수 있습니다."
    )

    actual = ground_ollama_draft(
        draft,
        pages=[_page(41, text, "loan")],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert actual.interim_loan.arrangement_status == LoanArrangementStatus.PLANNED
    assert actual.interim_loan.arranged_ratio == 0.6
    assert actual.interim_loan.prepay_requirement_ratio == 0.1
    assert actual.interim_loan.interest_type == InterestType.DEFERRED_INTEREST


def test_supply_amount_cap_and_spaced_deferred_interest_are_grounded(golden_cases) -> None:
    draft = golden_cases["2026000372"].expected.model_copy(deep=True)
    draft.evidence = []
    text = (
        "사업주체는 본 아파트의 중도금 대출을 공급금액의 60% 범위"
        "(중도금 1회부터 중도금 6회까지) 내에서 중도금 대출 이자 후불제 "
        "조건으로 대출 알선을 할 예정입니다."
    )

    actual = ground_ollama_draft(
        draft,
        pages=[_page(34, text, "loan")],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert actual.interim_loan.arranged_ratio == 0.6
    assert actual.interim_loan.interest_type == InterestType.DEFERRED_INTEREST


def test_deferred_interest_is_found_in_long_loan_terms_clause(golden_cases) -> None:
    draft = golden_cases["2026000372"].expected.model_copy(deep=True)
    draft.evidence = []
    text = (
        "중도금 대출조건은 시행위탁자와 대출협약을 체결한 대출 금융기관에서 "
        "대출받는 경우에 한하여 이자후불제 조건으로 시행할 예정입니다."
    )

    actual = ground_ollama_draft(
        draft,
        pages=[_page(40, text, "loan")],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert actual.interim_loan.interest_type == InterestType.DEFERRED_INTEREST


def test_total_supply_amount_cap_is_grounded(golden_cases) -> None:
    draft = golden_cases["2026000372"].expected.model_copy(deep=True)
    draft.evidence = []
    text = (
        "본 아파트의 중도금 대출에 대한 이자는 중도금 이자후불제 조건이며 "
        "총 공급금액 40% 범위 내에서 중도금 융자 알선을 시행할 예정입니다."
    )

    actual = ground_ollama_draft(
        draft,
        pages=[_page(47, text, "loan")],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert actual.interim_loan.arranged_ratio == 0.4
    assert actual.interim_loan.interest_type == InterestType.DEFERRED_INTEREST


def test_prepay_completion_with_parenthetical_is_grounded(golden_cases) -> None:
    draft = golden_cases["2026000372"].expected.model_copy(deep=True)
    draft.evidence = []
    text = "계약자는 공급대금의 10%(2차 계약금 포함) 완납 시 중도금 대출을 실행할 수 있습니다."

    actual = ground_ollama_draft(
        draft,
        pages=[_page(7, text, "loan")],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert actual.interim_loan.prepay_requirement_ratio == 0.1


def test_simple_contract_ratio_and_total_paid_prepay_are_grounded(golden_cases) -> None:
    draft = golden_cases["2026000372"].expected.model_copy(deep=True)
    draft.evidence = []
    pages = [
        _page(
            20,
            "계약금 5% 완납 이후 중도금 대출이 가능합니다.",
            "loan",
        ),
        _page(
            21,
            "대출은행과의 협약에 따라 공급대금의 10% 이상 납부 이후 대출이 가능합니다.",
            "loan",
        ),
    ]

    five_percent = ground_ollama_draft(
        draft.model_copy(deep=True),
        pages=[pages[0]],
        unit_type_name=None,
        sale_price_manwon=None,
    )
    ten_percent = ground_ollama_draft(
        draft.model_copy(deep=True),
        pages=[pages[1]],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert five_percent.interim_loan.prepay_requirement_ratio == 0.05
    assert ten_percent.interim_loan.prepay_requirement_ratio == 0.1


def test_total_paid_prepay_accepts_explicit_interim_loan(golden_cases) -> None:
    draft = golden_cases["2026000372"].expected.model_copy(deep=True)
    draft.evidence = []
    text = "대출은행과 대출협약에 따라 공급대금의 5% 이상 납부 이후 중도금 대출이 가능합니다."

    actual = ground_ollama_draft(
        draft,
        pages=[_page(38, text, "loan")],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert actual.interim_loan.prepay_requirement_ratio == 0.05


def test_non_loan_payment_phrases_do_not_become_prepay_requirements(golden_cases) -> None:
    draft = golden_cases["2026000372"].expected.model_copy(deep=True)
    draft.evidence = []
    texts = [
        "분양권 전매는 계약금 10% 완납 후 가능합니다.",
        "분양대금의 10% 이상 납부 이후 대출이 가능합니다.",
    ]

    for text in texts:
        actual = ground_ollama_draft(
            draft.model_copy(deep=True),
            pages=[_page(21, text, "loan")],
            unit_type_name=None,
            sale_price_manwon=None,
        )

        assert actual.interim_loan.prepay_requirement_ratio is None


def test_deferred_interest_can_precede_interim_loan_phrase(golden_cases) -> None:
    draft = golden_cases["2026000372"].expected.model_copy(deep=True)
    draft.evidence = []
    text = "사업주체는 이자후불제 조건으로 중도금대출을 알선할 수 있습니다."

    actual = ground_ollama_draft(
        draft,
        pages=[_page(21, text, "loan")],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert actual.interim_loan.interest_type == InterestType.DEFERRED_INTEREST


def test_exact_balance_due_clause_is_preserved(golden_cases) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    draft.evidence = []
    page = _page(
        6,
        "■ 공급금액 및 납부일정 계약금(10%) 중도금(60%) 잔금(30%) "
        "입주지정기간의\n약식 동별 공급 층 1차 2차 만료일 또는\n"
        "1회(10%) 2회(10%) 3회(10%) 세대출입\n"
        "2027.01.01. 2027.06.01. 열쇠 수령일 중\n선 도래일",
        "payment",
        "balance",
    )

    actual = ground_ollama_draft(
        draft,
        pages=[page],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert actual.payment_schedule.balance_payment.due_text == (
        "입주지정기간의 만료일 또는 세대출입 열쇠 수령일 중 선 도래일"
    )


def test_move_in_month_heading_is_not_balance_due_at_move_in(golden_cases) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    draft.evidence = []
    page = _page(
        6,
        "공급금액 및 납부일정 계약금(10%) 중도금(60%) 잔금(30%) "
        "1차(10%) 2차(10%) 3차(10%) 4차(10%) 5차(10%) 6차(10%) "
        "2027.02.05. 2027.09.03. 2028.04.07. 2028.12.08. "
        "2029.06.08. 2029.12.07. 입주지정일 입주시기 : 2030년 05월 예정",
        "payment",
        "balance",
    )

    actual = ground_ollama_draft(
        draft,
        pages=[page],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert actual.payment_schedule.balance_payment.due_text == "입주지정일"
    assert actual.payment_schedule.balance_payment.due_month == "2030-05"


def test_free_interest_does_not_become_deferred_for_post_move_interest(golden_cases) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    draft.evidence = []
    text = (
        "중도금 무이자 조건이며, 중도금 대출 이자는 입주개시월까지 "
        "사업주체가 계약자를 대신하여 납부합니다. "
        "그 이후 발생하는 대출이자는 계약자가 직접 납부합니다."
    )

    actual = ground_ollama_draft(
        draft,
        pages=[_page(41, text, "loan")],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert actual.interim_loan.interest_type == InterestType.INTEREST_FREE
    assert actual.interim_loan.interest_note is None


def test_free_label_with_buyer_interest_repayment_is_deferred_and_grounded(golden_cases) -> None:
    draft = golden_cases["2026000358"].expected.model_copy(deep=True)
    draft.evidence = []
    text = (
        "중도금 무이자 조건으로 시행할 예정이며, "
        "계약자는 입주시 사업주체가 대납한 중도금 대출이자 등을 일시 납부하여야 합니다."
    )

    actual = ground_ollama_draft(
        draft,
        pages=[_page(39, text, "loan")],
        unit_type_name=None,
        sale_price_manwon=None,
    )

    assert actual.interim_loan.interest_type == InterestType.DEFERRED_INTEREST
    assert actual.interim_loan.interest_note is not None
    assert {
        "/interim_loan/interest_type",
        "/interim_loan/interest_note",
    } <= {item.field for item in actual.evidence}
