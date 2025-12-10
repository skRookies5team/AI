package com.meetingoneline.meeting_one_line.feedback.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.*;

import java.util.List;
import java.util.UUID;

public class FeedbackResponseDto {

    @Getter
    @Builder
    @AllArgsConstructor
    @NoArgsConstructor
    @Schema(description = "회의 피드백 상세 조회 DTO")
    public static class FeedbackDetail {
        @Schema(description = "회의 UUID", example = "c41b89b7-0b69-4c90-9d3f-3a258fe682c4")
        private UUID meetingId;

        @Schema(description = "Action Item 목록")
        private List<ActionItem> actionItems;

        @Schema(description = "주요 주제 목록")
        private List<Topic> topics;

        @Schema(description = "후속 질문 카테고리 목록")
        private List<FollowUpCategory> followUpCategories;

        @Getter
        @Builder
        @AllArgsConstructor
        @NoArgsConstructor
        @Schema(description = "Action Item DTO")
        public static class ActionItem {
            private String name;
            private String content;
            private Integer orderIndex;
        }

        @Getter
        @Builder
        @AllArgsConstructor
        @NoArgsConstructor
        @Schema(description = "Topic DTO")
        public static class Topic {
            private String title;
            private String importance;
            private String summary;
            private Integer proportion;
        }

        @Getter
        @Builder
        @AllArgsConstructor
        @NoArgsConstructor
        @Schema(description = "Follow-up Category DTO")
        public static class FollowUpCategory {
            private String category;
            private List<Question> questions;

            @Getter
            @Builder
            @AllArgsConstructor
            @NoArgsConstructor
            @Schema(description = "Follow-up Question DTO")
            public static class Question {
                private String question;
                private Integer orderIndex;
            }
        }
    }
}
