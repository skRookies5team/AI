
import sys
import os

# Add the project root to the python path
sys.path.append(os.path.dirname(__file__))

from agents.master_graphs.document.generate.field_extractors.work_performance_review import extract_work_performance_review_fields

def test_extraction():
    requirements = """
    부서명: 개발팀, 코드번호: DEV-001, 평가 기간: 2024년 1월 1일 ~ 3월 31일
    업무명: 웹 애플리케이션 개발, 작성자명: 박개발, 담당 부서장명: 최부장
    주평가대상: 신입 개발자 교육, 벌점 0점, 주당 40시간
    평가 방법: 주별 코드 리뷰 및 과제 평가
    """
    
    document_title = "2024년 1분기 근무 평가서"
    field_values = {}
    
    print(f"Test Input Requirements:\n{requirements}")
    
    extract_work_performance_review_fields(field_values, requirements, document_title)
    
    print("\nExtraction Results:")
    expected_values = {
        'departmentName': '개발팀',
        'codeNumber': 'DEV-001',
        'workName': '웹 애플리케이션 개발',
        'authorName': '박개발',
        'departmentHeadName': '최부장',
        'evaluationTarget': '신입 개발자 교육',
        'evaluationMethod': '주별 코드 리뷰 및 과제 평가'
    }
    
    all_passed = True
    for key, expected in expected_values.items():
        actual = field_values.get(key)
        status = "✅ PASS" if actual == expected.strip() else f"❌ FAIL (Expected: '{expected}', Got: '{actual}')"
        print(f"{key}: {actual} [{status}]")
        if actual != expected.strip():
            all_passed = False
            
    if all_passed:
        print("\nAll tests passed successfully!")
    else:
        print("\nSome tests failed.")

if __name__ == "__main__":
    test_extraction()
