package com.meetingoneline.meeting_one_line.global.dto;

import com.fasterxml.jackson.annotation.JsonInclude;
import lombok.Builder;
import lombok.Getter;
import org.springframework.http.HttpStatus;

import java.time.LocalDateTime;
import java.util.Map;

@Getter
@Builder
@JsonInclude(JsonInclude.Include.NON_NULL) // null 필드는 응답에서 제외
public class ApiErrorResponse {

    private final int status;         // HTTP 상태 코드
    private final String message;     // 에러 메시지
    private final String traceId;     // 로그 추적용 ID (MDC)
    private final Map<String, Object> errors; // Validation 세부 정보
    private final LocalDateTime timestamp;    // 응답 시각

    /**
     * 기본 응답 생성 (errors 없음)
     */
    public static ApiErrorResponse of(HttpStatus status, String message, String traceId) {
        return ApiErrorResponse.builder()
                               .status(status.value())
                               .message(message)
                               .traceId(traceId)
                               .timestamp(LocalDateTime.now())
                               .build();
    }

    /**
     * Validation 에러 포함 응답 생성
     */
    public static ApiErrorResponse of(HttpStatus status, String message, String traceId, Map<String, Object> errors) {
        return ApiErrorResponse.builder()
                               .status(status.value())
                               .message(message)
                               .traceId(traceId)
                               .errors(errors)
                               .timestamp(LocalDateTime.now())
                               .build();
    }
}
