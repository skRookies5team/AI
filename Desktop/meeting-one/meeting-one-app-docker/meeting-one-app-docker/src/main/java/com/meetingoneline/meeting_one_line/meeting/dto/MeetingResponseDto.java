package com.meetingoneline.meeting_one_line.meeting.dto;

import com.meetingoneline.meeting_one_line.meeting.entity.MeetingEntity;
import com.meetingoneline.meeting_one_line.meeting.enums.RecordSaveStatus;
import io.swagger.v3.oas.annotations.media.Schema;
import lombok.*;
import org.springframework.data.domain.Page;

import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

public class MeetingResponseDto {
    @Getter
    @Builder
    @AllArgsConstructor
    @Schema(description = "회의 업로드 응답 DTO")
    public static class CreateResponse {
        @Schema(description = "회의 UUID", example = "c41b89b7-0b69-4c90-9d3f-3a258fe682c4")
        private UUID meetingId;

        @Schema(description = "상태", example = "uploaded")
        private RecordSaveStatus status;

        @Schema(description = "메시지", example = "파일 업로드 완료 및 분석 요청 전송됨")
        private String message;
    }

    /**
     * ai 분석 후 > callback api 응답
     */
    @Getter
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    @Schema(name = "MeetingRequestDto.AiCallbackResponse", description = "AI 분석 결과 콜백 응답 DTO")
    public static class AiCallbackResponse {
        private String message;
    }

    @Getter
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    @Schema(description = "분석 완료 회의 목록 응답 DTO")
    public static class CheckAnalysisCompleteResponse {
        @Schema(description = "분석 완료된 회의 리스트")
        private List<CheckAnalysisComplete> completedMeetings;

        @Getter
        @NoArgsConstructor
        @AllArgsConstructor
        @Builder
        public static class CheckAnalysisComplete {
            @Schema(description = "회의 UUID")
            private UUID meetingId;

            @Schema(description = "회의 제목")
            private String title;

            @Schema(description = "회의 상태", example = "completed")
            private RecordSaveStatus status;

            @Schema(description = "생성 시각")
            private LocalDateTime createdAt;
        }
    }


    @Getter
    @Builder
    @AllArgsConstructor
    @NoArgsConstructor
    @Schema(description = "회의록 단일 아이템 DTO")
    public static class ListItem {
        @Schema(description = "회의 UUID")
        private UUID meetingId;

        @Schema(description = "회의 제목")
        private String title;

        @Schema(description = "회의 상태", example = "completed")
        private RecordSaveStatus status;

        @Schema(description = "회의 요약문")
        private String summary;

        @Schema(description = "생성 시각")
        private LocalDateTime createdAt;

        public static ListItem from(MeetingEntity entity) {
            return ListItem.builder()
                           .meetingId(entity.getId())
                           .title(entity.getTitle())
                           .status(entity.getStatus())
                           .summary(entity.getSummary())
                           .createdAt(entity.getCreatedAt())
                           .build();
        }
    }

    @Getter
    @Builder
    @AllArgsConstructor
    @NoArgsConstructor
    @Schema(description = "회의록 목록 응답 DTO")
    public static class ListResponse {
        private List<ListItem> content;
        private int page;
        private int size;
        private int totalPages;

        public static ListResponse from(Page<MeetingEntity> pageResult) {
            List<ListItem> items = pageResult.getContent().stream()
                                             .map(ListItem::from)
                                             .toList();

            return ListResponse.builder()
                               .content(items)
                               .page(pageResult.getNumber() + 1)
                               .size(pageResult.getSize())
                               .totalPages(pageResult.getTotalPages())
                               .build();
        }
    }

    @Getter
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "회의 상세 조회 응답 DTO")
    public static class DetailResponse {
        @Schema(description = "회의 UUID", example = "c41b89b7-0b69-4c90-9d3f-3a258fe682c4")
        private UUID meetingId;

        @Schema(description = "회의 제목", example = "AI 회의록 설계 회의")
        private String title;

        @Schema(description = "회의 일시", example = "2025-10-20T15:00:00")
        private LocalDateTime date;

        @Schema(description = "회의 상태", example = "completed")
        private String status;

        @Schema(description = "회의 요약문", example = "AI 회의록 시스템 구조 논의")
        private String summary;

        @Schema(description = "핵심 키워드 목록")
        private List<String> keywords;

        @Schema(description = "화자 목록")
        private List<Speaker> speakers;

        @Schema(description = "파일 경로", example = "/data/uploads/meeting_123.wav")
        private String filePath;

        @Getter
        @Builder
        @NoArgsConstructor
        @AllArgsConstructor
        @Schema(description = "화자 정보")
        public static class Speaker {
            private String speakerId;
            private String name;
            private List<Segment> segments;
        }

        @Getter
        @Builder
        @NoArgsConstructor
        @AllArgsConstructor
        @Schema(description = "화자 세그먼트 정보")
        public static class Segment {
            private Float start;
            private Float end;
            private String text;
        }
    }

    @Getter
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "공통 메시지 응답 DTO")
    public static class CommonMessage {
        @Schema(description = "응답 메시지", example = "회의록이 수정되었습니다.")
        private String message;
    }

}
