package com.meetingoneline.meeting_one_line;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import java.util.HashMap;
import java.util.Map;

/**
 * 서버 상태 확인용 Health Check API
 * 프론트엔드, 모니터링 툴, Docker 환경에서 상태 체크 시 사용
 */
@RestController
@Tag(name = "Health", description = "서버 상태 확인 API")
public class HealthCheckController {

    @Operation(summary = "서버 상태 확인", description = "서버가 정상 동작 중인지 확인합니다.")
    @GetMapping("/api/health")
    public Map<String, Object> healthCheck() {
        Map<String, Object> response = new HashMap<>();
        response.put("status", "UP");
        response.put("service", "meeting-one-line");
        response.put("timestamp", System.currentTimeMillis());
        return response;
    }
}