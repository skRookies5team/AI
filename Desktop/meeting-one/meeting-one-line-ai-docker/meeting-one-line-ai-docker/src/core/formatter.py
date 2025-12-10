"""STT 결과를 읽기 좋은 대화록으로 포맷팅하는 유틸리티"""

import logging
from typing import List, Dict, Any, Optional
from datetime import timedelta


def format_segments_to_transcript(segments: List[Dict[str, Any]]) -> str:
    """
    CLOVA STT의 'segments' JSON을 대화록 텍스트로 변환합니다.

    Args:
        segments: STT 결과의 segments 리스트

    Returns:
        화자별로 구분된 대화록 텍스트

    Example:
        >>> segments = [
        ...     {"speaker": {"label": "1"}, "text": "안녕하세요"},
        ...     {"speaker": {"label": "2"}, "text": "반갑습니다"}
        ... ]
        >>> format_segments_to_transcript(segments)
        '[화자 1]: 안녕하세요\\n[화자 2]: 반갑습니다'
    """
    if not segments:
        return ""

    logger = logging.getLogger(__name__)
    logger.info(f"STT 결과를 대화록 텍스트로 변환 중... (세그먼트 수: {len(segments)})")

    transcript = ""
    current_speaker = None

    for segment in segments:
        speaker_label = segment.get("speaker", {}).get("label", "Unknown")
        text = segment.get("text", "").strip()

        if not text:
            continue

        # 화자가 바뀔 때마다 줄바꿈 및 라벨 추가
        if speaker_label != current_speaker:
            if transcript:  # 첫 번째가 아닌 경우에만 줄바꿈
                transcript += "\n"
            transcript += f"[화자 {speaker_label}]: "
            current_speaker = speaker_label
        else:
            # 같은 화자가 계속 말하는 경우 공백으로 연결
            transcript += " "

        transcript += text

    logger.info("대화록 변환 완료")
    return transcript.strip()


def format_segments_to_detailed_transcript(segments: List[Dict[str, Any]],
                                         include_timestamps: bool = True,
                                         include_confidence: bool = False) -> str:
    """
    STT 결과를 상세한 대화록으로 변환합니다 (타임스탬프, 신뢰도 포함).

    Args:
        segments: STT 결과의 segments 리스트
        include_timestamps: 타임스탬프 포함 여부
        include_confidence: 신뢰도 포함 여부

    Returns:
        상세한 대화록 텍스트
    """
    if not segments:
        return ""

    logger = logging.getLogger(__name__)
    logger.info("상세 대화록 생성 중...")

    transcript = ""

    for i, segment in enumerate(segments):
        speaker_label = segment.get("speaker", {}).get("label", "Unknown")
        text = segment.get("text", "").strip()
        start = segment.get("start", 0)
        end = segment.get("end", 0)
        confidence = segment.get("confidence", 0)

        if not text:
            continue

        # 세그먼트 구분선
        if i > 0:
            transcript += "\n"

        # 화자 정보
        transcript += f"[화자 {speaker_label}]"

        # 타임스탬프 추가
        if include_timestamps and start is not None:
            start_time = _ms_to_time_str(start)
            end_time = _ms_to_time_str(end) if end else ""
            if end_time:
                transcript += f" ({start_time} - {end_time})"
            else:
                transcript += f" ({start_time})"

        # 신뢰도 추가
        if include_confidence and confidence:
            transcript += f" [신뢰도: {confidence:.2f}]"

        transcript += f": {text}\n"

    return transcript.strip()


def format_transcript_with_speakers(segments: List[Dict[str, Any]],
                                   speaker_names: Optional[Dict[str, str]] = None) -> str:
    """
    화자별 이름을 매핑하여 대화록을 생성합니다.

    Args:
        segments: STT 결과의 segments 리스트
        speaker_names: 화자 라벨과 실제 이름의 매핑 (예: {"1": "김철수", "2": "이영희"})

    Returns:
        실제 이름으로 구성된 대화록
    """
    if not segments:
        return ""

    logger = logging.getLogger(__name__)
    logger.info("화자별 이름 매핑 대화록 생성 중...")

    transcript = ""
    current_speaker = None

    for segment in segments:
        speaker_label = segment.get("speaker", {}).get("label", "Unknown")
        text = segment.get("text", "").strip()

        if not text:
            continue

        # 실제 이름 또는 기본 라벨 사용
        if speaker_names and speaker_label in speaker_names:
            speaker_name = speaker_names[speaker_label]
        else:
            speaker_name = f"화자 {speaker_label}"

        # 화자가 바뀔 때마다 줄바꿈 및 라벨 추가
        if speaker_label != current_speaker:
            if transcript:
                transcript += "\n"
            transcript += f"[{speaker_name}]: "
            current_speaker = speaker_label
        else:
            transcript += " "

        transcript += text

    return transcript.strip()


