package com.meetingoneline.meeting_one_line.feedback.controller;

import com.meetingoneline.meeting_one_line.feedback.dto.FeedbackResponseDto;
import com.meetingoneline.meeting_one_line.feedback.service.FeedbackService;
import com.meetingoneline.meeting_one_line.global.dto.ApiErrorResponse;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import io.swagger.v3.oas.annotations.media.*;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import org.springframework.http.*;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

import java.util.UUID;

@RestController
@RequiredArgsConstructor
@RequestMapping("/api/meetings/{meetingId}/feedback")
@Tag(name = "Feedback", description = "회의 피드백 조회 API")
public class FeedbackController {

    private final FeedbackService feedbackService;

    @Operation(
            summary = "회의 피드백 조회",
            description = "회의 분석이 완료된 후, AI가 생성한 Action Items, Topics, Follow-up Questions 정보를 조회",
            responses = {
                    @ApiResponse(responseCode = "200", description = "조회 성공",
                            content = @Content(schema = @Schema(implementation = FeedbackResponseDto.FeedbackDetail.class))),
                    @ApiResponse(responseCode = "404", description = "피드백을 찾을 수 없음",
                            content = @Content(schema = @Schema(implementation = ApiErrorResponse.class)))
            }
    )
    @GetMapping
    public ResponseEntity<FeedbackResponseDto.FeedbackDetail> getFeedback(
            @AuthenticationPrincipal UUID userId,
            @PathVariable("meetingId") UUID meetingId
    ) {
        FeedbackResponseDto.FeedbackDetail response = feedbackService.getFeedbackByMeetingId(meetingId);
        return ResponseEntity.ok(response);
    }
}
