"""CLOVA Speech API Client"""

import json
import time
import logging
import requests
from typing import Dict, Any, Optional, Union
from pathlib import Path


class ClovaSpeechError(Exception):
    """CLOVA Speech API 오류"""
    pass


class ClovaSpeechClient:
    """NAVER Cloud Platform CLOVA Speech API 클라이언트"""

    def __init__(self, invoke_url: str, secret_key: str, timeout: int = 60):
        """
        클라이언트 초기화

        Args:
            invoke_url: API Gateway Invoke URL
            secret_key: CLOVA Speech Secret Key
            timeout: 요청 타임아웃 (초)
        """
        self.invoke_url = invoke_url.rstrip('/')
        self.secret_key = secret_key
        self.timeout = timeout
        self.session = requests.Session()

        # 공통 헤더 설정
        self.session.headers.update({
            'X-CLOVASPEECH-API-KEY': self.secret_key,
            'Accept': 'application/json'
        })

        # 로깅 설정
        self.logger = logging.getLogger(__name__)

    def _make_request(self, endpoint: str, method: str = 'POST', **kwargs) -> Dict[str, Any]:
        """
        API 요청 실행 (재시도 로직 포함)

        Args:
            endpoint: API 엔드포인트
            method: HTTP 메서드
            **kwargs: requests 파라미터

        Returns:
            API 응답 데이터

        Raises:
            ClovaSpeechError: API 오류 시
        """
        url = f"{self.invoke_url}/{endpoint}"
        max_retries = 3
        base_delay = 1

        for attempt in range(max_retries):
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    timeout=self.timeout,
                    **kwargs
                )

                # 응답 처리
                if response.status_code == 200:
                    try:
                        return response.json()
                    except json.JSONDecodeError:
                        raise ClovaSpeechError(f"JSON 파싱 실패: {response.text}")

                # 에러 응답 처리
                error_msg = f"API 요청 실패 (상태 코드: {response.status_code})"
                try:
                    error_data = response.json()
                    if 'error' in error_data:
                        error_msg += f"\n오류: {error_data['error']}"
                    if 'message' in error_data:
                        error_msg += f"\n메시지: {error_data['message']}"
                except:
                    error_msg += f"\n응답: {response.text}"

                # 재시도 가능한 오류인지 확인
                if response.status_code in [500, 502, 503, 504] and attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    self.logger.warning(f"재시도 {attempt + 1}/{max_retries} (지연: {delay}초)")
                    time.sleep(delay)
                    continue

                raise ClovaSpeechError(error_msg)

            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    self.logger.warning(f"네트워크 오류로 재시도 {attempt + 1}/{max_retries} (지연: {delay}초)")
                    time.sleep(delay)
                    continue
                raise ClovaSpeechError(f"네트워크 오류: {e}")

        raise ClovaSpeechError("최대 재시도 횟수 초과")

    def _build_params(self, **options) -> Dict[str, Any]:
        """
        요청 파라미터 구성

        Args:
            **options: 옵션 파라미터

        Returns:
            요청 파라미터 딕셔너리
        """
        # 기본 파라미터
        params = {
            'language': options.get('language', 'enko'),  # 한영 혼합 인식 기본값
            'completion': options.get('completion', 'sync'),
            'wordAlignment': options.get('wordAlignment', True),
            'fullText': options.get('fullText', True),
            'format': 'JSON'
        }

        # 회의용 고급 옵션 기본 활성화
        if options.get('enable_diarization', True):
            params['diarization'] = options.get('diarization', {
                'enable': True,
                'speakerCountMin': 2,
                'speakerCountMax': 10
            })

        # 노이즈 필터링 기본 활성화
        if options.get('enable_noise_filtering', True):
            params['noiseFiltering'] = True

        # 음향 이벤트 탐지 (SED) 기본 활성화
        if options.get('enable_sed', False):
            params['sed'] = options.get('sed', {
                'enable': True
            })

        # 금지어/강조어 설정
        if 'forbiddens' in options:
            params['forbiddens'] = options['forbiddens']

        if 'boostings' in options:
            params['boostings'] = options['boostings']

        # 선택적 파라미터
        optional_params = [
            'callback', 'userdata', 'endToEnd'
        ]

        for param in optional_params:
            if param in options:
                params[param] = options[param]

        return params

    def request_by_url(self, url: str, **options) -> Union[str, Dict[str, Any]]:
        """
        URL 방식으로 음성 인식 요청

        Args:
            url: 오디오 파일 URL
            **options: 옵션 파라미터
                - language: 언어 (기본: ko-KR)
                - completion: sync/async (기본: sync)
                - wordAlignment: 단어 정렬 (기본: True)
                - fullText: 전체 텍스트 (기본: True)
                - callback: 콜백 URL (async 모드)
                - userdata: 사용자 데이터
                - enable_diarization: 화자 분리 활성화 (기본: True)
                - diarization: 화자 분리 설정
                - enable_noise_filtering: 노이즈 필터링 활성화 (기본: True)
                - enable_sed: 음향 이벤트 탐지 활성화 (기본: False)
                - sed: 음향 이벤트 탐지 설정
                - forbiddens: 금지어 목록
                - boostings: 강조어 목록

        Returns:
            sync 모드: 인식 결과 딕셔너리
            async 모드: job_id 문자열
        """
        params = self._build_params(**options)
        params['url'] = url

        self.logger.info(f"URL 방식 음성 인식 요청 시작: {url}")

        response_data = self._make_request(
            'recognizer/url',
            json=params
        )

        if params['completion'] == 'sync':
            self.logger.info("동기 모드: 인식 결과 반환")
            return response_data
        else:
            # 비동기 모드: job_id 추출
            job_id = response_data.get('token') or response_data.get('jobId')
            if not job_id:
                raise ClovaSpeechError("비동기 응답에서 job_id를 찾을 수 없음")

            self.logger.info(f"비동기 모드: job_id 반환 - {job_id}")
            return job_id

    def request_by_file(self, file_path: Union[str, Path], **options) -> Union[str, Dict[str, Any]]:
        """
        파일 업로드 방식으로 음성 인식 요청

        Args:
            file_path: 로컬 오디오 파일 경로
            **options: 옵션 파라미터 (request_by_url과 동일)

        Returns:
            sync 모드: 인식 결과 딕셔너리
            async 모드: job_id 문자열
        """
        file_path = Path(file_path)

        if not file_path.exists():
            raise ClovaSpeechError(f"파일을 찾을 수 없습니다: {file_path}")

        params = self._build_params(**options)

        self.logger.info(f"파일 업로드 방식 음성 인식 요청 시작: {file_path}")

        # multipart/form-data 구성
        with open(file_path, 'rb') as f:
            files = {
                'media': (file_path.name, f, 'application/octet-stream'),
                'params': (None, json.dumps(params), 'application/json')
            }

            response_data = self._make_request(
                'recognizer/upload',
                files=files
            )

        if params['completion'] == 'sync':
            self.logger.info("동기 모드: 인식 결과 반환")
            return response_data
        else:
            # 비동기 모드: job_id 추출
            job_id = response_data.get('token') or response_data.get('jobId')
            if not job_id:
                raise ClovaSpeechError("비동기 응답에서 job_id를 찾을 수 없음")

            self.logger.info(f"비동기 모드: job_id 반환 - {job_id}")
            return job_id

    def get_result(self, job_id: str) -> Dict[str, Any]:
        """
        비동기 작업 결과 조회

        Args:
            job_id: 작업 ID

        Returns:
            인식 결과 딕셔너리
        """
        self.logger.info(f"작업 결과 조회: {job_id}")

        response_data = self._make_request(
            f'recognizer/{job_id}',
            method='GET'
        )

        return response_data

    def wait_for_completion(self, job_id: str, poll_interval: int = 2, max_wait: int = 300) -> Dict[str, Any]:
        """
        비동기 작업 완료까지 대기

        Args:
            job_id: 작업 ID
            poll_interval: 폴링 간격 (초)
            max_wait: 최대 대기 시간 (초)

        Returns:
            완료된 인식 결과
        """
        self.logger.info(f"작업 완료 대기 시작: {job_id}")

        start_time = time.time()

        while time.time() - start_time < max_wait:
            try:
                result = self.get_result(job_id)

                # 상태 확인
                status = result.get('status', '').lower()

                if status == 'completed' or 'text' in result:
                    self.logger.info("작업 완료")
                    return result
                elif status == 'failed' or status == 'error':
                    error_msg = result.get('message', '알 수 없는 오류')
                    raise ClovaSpeechError(f"작업 실패: {error_msg}")
                elif status in ['processing', 'running', 'queued']:
                    self.logger.info(f"처리 중... (상태: {status})")
                else:
                    self.logger.info(f"대기 중... (상태: {status})")

                time.sleep(poll_interval)

            except ClovaSpeechError as e:
                if "404" in str(e):
                    self.logger.info("아직 준비되지 않음, 계속 대기...")
                    time.sleep(poll_interval)
                    continue
                raise

        raise ClovaSpeechError(f"작업 완료 대기 시간 초과: {max_wait}초")