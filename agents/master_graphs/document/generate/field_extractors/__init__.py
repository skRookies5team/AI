"""템플릿별 필드 추출 모듈."""

from .product_development import extract_product_development_fields
from .purchase_order import extract_purchase_order_fields
from .work_performance_review import extract_work_performance_review_fields
from .product_comparison import extract_product_comparison_fields
from .monthly_profit_analysis import extract_monthly_profit_analysis_fields
from .activity_based_costing import extract_activity_based_costing_fields
from .hr_summary_report import extract_hr_summary_report_fields
from .vacation_request import extract_vacation_request_fields

__all__ = [
    'extract_product_development_fields',
    'extract_purchase_order_fields',
    'extract_work_performance_review_fields',
    'extract_product_comparison_fields',
    'extract_monthly_profit_analysis_fields',
    'extract_activity_based_costing_fields',
    'extract_hr_summary_report_fields',
    'extract_vacation_request_fields',
]

# 템플릿 이름과 추출 함수 매핑
TEMPLATE_EXTRACTORS = {
    'product_development_plan': extract_product_development_fields,
    'purchase_order': extract_purchase_order_fields,
    'work_performance_review': extract_work_performance_review_fields,
    'product_comparison_table': extract_product_comparison_fields,
    'monthly_profit_analysis_report': extract_monthly_profit_analysis_fields,
    'activity_based_costing': extract_activity_based_costing_fields,
    'hr_summary_report': extract_hr_summary_report_fields,
    'vacation_request_form': extract_vacation_request_fields,
}