def extract_speaker_statistics(segments: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    화자별 통계 정보를 추출합니다.

    Args:
        segments: STT 결과의 segments 리스트

    Returns:
        화자별 통계 정보 딕셔너리
    """
    if not segments:
        return {}

    logger = logging.getLogger(__name__)
    logger.info("화자별 통계 정보 추출 중...")

    stats = {}

    for segment in segments:
        speaker_label = segment.get("speaker", {}).get("label", "Unknown")
        text = segment.get("text", "").strip()
        start = segment.get("start", 0)
        end = segment.get("end", 0)
        duration = end - start if end and start else 0

        if speaker_label not in stats:
            stats[speaker_label] = {
                "segments_count": 0,
                "total_words": 0,
                "total_duration_ms": 0,
                "total_characters": 0,
                "first_appearance_ms": None,
                "last_appearance_ms": None
            }

        # 통계 업데이트
        stats[speaker_label]["segments_count"] += 1
        stats[speaker_label]["total_words"] += len(text.split()) if text else 0
        stats[speaker_label]["total_characters"] += len(text) if text else 0
        stats[speaker_label]["total_duration_ms"] += duration

        # 첫 등장 시간
        if stats[speaker_label]["first_appearance_ms"] is None or start < stats[speaker_label]["first_appearance_ms"]:
            stats[speaker_label]["first_appearance_ms"] = start

        # 마지막 등장 시간
        if stats[speaker_label]["last_appearance_ms"] is None or end > stats[speaker_label]["last_appearance_ms"]:
            stats[speaker_label]["last_appearance_ms"] = end

    # 비율 계산
    total_duration = sum(stat["total_duration_ms"] for stat in stats.values())
    total_words = sum(stat["total_words"] for stat in stats.values())

    for speaker_label, stat in stats.items():
        stat["duration_percentage"] = (stat["total_duration_ms"] / total_duration * 100) if total_duration > 0 else 0
        stat["words_percentage"] = (stat["total_words"] / total_words * 100) if total_words > 0 else 0
        stat["average_words_per_segment"] = stat["total_words"] / stat["segments_count"] if stat["segments_count"] > 0 else 0

    logger.info(f"통계 추출 완료 (화자 수: {len(stats)})")
    return stats


def generate_meeting_summary_header(segments: List[Dict[str, Any]],
                                   meeting_title: Optional[str] = None) -> str:
    """
    회의 요약 헤더를 생성합니다.

    Args:
        segments: STT 결과의 segments 리스트
        meeting_title: 회의 제목

    Returns:
        회의 요약 헤더 텍스트
    """
    if not segments:
        return ""

    stats = extract_speaker_statistics(segments)

    # 전체 시간 계산
    if segments:
        total_duration_ms = max(seg.get("end", 0) for seg in segments if seg.get("end"))
        total_duration_str = _ms_to_time_str(total_duration_ms)
    else:
        total_duration_str = "00:00"

    header = ""

    if meeting_title:
        header += f"# {meeting_title}\n\n"
    else:
        header += "# 회의록\n\n"

    header += f"**전체 시간**: {total_duration_str}\n"
    header += f"**참석자 수**: {len(stats)}명\n"
    header += f"**총 발화 수**: {sum(stat['segments_count'] for stat in stats.values())}\n\n"

    # 참석자별 발화 비율
    header += "**참석자별 발화 비율**:\n"
    for speaker_label, stat in sorted(stats.items()):
        duration_str = _ms_to_time_str(stat["total_duration_ms"])
        header += f"- 화자 {speaker_label}: {stat['duration_percentage']:.1f}% ({duration_str})\n"

    header += "\n---\n\n"
    return header


def _ms_to_time_str(milliseconds: int) -> str:
    """밀리초를 MM:SS 형태의 문자열로 변환"""
    if not milliseconds:
        return "00:00"

    seconds = milliseconds // 1000
    minutes = seconds // 60
    seconds = seconds % 60

    return f"{minutes:02d}:{seconds:02d}"


def create_word_level_transcript(segments: List[Dict[str, Any]]) -> str:
    """
    단어별 상세 정보를 포함한 대화록을 생성합니다.

    Args:
        segments: STT 결과의 segments 리스트

    Returns:
        단어별 상세 대화록
    """
    if not segments:
        return ""

    logger = logging.getLogger(__name__)
    logger.info("단어별 상세 대화록 생성 중...")

    transcript = ""

    for segment in segments:
        speaker_label = segment.get("speaker", {}).get("label", "Unknown")
        words = segment.get("words", [])

        if not words:
            continue

        transcript += f"\n[화자 {speaker_label}]:\n"

        for word_info in words:
            word = word_info.get("text", "")
            start = word_info.get("start", 0)
            end = word_info.get("end", 0)
            confidence = word_info.get("confidence", 0)

            start_str = _ms_to_time_str(start)
            end_str = _ms_to_time_str(end)

            transcript += f"  {word} ({start_str}-{end_str}, 신뢰도: {confidence:.2f})\n"

    return transcript.strip()