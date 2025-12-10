package com.meetingoneline.meeting_one_line.feedback.entity;

import com.meetingoneline.meeting_one_line.global.entity.BaseEntity;
import jakarta.persistence.*;
import lombok.*;

@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@Entity
@Table(name = "topics")
public class TopicEntity extends BaseEntity {

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "feedback_id", nullable = false)
    private FeedbackEntity feedback;

    @Column(nullable = false, length = 255)
    private String title;

    @Column(length = 20)
    private String importance;

    @Column(columnDefinition = "TEXT")
    private String summary;

    @Column
    private Integer proportion; // 비중(%)

    public static TopicEntity create(FeedbackEntity feedback, String title, String importance, String summary, Integer proportion) {
        TopicEntity topic = new TopicEntity();
        topic.feedback = feedback;
        topic.title = title;
        topic.importance = importance;
        topic.summary = summary;
        topic.proportion = proportion;
        return topic;
    }
}
