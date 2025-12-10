package com.meetingoneline.meeting_one_line.meeting.controller;

import com.meetingoneline.meeting_one_line.global.dto.ApiErrorResponse;
import com.meetingoneline.meeting_one_line.meeting.dto.MeetingRequestDto;
import com.meetingoneline.meeting_one_line.meeting.dto.MeetingResponseDto;
import com.meetingoneline.meeting_one_line.meeting.service.MeetingService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.media.*;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import org.springframework.http.*;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.util.UUID;

@RestController
@RequiredArgsConstructor
@RequestMapping("/api/meetings")
@Tag(name = "Meetings", description = "회의 관리 API")
public class MeetingController {

    private final MeetingService meetingService;

    @Operation(
            summary = "회의 녹음 업로드 및 생성",
            description = "사용자가 회의 음성 파일을 업로드하면 서버가 로컬에 저장 후 AI 서버에 분석 요청을 전송합니다.",
            requestBody = @io.swagger.v3.oas.annotations.parameters.RequestBody(
                    content = @Content(
                            mediaType = MediaType.MULTIPART_FORM_DATA_VALUE,
                            schema = @Schema(implementation = MeetingRequestDto.CreateRequest.class)
                    )
            ),
            responses = {
                    @ApiResponse(responseCode = "201", description = "업로드 성공",
                            content = @Content(schema = @Schema(implementation = MeetingResponseDto.CreateResponse.class))),
                    @ApiResponse(responseCode = "400", description = "잘못된 요청",
                            content = @Content(schema = @Schema(implementation = ApiErrorResponse.class))),
                    @ApiResponse(responseCode = "500", description = "서버 오류",
                            content = @Content(schema = @Schema(implementation = ApiErrorResponse.class)))
            }
    )
    @PostMapping(consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public ResponseEntity<MeetingResponseDto.CreateResponse> uploadMeeting(
            @AuthenticationPrincipal UUID userId,
            @RequestPart("title") String title,
            @RequestPart("date") String date,
            @RequestPart("file") MultipartFile file
    ) {
        LocalDateTime localDateTime = OffsetDateTime.parse(date).toLocalDateTime();

        MeetingRequestDto.CreateRequest req = new MeetingRequestDto.CreateRequest(title, localDateTime, file);
        MeetingResponseDto.CreateResponse response = meetingService.uploadMeeting(userId, req);
        return ResponseEntity.status(HttpStatus.CREATED).body(response);
    }

    @Operation(
            summary = "AI 분석 결과 콜백 (AI 서버 전용)",
            description = "AI 서버가 분석 완료 후 회의 결과 데이터를 전달합니다.",
            requestBody = @io.swagger.v3.oas.annotations.parameters.RequestBody(
                    content = @Content(
                            mediaType = MediaType.APPLICATION_JSON_VALUE,
                            schema = @Schema(implementation = MeetingRequestDto.AiCallbackRequest.class)
                    )
            ),
            responses = {
                    @ApiResponse(responseCode = "200", description = "회의록 결과 저장 성공",
                            content = @Content(schema = @Schema(implementation = MeetingResponseDto.AiCallbackResponse.class))),
                    @ApiResponse(responseCode = "404", description = "회의 ID를 찾을 수 없음",
                            content = @Content(schema = @Schema(implementation = ApiErrorResponse.class)))
            }
    )
    @PostMapping("/{id}/callback")
    public ResponseEntity<MeetingResponseDto.AiCallbackResponse> callbackMeeting(
            @PathVariable("id") UUID meetingId,
            @RequestBody MeetingRequestDto.AiCallbackRequest request
    ) {
        MeetingResponseDto.AiCallbackResponse response = meetingService.processCallback(meetingId, request);
        return ResponseEntity.ok(response);
    }

    @Operation(
            summary = "회의록 목록 조회 (검색/페이지네이션 지원)",
            description = "저장된 회의록 목록을 페이지 단위로 조회하며, title/summary/status별 검색을 지원합니다.",
            responses = {
                    @ApiResponse(responseCode = "200", description = "조회 성공",
                            content = @Content(schema = @Schema(implementation = MeetingResponseDto.ListResponse.class))),
                    @ApiResponse(responseCode = "401", description = "인증 실패",
                            content = @Content(schema = @Schema(implementation = ApiErrorResponse.class)))
            }
    )
    @GetMapping
    public ResponseEntity<MeetingResponseDto.ListResponse> getMeetings(
            @AuthenticationPrincipal UUID userId,
            @RequestParam(defaultValue = "1") int page,
            @RequestParam(defaultValue = "10") int size,
            @RequestParam(required = false) String keyword,
            @RequestParam(required = false) String title,
            @RequestParam(required = false) String summary,
            @RequestParam(required = false) String status
    ) {
        MeetingResponseDto.ListResponse response = meetingService.getMeetings(userId, page, size, keyword, title, summary, status);
        return ResponseEntity.ok(response);
    }

    @Operation(
            summary = "회의록 상세 조회",
            description = "특정 회의의 상세 정보 및 분석 결과를 조회합니다.",
            responses = {
                    @ApiResponse(responseCode = "200", description = "조회 성공",
                            content = @Content(schema = @Schema(implementation = MeetingResponseDto.DetailResponse.class))),
                    @ApiResponse(responseCode = "404", description = "회의록을 찾을 수 없습니다.",
                            content = @Content(schema = @Schema(implementation = ApiErrorResponse.class))),
                    @ApiResponse(responseCode = "403", description = "접근 권한이 없습니다.",
                            content = @Content(schema = @Schema(implementation = ApiErrorResponse.class)))
            }
    )
    @GetMapping("/{id}")
    public ResponseEntity<MeetingResponseDto.DetailResponse> getMeetingDetail(
            @AuthenticationPrincipal UUID userId,
            @PathVariable("id") UUID meetingId
    ) {
        MeetingResponseDto.DetailResponse response = meetingService.getMeetingDetail(userId, meetingId);
        return ResponseEntity.ok(response);
    }

    @Operation(
            summary = "회의록 수정",
            description = "회의 제목, 요약문, 키워드 등을 수정합니다.(* segments 수정시, speakerId는 일치해야합니다.)",
            requestBody = @io.swagger.v3.oas.annotations.parameters.RequestBody(
                    content = @Content(
                            mediaType = MediaType.APPLICATION_JSON_VALUE,
                            schema = @Schema(implementation = MeetingRequestDto.UpdateRequest.class)
                    )
            ),
            responses = {
                    @ApiResponse(responseCode = "200", description = "수정 성공",
                            content = @Content(schema = @Schema(implementation = MeetingResponseDto.CommonMessage.class))),
                    @ApiResponse(responseCode = "404", description = "회의록을 찾을 수 없습니다.",
                            content = @Content(schema = @Schema(implementation = ApiErrorResponse.class))),
                    @ApiResponse(responseCode = "403", description = "접근 권한이 없습니다.",
                            content = @Content(schema = @Schema(implementation = ApiErrorResponse.class)))
            }
    )
    @PutMapping("/{id}")
    public ResponseEntity<MeetingResponseDto.CommonMessage> updateMeeting(
            @AuthenticationPrincipal UUID userId,
            @PathVariable("id") UUID meetingId,
            @RequestBody MeetingRequestDto.UpdateRequest request
    ) {
        MeetingResponseDto.CommonMessage response = meetingService.updateMeeting(userId, meetingId, request);
        return ResponseEntity.ok(response);
    }

    @Operation(
            summary = "회의록 삭제",
            description = "특정 회의록 및 관련 파일을 Soft Delete 처리합니다.",
            responses = {
                    @ApiResponse(responseCode = "200", description = "삭제 성공",
                            content = @Content(schema = @Schema(implementation = MeetingResponseDto.CommonMessage.class))),
                    @ApiResponse(responseCode = "404", description = "회의록을 찾을 수 없습니다.",
                            content = @Content(schema = @Schema(implementation = ApiErrorResponse.class))),
                    @ApiResponse(responseCode = "403", description = "접근 권한이 없습니다.",
                            content = @Content(schema = @Schema(implementation = ApiErrorResponse.class)))
            }
    )
    @DeleteMapping("/{id}")
    public ResponseEntity<MeetingResponseDto.CommonMessage> deleteMeeting(
            @AuthenticationPrincipal UUID userId,
            @PathVariable("id") UUID meetingId
    ) {
        MeetingResponseDto.CommonMessage response = meetingService.deleteMeeting(userId, meetingId);
        return ResponseEntity.ok(response);
    }

    @Operation(
            summary = "AI 분석 진행 상태 조회 (Polling)",
            description = """
                로그인한 사용자의 회의 중 **AI 분석이 아직 완료되지 않은(`UPLOADED`, `PROCESSING`) 회의 목록**을 반환합니다.
                기본적으로 최근 1시간 이내(`created_at >= NOW() - INTERVAL 1 HOUR`) 생성된 회의만 포함됩니다.
                프론트엔드는 이 API를 5초~10초 간격으로 호출하여 분석 완료 여부를 확인할 수 있습니다.
                """,
            responses = {
                    @ApiResponse(responseCode = "200", description = "조회 성공",
                            content = @Content(schema = @Schema(implementation = MeetingResponseDto.CheckAnalysisCompleteResponse.class))),
            }
    )
    @GetMapping("/check")
    public ResponseEntity<MeetingResponseDto.CheckAnalysisCompleteResponse> checkAnalysisComplete(
            @AuthenticationPrincipal UUID userId
    ) {
        MeetingResponseDto.CheckAnalysisCompleteResponse response = meetingService.checkAnalysisComplete(userId);
        return ResponseEntity.ok(response);
    }

}
