package com.meetingoneline.meeting_one_line.meeting.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.*;
import org.springframework.web.multipart.MultipartFile;
import java.time.LocalDateTime;
import java.util.List;

public class MeetingRequestDto {
    @Getter
    @Setter
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "회의 업로드 요청 DTO")
    public static class CreateRequest {
        @Schema(description = "회의 제목", example = "AI 기획 회의")
        private String title;

        @Schema(description = "회의 일시 (YYYY-MM-DDTHH:mm:ss)", example = "2025-10-21T14:00:00")
        private LocalDateTime date;

        @Schema(description = "회의 음성 파일 (wav/mp3)", type = "string", format = "binary")
        private MultipartFile file;
    }

    /**
     * ai 분석 후 > callback api 요청
     */
    @Getter
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    @Schema(name = "MeetingRequestDto.AiCallbackRequest", description = "AI 분석 결과 콜백 요청 DTO")
    public static class AiCallbackRequest{
        @Schema(description = "상태", example = "completed")
        private String status;

        @Schema(description = "회의 요약문", example = "AI 회의록 시스템 구조 논의")
        private String summary;

        @Schema(description = "핵심 키워드 목록", example = "[\"AI\", \"요약\", \"화자분리\"]")
        private List<String> keywords;
        private List<Speaker> speakers;

        private Feedback feedback;

        @Getter
        @NoArgsConstructor
        @AllArgsConstructor
        @Builder
        public static class Speaker {
            private String speakerId;
            private List<Segment> segments;
        }

        @Getter
        @NoArgsConstructor
        @AllArgsConstructor
        @Builder
        public static class Segment {
            private Float start;
            private Float end;
            private String text;
        }

        @Getter
        @NoArgsConstructor
        @AllArgsConstructor
        @Builder
        public static class Feedback {
            private List<ActionItem> actionItems;
            private List<Topic> topics;
            private List<FollowUpCategory> followUpCategories;

            @Getter
            @NoArgsConstructor
            @AllArgsConstructor
            @Builder
            public static class ActionItem {
                private String name;
                private String content;
                private Integer orderIndex;
            }

            @Getter
            @NoArgsConstructor
            @AllArgsConstructor
            @Builder
            public static class Topic {
                private String title;
                private String importance;
                private String summary;
                private Integer proportion;
            }

            @Getter
            @NoArgsConstructor
            @AllArgsConstructor
            @Builder
            public static class FollowUpCategory {
                private String category;
                private List<Question> questions;

                @Getter
                @NoArgsConstructor
                @AllArgsConstructor
                @Builder
                public static class Question {
                    private String question;
                    private Integer orderIndex;
                }
            }
        }
    }

    @Getter
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    @Schema(description = "회의 수정 요청 DTO")
    public static class UpdateRequest {
        @Schema(description = "회의 제목", example = "AI 회의록 개선 회의")
        private String title;

        @Schema(description = "요약문", example = "AI 시스템 개선 방안 논의")
        private String summary;

        @Schema(description = "핵심 키워드 목록", example = "[\"LLM\", \"Whisper\", \"요약\"]")
        private List<String> keywords;

        private List<SpeakerUpdate> speakers;

        @Getter
        @Builder
        @Schema(description = "화자 이름 수정 DTO")
        public static class SpeakerUpdate {
            private String speakerId;
            private String name;
            private List<SegmentUpdate> segments;

            @Getter
            @Setter
            @Schema(description = "발화 수정 DTO")
            public static class SegmentUpdate{
                private Float start;
                private Float end;
                private String text;
            }
        }
    }

}
